"""Signed checkpoints for the experimental append-only witness log.

The checkpoint authenticates an operator's claim about one Merkle root and log
position.  It does not create civil time, prove availability, or prove that an
external anchor exists.  Append-only extension is a separate predicate checked
from an RFC 6962 consistency proof.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping
import urllib.parse

from bulla._canonical import canonical_json
from bulla.identity import LocalEd25519Signer, verify_proof_domain
from bulla.registry import DeedLog, verify_consistency_record


SCHEMA_VERSION = "0.1-draft"
PROFILE = "bulla.witness-checkpoint/0.1-draft"
PROOF_PURPOSE = "witness-checkpoint"
_HASH_FIELDS = {
    "schema_version",
    "profile",
    "log_id",
    "operator",
    "tree_size",
    "root",
    "previous_checkpoint_hash",
    "ordering_domain",
    "position",
    "issued_at",
    "anchor_evidence",
}
_SERIALIZED_FIELDS = _HASH_FIELDS | {"checkpoint_hash", "proof"}


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def ordering_domain(log_id: str, operator: str) -> str:
    """Comparable positions exist only inside this exact log/operator domain."""
    return _digest({"profile": PROFILE, "log_id": log_id, "operator": operator})


@dataclass(frozen=True)
class WitnessCheckpoint:
    log_id: str
    operator: str
    tree_size: int
    root: str
    previous_checkpoint_hash: str | None
    ordering_domain: str
    position: int
    issued_at: str | None = None
    anchor_evidence: Mapping[str, Any] | None = None
    proof: Mapping[str, Any] | None = None
    schema_version: str = SCHEMA_VERSION
    profile: str = PROFILE

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.profile != PROFILE:
            raise ValueError("unsupported witness checkpoint schema/profile")
        if not self.log_id.strip() or not self.operator.strip():
            raise ValueError("log_id and operator must be non-empty")
        if isinstance(self.tree_size, bool) or self.tree_size < 0:
            raise ValueError("tree_size must be a non-negative integer")
        if self.position != self.tree_size:
            raise ValueError("position must equal tree_size in the checkpoint ordering domain")
        expected_domain = ordering_domain(self.log_id, self.operator)
        if self.ordering_domain != expected_domain:
            raise ValueError("ordering_domain does not bind this log and operator")
        for label, value in (("root", self.root), ("ordering_domain", self.ordering_domain)):
            if not _is_hash(value):
                raise ValueError(f"{label} must be sha256:<64 lowercase hex>")
        if self.previous_checkpoint_hash is not None and not _is_hash(self.previous_checkpoint_hash):
            raise ValueError("previous_checkpoint_hash must be null or sha256:<64 lowercase hex>")
        if self.issued_at is not None and not isinstance(self.issued_at, str):
            raise ValueError("issued_at is metadata and must be a string or null")
        if self.anchor_evidence is not None and not isinstance(self.anchor_evidence, Mapping):
            raise ValueError("anchor_evidence must be an object or null")
        if self.proof is not None and not isinstance(self.proof, Mapping):
            raise ValueError("proof must be an object or null")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "log_id": self.log_id,
            "operator": self.operator,
            "tree_size": self.tree_size,
            "root": self.root,
            "previous_checkpoint_hash": self.previous_checkpoint_hash,
            "ordering_domain": self.ordering_domain,
            "position": self.position,
            "issued_at": self.issued_at,
            "anchor_evidence": dict(self.anchor_evidence) if self.anchor_evidence is not None else None,
        }

    @property
    def checkpoint_hash(self) -> str:
        return _digest(self.unsigned_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.unsigned_dict(),
            "checkpoint_hash": self.checkpoint_hash,
            "proof": dict(self.proof) if self.proof is not None else None,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "WitnessCheckpoint":
        if not isinstance(document, Mapping) or set(document) != _SERIALIZED_FIELDS:
            raise ValueError(f"checkpoint fields must be exactly {sorted(_SERIALIZED_FIELDS)}")
        checkpoint = cls(
            schema_version=document["schema_version"],
            profile=document["profile"],
            log_id=document["log_id"],
            operator=document["operator"],
            tree_size=document["tree_size"],
            root=document["root"],
            previous_checkpoint_hash=document["previous_checkpoint_hash"],
            ordering_domain=document["ordering_domain"],
            position=document["position"],
            issued_at=document["issued_at"],
            anchor_evidence=document["anchor_evidence"],
            proof=document["proof"],
        )
        if document["checkpoint_hash"] != checkpoint.checkpoint_hash:
            raise ValueError("checkpoint_hash does not match canonical checkpoint content")
        return checkpoint


@dataclass(frozen=True)
class CheckpointVerification:
    structure: str
    hash_binding: str
    operator_authenticity: str
    anchor_status: str
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.structure == "verified"
            and self.hash_binding == "verified"
            and self.operator_authenticity == "verified"
        )

    def __bool__(self) -> bool:
        raise TypeError("CheckpointVerification has independent dimensions; read .ok and statuses")

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure": self.structure,
            "hash_binding": self.hash_binding,
            "operator_authenticity": self.operator_authenticity,
            "anchor_status": self.anchor_status,
            "ok": self.ok,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ExtensionVerification:
    same_log: str
    linked_history: str
    monotonic_position: str
    append_only: str
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return all(
            value == "verified"
            for value in (self.same_log, self.linked_history, self.monotonic_position, self.append_only)
        )

    def __bool__(self) -> bool:
        raise TypeError("ExtensionVerification has independent dimensions; read .ok and statuses")

    def to_dict(self) -> dict[str, Any]:
        return {
            "same_log": self.same_log,
            "linked_history": self.linked_history,
            "monotonic_position": self.monotonic_position,
            "append_only": self.append_only,
            "ok": self.ok,
            "errors": list(self.errors),
        }


def issue_checkpoint(
    log: DeedLog,
    signer: LocalEd25519Signer,
    *,
    log_id: str,
    previous: WitnessCheckpoint | None = None,
    issued_at: str | None = None,
    anchor_evidence: Mapping[str, Any] | None = None,
) -> WitnessCheckpoint:
    if previous is not None:
        if previous.log_id != log_id or previous.operator != signer.issuer:
            raise ValueError("previous checkpoint belongs to a different log or operator")
        if previous.tree_size > len(log):
            raise ValueError("previous checkpoint is ahead of the local log")
    unsigned = WitnessCheckpoint(
        log_id=log_id,
        operator=signer.issuer,
        tree_size=len(log),
        root=log.root(),
        previous_checkpoint_hash=previous.checkpoint_hash if previous is not None else None,
        ordering_domain=ordering_domain(log_id, signer.issuer),
        position=len(log),
        issued_at=issued_at,
        anchor_evidence=anchor_evidence,
    )
    return WitnessCheckpoint(**{**unsigned.__dict__, "proof": signer.sign_domain(PROOF_PURPOSE, unsigned.checkpoint_hash)})


def verify_checkpoint(
    checkpoint: WitnessCheckpoint | Mapping[str, Any], *, public_key: bytes | None = None
) -> CheckpointVerification:
    errors: list[str] = []
    try:
        if isinstance(checkpoint, Mapping):
            checkpoint = WitnessCheckpoint.from_dict(checkpoint)
    except (KeyError, TypeError, ValueError) as exc:
        return CheckpointVerification("invalid", "invalid", "invalid", "unverified", (str(exc),))
    if not isinstance(checkpoint, WitnessCheckpoint):
        return CheckpointVerification("invalid", "invalid", "invalid", "unverified", ("checkpoint has wrong type",))
    structure = "verified"
    hash_binding = "verified"
    if checkpoint.proof is None:
        authenticity = "missing"
        errors.append("checkpoint has no operator proof")
    else:
        auth = verify_proof_domain(PROOF_PURPOSE, checkpoint.checkpoint_hash, dict(checkpoint.proof), public_key)
        authenticity = "verified" if auth.authentic and auth.issuer == checkpoint.operator else "invalid"
        if authenticity != "verified":
            errors.append(auth.detail or "operator proof does not authenticate checkpoint.operator")
    anchor_status = _anchor_status(checkpoint.anchor_evidence, checkpoint.checkpoint_hash)
    return CheckpointVerification(structure, hash_binding, authenticity, anchor_status, tuple(errors))


def verify_checkpoint_extension(
    old: WitnessCheckpoint | Mapping[str, Any],
    new: WitnessCheckpoint | Mapping[str, Any],
    consistency_record: Mapping[str, Any],
) -> ExtensionVerification:
    errors: list[str] = []
    try:
        old_cp = old if isinstance(old, WitnessCheckpoint) else WitnessCheckpoint.from_dict(old)
        new_cp = new if isinstance(new, WitnessCheckpoint) else WitnessCheckpoint.from_dict(new)
    except (KeyError, TypeError, ValueError) as exc:
        return ExtensionVerification("invalid", "invalid", "invalid", "invalid", (str(exc),))
    same = old_cp.log_id == new_cp.log_id and old_cp.operator == new_cp.operator and old_cp.ordering_domain == new_cp.ordering_domain
    linked = new_cp.previous_checkpoint_hash == old_cp.checkpoint_hash
    monotonic = new_cp.tree_size >= old_cp.tree_size
    expected_record = (
        consistency_record.get("old_size") == old_cp.tree_size
        and consistency_record.get("new_size") == new_cp.tree_size
        and consistency_record.get("old_root") == old_cp.root
        and consistency_record.get("new_root") == new_cp.root
    )
    try:
        append_only = expected_record and verify_consistency_record(dict(consistency_record))
    except (KeyError, TypeError, ValueError):
        append_only = False
    if not same:
        errors.append("checkpoints use different log/operator ordering domains")
    if not linked:
        errors.append("new checkpoint does not link to the old checkpoint hash")
    if not monotonic:
        errors.append("checkpoint position moved backward")
    if not append_only:
        errors.append("consistency proof does not bind an append-only extension of these roots")
    return ExtensionVerification(
        "verified" if same else "mismatch",
        "verified" if linked else "mismatch",
        "verified" if monotonic else "mismatch",
        "verified" if append_only else "invalid",
        tuple(errors),
    )


def _anchor_status(anchor: Mapping[str, Any] | None, checkpoint_hash: str) -> str:
    """Only classify supplied anchor evidence; no external anchor is trusted here."""
    if anchor is None:
        return "absent"
    if set(anchor) != {"status", "checkpoint_hash", "reference"}:
        return "invalid"
    if anchor.get("checkpoint_hash") != checkpoint_hash:
        return "invalid"
    if anchor.get("status") not in {"submitted", "confirmed"}:
        return "invalid"
    return str(anchor["status"])


def _is_hash(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        return False
    return all(ch in "0123456789abcdef" for ch in value[7:])


class CheckpointArchive:
    """Append-only JSONL archive of authenticated heads and adjacent proofs."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._entries: list[tuple[WitnessCheckpoint, Mapping[str, Any] | None]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                document = json.loads(line)
                if set(document) != {"checkpoint", "consistency_from_previous"}:
                    raise ValueError("checkpoint archive entry has missing or unknown fields")
                checkpoint = WitnessCheckpoint.from_dict(document["checkpoint"])
                consistency = document["consistency_from_previous"]
                self._validate_append(checkpoint, consistency)
                self._entries.append((checkpoint, consistency))

    def _validate_append(
        self,
        checkpoint: WitnessCheckpoint,
        consistency: Mapping[str, Any] | None,
    ) -> None:
        if not verify_checkpoint(checkpoint).ok:
            raise ValueError("archive refuses an unauthenticated checkpoint")
        if not self._entries:
            if checkpoint.previous_checkpoint_hash is not None or consistency is not None:
                raise ValueError("first archived checkpoint must have no predecessor")
            return
        previous = self._entries[-1][0]
        if consistency is None or not verify_checkpoint_extension(previous, checkpoint, consistency).ok:
            raise ValueError("archive refuses a checkpoint without a valid adjacent extension proof")

    def append(
        self,
        checkpoint: WitnessCheckpoint,
        *,
        consistency_from_previous: Mapping[str, Any] | None = None,
    ) -> None:
        self._validate_append(checkpoint, consistency_from_previous)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "checkpoint": checkpoint.to_dict(),
            "consistency_from_previous": (
                dict(consistency_from_previous) if consistency_from_previous is not None else None
            ),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry) + "\n")
        self._entries.append((checkpoint, consistency_from_previous))

    def latest(self) -> WitnessCheckpoint | None:
        return self._entries[-1][0] if self._entries else None

    def get(self, checkpoint_hash: str) -> WitnessCheckpoint | None:
        return next(
            (checkpoint for checkpoint, _ in self._entries if checkpoint.checkpoint_hash == checkpoint_hash),
            None,
        )

    def history(self) -> tuple[WitnessCheckpoint, ...]:
        return tuple(checkpoint for checkpoint, _ in self._entries)

    def adjacent_consistency(self, old_hash: str, new_hash: str) -> Mapping[str, Any] | None:
        for index in range(1, len(self._entries)):
            previous = self._entries[index - 1][0]
            current, consistency = self._entries[index]
            if previous.checkpoint_hash == old_hash and current.checkpoint_hash == new_hash:
                return consistency
        return None


def _make_checkpoint_handler(archive: CheckpointArchive):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: Mapping[str, Any]) -> None:
            payload = canonical_json(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/checkpoint/latest":
                checkpoint = archive.latest()
                self._send(200, {"checkpoint": checkpoint.to_dict() if checkpoint else None})
            elif parsed.path == "/checkpoint":
                requested = (query.get("hash") or [""])[0]
                checkpoint = archive.get(requested)
                self._send(
                    200 if checkpoint else 404,
                    {"checkpoint": checkpoint.to_dict() if checkpoint else None},
                )
            elif parsed.path == "/checkpoint/history":
                self._send(200, {"checkpoints": [x.to_dict() for x in archive.history()]})
            elif parsed.path == "/checkpoint/consistency":
                old_hash = (query.get("from") or [""])[0]
                new_hash = (query.get("to") or [""])[0]
                consistency = archive.adjacent_consistency(old_hash, new_hash)
                self._send(
                    200 if consistency is not None else 404,
                    {"from": old_hash, "to": new_hash, "consistency": consistency},
                )
            else:
                self._send(404, {"error": "unknown route", "path": parsed.path})

        def do_POST(self) -> None:  # noqa: N802
            self._send(405, {"error": "checkpoint archive is read-only"})

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def make_checkpoint_server(
    archive: CheckpointArchive,
    host: str = "127.0.0.1",
    port: int = 0,
) -> HTTPServer:
    """Read-only latest/history/adjacent-consistency transport."""
    return HTTPServer((host, port), _make_checkpoint_handler(archive))
