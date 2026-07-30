"""Receipt-coupled dispatch: durable intent before consequential I/O.

This reference runtime gives a precise local guarantee.  It does not make an
external effect atomic with the local journal.  A crash after remote execution
therefore becomes ``UNKNOWN`` until an adapter can reconcile it; absence is
never rewritten as failure.
"""

from __future__ import annotations

import enum
import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from bulla._canonical import canonical_jcs_int
from bulla.action_receipt import (
    ActionReceipt,
    build_action_receipt_v04,
    sign_action_receipt_v04,
    verify_receipt,
)
from bulla.envelope import RecourseEnvelope
from bulla.experimental.generalization import EffectWarrant, HarmClass


PROFILE = "bulla.causal-answerability/0.1-experimental"


class BoundaryError(ValueError):
    pass


class BoundaryState(str, enum.Enum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    CONFLICT = "CONFLICT"
    ROUTED = "ROUTED"


class IdempotencyStatus(str, enum.Enum):
    NONE = "NONE"
    DECLARED = "DECLARED"
    VERIFIED = "VERIFIED"


class ReconciliationStatus(str, enum.Enum):
    UNAVAILABLE = "UNAVAILABLE"
    QUERYABLE = "QUERYABLE"


class OrderingStatus(str, enum.Enum):
    LOCAL = "LOCAL"
    PROVIDER = "PROVIDER"
    RAIL_CAS = "RAIL_CAS"


class OutcomeClass(str, enum.Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = frozenset({
    BoundaryState.SUCCEEDED,
    BoundaryState.FAILED,
    BoundaryState.CANCELLED,
    BoundaryState.EXPIRED,
    BoundaryState.CONFLICT,
    BoundaryState.ROUTED,
})


@dataclass(frozen=True)
class AdapterCapabilities:
    idempotency: IdempotencyStatus
    reconciliation: ReconciliationStatus
    ordering: OrderingStatus
    reversible_effect: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "idempotency": self.idempotency.value,
            "reconciliation": self.reconciliation.value,
            "ordering": self.ordering.value,
            "reversible_effect": self.reversible_effect,
        }


@dataclass(frozen=True)
class DispatchResult:
    outcome: OutcomeClass
    provider_ref: str
    evidence_hash: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.provider_ref:
            raise BoundaryError("dispatch result requires provider_ref")
        if not self.evidence_hash.startswith("sha256:"):
            raise BoundaryError("dispatch result requires a sha256 evidence hash")


class DispatchAdapter(Protocol):
    adapter_id: str
    adapter_version: str
    capabilities: AdapterCapabilities

    def dispatch(self, request: dict[str, Any], *, idempotency_key: str) -> DispatchResult: ...
    def reconcile(self, *, idempotency_key: str) -> DispatchResult: ...


@dataclass(frozen=True)
class BoundaryPolicy:
    authority_regime_hash: str
    semantic_epoch: str

    def __post_init__(self) -> None:
        for name, value in (
            ("authority_regime_hash", self.authority_regime_hash),
            ("semantic_epoch", self.semantic_epoch),
        ):
            if not isinstance(value, str) or not value.startswith("sha256:"):
                raise BoundaryError(f"{name} must be a sha256 digest")


@dataclass(frozen=True)
class BoundarySnapshot:
    intent_id: str
    state: BoundaryState
    sequence: int
    intent_receipt_hash: str
    latest_receipt_hash: str
    request_hash: str
    adapter_id: str
    idempotency_key_hash: str
    harm_class: HarmClass
    semantic_epoch: str
    cause: str | None = None

    def __bool__(self) -> bool:
        raise TypeError("BoundarySnapshot has no truth value; inspect .state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "intent_id": self.intent_id,
            "state": self.state.value,
            "sequence": self.sequence,
            "intent_receipt_hash": self.intent_receipt_hash,
            "latest_receipt_hash": self.latest_receipt_hash,
            "request_hash": self.request_hash,
            "adapter_id": self.adapter_id,
            "idempotency_key_hash": self.idempotency_key_hash,
            "harm_class": self.harm_class.value,
            "semantic_epoch": self.semantic_epoch,
            "cause": self.cause,
        }


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_jcs_int(value).encode("utf-8")).hexdigest()


def _receipt_bytes(receipt: ActionReceipt) -> bytes:
    return (json.dumps(receipt.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class ActionBoundary:
    """SQLite/WAL journal and state reducer for one local dispatch boundary."""

    def __init__(self, path: str | Path, *, policy: BoundaryPolicy):
        self.path = Path(path)
        self.policy = policy
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS intents (
                  intent_id TEXT PRIMARY KEY,
                  state TEXT NOT NULL,
                  sequence INTEGER NOT NULL,
                  intent_receipt_hash TEXT NOT NULL,
                  latest_receipt_hash TEXT NOT NULL,
                  request_json TEXT NOT NULL,
                  request_hash TEXT NOT NULL,
                  adapter_id TEXT NOT NULL,
                  adapter_version TEXT NOT NULL,
                  capabilities_json TEXT NOT NULL,
                  idempotency_key_hash TEXT NOT NULL,
                  effect_warrant_hash TEXT NOT NULL,
                  harm_class TEXT NOT NULL,
                  semantic_epoch TEXT NOT NULL,
                  envelope_json TEXT NOT NULL,
                  permitted_terminal_json TEXT NOT NULL,
                  prepare_binding_hash TEXT NOT NULL,
                  cause TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                  intent_id TEXT NOT NULL,
                  sequence INTEGER NOT NULL,
                  action_type TEXT NOT NULL,
                  parent_receipt_hash TEXT,
                  receipt_hash TEXT NOT NULL UNIQUE,
                  receipt_bytes BLOB NOT NULL,
                  PRIMARY KEY (intent_id, sequence),
                  FOREIGN KEY(intent_id) REFERENCES intents(intent_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_intent_per_adapter_key
                  ON intents(adapter_id, idempotency_key_hash);
                """
            )

    def prepare(
        self,
        *,
        request: dict[str, Any],
        adapter: DispatchAdapter,
        idempotency_key: str,
        effect_warrant: EffectWarrant,
        envelope: RecourseEnvelope,
        signer: Any,
        claimed_at: str,
        deadline_checkpoint: dict[str, Any],
        required_evidence: tuple[str, ...] = (),
        permitted_terminal_outcomes: tuple[OutcomeClass, ...] = (
            OutcomeClass.SUCCEEDED, OutcomeClass.FAILED, OutcomeClass.UNKNOWN,
        ),
        intent_id: str | None = None,
        event_id: str | None = None,
    ) -> BoundarySnapshot:
        if effect_warrant.authority_regime_hash != self.policy.authority_regime_hash:
            raise BoundaryError("effect warrant belongs to another authority regime")
        if effect_warrant.semantic_epoch != self.policy.semantic_epoch:
            raise BoundaryError("effect warrant is stale for this semantic epoch")
        intent_id = intent_id or str(uuid.uuid4())
        event_id = event_id or str(uuid.uuid4())
        request_hash = _hash(request)
        idempotency_key_hash = _hash({"idempotency_key": idempotency_key})
        subject = {
            "profile": PROFILE,
            "intent_id": intent_id,
            "request_hash": request_hash,
            "adapter": {"id": adapter.adapter_id, "version": adapter.adapter_version},
            "capabilities": adapter.capabilities.to_dict(),
            "idempotency_key_hash": idempotency_key_hash,
            "effect_warrant_hash": effect_warrant.warrant_hash,
            "harm_class": effect_warrant.harm_class.value,
            "semantic_epoch": self.policy.semantic_epoch,
            "deadline_checkpoint": deadline_checkpoint,
            "required_evidence": list(required_evidence),
            "permitted_terminal_outcomes": [item.value for item in permitted_terminal_outcomes],
        }
        if not permitted_terminal_outcomes or len(set(permitted_terminal_outcomes)) != len(permitted_terminal_outcomes):
            raise BoundaryError("permitted terminal outcomes must be non-empty and unique")
        receipt = sign_action_receipt_v04(
            build_action_receipt_v04(
                action={"type": "bulla.action.intent", "subject": subject},
                diagnostic_ref={"status": "not_applicable"},
                envelope=envelope,
                event_id=event_id,
                claimed_at=claimed_at,
                producer={"profile": PROFILE},
            ),
            signer,
        )
        verification = verify_receipt(receipt.to_dict())
        if not verification.ok or verification.verified_to != "attestation":
            raise BoundaryError("intent receipt did not verify to attestation")
        self._require_authority_binding(verification, envelope, signer)

        receipt_hash = receipt.attestation_hash
        receipt_bytes = _receipt_bytes(receipt)
        prepare_binding_hash = _hash({
            "request_hash": request_hash,
            "adapter": {"id": adapter.adapter_id, "version": adapter.adapter_version},
            "capabilities": adapter.capabilities.to_dict(),
            "idempotency_key_hash": idempotency_key_hash,
            "effect_warrant_hash": effect_warrant.warrant_hash,
            "harm_class": effect_warrant.harm_class.value,
            "semantic_epoch": self.policy.semantic_epoch,
            "deadline_checkpoint": deadline_checkpoint,
            "required_evidence": list(required_evidence),
            "permitted_terminal_outcomes": [item.value for item in permitted_terminal_outcomes],
            "envelope": envelope.to_dict(),
        })
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM intents WHERE adapter_id = ? AND idempotency_key_hash = ?",
                    (adapter.adapter_id, idempotency_key_hash),
                ).fetchone()
                if existing is not None:
                    exact_repeat = existing["prepare_binding_hash"] == prepare_binding_hash
                    connection.execute("ROLLBACK")
                    if exact_repeat:
                        return self.inspect(existing["intent_id"])
                    raise BoundaryError(
                        "idempotency key is already bound to a different intent"
                    )
                connection.execute(
                    """INSERT INTO intents (
                         intent_id, state, sequence, intent_receipt_hash, latest_receipt_hash,
                         request_json, request_hash, adapter_id, adapter_version,
                         capabilities_json, idempotency_key_hash, effect_warrant_hash,
                         harm_class, semantic_epoch, envelope_json, permitted_terminal_json,
                         prepare_binding_hash, cause
                       ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                    (
                        intent_id, BoundaryState.PREPARED.value, receipt_hash, receipt_hash,
                        canonical_jcs_int(request), request_hash,
                        adapter.adapter_id, adapter.adapter_version,
                        canonical_jcs_int(adapter.capabilities.to_dict()), idempotency_key_hash,
                        effect_warrant.warrant_hash, effect_warrant.harm_class.value,
                        self.policy.semantic_epoch, canonical_jcs_int(envelope.to_dict()),
                        canonical_jcs_int([item.value for item in permitted_terminal_outcomes]),
                        prepare_binding_hash,
                    ),
                )
                connection.execute(
                    "INSERT INTO events VALUES (?, 0, ?, NULL, ?, ?)",
                    (intent_id, "bulla.action.intent", receipt_hash, receipt_bytes),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return self.inspect(intent_id)

    def inspect(self, intent_id: str) -> BoundarySnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,),
            ).fetchone()
        if row is None:
            raise BoundaryError(f"unknown intent {intent_id!r}")
        return BoundarySnapshot(
            intent_id=row["intent_id"], state=BoundaryState(row["state"]),
            sequence=int(row["sequence"]), intent_receipt_hash=row["intent_receipt_hash"],
            latest_receipt_hash=row["latest_receipt_hash"], request_hash=row["request_hash"],
            adapter_id=row["adapter_id"], idempotency_key_hash=row["idempotency_key_hash"],
            harm_class=HarmClass(row["harm_class"]), semantic_epoch=row["semantic_epoch"],
            cause=row["cause"],
        )

    def receipt_history(self, intent_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT receipt_bytes FROM events WHERE intent_id = ? ORDER BY sequence",
                (intent_id,),
            ).fetchall()
        return tuple(json.loads(bytes(row[0]).decode("utf-8")) for row in rows)

    def envelope_for(self, intent_id: str) -> RecourseEnvelope:
        """Return the exact committed envelope for local replay tooling."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT envelope_json FROM intents WHERE intent_id = ?", (intent_id,),
            ).fetchone()
        if row is None:
            raise BoundaryError(f"unknown intent {intent_id!r}")
        return RecourseEnvelope.from_dict(json.loads(row[0]))

    @staticmethod
    def _require_authority_binding(
        verification: Any, envelope: RecourseEnvelope, signer: Any,
    ) -> None:
        if verification.authority_authentic != "verified":
            raise BoundaryError("action authority is not authenticated")
        principal = envelope.authority.principal if envelope.authority else None
        same_principal = principal == getattr(signer, "issuer", None)
        delegated = (
            envelope.deed_schema == "0.3"
            and verification.chain_integrity == "verified"
            and verification.principal_binding == "verified"
            and verification.policy_binding == "verified"
            and verification.scope_binding == "verified"
            and verification.revocation_status != "revoked"
            and verification.temporal_status not in {"expired", "not_yet_valid"}
        )
        if not (same_principal or delegated):
            raise BoundaryError("action signer is neither the principal nor a verified delegate")

    def _require_committed_adapter(
        self, intent_id: str, adapter: DispatchAdapter, idempotency_key: str,
    ) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE intent_id = ?", (intent_id,),
            ).fetchone()
        if row is None:
            raise BoundaryError(f"unknown intent {intent_id!r}")
        if row["adapter_id"] != adapter.adapter_id or row["adapter_version"] != adapter.adapter_version:
            raise BoundaryError("adapter identity/version does not match the committed intent")
        if row["capabilities_json"] != canonical_jcs_int(adapter.capabilities.to_dict()):
            raise BoundaryError("adapter capabilities differ from the committed declaration")
        if row["idempotency_key_hash"] != _hash({"idempotency_key": idempotency_key}):
            raise BoundaryError("idempotency key does not match the committed hash")
        return row

    def _route_cause(self, harm: HarmClass, capabilities: AdapterCapabilities) -> str | None:
        if harm is HarmClass.CATEGORICAL_REFUSE:
            return "CATEGORICAL_EFFECT_BARRIER"
        if harm is HarmClass.HUMAN_REVIEW_REQUIRED:
            return "HUMAN_REVIEW_REQUIRED"
        if harm is HarmClass.REVERSIBLE_ONLY and not capabilities.reversible_effect:
            return "REVERSIBILITY_NOT_ESTABLISHED"
        if (
            capabilities.idempotency is not IdempotencyStatus.VERIFIED
            and capabilities.reconciliation is ReconciliationStatus.UNAVAILABLE
        ):
            return "NO_VERIFIED_IDEMPOTENCY_OR_RECONCILIATION"
        return None

    def _append(
        self,
        *,
        intent_id: str,
        expected: set[BoundaryState],
        new_state: BoundaryState,
        action_type: str,
        subject: dict[str, Any],
        envelope: RecourseEnvelope,
        signer: Any,
        claimed_at: str,
        cause: str | None = None,
        event_id: str | None = None,
    ) -> BoundarySnapshot:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM intents WHERE intent_id = ?", (intent_id,),
                ).fetchone()
                if row is None:
                    raise BoundaryError(f"unknown intent {intent_id!r}")
                current = BoundaryState(row["state"])
                if current not in expected:
                    raise BoundaryError(
                        f"invalid transition {current.value} -> {new_state.value} for {intent_id}"
                    )
                if row["envelope_json"] != canonical_jcs_int(envelope.to_dict()):
                    raise BoundaryError("transition envelope differs from the committed intent")
                sequence = int(row["sequence"]) + 1
                full_subject = {
                    "profile": PROFILE,
                    "intent_id": intent_id,
                    "sequence": sequence,
                    "parent_attestation_hash": row["latest_receipt_hash"],
                    "semantic_epoch": row["semantic_epoch"],
                    **subject,
                }
                receipt = sign_action_receipt_v04(
                    build_action_receipt_v04(
                        action={"type": action_type, "subject": full_subject},
                        diagnostic_ref={"status": "not_applicable"}, envelope=envelope,
                        event_id=event_id or str(uuid.uuid4()), claimed_at=claimed_at,
                        producer={"profile": PROFILE},
                    ), signer,
                )
                verification = verify_receipt(receipt.to_dict())
                if not verification.ok:
                    raise BoundaryError("transition receipt failed integrity verification")
                self._require_authority_binding(verification, envelope, signer)
                receipt_hash = receipt.attestation_hash
                connection.execute(
                    """UPDATE intents SET state=?, sequence=?, latest_receipt_hash=?, cause=?
                       WHERE intent_id=? AND sequence=?""",
                    (new_state.value, sequence, receipt_hash, cause, intent_id, sequence - 1),
                )
                if connection.total_changes != 1:
                    raise BoundaryError("concurrent transition lost compare-and-swap")
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        intent_id, sequence, action_type, row["latest_receipt_hash"],
                        receipt_hash, _receipt_bytes(receipt),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return self.inspect(intent_id)

    def dispatch(
        self,
        intent_id: str,
        *,
        adapter: DispatchAdapter,
        idempotency_key: str,
        envelope: RecourseEnvelope,
        signer: Any,
        claimed_at: str,
        crash_after_commit: bool = False,
        crash_after_remote: bool = False,
    ) -> BoundarySnapshot:
        snapshot = self.inspect(intent_id)
        if snapshot.state is not BoundaryState.PREPARED:
            raise BoundaryError("dispatch requires PREPARED intent")
        self._require_committed_adapter(intent_id, adapter, idempotency_key)
        cause = self._route_cause(snapshot.harm_class, adapter.capabilities)
        if cause:
            return self._append(
                intent_id=intent_id, expected={BoundaryState.PREPARED},
                new_state=BoundaryState.ROUTED, action_type="bulla.action.route",
                subject={"cause": cause}, envelope=envelope, signer=signer,
                claimed_at=claimed_at, cause=cause,
            )
        self._append(
            intent_id=intent_id, expected={BoundaryState.PREPARED},
            new_state=BoundaryState.DISPATCHING, action_type="bulla.action.dispatch",
            subject={"adapter": adapter.adapter_id, "capabilities": adapter.capabilities.to_dict()},
            envelope=envelope, signer=signer, claimed_at=claimed_at,
        )
        if crash_after_commit:
            return self.inspect(intent_id)
        with self._connect() as connection:
            request = json.loads(connection.execute(
                "SELECT request_json FROM intents WHERE intent_id=?", (intent_id,),
            ).fetchone()[0])
        try:
            result = adapter.dispatch(request, idempotency_key=idempotency_key)
        except Exception as exc:
            return self._append(
                intent_id=intent_id, expected={BoundaryState.DISPATCHING},
                new_state=BoundaryState.UNKNOWN, action_type="bulla.action.outcome",
                subject={"outcome": "UNKNOWN", "detail": f"adapter exception: {type(exc).__name__}"},
                envelope=envelope, signer=signer, claimed_at=claimed_at,
                cause="REMOTE_OUTCOME_UNRESOLVED",
            )
        if crash_after_remote:
            return self.inspect(intent_id)
        return self._record_result(
            intent_id, result=result, action_type="bulla.action.outcome",
            expected={BoundaryState.DISPATCHING}, envelope=envelope,
            signer=signer, claimed_at=claimed_at,
        )

    def _record_result(
        self, intent_id: str, *, result: DispatchResult, action_type: str,
        expected: set[BoundaryState], envelope: RecourseEnvelope, signer: Any,
        claimed_at: str,
    ) -> BoundarySnapshot:
        new_state = BoundaryState(result.outcome.value)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT permitted_terminal_json FROM intents WHERE intent_id = ?", (intent_id,),
            ).fetchone()
        if row is None:
            raise BoundaryError(f"unknown intent {intent_id!r}")
        permitted = set(json.loads(row[0]))
        if result.outcome.value not in permitted:
            return self._append(
                intent_id=intent_id, expected=expected, new_state=BoundaryState.CONFLICT,
                action_type=action_type,
                subject={
                    "outcome": result.outcome.value,
                    "provider_ref": result.provider_ref,
                    "evidence_hash": result.evidence_hash,
                    "detail": result.detail,
                    "permitted_terminal_outcomes": sorted(permitted),
                },
                envelope=envelope, signer=signer, claimed_at=claimed_at,
                cause="UNPERMITTED_EXTERNAL_OUTCOME",
            )
        return self._append(
            intent_id=intent_id, expected=expected, new_state=new_state,
            action_type=action_type,
            subject={
                "outcome": result.outcome.value,
                "provider_ref": result.provider_ref,
                "evidence_hash": result.evidence_hash,
                "detail": result.detail,
            },
            envelope=envelope, signer=signer, claimed_at=claimed_at,
            cause="REMOTE_OUTCOME_UNRESOLVED" if new_state is BoundaryState.UNKNOWN else None,
        )

    def recover(
        self, intent_id: str, *, adapter: DispatchAdapter, idempotency_key: str,
        envelope: RecourseEnvelope, signer: Any, claimed_at: str,
    ) -> BoundarySnapshot:
        snapshot = self.inspect(intent_id)
        if snapshot.state not in {BoundaryState.DISPATCHING, BoundaryState.UNKNOWN}:
            raise BoundaryError("recovery requires DISPATCHING or UNKNOWN state")
        self._require_committed_adapter(intent_id, adapter, idempotency_key)
        if adapter.capabilities.reconciliation is ReconciliationStatus.UNAVAILABLE:
            if snapshot.state is BoundaryState.DISPATCHING:
                return self._append(
                    intent_id=intent_id, expected={BoundaryState.DISPATCHING},
                    new_state=BoundaryState.UNKNOWN, action_type="bulla.action.reconcile",
                    subject={"outcome": "UNKNOWN", "cause": "RECONCILIATION_UNAVAILABLE"},
                    envelope=envelope, signer=signer, claimed_at=claimed_at,
                    cause="RECONCILIATION_UNAVAILABLE",
                )
            return snapshot
        result = adapter.reconcile(idempotency_key=idempotency_key)
        return self._record_result(
            intent_id, result=result, action_type="bulla.action.reconcile",
            expected={BoundaryState.DISPATCHING, BoundaryState.UNKNOWN},
            envelope=envelope, signer=signer, claimed_at=claimed_at,
        )

    def retry_dispatch(
        self, intent_id: str, *, adapter: DispatchAdapter, idempotency_key: str,
        envelope: RecourseEnvelope, signer: Any, claimed_at: str,
    ) -> BoundarySnapshot:
        """Retry an unresolved effect only under a verified idempotency contract."""
        snapshot = self.inspect(intent_id)
        if snapshot.state is not BoundaryState.UNKNOWN:
            raise BoundaryError("retry requires UNKNOWN state")
        if adapter.capabilities.idempotency is not IdempotencyStatus.VERIFIED:
            raise BoundaryError("retry requires verified adapter idempotency")
        self._require_committed_adapter(intent_id, adapter, idempotency_key)
        self._append(
            intent_id=intent_id, expected={BoundaryState.UNKNOWN},
            new_state=BoundaryState.DISPATCHING, action_type="bulla.action.dispatch",
            subject={"adapter": adapter.adapter_id, "retry": True,
                     "capabilities": adapter.capabilities.to_dict()},
            envelope=envelope, signer=signer, claimed_at=claimed_at,
        )
        with self._connect() as connection:
            request = json.loads(connection.execute(
                "SELECT request_json FROM intents WHERE intent_id=?", (intent_id,),
            ).fetchone()[0])
        try:
            result = adapter.dispatch(request, idempotency_key=idempotency_key)
        except Exception as exc:
            result = DispatchResult(
                OutcomeClass.UNKNOWN, provider_ref=f"adapter-exception:{type(exc).__name__}",
                evidence_hash=_hash({"exception": type(exc).__name__}),
            )
        return self._record_result(
            intent_id, result=result, action_type="bulla.action.outcome",
            expected={BoundaryState.DISPATCHING}, envelope=envelope,
            signer=signer, claimed_at=claimed_at,
        )

    def cancel(
        self, intent_id: str, *, envelope: RecourseEnvelope, signer: Any,
        claimed_at: str, reason: str,
    ) -> BoundarySnapshot:
        return self._append(
            intent_id=intent_id, expected={BoundaryState.PREPARED},
            new_state=BoundaryState.CANCELLED, action_type="bulla.action.cancel",
            subject={"reason": reason}, envelope=envelope, signer=signer,
            claimed_at=claimed_at,
        )

    def expire(
        self, intent_id: str, *, envelope: RecourseEnvelope, signer: Any,
        claimed_at: str, checkpoint: dict[str, Any],
    ) -> BoundarySnapshot:
        return self._append(
            intent_id=intent_id, expected={BoundaryState.PREPARED},
            new_state=BoundaryState.EXPIRED, action_type="bulla.action.expire",
            subject={"checkpoint": checkpoint}, envelope=envelope, signer=signer,
            claimed_at=claimed_at,
        )

    def record_external_outcome(
        self, intent_id: str, *, result: DispatchResult, envelope: RecourseEnvelope,
        signer: Any, claimed_at: str,
    ) -> BoundarySnapshot:
        """Append later provider evidence; incompatible terminal evidence conflicts."""
        snapshot = self.inspect(intent_id)
        if snapshot.state in {BoundaryState.SUCCEEDED, BoundaryState.FAILED}:
            if snapshot.state.value == result.outcome.value:
                return snapshot
            return self._append(
                intent_id=intent_id, expected={snapshot.state},
                new_state=BoundaryState.CONFLICT, action_type="bulla.action.outcome",
                subject={
                    "outcome": result.outcome.value, "provider_ref": result.provider_ref,
                    "evidence_hash": result.evidence_hash,
                    "conflicts_with_state": snapshot.state.value,
                },
                envelope=envelope, signer=signer, claimed_at=claimed_at,
                cause="CONFLICTING_TERMINAL_OUTCOME",
            )
        if snapshot.state not in {BoundaryState.DISPATCHING, BoundaryState.UNKNOWN}:
            raise BoundaryError("external outcome cannot apply in the current state")
        return self._record_result(
            intent_id, result=result, action_type="bulla.action.outcome",
            expected={snapshot.state}, envelope=envelope, signer=signer,
            claimed_at=claimed_at,
        )


class LocalPaymentRail:
    """Captive deterministic rail used to exercise idempotency and reconciliation."""

    adapter_id = "bulla.fixture.local-payment-rail"
    adapter_version = "1"
    capabilities = AdapterCapabilities(
        idempotency=IdempotencyStatus.VERIFIED,
        reconciliation=ReconciliationStatus.QUERYABLE,
        ordering=OrderingStatus.RAIL_CAS,
        reversible_effect=True,
    )

    def __init__(self) -> None:
        self._results: dict[str, DispatchResult] = {}
        self.effect_count = 0

    def dispatch(self, request: dict[str, Any], *, idempotency_key: str) -> DispatchResult:
        if idempotency_key in self._results:
            return self._results[idempotency_key]
        self.effect_count += 1
        evidence = _hash({"request": request, "rail_sequence": self.effect_count})
        result = DispatchResult(
            OutcomeClass.SUCCEEDED,
            provider_ref=f"local-rail:{self.effect_count}",
            evidence_hash=evidence,
        )
        self._results[idempotency_key] = result
        return result

    def reconcile(self, *, idempotency_key: str) -> DispatchResult:
        return self._results.get(idempotency_key) or DispatchResult(
            OutcomeClass.UNKNOWN,
            provider_ref=f"local-rail:missing:{_hash(idempotency_key)}",
            evidence_hash=_hash({"missing": idempotency_key}),
            detail="rail has no outcome for this idempotency key",
        )


class NonIdempotentAdapter:
    """Captive unsafe adapter: useful only to prove that policy routes it."""

    adapter_id = "bulla.fixture.non-idempotent"
    adapter_version = "1"
    capabilities = AdapterCapabilities(
        idempotency=IdempotencyStatus.NONE,
        reconciliation=ReconciliationStatus.UNAVAILABLE,
        ordering=OrderingStatus.LOCAL,
        reversible_effect=False,
    )

    def dispatch(self, request: dict[str, Any], *, idempotency_key: str) -> DispatchResult:
        return DispatchResult(
            OutcomeClass.SUCCEEDED,
            provider_ref=f"unsafe:{uuid.uuid4()}", evidence_hash=_hash(request),
        )

    def reconcile(self, *, idempotency_key: str) -> DispatchResult:
        raise BoundaryError("adapter cannot reconcile")
