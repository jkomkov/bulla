#!/usr/bin/env python3
"""Authenticate and score the frozen recheckable-inference comprehension gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from bulla._canonical import canonical_json  # noqa: E402
from bulla.identity import verify_proof_domain  # noqa: E402


HASH_PREFIX = "sha256:"
SEMANTIC_QUESTIONS = {
    "relation_reproduction",
    "historical_provider_execution",
    "answer_correctness",
    "buyer_policy_eligibility",
    "settlement_stages",
    "funds_movement",
    "integrity_and_coverage",
    "receiver_record_completeness",
}
ALL_QUESTIONS = {"role_handoff", *SEMANTIC_QUESTIONS}
ACCEPTANCE_DIMENSIONS = {
    "browser_story_without_help",
    "role_handoff",
    "all_eight_distinctions",
    "answer_correctness",
    "funds_movement",
    "terminal_reproduction",
}
INITIAL_SLOTS = {
    "developer-1": "DEVELOPER",
    "developer-2": "DEVELOPER",
    "developer-3": "DEVELOPER",
    "decision-maker-1": "TECHNICAL_DECISION_MAKER",
    "decision-maker-2": "TECHNICAL_DECISION_MAKER",
}
TOP_PROOF_FIELDS = {"type", "purpose", "issuer", "verificationMethod", "proofValue"}


class GateError(ValueError):
    pass


def _json_identity(value: Any) -> str:
    """Return a type-preserving identity for JSON equality and uniqueness."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    raise GateError(f"unsupported schema type {expected!r}")


def _validate_schema(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
) -> None:
    """Validate the closed Draft 2020-12 subset used by the trial schemas."""

    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise GateError(f"{path}: unsupported schema reference")
        name = reference.removeprefix("#/$defs/")
        target = root.get("$defs", {}).get(name)
        if not isinstance(target, dict):
            raise GateError(f"{path}: unresolved schema reference {reference}")
        _validate_schema(value, target, root, path)

    for subschema in schema.get("allOf", []):
        _validate_schema(value, subschema, root, path)
    condition = schema.get("if")
    if isinstance(condition, dict):
        try:
            _validate_schema(value, condition, root, path)
        except GateError:
            branch = schema.get("else")
        else:
            branch = schema.get("then")
        if isinstance(branch, dict):
            _validate_schema(value, branch, root, path)

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not choices or not all(isinstance(item, str) for item in choices):
            raise GateError(f"{path}: malformed schema type")
        if not any(_schema_type_matches(value, item) for item in choices):
            raise GateError(f"{path}: value does not match schema type {expected!r}")

    if "const" in schema and _json_identity(value) != _json_identity(schema["const"]):
        raise GateError(f"{path}: value does not match schema const")
    if "enum" in schema and not any(
        _json_identity(value) == _json_identity(item) for item in schema["enum"]
    ):
        raise GateError(f"{path}: value is outside the schema enum")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise GateError(f"{path}: string is shorter than the schema minimum")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise GateError(f"{path}: string is longer than the schema maximum")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise GateError(f"{path}: string does not match the schema pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise GateError(f"{path}: number is below the schema minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise GateError(f"{path}: number is above the schema maximum")

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise GateError(f"{path}: array has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise GateError(f"{path}: array has too many items")
        if schema.get("uniqueItems"):
            identities = [_json_identity(item) for item in value]
            if len(identities) != len(set(identities)):
                raise GateError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema(item, item_schema, root, f"{path}[{index}]")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise GateError(f"{path}: missing required schema fields {missing}")
        if len(value) < schema.get("minProperties", 0):
            raise GateError(f"{path}: object has too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise GateError(f"{path}: object has too many properties")
        properties = schema.get("properties", {})
        patterns = schema.get("patternProperties", {})
        matched: set[str] = set()
        for name, member_schema in properties.items():
            if name in value:
                matched.add(name)
                _validate_schema(value[name], member_schema, root, f"{path}.{name}")
        for pattern, member_schema in patterns.items():
            for name, member in value.items():
                if re.search(pattern, name):
                    matched.add(name)
                    _validate_schema(member, member_schema, root, f"{path}.{name}")
        if schema.get("additionalProperties") is False:
            additional = set(value) - matched
            if additional:
                raise GateError(f"{path}: additional schema fields {sorted(additional)}")


def _validate_document(value: Any, schema_name: str, label: str) -> None:
    _, schema = _strict_json(HERE / schema_name, maximum=262_144)
    if not isinstance(schema, dict):
        raise GateError(f"{schema_name} is not a schema object")
    _validate_schema(value, schema, schema, label)


def _sha(raw: bytes) -> str:
    return HASH_PREFIX + hashlib.sha256(raw).hexdigest()


def _content_digest(content: dict[str, Any]) -> str:
    return _sha(canonical_json(content).encode("utf-8"))


def _strict_json(path: Path, *, maximum: int = 1_048_576) -> tuple[bytes, Any]:
    raw = path.read_bytes()
    if len(raw) > maximum:
        raise GateError(f"{path.name} exceeds {maximum} bytes")
    duplicates: list[str] = []

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                duplicates.append(key)
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite number {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GateError(f"{path.name} is not strict JSON") from exc
    if duplicates:
        raise GateError(f"{path.name} contains duplicate members")
    return raw, value


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise GateError(f"{label} fields must be exactly {sorted(fields)}")
    return value


def _hash(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith(HASH_PREFIX)
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise GateError(f"{label} is not a sha256 commitment")
    return value


def _proof(value: Any, digest: str, issuer: str | None, label: str) -> str:
    proof = _object(value, TOP_PROOF_FIELDS, label)
    if issuer is not None and proof["issuer"] != issuer:
        raise GateError(f"{label} issuer is not accepted")
    result = verify_proof_domain("content", digest, proof)
    if not result.authentic:
        raise GateError(f"{label} does not authenticate: {result.detail}")
    return str(proof["issuer"])


def _safe_path(root: Path, reference: str) -> Path:
    if not isinstance(reference, str) or not reference or "\\" in reference:
        raise GateError("evidence path is malformed")
    pure = PurePosixPath(reference)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != reference:
        raise GateError("evidence path escapes the evidence directory")
    target = root.joinpath(*pure.parts)
    if not target.is_file() or target.is_symlink():
        raise GateError(f"evidence member is missing or unsafe: {reference}")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise GateError("evidence path escapes the evidence directory") from exc
    return target


def _load_context(path: Path, evidence: Path) -> str:
    try:
        path.resolve().relative_to(evidence.resolve())
    except ValueError:
        pass
    else:
        raise GateError("the coordinator trust context must remain outside the evidence directory")
    _, value = _strict_json(path, maximum=16_384)
    _validate_document(value, "comprehension-context.schema.json", "context")
    context = _object(value, {"profile", "accepted_coordinator"}, "context")
    if context["profile"] != "bulla.inference-clearing-comprehension-context/0.1":
        raise GateError("wrong comprehension context profile")
    coordinator = context["accepted_coordinator"]
    if not isinstance(coordinator, str) or not coordinator.startswith("did:key:z"):
        raise GateError("accepted coordinator must be an externally supplied did:key")
    return coordinator


def _expected_bindings() -> tuple[str, str, str]:
    protocol = (HERE / "comprehension-protocol.json").read_bytes()
    projection = json.loads((HERE / "site-projection.json").read_bytes())
    commands = projection["reproduction"]["commands"]
    if not isinstance(commands, list) or not commands or not all(isinstance(item, str) for item in commands):
        raise GateError("generated reproduction commands are malformed")
    command_digest = _sha("\n".join(commands).encode("utf-8"))
    sidecar = (HERE / "inference-clearing-reproduction-kit.tar.sha256").read_text("ascii").split()
    if len(sidecar) != 2 or sidecar[1] != "inference-clearing-reproduction-kit.tar":
        raise GateError("reproduction kit sidecar is malformed")
    return _sha(protocol), command_digest, HASH_PREFIX + sidecar[0]


def _load_gate(evidence: Path, coordinator: str) -> tuple[dict[str, Any], str]:
    raw, value = _strict_json(_safe_path(evidence, "gate-open.json"))
    _validate_document(value, "comprehension-gate-open.schema.json", "gate opening")
    gate = _object(value, {"profile", "content", "proof"}, "gate opening")
    if gate["profile"] != "bulla.inference-clearing-comprehension-gate-open/0.1":
        raise GateError("wrong gate-opening profile")
    fields = {
        "protocol_sha256", "preview_commit", "immutable_preview_url", "route",
        "reproduction_command_sha256", "reproduction_kit_sha256",
        "preview_artifact_path", "preview_artifact_sha256",
        "preview_artifact_byte_length", "preview_content_type",
    }
    content = _object(gate["content"], fields, "gate-opening content")
    _proof(gate["proof"], _content_digest(content), coordinator, "gate-opening proof")
    expected_protocol, expected_commands, expected_kit = _expected_bindings()
    for field, expected in (
        ("protocol_sha256", expected_protocol),
        ("reproduction_command_sha256", expected_commands),
        ("reproduction_kit_sha256", expected_kit),
    ):
        if content[field] != expected:
            raise GateError(f"gate opening does not bind the current {field}")
    commit = content["preview_commit"]
    if not isinstance(commit, str) or len(commit) != 40 or set(commit) == {"0"}:
        raise GateError("preview commit is absent or invalid")
    parsed = urlparse(str(content["immutable_preview_url"]))
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise GateError("preview URL is not an anonymous immutable HTTPS URL")
    if content["route"] != "/bulla/experimental/inference-clearing":
        raise GateError("wrong preview route")
    if content["preview_artifact_path"] != "preview-page.html" or content["preview_content_type"] != "text/html":
        raise GateError("preview artifact contract is wrong")
    preview = _safe_path(evidence, content["preview_artifact_path"])
    preview_raw = preview.read_bytes()
    if _sha(preview_raw) != content["preview_artifact_sha256"]:
        raise GateError("retrieved preview bytes do not match the gate opening")
    if len(preview_raw) != content["preview_artifact_byte_length"]:
        raise GateError("retrieved preview length does not match the gate opening")
    return content, _sha(raw)


def _load_response(
    path: Path,
    *,
    coordinator: str,
    gate_digest: str,
) -> tuple[dict[str, Any], str]:
    raw, value = _strict_json(path)
    _validate_document(value, "comprehension-response.schema.json", "response")
    response = _object(
        value,
        {"profile", "content", "reader_proof", "coordinator_proof"},
        "response",
    )
    if response["profile"] != "bulla.inference-clearing-comprehension-response/0.1":
        raise GateError("wrong response profile")
    fields = {
        "attempt_id", "participant_id", "reader_key", "slot", "participant_role",
        "eligibility", "gate_open_sha256", "browser_story_without_help",
        "first_responses", "first_response_hashes", "rubric", "assistance",
        "terminal_reproduction", "corrections", "limitations",
    }
    content = _object(response["content"], fields, "response content")
    digest = _content_digest(content)
    reader = str(content["reader_key"])
    _proof(response["reader_proof"], digest, reader, "reader proof")
    _proof(response["coordinator_proof"], digest, coordinator, "coordinator countersignature")
    if content["gate_open_sha256"] != gate_digest:
        raise GateError("response is bound to the wrong gate opening")
    eligibility = _object(
        content["eligibility"],
        {
            "identity_commitment", "no_prior_bulla", "no_prior_glyph",
            "no_prior_res_agentica", "no_pr_review", "no_implementation_involvement",
        },
        "eligibility",
    )
    _hash(eligibility["identity_commitment"], "identity commitment")
    if not all(value is True for key, value in eligibility.items() if key != "identity_commitment"):
        raise GateError("participant does not satisfy the frozen exclusions")
    first = _object(content["first_responses"], ALL_QUESTIONS, "first responses")
    hashes = _object(content["first_response_hashes"], ALL_QUESTIONS, "first-response hashes")
    rubric = _object(content["rubric"], ALL_QUESTIONS, "rubric")
    for question in ALL_QUESTIONS:
        if not isinstance(first[question], str):
            raise GateError("first responses must be strings")
        if hashes[question] != _sha(first[question].encode("utf-8")):
            raise GateError(f"first response hash does not bind {question}")
        if rubric[question] not in {"PASS", "FAIL"}:
            raise GateError("rubric values must be PASS or FAIL")
        if not first[question] and rubric[question] != "FAIL":
            raise GateError("silence cannot pass a rubric item")
    assistance = content["assistance"]
    if not isinstance(assistance, list):
        raise GateError("assistance must be a list")
    for item in assistance:
        record = _object(item, {"category", "description"}, "assistance record")
        if record["category"] not in {"NAVIGATION", "ACCESSIBILITY"}:
            raise GateError("operative semantic assistance invalidates the response")
        if not isinstance(record["description"], str) or not record["description"]:
            raise GateError("assistance descriptions cannot be empty")
    terminal = _object(
        content["terminal_reproduction"], {"required", "status", "duration_seconds"},
        "terminal reproduction",
    )
    if terminal["status"] not in {"COMPLETED", "FAILED", "NOT_REQUIRED"}:
        raise GateError("unknown terminal reproduction status")
    if terminal["required"] is False and terminal["status"] != "NOT_REQUIRED":
        raise GateError("a non-required terminal run must say NOT_REQUIRED")
    if terminal["required"] is True and terminal["status"] == "NOT_REQUIRED":
        raise GateError("a required terminal run cannot say NOT_REQUIRED")
    if terminal["status"] == "COMPLETED":
        duration = terminal["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
            raise GateError("completed terminal reproduction needs a duration")
    elif terminal["duration_seconds"] is not None:
        raise GateError("an uncompleted terminal run cannot claim a duration")
    if not isinstance(content["browser_story_without_help"], bool):
        raise GateError("browser story outcome must be Boolean")
    return content, _sha(raw)


def _measures(responses: list[dict[str, Any]]) -> dict[str, Any]:
    terminal = next(
        (item["terminal_reproduction"]["duration_seconds"] for item in responses if item["terminal_reproduction"]["required"]),
        None,
    )
    return {
        "participants": len(responses),
        "browser_story_passes": sum(item["browser_story_without_help"] for item in responses),
        "role_handoff_passes": sum(item["rubric"]["role_handoff"] == "PASS" for item in responses),
        "all_distinctions_passes": sum(
            all(item["rubric"][question] == "PASS" for question in SEMANTIC_QUESTIONS)
            for item in responses
        ),
        "answer_correctness_boundary_passes": sum(item["rubric"]["answer_correctness"] == "PASS" for item in responses),
        "funds_movement_boundary_passes": sum(item["rubric"]["funds_movement"] == "PASS" for item in responses),
        "terminal_reproduction_seconds": terminal,
    }


def _failed_dimensions(measures: dict[str, Any], denominator: int) -> set[str]:
    threshold = 4 if denominator == 5 else 2
    failures: set[str] = set()
    if measures["browser_story_passes"] < threshold:
        failures.add("browser_story_without_help")
    if measures["role_handoff_passes"] < threshold:
        failures.add("role_handoff")
    if measures["all_distinctions_passes"] < threshold:
        failures.add("all_eight_distinctions")
    if measures["answer_correctness_boundary_passes"] < denominator:
        failures.add("answer_correctness")
    if measures["funds_movement_boundary_passes"] < denominator:
        failures.add("funds_movement")
    terminal = measures["terminal_reproduction_seconds"]
    if terminal is None or terminal > 180:
        failures.add("terminal_reproduction")
    return failures


def score(evidence: Path, context_path: Path) -> dict[str, Any]:
    coordinator = _load_context(context_path, evidence)
    _, gate_digest = _load_gate(evidence, coordinator)
    manifest_raw, manifest_value = _strict_json(_safe_path(evidence, "attempt-manifest.json"))
    _validate_document(
        manifest_value,
        "comprehension-attempt-manifest.schema.json",
        "attempt manifest",
    )
    manifest = _object(manifest_value, {"profile", "content", "proof"}, "attempt manifest")
    if manifest["profile"] != "bulla.inference-clearing-comprehension-attempt-manifest/0.1":
        raise GateError("wrong attempt-manifest profile")
    manifest_content = _object(manifest["content"], {"gate_open_sha256", "attempts"}, "attempt manifest content")
    _proof(manifest["proof"], _content_digest(manifest_content), coordinator, "attempt-manifest proof")
    if manifest_content["gate_open_sha256"] != gate_digest:
        raise GateError("attempt manifest is bound to the wrong gate opening")
    attempts = manifest_content["attempts"]
    if not isinstance(attempts, list) or len(attempts) not in {1, 2}:
        raise GateError("attempt manifest must contain one or two attempts")

    participant_ids: set[str] = set()
    reader_keys: set[str] = set()
    identity_commitments: set[str] = set()
    attempt_history: list[dict[str, Any]] = []
    all_evidence: list[str] = []
    previous_failures: set[str] | None = None
    final_measures: dict[str, Any] | None = None

    for index, attempt_value in enumerate(attempts):
        attempt = _object(
            attempt_value, {"attempt_id", "scope", "dimensions", "response_refs"},
            "attempt",
        )
        scope = attempt["scope"]
        dimensions = attempt["dimensions"]
        refs = attempt["response_refs"]
        if not isinstance(dimensions, list) or set(dimensions) - ACCEPTANCE_DIMENSIONS or len(dimensions) != len(set(dimensions)):
            raise GateError("attempt dimensions are invalid")
        expected_dimensions = ACCEPTANCE_DIMENSIONS if index == 0 else previous_failures
        if set(dimensions) != expected_dimensions:
            raise GateError("attempt scope does not equal the required dimensions")
        expected_scope = "ALL_DIMENSIONS" if index == 0 else "FAILED_DIMENSIONS_ONLY"
        if scope != expected_scope:
            raise GateError("attempt scope is out of order")
        expected_count = 5 if index == 0 else 3
        if not isinstance(refs, list) or len(refs) != expected_count:
            raise GateError("attempt contains the wrong response count")
        responses: list[dict[str, Any]] = []
        evidence_hashes: list[str] = []
        slots: set[str] = set()
        for reference in refs:
            ref = _object(reference, {"path", "sha256"}, "response reference")
            response_path = _safe_path(evidence, ref["path"])
            response_raw = response_path.read_bytes()
            if _sha(response_raw) != ref["sha256"]:
                raise GateError("attempt manifest response hash differs")
            content, evidence_hash = _load_response(
                response_path, coordinator=coordinator, gate_digest=gate_digest
            )
            if content["attempt_id"] != attempt["attempt_id"]:
                raise GateError("response is bound to the wrong attempt")
            for value, seen, label in (
                (content["participant_id"], participant_ids, "participant"),
                (content["reader_key"], reader_keys, "reader key"),
                (content["eligibility"]["identity_commitment"], identity_commitments, "identity commitment"),
            ):
                if value in seen:
                    raise GateError(f"duplicate or reused {label}")
                seen.add(value)
            slot = content["slot"]
            if slot in slots:
                raise GateError("duplicate participant slot")
            slots.add(slot)
            responses.append(content)
            evidence_hashes.append(evidence_hash)
        if index == 0:
            if slots != set(INITIAL_SLOTS):
                raise GateError("first attempt does not fill the frozen five slots")
            for response in responses:
                if response["participant_role"] != INITIAL_SLOTS[response["slot"]]:
                    raise GateError("participant role does not match the frozen slot")
                terminal = response["terminal_reproduction"]
                if response["slot"] == "developer-1":
                    if not terminal["required"] or terminal["status"] != "COMPLETED" or terminal["duration_seconds"] > 180:
                        raise GateError("developer-1 did not complete the terminal path in time")
                elif terminal != {"required": False, "status": "NOT_REQUIRED", "duration_seconds": None}:
                    raise GateError("only developer-1 may be the initial terminal slot")
        else:
            if slots != {"retest-1", "retest-2", "retest-3"}:
                raise GateError("retest does not fill the three fresh slots")
            terminal_required = "terminal_reproduction" in dimensions
            for response in responses:
                terminal = response["terminal_reproduction"]
                if response["slot"] == "retest-1" and terminal_required:
                    if not terminal["required"] or terminal["status"] != "COMPLETED" or terminal["duration_seconds"] > 180:
                        raise GateError("retest terminal path did not complete in time")
                elif terminal != {"required": False, "status": "NOT_REQUIRED", "duration_seconds": None}:
                    raise GateError("unexpected terminal reproduction in retest")
        measures = _measures(responses)
        failures = _failed_dimensions(measures, expected_count) & set(dimensions)
        status = "ESTABLISHED" if not failures else "NOT_ESTABLISHED"
        attempt_history.append({
            "attempt_id": attempt["attempt_id"],
            "status": status,
            "scope": scope,
            "dimensions": dimensions,
            "participant_evidence": evidence_hashes,
        })
        all_evidence.extend(evidence_hashes)
        final_measures = measures
        previous_failures = failures
        if index == 0 and status == "ESTABLISHED" and len(attempts) != 1:
            raise GateError("a passing first attempt cannot be followed by a retest")
        if index == 0 and status == "NOT_ESTABLISHED" and len(attempts) == 2:
            continue
        if index == 0 and status == "NOT_ESTABLISHED" and len(attempts) == 1:
            break

    assert final_measures is not None
    final_status = attempt_history[-1]["status"]
    result = {
        "profile": "bulla.inference-clearing-comprehension-result/0.1",
        "status": final_status,
        "protocol_sha256": _sha((HERE / "comprehension-protocol.json").read_bytes()),
        "gate_open_sha256": gate_digest,
        "attempt_manifest_sha256": _sha(manifest_raw),
        "attempt_history": attempt_history,
        "participant_evidence": all_evidence,
        "measures": final_measures,
        "limitations": [
            "This is product-comprehension evidence, not independent profile verification.",
            "Participants are a small purposive cohort and do not establish representative adoption.",
        ],
    }
    _validate_document(result, "comprehension-result.schema.json", "derived result")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = score(args.evidence, args.context)
    except (GateError, OSError, KeyError, TypeError) as exc:
        print(f"comprehension gate rejected: {exc}", file=sys.stderr)
        return 2
    raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.out.parent, delete=False) as handle:
        handle.write(raw)
        temporary = Path(handle.name)
    os.replace(temporary, args.out)
    print(f"comprehension gate {result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
