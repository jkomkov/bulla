"""Minimal, experimental proof of same-size log equivocation.

This is intentionally one artifact and one check, not a witness protocol.  It
does not define transport, operator discovery, pooling, bonds, or inclusion
bundles.  It answers only the objective question available before those
systems exist: did the same authenticated operator sign two different roots
for the same log and tree size?
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bulla._canonical import canonical_json
from bulla.identity import verify_proof


KIND = "equivocation_evidence"
VERSION = "0.1-experimental"
HEAD_KIND = "signed_log_head"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEAD_FIELDS = ("operator_id", "log_id", "tree_size", "root", "observed_at", "signature")


def _head_preimage(head: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": HEAD_KIND,
        "version": VERSION,
        "operator_id": head["operator_id"],
        "log_id": head["log_id"],
        "tree_size": head["tree_size"],
        "root": head["root"],
        "observed_at": head["observed_at"],
    }


def log_head_hash(head: dict[str, Any]) -> str:
    """The detached signature preimage for one log head."""
    raw = canonical_json(_head_preimage(head)).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validate_head(head: Any, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(head, dict):
        return [f"{label} must be an object"]
    for field in _HEAD_FIELDS:
        if field not in head:
            errors.append(f"{label}.{field} is required")
    if errors:
        return errors
    if not isinstance(head["operator_id"], str) or not head["operator_id"].strip():
        errors.append(f"{label}.operator_id must be a non-empty string")
    if not isinstance(head["log_id"], str) or not head["log_id"].strip():
        errors.append(f"{label}.log_id must be a non-empty string")
    if not isinstance(head["tree_size"], int) or isinstance(head["tree_size"], bool) or head["tree_size"] < 0:
        errors.append(f"{label}.tree_size must be a non-negative integer")
    if not isinstance(head["root"], str) or not _HASH_RE.fullmatch(head["root"]):
        errors.append(f"{label}.root must be sha256:<64 lowercase hex>")
    try:
        datetime.fromisoformat(str(head["observed_at"]).replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label}.observed_at must be an ISO-8601 timestamp")
    if not isinstance(head["signature"], dict):
        errors.append(f"{label}.signature must be a detached proof object")
    return errors


@dataclass(frozen=True)
class EquivocationEvidence:
    """Two signed log heads alleged to conflict."""

    head_a: dict[str, Any]
    head_b: dict[str, Any]
    kind: str = KIND
    version: str = VERSION

    def __post_init__(self) -> None:
        if self.kind != KIND:
            raise ValueError(f"kind must be {KIND!r}")
        if self.version != VERSION:
            raise ValueError(f"version must be {VERSION!r}")
        errors = _validate_head(self.head_a, "head_a") + _validate_head(self.head_b, "head_b")
        if errors:
            raise ValueError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "head_a": dict(self.head_a),
            "head_b": dict(self.head_b),
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "EquivocationEvidence":
        if not isinstance(document, dict):
            raise ValueError("equivocation evidence must be an object")
        return cls(
            head_a=document.get("head_a"),
            head_b=document.get("head_b"),
            kind=document.get("kind", ""),
            version=document.get("version", ""),
        )


def verify_equivocation_evidence(
    evidence: EquivocationEvidence | dict[str, Any],
    *,
    public_key: bytes | None = None,
) -> dict[str, Any]:
    """Verify the narrow same-size conflict predicate.

    ``equivocation`` is true only when every structural and authenticity check
    passes.  A false result carries named checks so incompatible observations
    are never presented as exoneration or conviction.
    """
    try:
        item = evidence if isinstance(evidence, EquivocationEvidence) else EquivocationEvidence.from_dict(evidence)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "equivocation": False,
            "checks": {"well_formed": False},
            "reasons": [str(exc)],
        }

    a, b = item.head_a, item.head_b
    checks: dict[str, bool] = {
        "well_formed": True,
        "same_operator": a["operator_id"] == b["operator_id"],
        "same_log": a["log_id"] == b["log_id"],
        "same_tree_size": a["tree_size"] == b["tree_size"],
        "different_roots": a["root"] != b["root"],
        "head_a_issuer_bound": a["signature"].get("issuer") == a["operator_id"],
        "head_b_issuer_bound": b["signature"].get("issuer") == b["operator_id"],
    }
    reasons: list[str] = []
    for label, head in (("head_a", a), ("head_b", b)):
        try:
            authenticity = verify_proof(log_head_hash(head), head["signature"], public_key=public_key)
            checks[f"{label}_signature"] = authenticity.authentic
            if not authenticity.authentic:
                reasons.append(f"{label} signature is not authentic: {authenticity.detail}")
        except (ImportError, ValueError) as exc:
            checks[f"{label}_signature"] = False
            reasons.append(f"{label} signature could not be verified: {exc}")

    labels = {
        "same_operator": "heads name different operators",
        "same_log": "heads name different logs",
        "same_tree_size": "heads have different tree sizes",
        "different_roots": "heads carry the same root",
        "head_a_issuer_bound": "head_a signature issuer does not equal operator_id",
        "head_b_issuer_bound": "head_b signature issuer does not equal operator_id",
    }
    for check, reason in labels.items():
        if not checks[check]:
            reasons.append(reason)

    equivocation = all(checks.values())
    return {
        "ok": True,
        "equivocation": equivocation,
        "checks": checks,
        "reasons": reasons,
        "operator_id": a["operator_id"] if equivocation else None,
        "log_id": a["log_id"] if equivocation else None,
        "tree_size": a["tree_size"] if equivocation else None,
        "roots": [a["root"], b["root"]] if equivocation else [],
    }
