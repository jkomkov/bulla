"""Finite receiver admission for evidence-carrying agent handoffs.

This source-only research profile keeps four questions separate:

* did the named sender authenticate the retained handoff record?
* did an accepted principal convey a current, non-amplified capability?
* does the retained message distinguish the receiver policy's decision?
* if a retained premise is corrected, which declared downstream reliances must
  be revisited?

It does not authorize a downstream side effect, infer the truth of a message,
prove complete observation, or turn transport identity into institutional
authority.  See ``spec/handoff-admission/PROFILE.md``.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from bulla._canonical import CanonicalizationError, JCS_SAFE_INTEGER, canonical_jcs_int
from bulla.action_receipt import verify_receipt
from bulla.identity import pubkey_from_did_key, verify_proof_domain


PROFILE = "bulla.handoff-admission/0.1-experimental"
CAPABILITY_PROFILE = "bulla.handoff-capability/0.1-experimental"
CONTEXT_PROFILE = "bulla.handoff-verification-context/0.1-experimental"
DECISION_MODEL_PROFILE = "bulla.handoff-decision-model/0.1-experimental"
TRANSPORT_PROFILE = "bulla.handoff-transport-observation/0.1-experimental"
CORRECTION_LEDGER_PROFILE = "bulla.handoff-correction-ledger/0.1-experimental"
RELIANCE_GRAPH_PROFILE = "bulla.handoff-reliance-graph/0.1-experimental"
RECALL_REPORT_PROFILE = "bulla.handoff-recall-report/0.1-experimental"

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class HandoffAdmissionError(ValueError):
    """Malformed, unsafe, unsupported, or internally inconsistent input."""


class Admission(str, Enum):
    ADMIT = "ADMIT"
    REFUSE = "REFUSE"
    UNDETERMINED = "UNDETERMINED"


class Sufficiency(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    CONTRADICTORY = "CONTRADICTORY"


class RecallStatus(str, Enum):
    AFFECTED = "AFFECTED"
    NOT_AFFECTED = "NOT_AFFECTED"
    UNDETERMINED = "UNDETERMINED"


@dataclass(frozen=True)
class HandoffParseLimits:
    max_bytes: int = 262_144
    max_depth: int = 16
    max_nodes: int = 4_096
    max_string_bytes: int = 4_096
    max_capability_depth: int = 8
    max_worlds: int = 128
    max_variables: int = 16
    max_graph_nodes: int = 128

    def __post_init__(self) -> None:
        if min(
            self.max_bytes,
            self.max_depth,
            self.max_nodes,
            self.max_string_bytes,
            self.max_capability_depth,
            self.max_worlds,
            self.max_variables,
            self.max_graph_nodes,
        ) <= 0:
            raise ValueError("handoff parse limits must be positive")


@dataclass(frozen=True)
class HandoffAdmissionReport:
    report: Mapping[str, Any]

    def __bool__(self) -> bool:
        raise TypeError(
            "A handoff admission report has independent integrity, authority, "
            "sufficiency, and admission dimensions; inspect a named field"
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.report)


def verify_handoff_report_commitment(
    report: bytes | bytearray | memoryview,
    *,
    limits: HandoffParseLimits = HandoffParseLimits(),
) -> Mapping[str, Any]:
    """Verify the commitment on a structurally validated handoff report.

    JSON Schema closes the report's wire shape.  This function supplies the
    separate computation JSON Schema cannot express: ``report_digest`` must be
    the canonical hash of every other report member.
    """

    value = parse_json_bytes(report, label="handoff report", limits=limits)
    if not isinstance(value, Mapping):
        raise HandoffAdmissionError("handoff report must be an object")
    expected_fields = {
        "profile",
        "handoff_id",
        "integrity",
        "receipt_integrity",
        "transport_binding",
        "transport_authority",
        "policy_binding",
        "authority",
        "decision_sufficiency",
        "observation_grounding",
        "observation_acceptance",
        "observation_truth",
        "receiver_admission",
        "blockers",
        "downstream_action_authority",
        "worldly_truth",
        "denominator_completeness",
        "limitations",
        "report_digest",
    }
    value = _exact(value, expected_fields, "handoff report")
    supplied = _digest(value["report_digest"], "handoff report.report_digest")
    body = {key: item for key, item in value.items() if key != "report_digest"}
    if supplied != canonical_hash(body):
        raise HandoffAdmissionError("handoff report digest mismatch")
    return value


def canonical_hash(value: Any) -> str:
    try:
        encoded = canonical_jcs_int(value).encode("utf-8")
    except CanonicalizationError as exc:
        raise HandoffAdmissionError(f"value is not canonicalizable: {exc}") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _exact(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HandoffAdmissionError(f"{label} must be an object")
    missing, extra = fields - set(value), set(value) - fields
    if missing or extra:
        raise HandoffAdmissionError(
            f"{label} keys mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise HandoffAdmissionError(
            f"{label} must be a non-empty, surrounding-whitespace-free string"
        )
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise HandoffAdmissionError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _safe_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or abs(value) > JCS_SAFE_INTEGER
    ):
        raise HandoffAdmissionError(
            f"{label} must be a safe integer greater than or equal to {minimum}"
        )
    return value


def _string_array(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise HandoffAdmissionError(f"{label} must be a non-empty array")
    result = [_string(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise HandoffAdmissionError(f"{label} must not contain duplicates")
    return result


def _did_key(value: Any, label: str) -> str:
    identifier = _string(value, label)
    try:
        pubkey_from_did_key(identifier)
    except ValueError as exc:
        raise HandoffAdmissionError(f"{label} must be an Ed25519 did:key") from exc
    return identifier


def parse_json_bytes(
    raw: bytes | bytearray | memoryview,
    *,
    label: str = "document",
    limits: HandoffParseLimits = HandoffParseLimits(),
) -> Any:
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise HandoffAdmissionError(f"{label} must be supplied as bytes")
    encoded = bytes(raw)
    if len(encoded) > limits.max_bytes:
        raise HandoffAdmissionError(f"{label} exceeds {limits.max_bytes} bytes")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HandoffAdmissionError(f"{label} is not valid UTF-8") from exc

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HandoffAdmissionError(f"duplicate JSON member {key!r} in {label}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise HandoffAdmissionError(f"non-finite number {value!r} in {label}")

    try:
        value = json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)
    except HandoffAdmissionError:
        raise
    except (json.JSONDecodeError, RecursionError) as exc:
        raise HandoffAdmissionError(f"invalid JSON in {label}: {exc}") from exc

    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes or depth > limits.max_depth:
            raise HandoffAdmissionError(f"{label} exceeds JSON resource limits")
        if isinstance(current, str):
            try:
                string_bytes = current.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise HandoffAdmissionError(
                    f"{label} contains a lone Unicode surrogate"
                ) from exc
            if len(string_bytes) > limits.max_string_bytes:
                raise HandoffAdmissionError(f"{label} contains an oversized string")
        elif isinstance(current, int) and not isinstance(current, bool):
            if abs(current) > JCS_SAFE_INTEGER:
                raise HandoffAdmissionError(f"{label} contains an unsafe integer")
        elif isinstance(current, float):
            raise HandoffAdmissionError(f"{label} contains a floating-point number")
        elif isinstance(current, Mapping):
            for key, item in current.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return value


_SCOPE_FIELDS = {"services", "actions", "resources"}


def validate_scope(value: Any, label: str = "scope") -> dict[str, Any]:
    value = _exact(value, _SCOPE_FIELDS, label)
    result = {
        "services": sorted(_string_array(value["services"], f"{label}.services")),
        "actions": sorted(_string_array(value["actions"], f"{label}.actions")),
        "resources": sorted(_string_array(value["resources"], f"{label}.resources")),
    }
    if any(result[name] != value[name] for name in ("services", "actions", "resources")):
        raise HandoffAdmissionError(f"{label} set-valued fields must be sorted")
    return result


def scope_attenuates(child: Mapping[str, Any], parent: Mapping[str, Any]) -> bool:
    child_scope = validate_scope(child, "child_scope")
    parent_scope = validate_scope(parent, "parent_scope")
    return (
        set(child_scope["services"]) <= set(parent_scope["services"])
        and set(child_scope["actions"]) <= set(parent_scope["actions"])
        and set(child_scope["resources"]) <= set(parent_scope["resources"])
    )


_GRANT_FIELDS = {
    "profile",
    "grant_hash",
    "grantor",
    "grantee",
    "principal",
    "parent_grant",
    "authority_epoch",
    "checkpoint_domain",
    "valid_from",
    "valid_through",
    "scope",
    "proof",
}


def _grant_core(grant: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: grant[key]
        for key in (
            "profile",
            "grantor",
            "grantee",
            "principal",
            "parent_grant",
            "authority_epoch",
            "checkpoint_domain",
            "valid_from",
            "valid_through",
            "scope",
        )
    }


def build_capability_grant(
    *,
    signer: Any,
    grantee: str,
    principal: str,
    parent_grant: str | None,
    authority_epoch: int,
    checkpoint_domain: str,
    valid_from: int,
    valid_through: int,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    grantor = getattr(signer, "verification_method", None)
    _did_key(grantor, "signer.verification_method")
    core = {
        "profile": CAPABILITY_PROFILE,
        "grantor": grantor,
        "grantee": _did_key(grantee, "grantee"),
        "principal": _did_key(principal, "principal"),
        "parent_grant": None if parent_grant is None else _digest(parent_grant, "parent_grant"),
        "authority_epoch": _safe_int(authority_epoch, "authority_epoch"),
        "checkpoint_domain": _string(checkpoint_domain, "checkpoint_domain"),
        "valid_from": _safe_int(valid_from, "valid_from"),
        "valid_through": _safe_int(valid_through, "valid_through"),
        "scope": validate_scope(scope),
    }
    if core["valid_from"] > core["valid_through"]:
        raise HandoffAdmissionError("valid_from must not exceed valid_through")
    grant_hash = canonical_hash(core)
    return {
        **core,
        "grant_hash": grant_hash,
        "proof": signer.sign_domain("delegation-grant", grant_hash),
    }


def _validate_context(context: Any, limits: HandoffParseLimits) -> Mapping[str, Any]:
    context = _exact(
        context,
        {
            "profile",
            "accepted_principals",
            "accepted_senders",
            "accepted_receivers",
            "authority_epoch",
            "semantic_epoch",
            "checkpoint",
            "revocation_registry",
            "admission_policy",
            "observation_policy",
            "decision_model",
            "correction_authorities",
            "accepted_reliance_graphs",
        },
        "context",
    )
    if context["profile"] != CONTEXT_PROFILE:
        raise HandoffAdmissionError(f"context.profile must be {CONTEXT_PROFILE!r}")
    for field in (
        "accepted_principals",
        "accepted_senders",
        "accepted_receivers",
        "correction_authorities",
    ):
        values = _string_array(context[field], f"context.{field}")
        for index, value in enumerate(values):
            _did_key(value, f"context.{field}[{index}]")
    accepted_graphs = _string_array(
        context["accepted_reliance_graphs"],
        "context.accepted_reliance_graphs",
        allow_empty=True,
    )
    for index, value in enumerate(accepted_graphs):
        _digest(value, f"context.accepted_reliance_graphs[{index}]")
    _safe_int(context["authority_epoch"], "context.authority_epoch")
    _safe_int(context["semantic_epoch"], "context.semantic_epoch")
    checkpoint = _exact(context["checkpoint"], {"domain", "value"}, "context.checkpoint")
    _string(checkpoint["domain"], "context.checkpoint.domain")
    _safe_int(checkpoint["value"], "context.checkpoint.value")
    registry = _exact(
        context["revocation_registry"],
        {"coverage", "revoked_grant_hashes"},
        "context.revocation_registry",
    )
    if registry["coverage"] not in {"COMPLETE", "PARTIAL"}:
        raise HandoffAdmissionError("revocation coverage must be COMPLETE or PARTIAL")
    revoked = _string_array(
        registry["revoked_grant_hashes"],
        "context.revocation_registry.revoked_grant_hashes",
        allow_empty=True,
    )
    for index, value in enumerate(revoked):
        _digest(value, f"revoked_grant_hashes[{index}]")
    policy = _exact(
        context["admission_policy"],
        {
            "required_transport_profiles",
            "require_current_capability",
            "require_decision_sufficiency",
            "require_complete_ancestry",
        },
        "context.admission_policy",
    )
    _string_array(policy["required_transport_profiles"], "required_transport_profiles")
    for boolean in (
        "require_current_capability",
        "require_decision_sufficiency",
        "require_complete_ancestry",
    ):
        if not isinstance(policy[boolean], bool):
            raise HandoffAdmissionError(f"context.admission_policy.{boolean} must be Boolean")
        if policy[boolean] is not True:
            raise HandoffAdmissionError(
                f"context.admission_policy.{boolean} must be true in profile 0.1; "
                "the alpha defines no weaker implicit policy"
            )
    decision_model = validate_decision_model(context["decision_model"], limits=limits)
    variable_names = {item["name"] for item in decision_model["variables"]}
    observation_policy = _exact(
        context["observation_policy"], variable_names, "context.observation_policy"
    )
    for name, accepted in observation_policy.items():
        values = _string_array(
            accepted,
            f"context.observation_policy.{name}",
            allow_empty=True,
        )
        if any(value not in {"COUNTERPARTY_SIGNED"} for value in values):
            raise HandoffAdmissionError(
                f"context.observation_policy.{name} contains unsupported grounding"
            )
    return context


def verify_capability_chain(
    chain: Any,
    *,
    sender: str,
    requested_scope: Mapping[str, Any],
    context: Mapping[str, Any],
    limits: HandoffParseLimits = HandoffParseLimits(),
) -> dict[str, Any]:
    if not isinstance(chain, list) or not chain or len(chain) > limits.max_capability_depth:
        raise HandoffAdmissionError(
            f"capability_chain must contain 1..{limits.max_capability_depth} grants"
        )
    requested = validate_scope(requested_scope, "requested_scope")
    grants: list[Mapping[str, Any]] = []
    reasons: list[str] = []
    chain_integrity = "VERIFIED"
    scope_status = "ATTENUATED"
    temporal_status = "CURRENT"
    epoch_status = "CURRENT"
    principal_status = "ACCEPTED"
    checkpoint = context["checkpoint"]

    for index, raw in enumerate(chain):
        grant = _exact(raw, _GRANT_FIELDS, f"capability_chain[{index}]")
        if grant["profile"] != CAPABILITY_PROFILE:
            raise HandoffAdmissionError(f"capability_chain[{index}] has unsupported profile")
        for name in ("grantor", "grantee", "principal"):
            _did_key(grant[name], f"capability_chain[{index}].{name}")
        _digest(grant["grant_hash"], f"capability_chain[{index}].grant_hash")
        if grant["parent_grant"] is not None:
            _digest(grant["parent_grant"], f"capability_chain[{index}].parent_grant")
        _safe_int(grant["authority_epoch"], f"capability_chain[{index}].authority_epoch")
        _string(grant["checkpoint_domain"], f"capability_chain[{index}].checkpoint_domain")
        start = _safe_int(grant["valid_from"], f"capability_chain[{index}].valid_from")
        end = _safe_int(grant["valid_through"], f"capability_chain[{index}].valid_through")
        if start > end:
            raise HandoffAdmissionError("capability grant valid_from exceeds valid_through")
        validate_scope(grant["scope"], f"capability_chain[{index}].scope")
        expected_hash = canonical_hash(_grant_core(grant))
        if grant["grant_hash"] != expected_hash:
            chain_integrity = "FAILED"
            reasons.append(f"grant {index} hash mismatch")
        proof = verify_proof_domain("delegation-grant", expected_hash, grant["proof"])
        if (
            not proof.authentic
            or proof.method != "did:key"
            or proof.issuer != grant["grantor"]
        ):
            chain_integrity = "FAILED"
            reasons.append(f"grant {index} proof is not a self-certifying grantor proof")
        if grant["authority_epoch"] != context["authority_epoch"]:
            epoch_status = "STALE"
        if grant["checkpoint_domain"] != checkpoint["domain"]:
            temporal_status = "UNRESOLVED"
        elif checkpoint["value"] < start:
            temporal_status = "NOT_YET_CURRENT"
        elif checkpoint["value"] > end:
            temporal_status = "EXPIRED"
        grants.append(grant)

    principal = grants[0]["principal"]
    if (
        grants[0]["parent_grant"] is not None
        or grants[0]["grantor"] != principal
        or principal not in context["accepted_principals"]
        or any(grant["principal"] != principal for grant in grants)
    ):
        principal_status = "REJECTED"
        reasons.append("root principal is absent, inconsistent, or not externally accepted")
    identities = [grants[0]["grantor"], *[grant["grantee"] for grant in grants]]
    if len(identities) != len(set(identities)):
        chain_integrity = "FAILED"
        reasons.append("capability chain contains an identity cycle")
    for index in range(1, len(grants)):
        previous, current = grants[index - 1], grants[index]
        if (
            current["grantor"] != previous["grantee"]
            or current["parent_grant"] != previous["grant_hash"]
        ):
            chain_integrity = "FAILED"
            reasons.append(f"grant {index} breaks chain continuity")
        if not scope_attenuates(current["scope"], previous["scope"]):
            scope_status = "AMPLIFIED"
            reasons.append(f"grant {index} broadens its parent scope")
    if not scope_attenuates(requested, grants[-1]["scope"]):
        scope_status = "AMPLIFIED"
        reasons.append("requested scope exceeds the leaf capability")
    if grants[-1]["grantee"] != sender or sender not in context["accepted_senders"]:
        principal_status = "REJECTED"
        reasons.append("capability leaf does not bind an externally accepted sender")

    revoked_hashes = set(context["revocation_registry"]["revoked_grant_hashes"])
    if any(grant["grant_hash"] in revoked_hashes for grant in grants):
        revocation_status = "REVOKED"
        reasons.append("a grant is present in the supplied revocation registry")
    elif context["revocation_registry"]["coverage"] == "COMPLETE":
        revocation_status = "NOT_REVOKED"
    else:
        revocation_status = "UNDETERMINED"
        reasons.append("the supplied revocation registry is not declared complete")

    current = (
        chain_integrity == "VERIFIED"
        and principal_status == "ACCEPTED"
        and scope_status == "ATTENUATED"
        and temporal_status == "CURRENT"
        and epoch_status == "CURRENT"
        and revocation_status == "NOT_REVOKED"
    )
    return {
        "chain_integrity": chain_integrity,
        "principal_acceptance": principal_status,
        "scope_attenuation": scope_status,
        "temporal_status": temporal_status,
        "authority_epoch": epoch_status,
        "revocation_status": revocation_status,
        "current_capability": "VERIFIED" if current else "NOT_VERIFIED",
        "principal": principal,
        "leaf_grant": grants[-1]["grant_hash"],
        "reasons": reasons,
    }


def validate_decision_model(
    model: Any,
    *,
    limits: HandoffParseLimits = HandoffParseLimits(),
) -> Mapping[str, Any]:
    model = _exact(model, {"profile", "variables", "worlds"}, "decision_model")
    if model["profile"] != DECISION_MODEL_PROFILE:
        raise HandoffAdmissionError(
            f"decision_model.profile must be {DECISION_MODEL_PROFILE!r}"
        )
    variables = model["variables"]
    worlds = model["worlds"]
    if not isinstance(variables, list) or not 1 <= len(variables) <= limits.max_variables:
        raise HandoffAdmissionError("decision_model.variables is empty or over limit")
    if not isinstance(worlds, list) or not 1 <= len(worlds) <= limits.max_worlds:
        raise HandoffAdmissionError("decision_model.worlds is empty or over limit")
    domains: dict[str, list[str]] = {}
    for index, raw in enumerate(variables):
        item = _exact(raw, {"name", "values"}, f"decision_model.variables[{index}]")
        name = _string(item["name"], f"decision_model.variables[{index}].name")
        if name in domains:
            raise HandoffAdmissionError(f"duplicate decision variable {name!r}")
        domains[name] = _string_array(item["values"], f"decision variable {name}.values")
    world_ids: set[str] = set()
    assignments: set[str] = set()
    for index, raw in enumerate(worlds):
        world = _exact(raw, {"id", "values", "decision"}, f"decision_model.worlds[{index}]")
        world_id = _string(world["id"], f"decision_model.worlds[{index}].id")
        if world_id in world_ids:
            raise HandoffAdmissionError(f"duplicate world id {world_id!r}")
        world_ids.add(world_id)
        values = _exact(world["values"], set(domains), f"world {world_id}.values")
        for name, value in values.items():
            if value not in domains[name]:
                raise HandoffAdmissionError(
                    f"world {world_id} uses {value!r} outside domain of {name!r}"
                )
        if world["decision"] not in {"PROCEED", "HOLD"}:
            raise HandoffAdmissionError(f"world {world_id} has unsupported decision")
        signature = canonical_hash(values)
        if signature in assignments:
            raise HandoffAdmissionError("decision model contains duplicate assignments")
        assignments.add(signature)
    return model


def assess_decision_sufficiency(
    model: Mapping[str, Any],
    observations: Any,
    *,
    limits: HandoffParseLimits = HandoffParseLimits(),
) -> dict[str, Any]:
    model = validate_decision_model(model, limits=limits)
    if not isinstance(observations, Mapping):
        raise HandoffAdmissionError("observations must be an object")
    domains = {item["name"]: item["values"] for item in model["variables"]}
    if not set(observations) <= set(domains):
        raise HandoffAdmissionError(
            f"observations contain unknown variables {sorted(set(observations) - set(domains))}"
        )
    for name, value in observations.items():
        if value not in domains[name]:
            raise HandoffAdmissionError(f"observation {name!r} is outside its closed domain")
    compatible = [
        world
        for world in model["worlds"]
        if all(world["values"][name] == value for name, value in observations.items())
    ]
    decisions = sorted({world["decision"] for world in compatible})
    if not compatible:
        status = Sufficiency.CONTRADICTORY
        decision = None
        counterexample = None
        minimum_disclosures: list[list[str]] = []
    elif len(decisions) == 1:
        status = Sufficiency.SUFFICIENT
        decision = decisions[0]
        counterexample = None
        minimum_disclosures = [[]]
    else:
        status = Sufficiency.INSUFFICIENT
        decision = None
        left = next(world for world in compatible if world["decision"] == decisions[0])
        right = next(world for world in compatible if world["decision"] != decisions[0])
        counterexample = {
            "left_world": left["id"],
            "right_world": right["id"],
            "left_decision": left["decision"],
            "right_decision": right["decision"],
            "differing_values": [
                name
                for name in domains
                if left["values"][name] != right["values"][name]
            ],
        }
        unknown = sorted(set(domains) - set(observations))
        minimum_disclosures = []
        for size in range(1, len(unknown) + 1):
            for candidate in combinations(unknown, size):
                groups: dict[tuple[str, ...], set[str]] = defaultdict(set)
                for world in compatible:
                    groups[tuple(world["values"][name] for name in candidate)].add(
                        world["decision"]
                    )
                if all(len(values) == 1 for values in groups.values()):
                    minimum_disclosures.append(list(candidate))
            if minimum_disclosures:
                break
    return {
        "status": status.value,
        "compatible_worlds": [world["id"] for world in compatible],
        "decision_image": decisions,
        "decision": decision,
        "counterexample": counterexample,
        "minimum_cardinality_disclosure_sets": minimum_disclosures,
        "limitations": [
            "The result is relative to the supplied finite decision model and observations.",
            "A singleton decision image does not establish that the model "
            "contains every worldly possibility.",
            "Minimum-cardinality disclosure sets minimize field count, not privacy "
            "loss, sensitivity, acquisition cost, or information content.",
        ],
    }


_TRANSPORT_FIELDS = {
    "profile",
    "adapter",
    "protocol_version",
    "transport_id",
    "source_actor",
    "destination_actor",
    "task_or_session_id",
    "observation",
    "observation_digest",
    "payload_digest",
    "persistence",
    "transport_authority",
}


def _validate_message(value: Any, label: str) -> Mapping[str, Any]:
    message = _exact(value, {"summary", "payload"}, label)
    _string(message["summary"], f"{label}.summary")
    return message


def _validate_transport(value: Any) -> Mapping[str, Any]:
    value = _exact(value, _TRANSPORT_FIELDS, "transport")
    if value["profile"] != TRANSPORT_PROFILE:
        raise HandoffAdmissionError(f"transport.profile must be {TRANSPORT_PROFILE!r}")
    for field in (
        "adapter",
        "protocol_version",
        "transport_id",
        "source_actor",
        "destination_actor",
        "task_or_session_id",
    ):
        _string(value[field], f"transport.{field}")
    observation_digest = _digest(
        value["observation_digest"], "transport.observation_digest"
    )
    if observation_digest != canonical_hash(value["observation"]):
        raise HandoffAdmissionError("transport observation digest mismatch")
    adapter = value["adapter"]
    if adapter == "fixture-mailbox/1":
        observation = _exact(
            value["observation"], {"message"}, "transport.observation"
        )
        projected_message = _validate_message(
            observation["message"], "transport.observation.message"
        )
    elif adapter == "claude-code-agent-team-observation/1":
        observation = _exact(
            value["observation"],
            {"team", "sender", "recipient", "message_id", "body"},
            "transport.observation",
        )
        for field in ("team", "sender", "recipient", "message_id"):
            _string(observation[field], f"transport.observation.{field}")
        if (
            value["transport_id"] != observation["message_id"]
            or value["task_or_session_id"] != observation["team"]
        ):
            raise HandoffAdmissionError(
                "transport Claude identifiers do not match the retained observation"
            )
        projected_message = _validate_message(
            observation["body"], "transport.observation.body"
        )
    elif adapter == "a2a-message-observation/1":
        observation = _exact(
            value["observation"],
            {"messageId", "contextId", "taskId", "role", "parts", "metadata"},
            "transport.observation",
        )
        for field in ("messageId", "contextId", "taskId"):
            _string(observation[field], f"transport.observation.{field}")
        if (
            value["transport_id"] != observation["messageId"]
            or value["task_or_session_id"]
            != f"{observation['contextId']}:{observation['taskId']}"
        ):
            raise HandoffAdmissionError(
                "transport A2A identifiers do not match the retained observation"
            )
        if observation["role"] not in {"ROLE_AGENT", "agent"}:
            raise HandoffAdmissionError("transport A2A observation role must be agent")
        if not isinstance(observation["parts"], list) or len(observation["parts"]) != 1:
            raise HandoffAdmissionError(
                "transport A2A observation must contain one closed data part"
            )
        part = _exact(
            observation["parts"][0], {"data"}, "transport.observation.parts[0]"
        )
        projected_message = _validate_message(
            part["data"], "transport.observation.parts[0].data"
        )
        if not isinstance(observation["metadata"], Mapping):
            raise HandoffAdmissionError("transport A2A observation metadata must be an object")
    else:
        raise HandoffAdmissionError(f"unsupported transport adapter {adapter!r}")
    payload_digest = _digest(value["payload_digest"], "transport.payload_digest")
    if payload_digest != canonical_hash(projected_message):
        raise HandoffAdmissionError("transport payload projection digest mismatch")
    if value["persistence"] != "RETAINED_CANONICAL_OBSERVATION":
        raise HandoffAdmissionError(
            "transport.persistence must be RETAINED_CANONICAL_OBSERVATION"
        )
    if value["transport_authority"] != "NOT_COMPUTED":
        raise HandoffAdmissionError("transport metadata cannot establish authority")
    return value


def adapt_claude_team_message(
    raw: bytes, *, source_actor: str, destination_actor: str
) -> dict[str, Any]:
    """Commit a closed local observation of a Claude Code teammate message.

    Anthropic documents separate teammate contexts, a shared task list, and
    direct messaging, but not a stable signed public mailbox wire format.  This
    adapter therefore accepts an explicit observation object and claims only
    canonical field binding—not Claude identity, delivery completeness, exact
    source bytes, or authority.
    """

    value = _exact(
        parse_json_bytes(raw, label="Claude team observation"),
        {"team", "sender", "recipient", "message_id", "body"},
        "Claude team observation",
    )
    for field in ("team", "sender", "recipient", "message_id"):
        _string(value[field], f"Claude team observation.{field}")
    message = _validate_message(value["body"], "Claude team observation.body")
    return {
        "profile": TRANSPORT_PROFILE,
        "adapter": "claude-code-agent-team-observation/1",
        "protocol_version": "experimental-agent-teams",
        "transport_id": value["message_id"],
        "source_actor": _string(source_actor, "source_actor"),
        "destination_actor": _string(destination_actor, "destination_actor"),
        "task_or_session_id": value["team"],
        "observation": value,
        "observation_digest": canonical_hash(value),
        "payload_digest": canonical_hash(message),
        "persistence": "RETAINED_CANONICAL_OBSERVATION",
        "transport_authority": "NOT_COMPUTED",
    }


def adapt_a2a_message(
    raw: bytes,
    *,
    source_actor: str,
    destination_actor: str,
    protocol_version: str = "1.0",
) -> dict[str, Any]:
    """Commit the closed A2A Message fields used by this profile.

    A2A message/task metadata is transport state.  It does not become a Bulla
    authority grant or a receiver-policy decision merely by being well formed.
    """

    value = _exact(
        parse_json_bytes(raw, label="A2A message"),
        {"messageId", "contextId", "taskId", "role", "parts", "metadata"},
        "A2A message",
    )
    message_id = _string(value["messageId"], "A2A message.messageId")
    context_id = _string(value["contextId"], "A2A message.contextId")
    task_id = _string(value["taskId"], "A2A message.taskId")
    if value["role"] not in {"ROLE_AGENT", "agent"}:
        raise HandoffAdmissionError("A2A message.role must be agent")
    if not isinstance(value["parts"], list) or len(value["parts"]) != 1:
        raise HandoffAdmissionError("A2A message must contain one closed data part")
    part = _exact(value["parts"][0], {"data"}, "A2A message.parts[0]")
    message = _validate_message(part["data"], "A2A message.parts[0].data")
    if not isinstance(value["metadata"], Mapping):
        raise HandoffAdmissionError("A2A message.metadata must be an object")
    return {
        "profile": TRANSPORT_PROFILE,
        "adapter": "a2a-message-observation/1",
        "protocol_version": _string(protocol_version, "protocol_version"),
        "transport_id": message_id,
        "source_actor": _string(source_actor, "source_actor"),
        "destination_actor": _string(destination_actor, "destination_actor"),
        "task_or_session_id": f"{context_id}:{task_id}",
        "observation": value,
        "observation_digest": canonical_hash(value),
        "payload_digest": canonical_hash(message),
        "persistence": "RETAINED_CANONICAL_OBSERVATION",
        "transport_authority": "NOT_COMPUTED",
    }


_HANDOFF_FIELDS = {
    "profile",
    "handoff_id",
    "sender",
    "receiver",
    "authority_epoch",
    "semantic_epoch",
    "requested_scope",
    "message",
    "message_digest",
    "transport",
    "capability_chain",
    "observations",
    "ancestry",
    "policy_hash",
    "receipt",
}


def verify_handoff(
    handoff: bytes | bytearray | memoryview,
    context: bytes | bytearray | memoryview,
    *,
    limits: HandoffParseLimits = HandoffParseLimits(),
) -> HandoffAdmissionReport:
    handoff = parse_json_bytes(handoff, label="handoff", limits=limits)
    context = parse_json_bytes(context, label="verification context", limits=limits)
    handoff = _exact(handoff, _HANDOFF_FIELDS, "handoff")
    context = _validate_context(context, limits)
    if handoff["profile"] != PROFILE:
        raise HandoffAdmissionError(f"handoff.profile must be {PROFILE!r}")
    handoff_id = _string(handoff["handoff_id"], "handoff.handoff_id")
    sender = _did_key(handoff["sender"], "handoff.sender")
    receiver = _did_key(handoff["receiver"], "handoff.receiver")
    _safe_int(handoff["authority_epoch"], "handoff.authority_epoch")
    _safe_int(handoff["semantic_epoch"], "handoff.semantic_epoch")
    requested_scope = validate_scope(handoff["requested_scope"], "handoff.requested_scope")
    message = _validate_message(handoff["message"], "handoff.message")
    message_digest = _digest(handoff["message_digest"], "handoff.message_digest")
    transport = _validate_transport(handoff["transport"])
    ancestry = _exact(handoff["ancestry"], {"complete", "parents"}, "handoff.ancestry")
    if not isinstance(ancestry["complete"], bool):
        raise HandoffAdmissionError("handoff.ancestry.complete must be Boolean")
    parents = _string_array(ancestry["parents"], "handoff.ancestry.parents", allow_empty=True)
    for index, parent in enumerate(parents):
        _digest(parent, f"handoff.ancestry.parents[{index}]")
    policy_hash = _digest(handoff["policy_hash"], "handoff.policy_hash")

    expected_message_digest = canonical_hash(message)
    expected_policy_hash = canonical_hash(
        {
            "admission_policy": context["admission_policy"],
            "observation_policy": context["observation_policy"],
            "decision_model": context["decision_model"],
        }
    )
    expected_chain_hash = canonical_hash(handoff["capability_chain"])
    expected_observations_hash = canonical_hash(handoff["observations"])
    expected_scope_hash = canonical_hash(requested_scope)
    transport_binding = (
        transport["payload_digest"] == message_digest == expected_message_digest
        and transport["source_actor"] == sender
        and transport["destination_actor"] == receiver
    )
    policy_binding = policy_hash == expected_policy_hash

    receipt = handoff["receipt"]
    if not isinstance(receipt, Mapping):
        raise HandoffAdmissionError("handoff.receipt must be an ActionReceipt object")
    receipt_verification = verify_receipt(dict(receipt))
    action = receipt.get("action") if isinstance(receipt.get("action"), Mapping) else {}
    subject = action.get("subject") if isinstance(action.get("subject"), Mapping) else {}
    expected_subject = {
        "profile": PROFILE,
        "handoff_id": handoff_id,
        "sender": sender,
        "receiver": receiver,
        "message_digest": message_digest,
        "transport_digest": canonical_hash(transport),
        "capability_chain_hash": expected_chain_hash,
        "requested_scope_hash": expected_scope_hash,
        "observations_hash": expected_observations_hash,
        "policy_hash": policy_hash,
        "authority_epoch": handoff["authority_epoch"],
        "semantic_epoch": handoff["semantic_epoch"],
        "ancestry_hash": canonical_hash(ancestry),
    }
    signature_issuer = (receipt.get("signature") or {}).get("issuer")
    authority = ((receipt.get("mandate") or {}).get("authority") or {})
    receipt_binding = (
        receipt.get("schema_version") == "0.4"
        and action.get("type") == "handoff.propose"
        and subject == expected_subject
        and receipt_verification.ok
        and receipt_verification.verified_to == "attestation"
        and receipt_verification.authority_authentic == "verified"
        and signature_issuer == sender
        and authority.get("principal") == sender
        and authority.get("policy") == policy_hash
        and authority.get("delegation") == []
        and ((receipt.get("mandate") or {}).get("bounds") or {}).get("scope")
        == f"profile:{PROFILE};action:handoff.propose"
        and receipt.get("conventions") == []
    )

    authority_report = verify_capability_chain(
        handoff["capability_chain"],
        sender=sender,
        requested_scope=requested_scope,
        context=context,
        limits=limits,
    )
    sufficiency = assess_decision_sufficiency(
        context["decision_model"], handoff["observations"], limits=limits
    )
    observation_grounding = {
        name: "COUNTERPARTY_SIGNED" for name in sorted(handoff["observations"])
    }
    observations_accepted = all(
        grounding in context["observation_policy"][name]
        for name, grounding in observation_grounding.items()
    )
    transport_accepted = (
        transport["adapter"] in context["admission_policy"]["required_transport_profiles"]
    )
    receiver_accepted = receiver in context["accepted_receivers"]
    semantic_current = handoff["semantic_epoch"] == context["semantic_epoch"]
    authority_epoch_current = handoff["authority_epoch"] == context["authority_epoch"]
    integrity = (
        "VERIFIED"
        if receipt_binding and transport_binding and policy_binding
        else "FAILED"
    )

    blockers: list[str] = []
    if integrity != "VERIFIED":
        blockers.append("INTEGRITY_OR_BINDING_FAILURE")
    if not transport_accepted:
        blockers.append("TRANSPORT_PROFILE_NOT_ACCEPTED")
    if not receiver_accepted:
        blockers.append("RECEIVER_NOT_ACCEPTED")
    if not semantic_current:
        blockers.append("STALE_SEMANTIC_EPOCH")
    if not authority_epoch_current:
        blockers.append("STALE_AUTHORITY_EPOCH")
    if not observations_accepted:
        blockers.append("OBSERVATION_GROUNDING_NOT_ACCEPTED")
    if authority_report["current_capability"] != "VERIFIED":
        blockers.append("CURRENT_CAPABILITY_NOT_VERIFIED")
    if sufficiency["status"] == Sufficiency.CONTRADICTORY.value:
        blockers.append("CONTRADICTORY_OBSERVATIONS")
    elif sufficiency["status"] == Sufficiency.INSUFFICIENT.value:
        blockers.append("DECISION_INSUFFICIENT")
    if not ancestry["complete"]:
        blockers.append("ANCESTRY_INCOMPLETE")

    hard_refusal = any(
        blocker
        in {
            "INTEGRITY_OR_BINDING_FAILURE",
            "TRANSPORT_PROFILE_NOT_ACCEPTED",
            "RECEIVER_NOT_ACCEPTED",
            "STALE_SEMANTIC_EPOCH",
            "STALE_AUTHORITY_EPOCH",
            "CONTRADICTORY_OBSERVATIONS",
            "OBSERVATION_GROUNDING_NOT_ACCEPTED",
        }
        for blocker in blockers
    ) or authority_report["revocation_status"] == "REVOKED" or authority_report[
        "scope_attenuation"
    ] == "AMPLIFIED"
    unresolved = (
        sufficiency["status"] == Sufficiency.INSUFFICIENT.value
        or authority_report["revocation_status"] == "UNDETERMINED"
        or authority_report["temporal_status"] == "UNRESOLVED"
        or not ancestry["complete"]
    )
    if hard_refusal:
        admission = Admission.REFUSE
    elif (
        sufficiency["status"] == Sufficiency.SUFFICIENT.value
        and sufficiency["decision"] == "HOLD"
    ):
        # A receiver policy that already determines HOLD is a known reason not
        # to admit. An unrelated unresolved prerequisite cannot weaken that
        # refusal to UNDETERMINED.
        admission = Admission.REFUSE
    elif unresolved:
        admission = Admission.UNDETERMINED
    elif not blockers and sufficiency["decision"] == "PROCEED":
        admission = Admission.ADMIT
    else:
        admission = Admission.REFUSE

    body = {
        "profile": PROFILE,
        "handoff_id": handoff_id,
        "integrity": integrity,
        "receipt_integrity": (
            "VERIFIED" if receipt_verification.ok else "FAILED"
        ),
        "transport_binding": "VERIFIED" if transport_binding else "FAILED",
        "transport_authority": "NOT_COMPUTED",
        "policy_binding": "VERIFIED" if policy_binding else "FAILED",
        "authority": authority_report,
        "decision_sufficiency": sufficiency,
        "observation_grounding": observation_grounding,
        "observation_acceptance": "ACCEPTED" if observations_accepted else "REJECTED",
        "observation_truth": "NOT_COMPUTED",
        "receiver_admission": admission.value,
        "blockers": blockers,
        "downstream_action_authority": "NOT_COMPUTED",
        "worldly_truth": "NOT_COMPUTED",
        "denominator_completeness": "NOT_COMPUTED",
        "limitations": [
            "Admission is relative to the separately supplied verification context.",
            "ADMIT makes a retained message eligible for the receiver policy; "
            "it does not authorize or execute a downstream side effect.",
            "Transport metadata does not establish sender authority.",
            "Decision sufficiency is finite-model relative and does not establish worldly truth.",
        ],
    }
    return HandoffAdmissionReport({**body, "report_digest": canonical_hash(body)})


_GRAPH_NODE_FIELDS = {"node_id", "kind", "artifact_digest", "ancestry_complete"}
_GRAPH_EDGE_FIELDS = {"source", "target", "relation"}
_GRAPH_KINDS = {"HANDOFF", "CAPABILITY", "EVIDENCE", "RELIANCE", "ACTION"}
_GRAPH_RELATIONS = {
    "SUPPORTS": ({"HANDOFF", "CAPABILITY", "EVIDENCE"}, {"RELIANCE"}),
    "DERIVES": ({"RELIANCE"}, {"RELIANCE"}),
    "INFORMS": ({"RELIANCE"}, {"ACTION"}),
}


def validate_reliance_graph(
    graph: Any, *, limits: HandoffParseLimits = HandoffParseLimits()
) -> Mapping[str, Any]:
    graph = _exact(graph, {"profile", "nodes", "edges"}, "reliance_graph")
    if graph["profile"] != RELIANCE_GRAPH_PROFILE:
        raise HandoffAdmissionError("unsupported reliance graph profile")
    if (
        not isinstance(graph["nodes"], list)
        or not 1 <= len(graph["nodes"]) <= limits.max_graph_nodes
    ):
        raise HandoffAdmissionError("reliance graph node count is empty or over limit")
    nodes: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(graph["nodes"]):
        node = _exact(raw, _GRAPH_NODE_FIELDS, f"reliance_graph.nodes[{index}]")
        node_id = _string(node["node_id"], f"reliance_graph.nodes[{index}].node_id")
        if node_id in nodes:
            raise HandoffAdmissionError(f"duplicate graph node {node_id!r}")
        if node["kind"] not in _GRAPH_KINDS:
            raise HandoffAdmissionError(f"graph node {node_id!r} has unsupported kind")
        _digest(node["artifact_digest"], f"graph node {node_id}.artifact_digest")
        if not isinstance(node["ancestry_complete"], bool):
            raise HandoffAdmissionError(f"graph node {node_id}.ancestry_complete must be Boolean")
        nodes[node_id] = node
    seen_edges: set[tuple[str, str, str]] = set()
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in nodes}
    if not isinstance(graph["edges"], list):
        raise HandoffAdmissionError("reliance_graph.edges must be an array")
    for index, raw in enumerate(graph["edges"]):
        edge = _exact(raw, _GRAPH_EDGE_FIELDS, f"reliance_graph.edges[{index}]")
        source = _string(edge["source"], f"edge[{index}].source")
        target = _string(edge["target"], f"edge[{index}].target")
        relation = _string(edge["relation"], f"edge[{index}].relation")
        if source not in nodes or target not in nodes:
            raise HandoffAdmissionError("graph edge references an unknown node")
        signature = (source, target, relation)
        if signature in seen_edges:
            raise HandoffAdmissionError("reliance graph contains a duplicate edge")
        seen_edges.add(signature)
        if relation not in _GRAPH_RELATIONS:
            raise HandoffAdmissionError(f"unsupported graph relation {relation!r}")
        allowed_sources, allowed_targets = _GRAPH_RELATIONS[relation]
        if (
            nodes[source]["kind"] not in allowed_sources
            or nodes[target]["kind"] not in allowed_targets
        ):
            raise HandoffAdmissionError(f"edge {signature!r} violates its typed signature")
        outgoing[source].append(target)
        indegree[target] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(nodes):
        raise HandoffAdmissionError("reliance graph must be acyclic")
    return graph


def _correction_subject(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    action = receipt.get("action") if isinstance(receipt.get("action"), Mapping) else {}
    subject = action.get("subject") if isinstance(action.get("subject"), Mapping) else {}
    if action.get("type") != "handoff.correct":
        raise HandoffAdmissionError("correction receipt action type must be handoff.correct")
    return _exact(
        subject,
        {
            "profile",
            "sequence",
            "previous_correction",
            "target_digest",
            "replacement_digest",
            "reason_digest",
            "authority_epoch",
        },
        "handoff.correct subject",
    )


def verify_correction_ledger(
    ledger: Any, context: Mapping[str, Any]
) -> list[dict[str, Any]]:
    ledger = _exact(ledger, {"profile", "corrections"}, "correction_ledger")
    if ledger["profile"] != CORRECTION_LEDGER_PROFILE:
        raise HandoffAdmissionError("unsupported correction ledger profile")
    if not isinstance(ledger["corrections"], list):
        raise HandoffAdmissionError("correction_ledger.corrections must be an array")
    result: list[dict[str, Any]] = []
    previous: str | None = None
    for index, raw in enumerate(ledger["corrections"]):
        if not isinstance(raw, Mapping):
            raise HandoffAdmissionError(f"correction {index} must be an ActionReceipt")
        verdict = verify_receipt(dict(raw))
        subject = _correction_subject(raw)
        sequence = _safe_int(subject["sequence"], f"correction {index}.sequence")
        _safe_int(subject["authority_epoch"], f"correction {index}.authority_epoch")
        if subject["previous_correction"] is not None:
            _digest(
                subject["previous_correction"],
                f"correction {index}.previous_correction",
            )
        issuer = (raw.get("signature") or {}).get("issuer")
        authority = ((raw.get("mandate") or {}).get("authority") or {})
        expected_policy_hash = canonical_hash(
            {
                "admission_policy": context["admission_policy"],
                "observation_policy": context["observation_policy"],
                "decision_model": context["decision_model"],
            }
        )
        if (
            subject["profile"] != PROFILE
            or sequence != index
            or subject["previous_correction"] != previous
            or subject["authority_epoch"] != context["authority_epoch"]
            or raw.get("schema_version") != "0.4"
            or not verdict.ok
            or verdict.verified_to != "attestation"
            or verdict.authority_authentic != "verified"
            or issuer not in context["correction_authorities"]
            or authority.get("principal") != issuer
            or authority.get("policy") != expected_policy_hash
            or authority.get("delegation") != []
            or ((raw.get("mandate") or {}).get("bounds") or {}).get("scope")
            != f"profile:{PROFILE};action:handoff.correct"
            or raw.get("conventions") != []
        ):
            raise HandoffAdmissionError(
                f"correction {index} fails order, authority, or receipt verification"
            )
        _digest(subject["target_digest"], f"correction {index}.target_digest")
        if subject["replacement_digest"] is not None:
            _digest(subject["replacement_digest"], f"correction {index}.replacement_digest")
        _digest(subject["reason_digest"], f"correction {index}.reason_digest")
        previous = raw["hashes"]["attestation"]
        result.append({"event_id": previous, **dict(subject)})
    return result


def recall_after_corrections(
    graph: bytes | bytearray | memoryview,
    ledger: bytes | bytearray | memoryview,
    context: bytes | bytearray | memoryview,
    *,
    limits: HandoffParseLimits = HandoffParseLimits(),
) -> dict[str, Any]:
    graph = parse_json_bytes(graph, label="reliance graph", limits=limits)
    ledger = parse_json_bytes(ledger, label="correction ledger", limits=limits)
    context = parse_json_bytes(context, label="verification context", limits=limits)
    graph = validate_reliance_graph(graph, limits=limits)
    context = _validate_context(context, limits)
    graph_digest = canonical_hash(graph)
    if graph_digest not in context["accepted_reliance_graphs"]:
        raise HandoffAdmissionError(
            "reliance graph is not accepted by the external verification context"
        )
    corrections = verify_correction_ledger(ledger, context)
    nodes = {node["node_id"]: node for node in graph["nodes"]}
    by_digest: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for node in graph["nodes"]:
        by_digest[node["artifact_digest"]].append(node["node_id"])
    for edge in graph["edges"]:
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]].append(edge["source"])

    incomplete: dict[str, bool] = {}
    topological = []
    indegree = {node_id: len(incoming[node_id]) for node_id in nodes}
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    while queue:
        current = queue.popleft()
        topological.append(current)
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    for node_id in topological:
        incomplete[node_id] = (
            not nodes[node_id]["ancestry_complete"]
            or any(incomplete[parent] for parent in incoming[node_id])
        )

    result_kinds = {"RELIANCE", "ACTION"}
    statuses = {
        node_id: (
            RecallStatus.UNDETERMINED if incomplete[node_id] else RecallStatus.NOT_AFFECTED
        )
        for node_id, node in nodes.items()
        if node["kind"] in result_kinds
    }
    paths: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for correction in corrections:
        starts = by_digest.get(correction["target_digest"], [])
        if not starts:
            raise HandoffAdmissionError("correction target is absent from the reliance graph")
        for start in starts:
            queue_paths: deque[tuple[str, list[str]]] = deque([(start, [start])])
            seen: set[str] = set()
            while queue_paths:
                current, path = queue_paths.popleft()
                if current in seen:
                    continue
                seen.add(current)
                if current in statuses:
                    statuses[current] = RecallStatus.AFFECTED
                    paths[current].append(
                        {"correction": correction["event_id"], "nodes": path}
                    )
                for target in outgoing[current]:
                    queue_paths.append((target, [*path, target]))

    results = [
        {
            "node_id": node_id,
            "node_kind": nodes[node_id]["kind"],
            "artifact_digest": nodes[node_id]["artifact_digest"],
            "status": statuses[node_id].value,
            "paths": paths[node_id],
            "conditional_action": (
                "RECHECK_REQUIRED"
                if statuses[node_id] is RecallStatus.AFFECTED
                else (
                    "NO_AUTOMATIC_CLEARANCE"
                    if statuses[node_id] is RecallStatus.UNDETERMINED
                    else None
                )
            ),
        }
        for node_id in sorted(statuses)
    ]
    body = {
        "profile": RECALL_REPORT_PROFILE,
        "graph_digest": graph_digest,
        "graph_acceptance": "ACCEPTED",
        "ledger_digest": canonical_hash(ledger),
        "results": results,
        "limitations": [
            "AFFECTED means a declared dependency should be rechecked; "
            "it is not an instruction to undo an action.",
            "NOT_AFFECTED is emitted only when the supplied ancestry is declared complete.",
            "The profile verifies correction authority relative to the external "
            "context; it does not establish the correction's worldly truth.",
        ],
    }
    return {**body, "report_digest": canonical_hash(body)}


__all__ = [
    "Admission",
    "CAPABILITY_PROFILE",
    "CONTEXT_PROFILE",
    "CORRECTION_LEDGER_PROFILE",
    "DECISION_MODEL_PROFILE",
    "HandoffAdmissionError",
    "HandoffAdmissionReport",
    "HandoffParseLimits",
    "PROFILE",
    "RECALL_REPORT_PROFILE",
    "RELIANCE_GRAPH_PROFILE",
    "RecallStatus",
    "Sufficiency",
    "TRANSPORT_PROFILE",
    "adapt_a2a_message",
    "adapt_claude_team_message",
    "assess_decision_sufficiency",
    "build_capability_grant",
    "canonical_hash",
    "parse_json_bytes",
    "recall_after_corrections",
    "scope_attenuates",
    "validate_decision_model",
    "validate_reliance_graph",
    "validate_scope",
    "verify_capability_chain",
    "verify_correction_ledger",
    "verify_handoff",
    "verify_handoff_report_commitment",
]
