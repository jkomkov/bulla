"""Reliance — the consumer half of answerability.

The kernel makes the *actor* answerable and leaves the *relier* unrecorded. But
relying on a receipt ("I shipped the money because I believed it") is itself a
consequential act, and fault cannot be allocated without knowing what the relier
required. This module makes that requirement **declarable, pinnable, and
recomputable**:

- a :class:`ReliancePolicy` — a named, versioned, hashable declaration of the
  dimension values a relying party will accept (modelled on
  :class:`bulla.model.PolicyProfile`, the one policy already pinned into a
  content-addressed artifact);
- :func:`decide` — a **pure, crypto-free, zero-import-reproducible** function from a
  verification view to a :class:`RelianceDecision` ``∈ {RELY, REFUSE, ESCALATE}``.

The trichotomy is the point. ``RELY`` records what you accepted. ``REFUSE`` is a
defect signal (aggregated refusals identify a bad actor with no operator adjudicating
anything). ``ESCALATE`` is the forum's input queue — forum-completeness on the
consumer side. And because the policy hash rides in the relier's own ``bulla.rely``
receipt, *who eats a bad outcome is a calculation*: met the policy and the record
lied → the signer; didn't meet it and relied anyway → you; met it and the policy was
defective → the policy author.

Nothing here is authorization by itself; it is the relying party's declared standard
of care applied to what the kernel proved.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from bulla.executable_form import definition_hash

RELY = "rely"
REFUSE = "refuse"
ESCALATE = "escalate"


class RelianceError(ValueError):
    """A policy, verification view, or reliance receipt is malformed."""

#: Verification rungs, lowest first (a policy names a floor; below it → REFUSE).
_RUNGS = ("none", "digest", "attestation", "log_inclusion")

#: The per-dimension CLEAR-VIOLATION values. An unmet requirement whose actual value
#: is here is a *negative* (→ REFUSE); an unmet value NOT here is *ambiguous /
#: undetermined* (→ ESCALATE). This taxonomy is a property of each dimension's value
#: space, not of the policy — so a relying party's declared thresholds decide *whether*
#: a value is acceptable, while this decides *how a rejection is routed*.
_NEGATIVE: dict[str, frozenset[str]] = {
    "authority_authentic": frozenset({"forged"}),
    "bounds_conformance": frozenset({"violates"}),
    "scope_binding": frozenset({"mismatch"}),
    "chain_integrity": frozenset({"broken", "cycle", "over_depth"}),
    "principal_binding": frozenset({"wrong_principal"}),
    "policy_binding": frozenset({"mismatch"}),
    "temporal_status": frozenset({"expired", "not_yet_valid"}),
    "revocation_status": frozenset({"revoked"}),
}

#: Dimensions a policy may constrain (each a tuple of accepted values, or None = don't
#: enforce). Ordered so the decision surface reads the way a relying party thinks.
_DIMENSIONS = (
    "authority_authentic",
    "chain_integrity",
    "principal_binding",
    "policy_binding",
    "scope_binding",
    "bounds_conformance",
    "temporal_status",
    "revocation_status",
)

_DIMENSION_VALUES: dict[str, frozenset[str]] = {
    "authority_authentic": frozenset({
        "verified", "forged", "unauthenticated", "unresolved", "not_applicable",
    }),
    "chain_integrity": frozenset({
        "verified", "broken", "cycle", "over_depth", "not_applicable",
    }),
    "principal_binding": frozenset({
        "verified", "wrong_principal", "unresolved", "not_applicable",
    }),
    "policy_binding": frozenset({"verified", "mismatch", "not_applicable"}),
    "scope_binding": frozenset({"verified", "mismatch", "not_applicable"}),
    "bounds_conformance": frozenset({
        "conforms", "violates", "not_checkable", "not_applicable",
    }),
    "temporal_status": frozenset({
        "unresolved", "within_window", "expired", "not_yet_valid", "not_applicable",
    }),
    "revocation_status": frozenset({
        "unresolved", "not_revoked", "revoked", "not_applicable",
    }),
}

_GROUNDING_VALUES = frozenset({
    "self_asserted", "counterparty_signed", "third_party_anchored", "execution_verified",
})
_CONVENTION_VALUES = frozenset({"conforms", "violates", "pinned"})
_VIEW_KEYS = frozenset({
    "ok", "verified_to", "authority_authentic", "effective_grounding", "conventions",
    *_DIMENSIONS,
})
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_POLICY_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


@dataclass(frozen=True)
class ReliancePolicy:
    """What a relying party requires before it will act on a receipt. Each dimension
    field is the tuple of values it will RELY on (``None`` = do not enforce). Named
    and versioned, ``to_dict``-serializable, and hashable — so the exact rule can be
    pinned into a ``bulla.rely`` receipt and recomputed by anyone.

    The defaults are **strict**: they refuse ``unresolved`` temporal/revocation status,
    so a policy that wants to proceed under today's unbuilt revocation transport must
    *explicitly* accept it (see :data:`PRAGMATIC_RELIANCE_POLICY`) — and that acceptance
    is then on the record."""

    name: str
    require_ok: bool = True
    min_verified_to: str = "attestation"
    require_conventions_conform: bool = True
    authority_authentic: tuple[str, ...] | None = ("verified", "not_applicable")
    chain_integrity: tuple[str, ...] | None = ("verified", "not_applicable")
    principal_binding: tuple[str, ...] | None = ("verified", "not_applicable")
    policy_binding: tuple[str, ...] | None = ("verified", "not_applicable")
    scope_binding: tuple[str, ...] | None = ("verified", "not_applicable")
    bounds_conformance: tuple[str, ...] | None = ("conforms", "not_applicable")
    temporal_status: tuple[str, ...] | None = ("within_window", "not_applicable")
    revocation_status: tuple[str, ...] | None = ("not_revoked", "not_applicable")

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _POLICY_NAME_RE.fullmatch(self.name) is None:
            raise RelianceError(
                "reliance policy name must be 1-128 stable URI-token characters"
            )
        if self.min_verified_to not in _RUNGS:
            raise RelianceError(
                f"min_verified_to must be one of {_RUNGS}, got {self.min_verified_to!r}"
            )
        if not isinstance(self.require_ok, bool) or not isinstance(
            self.require_conventions_conform, bool
        ):
            raise RelianceError("reliance policy flags must be booleans")
        for dim in _DIMENSIONS:
            accepted = getattr(self, dim)
            if accepted is None:
                continue
            if not isinstance(accepted, tuple) or not accepted:
                raise RelianceError(f"policy {dim} must be None or a non-empty tuple")
            if any(not isinstance(value, str) for value in accepted):
                raise RelianceError(f"policy {dim} accepted values must be strings")
            if len(set(accepted)) != len(accepted):
                raise RelianceError(f"policy {dim} contains duplicate accepted values")
            unknown = set(accepted) - _DIMENSION_VALUES[dim]
            if unknown:
                raise RelianceError(
                    f"policy {dim} contains unknown values {sorted(unknown)}"
                )

    def to_dict(self) -> dict:
        out: dict = {
            "name": self.name,
            "require_ok": self.require_ok,
            "min_verified_to": self.min_verified_to,
            "require_conventions_conform": self.require_conventions_conform,
        }
        for dim in _DIMENSIONS:
            v = getattr(self, dim)
            out[dim] = list(v) if v is not None else None
        return out

    @property
    def policy_hash(self) -> str:
        """``sha256:`` over the canonical policy — the pin a reliance receipt records
        so a stranger recomputes the decision against the exact rule that produced it."""
        return definition_hash(self.to_dict())


#: Strict: refuses anything not positively established, including unresolved temporal
#: and revocation status. Correct once revocation transport exists.
STRICT_RELIANCE_POLICY = ReliancePolicy(name="reliance.strict.v1")

#: Pragmatic: additionally accepts the states that are ``unresolved`` *by construction*
#: today (no pinned checkpoint; revocation transport unbuilt). It relies under declared,
#: recorded risk — when transport lands, every past reliance under this policy is
#: auditable ("you accepted unresolved revocation on these acts"). It is deliberately
#: NOT a default: accepting unresolved revocation must be an explicit call-site choice.
PRAGMATIC_RELIANCE_POLICY = ReliancePolicy(
    name="reliance.pragmatic.v1",
    temporal_status=("within_window", "unresolved", "not_applicable"),
    revocation_status=("not_revoked", "unresolved", "not_applicable"),
)


@dataclass(frozen=True)
class RelianceDecision:
    """The relying party's decision. ``outcome ∈ {RELY, REFUSE, ESCALATE}``; ``unmet``
    lists each failed requirement with how its rejection was routed. ``policy_name`` +
    ``policy_hash`` identify the exact rule, so the decision recomputes."""

    outcome: str
    unmet: tuple[dict, ...]
    policy_name: str
    policy_hash: str

    def __bool__(self) -> bool:
        # A three-way decision is not a boolean — RELY/REFUSE/ESCALATE do not collapse.
        # (Same discipline as the verdict objects: read `.outcome`.)
        raise TypeError(
            "The truth value of a RelianceDecision is ambiguous — read `.outcome` "
            "(one of RELY / REFUSE / ESCALATE), never `if decision:`."
        )

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "unmet": [dict(u) for u in self.unmet],
            "policy": f"{self.policy_name}@{self.policy_hash}",
        }


def _view_of(verification: Any) -> dict:
    """Accept either a ``ReceiptVerification`` (via its ``to_dict``) or an already-
    serialized view dict. Never uses the object in boolean context (it would raise)."""
    if isinstance(verification, dict):
        view = verification
    else:
        try:
            view = verification.to_dict()
        except AttributeError as exc:
            raise RelianceError(
                "verification must be a complete serialized view or expose to_dict()"
            ) from exc
    if not isinstance(view, dict):
        raise RelianceError("verification view must serialize to an object")
    missing = _VIEW_KEYS - set(view)
    extra = set(view) - _VIEW_KEYS
    if missing:
        raise RelianceError(
            f"verification view is incomplete; missing dimensions {sorted(missing)}"
        )
    if extra:
        raise RelianceError(
            f"verification view contains unknown dimensions {sorted(extra)}"
        )
    if not isinstance(view["ok"], bool):
        raise RelianceError("verification view ok must be boolean")
    if not isinstance(view["verified_to"], str) or view["verified_to"] not in _RUNGS:
        raise RelianceError(f"verification view has unknown rung {view['verified_to']!r}")
    grounding = view["effective_grounding"]
    if grounding is not None and (
        not isinstance(grounding, str) or grounding not in _GROUNDING_VALUES
    ):
        raise RelianceError(f"verification view has unknown grounding {grounding!r}")
    conventions = view["conventions"]
    if not isinstance(conventions, dict):
        raise RelianceError("verification view conventions must be an object")
    for name, status in conventions.items():
        if not isinstance(name, str) or not name.strip() \
                or not isinstance(status, str) or status not in _CONVENTION_VALUES:
            raise RelianceError(
                f"verification view has malformed convention verdict {name!r}: {status!r}"
            )
    for dim in _DIMENSIONS:
        if not isinstance(view[dim], str) or view[dim] not in _DIMENSION_VALUES[dim]:
            raise RelianceError(
                f"verification view {dim} has unknown value {view[dim]!r}"
            )
    return view


def _rung_below_floor(actual: str, floor: str) -> bool:
    a = _RUNGS.index(actual) if actual in _RUNGS else 0
    f = _RUNGS.index(floor) if floor in _RUNGS else 0
    return a < f


def decide(verification: Any, policy: ReliancePolicy) -> RelianceDecision:
    """Decide whether to rely on a verified receipt under a declared policy. Pure and
    crypto-free — a relying party (or any auditor) recomputes it from the verification
    view and the policy alone.

    Routing: every unmet requirement whose actual value is a CLEAR VIOLATION
    (:data:`_NEGATIVE`) forces ``REFUSE``; if all unmet requirements are merely
    *ambiguous* (``unresolved`` / ``unauthenticated`` / ``not_checkable`` — the record
    is inconclusive, not adverse) the outcome is ``ESCALATE`` (route to a forum). Only
    an entirely-met set is ``RELY``."""
    view = _view_of(verification)
    unmet: list[dict] = []

    def _flag(dim: str, actual: Any, accepted: Any) -> None:
        negative = actual in _NEGATIVE.get(dim, frozenset())
        unmet.append({
            "dimension": dim,
            "actual": actual,
            "accepted": list(accepted) if isinstance(accepted, (tuple, list)) else accepted,
            "routing": REFUSE if negative else ESCALATE,
        })

    if not isinstance(policy, ReliancePolicy):
        raise RelianceError("policy must be an explicit ReliancePolicy")

    if policy.require_ok and not view["ok"]:
        _flag("ok", view["ok"], True)          # record integrity is never ambiguous
        unmet[-1]["routing"] = REFUSE
    floor = policy.min_verified_to
    if _rung_below_floor(view["verified_to"], floor):
        unmet.append({"dimension": "verified_to", "actual": view["verified_to"],
                      "accepted": f">= {floor}", "routing": REFUSE})

    for dim in _DIMENSIONS:
        accepted = getattr(policy, dim)
        if accepted is None:
            continue
        actual = view[dim]
        if actual not in accepted:
            _flag(dim, actual, accepted)

    if policy.require_conventions_conform:
        for cname, status in view["conventions"].items():
            if status not in ("conforms", "pinned"):
                unmet.append({"dimension": f"convention:{cname}", "actual": status,
                              "accepted": ["conforms", "pinned"],
                              "routing": REFUSE if status == "violates" else ESCALATE})

    if not unmet:
        outcome = RELY
    elif any(u["routing"] == REFUSE for u in unmet):
        outcome = REFUSE
    else:
        outcome = ESCALATE
    return RelianceDecision(outcome, tuple(unmet), policy.name, policy.policy_hash)


# ── reliance as a receipt (NOT a new type — an action.type) ──────────────────
#
# Relying on a receipt is a consequential act, so by the receipt primitive it takes a
# receipt. By THE ONE ABSTRACTION (a *release* is not a new type; it is an action.type),
# a reliance receipt is an ordinary ActionReceipt with action.type "bulla.rely". The
# relier signs its OWN envelope and is thereby answerable for its reliance — the
# reflexivity clause honoured. The verdict recomputes: anyone re-derives
# ``decide(verify_receipt(relied_on), policy)`` and checks it against what was claimed,
# so a lying relier is caught by the same machinery as a lying actor.

RELIANCE_ACTION_TYPE = "bulla.rely"


@dataclass(frozen=True)
class ReceiptRef:
    """Additive wire reference to one observed ActionReceipt occurrence.

    ``event`` binds content plus the receipt's claimed timestamp;
    ``attestation`` binds content, signer, and recourse envelope. The pair identifies
    what the relying party observed. It does not independently prove the timestamp.
    """

    event: str
    attestation: str

    def __post_init__(self) -> None:
        for name in ("event", "attestation"):
            value = getattr(self, name)
            if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
                raise RelianceError(f"receipt_ref.{name} must be a full lowercase sha256 digest")

    @classmethod
    def from_receipt(cls, receipt: dict) -> "ReceiptRef":
        if not isinstance(receipt, dict):
            raise RelianceError("relied-on receipt must be an object")
        hashes = receipt.get("hashes")
        if not isinstance(hashes, dict):
            raise RelianceError("relied-on receipt has no hashes object")
        return cls(event=hashes.get("event"), attestation=hashes.get("attestation"))

    @classmethod
    def from_dict(cls, value: Any) -> "ReceiptRef":
        if not isinstance(value, dict) or set(value) != {"event", "attestation"}:
            raise RelianceError("receipt reference must contain exactly event and attestation")
        return cls(event=value["event"], attestation=value["attestation"])

    def to_dict(self) -> dict:
        return {"event": self.event, "attestation": self.attestation}


@dataclass(frozen=True)
class RelianceVerification:
    """Typed verification of the relier's own receipt and recomputed decision."""

    ok: bool
    claimed: str | None
    recomputed: str | None
    checks: dict
    reasons: tuple[str, ...]
    receipt_verification: Any
    decision: RelianceDecision | None = None

    def __bool__(self) -> bool:
        raise TypeError(
            "The truth value of a RelianceVerification is ambiguous — read `.ok` and "
            "the named `.checks`, never `if verify_reliance(...):`."
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "claimed": self.claimed,
            "recomputed": self.recomputed,
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
            "receipt_verified_to": self.receipt_verification.verified_to,
            "decision": self.decision.to_dict() if self.decision is not None else None,
        }


def _verdict_pin(relied_ref: ReceiptRef, policy_ref: str, outcome: str) -> str:
    """The recomputable reliance verdict, pinned — ``diagnostic_ref.ref`` points at
    this. A third party recomputes the verdict and compares; the pin makes the claim
    explicit and content-addressed."""
    return definition_hash({
        "relied_on": relied_ref.to_dict(), "policy": policy_ref, "decision": outcome,
    })


def _relied_grounding(relied_on: dict) -> str:
    # The evidence backing a reliance is the relied-upon receipt. Its grounding TO THE
    # RELIER: a signed record from the actor is counterparty_signed; an unsigned one is
    # the relier's own testimony that it saw the bytes.
    return "counterparty_signed" if relied_on.get("signature") else "self_asserted"


def build_reliance_receipt(
    *,
    relied_on: dict,
    policy: ReliancePolicy,
    envelope,
    decision: RelianceDecision | None = None,
    public_key: bytes | None = None,
    timestamp: str = "",
    producer: dict | None = None,
):
    """Build a ``bulla.rely`` ActionReceipt recording this relying party's decision
    about ``relied_on`` under ``policy``. If ``decision`` is not supplied it is computed
    here via ``decide(verify_receipt(relied_on), policy)``. Sign the result with the
    RELIER's own signer (``sign_action_receipt``) so the reliance is itself answerable."""
    from bulla.action_receipt import build_action_receipt, verify_receipt

    relied_ref = ReceiptRef.from_receipt(relied_on)
    recomputed = decide(verify_receipt(relied_on, public_key=public_key), policy)
    if decision is None:
        decision = recomputed
    elif decision != recomputed:
        raise RelianceError("supplied reliance decision does not recompute under this policy")
    policy_ref = f"{policy.name}@{policy.policy_hash}"
    subject = {
        "relied_on": relied_ref.to_dict(), "policy": policy_ref, "decision": decision.outcome,
    }
    return build_action_receipt(
        action={"type": RELIANCE_ACTION_TYPE, "subject": subject},
        diagnostic_ref={"status": "reference", "ref": _verdict_pin(relied_ref, policy_ref, decision.outcome)},
        envelope=envelope,
        evidence_refs=({"name": "relied_on", "hash": relied_ref.attestation,
                        "grounding": _relied_grounding(relied_on)},),
        timestamp=timestamp,
        producer=producer,
    )


def verify_reliance(reliance: dict, relied_on: dict, policy: ReliancePolicy,
                    *, public_key: bytes | None = None) -> RelianceVerification:
    """Authenticate a ``bulla.rely`` receipt and recompute its declared decision.

    ``ok`` is true only when the relier's own receipt reaches the attestation rung with
    a verified envelope and every linkage/decision check passes. The relied-on receipt
    may itself fail verification — recording REFUSE is still a meaningful reliance act.
    """
    from bulla.action_receipt import verify_receipt

    if not isinstance(policy, ReliancePolicy):
        raise RelianceError("policy must be an explicit ReliancePolicy")
    reliance_doc = reliance if isinstance(reliance, dict) else {}
    relied_on_doc = relied_on if isinstance(relied_on, dict) else {}
    receipt_verification = verify_receipt(reliance_doc)
    action = reliance_doc.get("action")
    action = action if isinstance(action, dict) else {}
    subject = action.get("subject") if isinstance(action.get("subject"), dict) else {}
    claimed = subject.get("decision") if isinstance(subject.get("decision"), str) else None
    reasons: list[str] = []

    try:
        expected_ref = ReceiptRef.from_receipt(relied_on_doc)
    except RelianceError as exc:
        expected_ref = None
        reasons.append(str(exc))
    try:
        claimed_ref = ReceiptRef.from_dict(subject.get("relied_on"))
    except RelianceError as exc:
        claimed_ref = None
        reasons.append(str(exc))

    policy_ref = f"{policy.name}@{policy.policy_hash}"
    recomputed: RelianceDecision | None = None
    try:
        recomputed = decide(verify_receipt(relied_on_doc, public_key=public_key), policy)
    except RelianceError as exc:
        reasons.append(str(exc))

    evidence = reliance_doc.get("evidence_refs")
    evidence = evidence if isinstance(evidence, list) else []
    relied_evidence = [e for e in evidence if isinstance(e, dict) and e.get("name") == "relied_on"]
    diagnostic = reliance_doc.get("diagnostic_ref")
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}

    self_authentic = (
        receipt_verification.ok
        and receipt_verification.verified_to in ("attestation", "log_inclusion")
        and receipt_verification.authority_authentic == "verified"
    )

    checks = {
        "receipt_authentic": self_authentic,
        "action_type": action.get("type") == RELIANCE_ACTION_TYPE,
        "subject_shape": set(subject) == {"relied_on", "policy", "decision"},
        "decision_value": claimed in (RELY, REFUSE, ESCALATE),
        "relied_on_matches": claimed_ref is not None and claimed_ref == expected_ref,
        "policy_matches": subject.get("policy") == policy_ref,
        "decision_recomputes": recomputed is not None and claimed == recomputed.outcome,
        "evidence_binding": (
            expected_ref is not None
            and len(relied_evidence) == 1
            and relied_evidence[0].get("hash") == expected_ref.attestation
            and relied_evidence[0].get("grounding") == _relied_grounding(relied_on_doc)
        ),
        "verdict_pinned": (
            expected_ref is not None
            and recomputed is not None
            and (diagnostic.get("ref")
                 == _verdict_pin(expected_ref, policy_ref, recomputed.outcome))
        ),
    }
    for name, passed in checks.items():
        if not passed:
            reasons.append(f"reliance check failed: {name}")
    reasons.extend(receipt_verification.reasons if not self_authentic else ())
    return RelianceVerification(
        ok=all(checks.values()),
        claimed=claimed,
        recomputed=recomputed.outcome if recomputed is not None else None,
        checks=checks,
        reasons=tuple(reasons),
        receipt_verification=receipt_verification,
        decision=recomputed,
    )
