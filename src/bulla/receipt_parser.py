"""Fail-closed ingestion for untrusted ActionReceipt JSON bytes.

Hash verification over an already-decoded mapping cannot detect duplicate JSON
members and historically tolerated unknown top-level members.  This module is
the one byte boundary for the CLI, witnesses, and action dispatcher.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bulla.action_receipt import ActionReceipt, ActionReceiptError


@dataclass(frozen=True)
class ReceiptParseLimits:
    max_bytes: int = 1_048_576
    max_depth: int = 32
    max_nodes: int = 50_000
    max_string_bytes: int = 262_144

    def __post_init__(self) -> None:
        if min(self.max_bytes, self.max_depth, self.max_nodes, self.max_string_bytes) <= 0:
            raise ValueError("receipt parse limits must be positive")


class ReceiptParseError(ActionReceiptError):
    """The served JSON is malformed, ambiguous, oversized, or off-schema."""


def _reject_constant(value: str) -> None:
    raise ReceiptParseError(f"non-finite JSON number {value!r} is not permitted")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptParseError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _enforce_limits(value: Any, limits: ReceiptParseLimits) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise ReceiptParseError(f"receipt exceeds {limits.max_nodes} aggregate JSON nodes")
        if depth > limits.max_depth:
            raise ReceiptParseError(f"receipt exceeds maximum JSON depth {limits.max_depth}")
        if isinstance(current, str):
            try:
                size = len(current.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ReceiptParseError("lone Unicode surrogates are not permitted") from exc
            if size > limits.max_string_bytes:
                raise ReceiptParseError(
                    f"receipt string exceeds {limits.max_string_bytes} UTF-8 bytes"
                )
        elif isinstance(current, dict):
            for key, item in current.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _exact_keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ReceiptParseError(f"{label} must be an object")
    missing, unknown = expected - set(value), set(value) - expected
    if missing:
        raise ReceiptParseError(f"{label} is missing required fields {sorted(missing)}")
    if unknown:
        raise ReceiptParseError(f"{label} has unknown fields {sorted(unknown)}")


def _validate_closed_members(document: dict[str, Any]) -> None:
    schema = document.get("schema_version")
    diagnostic = document.get("diagnostic_ref")
    if not isinstance(diagnostic, dict):
        raise ReceiptParseError("diagnostic_ref must be an object")
    if schema != "0.1":
        allowed_diagnostic = {"status", "ref"}
        unknown = set(diagnostic) - allowed_diagnostic
        if unknown:
            raise ReceiptParseError(f"diagnostic_ref has unknown fields {sorted(unknown)}")
    if "status" not in diagnostic:
        raise ReceiptParseError("diagnostic_ref is missing required field 'status'")

    hashes = document.get("hashes")
    if schema == "0.1":
        if not isinstance(hashes, dict) or not {"content", "event", "attestation", "log_leaf"} <= set(hashes):
            raise ReceiptParseError("hashes is missing a required historical digest")
    else:
        _exact_keys(hashes, {"content", "event", "attestation", "log_leaf"}, "hashes")

    evidence = document.get("evidence_refs")
    if not isinstance(evidence, list):
        raise ReceiptParseError("evidence_refs must be an array")
    expected_evidence = {"name", "hash", "grounding"}
    for index, item in enumerate(evidence):
        if schema == "0.1":
            if not isinstance(item, dict) or not {"name", "hash"} <= set(item):
                raise ReceiptParseError(f"evidence_refs[{index}] is missing name or hash")
        else:
            _exact_keys(item, expected_evidence, f"evidence_refs[{index}]")

    if not isinstance(document.get("action"), dict):
        raise ReceiptParseError("action must be an object")
    required_action = {"type"} if schema == "0.1" else {"type", "subject"}
    if not required_action <= set(document["action"]):
        raise ReceiptParseError(f"action requires {sorted(required_action)}")
    if "subject" in document["action"] and not isinstance(document["action"].get("subject"), dict):
        raise ReceiptParseError("action.subject must be an object")

    if schema in {"0.3", "0.4"}:
        _validate_closed_v03_plus(document)


def _optional_closed(value: Any, allowed: set[str], label: str) -> None:
    if not isinstance(value, dict):
        raise ReceiptParseError(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ReceiptParseError(f"{label} has unknown fields {sorted(unknown)}")


def _validate_closed_v03_plus(document: dict[str, Any]) -> None:
    """Apply the closed nested wire shapes used by authority-bound drafts."""
    mandate = document.get("mandate")
    _optional_closed(mandate, {"deed_schema", "authority", "bounds"}, "mandate")
    if mandate.get("authority") is not None:
        _exact_keys(
            mandate["authority"], {"principal", "policy", "delegation"},
            "mandate.authority",
        )
        for index, grant in enumerate(mandate["authority"]["delegation"]):
            if isinstance(grant, dict):
                _optional_closed(
                    grant,
                    {"grantor", "grantee", "principal", "parent", "policy_digest",
                     "scope_digest", "not_before", "not_after", "proof"},
                    f"mandate.authority.delegation[{index}]",
                )
                for checkpoint in ("not_before", "not_after"):
                    if checkpoint in grant:
                        _exact_keys(
                            grant[checkpoint], {"domain", "value"},
                            f"delegation[{index}].{checkpoint}",
                        )
                _validate_proof(grant.get("proof"), f"delegation[{index}].proof")
    if mandate.get("bounds") is not None:
        _optional_closed(
            mandate["bounds"], {"scope", "expires", "rollback_window"},
            "mandate.bounds",
        )

    remedy = document.get("remedy")
    if remedy:
        _exact_keys(remedy, {"challenge_window", "forum", "remedies"}, "remedy")
        _exact_keys(remedy["forum"], {"log_endpoint", "trusted_root_ref"}, "remedy.forum")
        if not isinstance(remedy["remedies"], list):
            raise ReceiptParseError("remedy.remedies must be an array")
        for index, item in enumerate(remedy["remedies"]):
            _exact_keys(item, {"rung", "verifier", "anchor"}, f"remedy.remedies[{index}]")
    elif not isinstance(remedy, dict):
        raise ReceiptParseError("remedy must be an object")

    _optional_closed(document.get("retention"), {"record", "disclosure"}, "retention")
    for name in ("signature", "authorization"):
        proof = document.get(name)
        if proof is not None:
            _validate_proof(proof, name)
    if document.get("schema_version") == "0.4" and document.get("occurrence") is not None:
        _validate_proof(document["occurrence"], "occurrence")
    for index, convention in enumerate(document.get("conventions", ())):
        _optional_closed(
            convention, {"name", "scope", "kind", "definition", "definition_hash", "forum"},
            f"conventions[{index}]",
        )
        if convention.get("forum") is not None:
            _exact_keys(
                convention["forum"], {"log_endpoint", "trusted_root_ref"},
                f"conventions[{index}].forum",
            )


def _validate_proof(value: Any, label: str) -> None:
    _exact_keys(
        value, {"type", "purpose", "issuer", "verificationMethod", "proofValue"}, label,
    )


def parse_action_receipt_json(
    raw: bytes | bytearray | memoryview | str,
    limits: ReceiptParseLimits = ReceiptParseLimits(),
) -> ActionReceipt:
    """Parse and validate one ActionReceipt from its exact served JSON bytes."""

    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ReceiptParseError("receipt is not valid Unicode") from exc
        text = raw
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        encoded = bytes(raw)
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReceiptParseError("receipt is not valid UTF-8") from exc
    else:
        raise ReceiptParseError("receipt input must be bytes or text")

    if len(encoded) > limits.max_bytes:
        raise ReceiptParseError(f"receipt exceeds {limits.max_bytes} bytes")
    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ReceiptParseError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ReceiptParseError(f"invalid receipt JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ReceiptParseError("receipt root must be an object")
    _enforce_limits(document, limits)
    _validate_closed_members(document)
    try:
        receipt = ActionReceipt.from_dict(document)
    except ActionReceiptError as exc:
        raise ReceiptParseError(str(exc)) from exc
    recomputed = receipt.hashes()
    if document.get("hashes") != recomputed:
        mismatches = sorted(
            name for name, value in recomputed.items()
            if document.get("hashes", {}).get(name) != value
        )
        raise ReceiptParseError(
            "served receipt hashes do not match the validated typed receipt: "
            + ", ".join(mismatches)
        )
    return receipt


def load_action_receipt_document(
    raw: bytes | bytearray | memoryview | str,
    limits: ReceiptParseLimits = ReceiptParseLimits(),
) -> dict[str, Any]:
    """Return the canonical validated mapping consumed by downstream code."""

    return parse_action_receipt_json(raw, limits).to_dict()
