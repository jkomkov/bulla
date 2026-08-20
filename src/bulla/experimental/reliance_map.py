"""Source-only correction recall over an externally accepted reliance map.

The profile scales the finite correction-recall semantics from Handoff
Admission without changing that profile.  It answers one narrow question:
which declared downstream reliances and actions must be revisited after an
authenticated correction targets an exact retained artifact?

It does not establish the correction's worldly truth, graph completeness,
rollback, downstream safety, or authority to perform a consequence.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from bulla._canonical import JCS_SAFE_INTEGER
from bulla.action_receipt import verify_receipt
from bulla.experimental.handoff_admission import (
    HandoffAdmissionError,
    HandoffParseLimits,
    canonical_hash as _canonical_hash,
    parse_json_bytes as _parse_json_bytes,
)


GRAPH_PROFILE = "bulla.reliance-map/0.1-experimental"
LEDGER_PROFILE = "bulla.reliance-correction-ledger/0.1-experimental"
CONTEXT_PROFILE = "bulla.reliance-map-context/0.1-experimental"
REPORT_PROFILE = "bulla.reliance-map-report/0.1-experimental"
CORRECTION_ACTION = "reliance.correct"

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
NODE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
NODE_KINDS = frozenset({"HANDOFF", "CAPABILITY", "EVIDENCE", "RELIANCE", "ACTION"})
RESULT_KINDS = frozenset({"RELIANCE", "ACTION"})
RELATIONS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "SUPPORTS": (
        frozenset({"HANDOFF", "CAPABILITY", "EVIDENCE"}),
        frozenset({"RELIANCE"}),
    ),
    "DERIVES": (frozenset({"RELIANCE"}), frozenset({"RELIANCE"})),
    "INFORMS": (frozenset({"RELIANCE"}), frozenset({"ACTION"})),
}
RECEIPT_FIELDS = {
    "schema_version",
    "canonicalization",
    "kind",
    "action",
    "diagnostic_ref",
    "evidence_refs",
    "anchor_ref",
    "mandate",
    "remedy",
    "retention",
    "stake",
    "conventions",
    "signature",
    "occurrence",
    "authorization",
    "event_id",
    "claimed_at",
    "producer",
    "hashes",
}


class RelianceMapError(ValueError):
    """Malformed, unsupported, untrusted, or internally inconsistent input."""


@dataclass(frozen=True)
class RelianceMapLimits:
    max_bytes: int = 32 * 1024 * 1024
    max_json_nodes: int = 500_000
    max_depth: int = 24
    max_string_bytes: int = 4_096
    max_graph_nodes: int = 16_384
    max_graph_edges: int = 65_536
    max_graph_depth: int = 64
    max_path_steps: int = 262_144
    max_corrections: int = 16

    def __post_init__(self) -> None:
        if min(
            self.max_bytes,
            self.max_json_nodes,
            self.max_depth,
            self.max_string_bytes,
            self.max_graph_nodes,
            self.max_graph_edges,
            self.max_graph_depth,
            self.max_path_steps,
            self.max_corrections,
        ) <= 0:
            raise ValueError("reliance-map limits must be positive")


def _parse(
    raw: bytes | bytearray | memoryview,
    label: str,
    limits: RelianceMapLimits,
) -> Any:
    try:
        return _parse_json_bytes(
            raw,
            label=label,
            limits=HandoffParseLimits(
                max_bytes=limits.max_bytes,
                max_depth=limits.max_depth,
                max_nodes=limits.max_json_nodes,
                max_string_bytes=limits.max_string_bytes,
                max_capability_depth=1,
                max_worlds=1,
                max_variables=1,
                max_graph_nodes=limits.max_graph_nodes,
            ),
        )
    except HandoffAdmissionError as exc:
        raise RelianceMapError(str(exc)) from exc


def canonical_hash(value: Any) -> str:
    try:
        return _canonical_hash(value)
    except HandoffAdmissionError as exc:
        raise RelianceMapError(str(exc)) from exc


def _exact(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RelianceMapError(f"{label} must be an object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise RelianceMapError(
            f"{label} keys mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise RelianceMapError(f"{label} must be a non-empty trimmed string")
    return value


def _node_id(value: Any, label: str) -> str:
    item = _string(value, label)
    if NODE_ID_RE.fullmatch(item) is None:
        raise RelianceMapError(f"{label} is not a stable node identifier")
    return item


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise RelianceMapError(f"{label} must be sha256:<64 lowercase hex>")
    return value


def _safe_int(value: Any, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > JCS_SAFE_INTEGER
    ):
        raise RelianceMapError(f"{label} must be a non-negative safe integer")
    return value


def _string_set(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise RelianceMapError(f"{label} must be an array")
    result = [_string(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if result != sorted(result) or len(result) != len(set(result)):
        raise RelianceMapError(f"{label} must be sorted and unique")
    return result


def _validate_graph(value: Any, limits: RelianceMapLimits) -> Mapping[str, Any]:
    graph = _exact(value, {"profile", "graph_id", "nodes", "edges"}, "graph")
    if graph["profile"] != GRAPH_PROFILE:
        raise RelianceMapError(f"graph.profile must be {GRAPH_PROFILE!r}")
    _string(graph["graph_id"], "graph.graph_id")
    if not isinstance(graph["nodes"], list) or not 1 <= len(graph["nodes"]) <= limits.max_graph_nodes:
        raise RelianceMapError("graph node count is empty or over limit")
    if not isinstance(graph["edges"], list) or len(graph["edges"]) > limits.max_graph_edges:
        raise RelianceMapError("graph edge count is malformed or over limit")

    nodes: dict[str, Mapping[str, Any]] = {}
    digests: set[str] = set()
    node_order: list[str] = []
    for index, raw in enumerate(graph["nodes"]):
        node = _exact(
            raw,
            {"node_id", "kind", "artifact_digest", "ancestry_complete"},
            f"graph.nodes[{index}]",
        )
        identifier = _node_id(node["node_id"], f"graph.nodes[{index}].node_id")
        if identifier in nodes:
            raise RelianceMapError(f"duplicate graph node {identifier!r}")
        if node["kind"] not in NODE_KINDS:
            raise RelianceMapError(f"graph node {identifier!r} has unsupported kind")
        digest = _digest(node["artifact_digest"], f"graph node {identifier}.artifact_digest")
        if digest in digests:
            raise RelianceMapError("graph artifact digests must uniquely identify one node")
        if not isinstance(node["ancestry_complete"], bool):
            raise RelianceMapError(f"graph node {identifier}.ancestry_complete must be Boolean")
        nodes[identifier] = node
        digests.add(digest)
        node_order.append(identifier)
    if node_order != sorted(node_order):
        raise RelianceMapError("graph nodes must be sorted by node_id")

    edge_order: list[tuple[str, str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {identifier: 0 for identifier in nodes}
    for index, raw in enumerate(graph["edges"]):
        edge = _exact(raw, {"source", "target", "relation"}, f"graph.edges[{index}]")
        source = _node_id(edge["source"], f"graph.edges[{index}].source")
        target = _node_id(edge["target"], f"graph.edges[{index}].target")
        relation = _string(edge["relation"], f"graph.edges[{index}].relation")
        signature = (source, target, relation)
        if source not in nodes or target not in nodes:
            raise RelianceMapError("graph edge references an unknown node")
        if signature in seen_edges:
            raise RelianceMapError("graph contains a duplicate edge")
        if relation not in RELATIONS:
            raise RelianceMapError(f"unsupported graph relation {relation!r}")
        sources, targets = RELATIONS[relation]
        if nodes[source]["kind"] not in sources or nodes[target]["kind"] not in targets:
            raise RelianceMapError(f"edge {signature!r} violates its typed signature")
        seen_edges.add(signature)
        edge_order.append(signature)
        outgoing[source].append(target)
        indegree[target] += 1
    if edge_order != sorted(edge_order):
        raise RelianceMapError("graph edges must be sorted by source, target, and relation")

    queue = deque(sorted(identifier for identifier, degree in indegree.items() if degree == 0))
    graph_depth = {identifier: 0 for identifier in nodes}
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in sorted(outgoing[current]):
            graph_depth[target] = max(graph_depth[target], graph_depth[current] + 1)
            if graph_depth[target] > limits.max_graph_depth:
                raise RelianceMapError("reliance map exceeds graph depth limit")
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(nodes):
        raise RelianceMapError("reliance map must be acyclic")
    return graph


def _validate_context(value: Any) -> Mapping[str, Any]:
    context = _exact(
        value,
        {
            "profile",
            "authority_epoch",
            "accepted_graph_digests",
            "correction_authorities",
            "policy_hash",
        },
        "context",
    )
    if context["profile"] != CONTEXT_PROFILE:
        raise RelianceMapError(f"context.profile must be {CONTEXT_PROFILE!r}")
    _safe_int(context["authority_epoch"], "context.authority_epoch")
    graphs = _string_set(context["accepted_graph_digests"], "context.accepted_graph_digests")
    authorities = _string_set(context["correction_authorities"], "context.correction_authorities")
    for index, item in enumerate(graphs):
        _digest(item, f"context.accepted_graph_digests[{index}]")
    if not authorities:
        raise RelianceMapError("context.correction_authorities must not be empty")
    _digest(context["policy_hash"], "context.policy_hash")
    return context


def _correction_subject(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    action = receipt.get("action") if isinstance(receipt.get("action"), Mapping) else {}
    action = _exact(action, {"type", "subject"}, "reliance.correct action")
    if action.get("type") != CORRECTION_ACTION:
        raise RelianceMapError(f"correction action type must be {CORRECTION_ACTION!r}")
    return _exact(
        action.get("subject"),
        {
            "profile",
            "sequence",
            "previous_correction",
            "target_digest",
            "replacement_digest",
            "reason_digest",
            "authority_epoch",
        },
        "reliance.correct subject",
    )


def _validate_ledger(
    value: Any,
    context: Mapping[str, Any],
    graph_digest: str,
    limits: RelianceMapLimits,
) -> list[dict[str, Any]]:
    ledger = _exact(value, {"profile", "corrections"}, "ledger")
    if ledger["profile"] != LEDGER_PROFILE:
        raise RelianceMapError(f"ledger.profile must be {LEDGER_PROFILE!r}")
    if not isinstance(ledger["corrections"], list) or len(ledger["corrections"]) > limits.max_corrections:
        raise RelianceMapError("correction count is malformed or over limit")
    previous: str | None = None
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(ledger["corrections"]):
        if not isinstance(raw, Mapping):
            raise RelianceMapError(f"correction {index} must be an ActionReceipt")
        raw = _exact(raw, RECEIPT_FIELDS, f"correction {index}")
        try:
            verdict = verify_receipt(dict(raw))
        except (TypeError, ValueError) as exc:
            raise RelianceMapError(f"correction {index} is malformed: {exc}") from exc
        subject = _correction_subject(raw)
        sequence = _safe_int(subject["sequence"], f"correction {index}.sequence")
        epoch = _safe_int(subject["authority_epoch"], f"correction {index}.authority_epoch")
        prior = subject["previous_correction"]
        if prior is not None:
            _digest(prior, f"correction {index}.previous_correction")
        target = _digest(subject["target_digest"], f"correction {index}.target_digest")
        replacement = _digest(
            subject["replacement_digest"],
            f"correction {index}.replacement_digest",
        )
        if replacement == target:
            raise RelianceMapError("correction replacement must differ from its target")
        _digest(subject["reason_digest"], f"correction {index}.reason_digest")

        signature = raw.get("signature") if isinstance(raw.get("signature"), Mapping) else {}
        issuer = signature.get("issuer")
        mandate = raw.get("mandate") if isinstance(raw.get("mandate"), Mapping) else {}
        mandate = _exact(mandate, {"authority", "bounds"}, f"correction {index}.mandate")
        authority = _exact(
            mandate.get("authority"),
            {"principal", "policy", "delegation"},
            f"correction {index}.mandate.authority",
        )
        bounds = _exact(
            mandate.get("bounds"), {"scope"}, f"correction {index}.mandate.bounds"
        )
        remedy = _exact(
            raw.get("remedy"),
            {"challenge_window", "forum", "remedies"},
            f"correction {index}.remedy",
        )
        forum = _exact(
            remedy.get("forum"),
            {"log_endpoint", "trusted_root_ref"},
            f"correction {index}.remedy.forum",
        )
        remedies = remedy.get("remedies")
        remedy_item = (
            _exact(
                remedies[0],
                {"anchor", "rung", "verifier"},
                f"correction {index}.remedy.remedies[0]",
            )
            if isinstance(remedies, list) and len(remedies) == 1
            else {}
        )
        retention = _exact(
            raw.get("retention"),
            {"disclosure", "record"},
            f"correction {index}.retention",
        )
        hashes = raw.get("hashes") if isinstance(raw.get("hashes"), Mapping) else {}
        attestation = _digest(hashes.get("attestation"), f"correction {index}.attestation")
        anchor = _exact(
            raw.get("anchor_ref"),
            {"relation", "graph_digest"},
            f"correction {index}.anchor_ref",
        )
        if (
            raw.get("schema_version") != "0.4"
            or raw.get("canonicalization") != "bulla-jcs-int/1"
            or raw.get("kind") != "action_receipt"
            or subject["profile"] != GRAPH_PROFILE
            or sequence != index
            or prior != previous
            or epoch != context["authority_epoch"]
            or not verdict.ok
            or verdict.verified_to != "attestation"
            or verdict.authority_authentic != "verified"
            or issuer not in context["correction_authorities"]
            or authority.get("principal") != issuer
            or authority.get("policy") != context["policy_hash"]
            or authority.get("delegation") != []
            or bounds.get("scope") != f"profile:{GRAPH_PROFILE};action:{CORRECTION_ACTION}"
            or raw.get("conventions") != []
            or raw.get("stake") is not None
            or raw.get("diagnostic_ref") != {"status": "not_applicable"}
            or raw.get("evidence_refs") != []
            or anchor.get("relation") != "reliance_map"
            or anchor.get("graph_digest") != graph_digest
            or not isinstance(raw.get("producer"), Mapping)
            or remedy.get("challenge_window") != "checkpoint:reliance-map-correction"
            or forum.get("log_endpoint")
            != "https://glyphstandard.com/evidence#reliance-map"
            or HASH_RE.fullmatch(str(forum.get("trusted_root_ref"))) is None
            or remedy_item.get("anchor") != "forum:reliance-map"
            or remedy_item.get("rung") != "challenge"
            or remedy_item.get("verifier")
            != "recompute exact declared descendants"
            or retention.get("disclosure") != "public"
            or retention.get("record") != "authority-permanent"
        ):
            raise RelianceMapError(
                f"correction {index} fails order, authority, profile, or receipt verification"
            )
        previous = attestation
        result.append(
            {
                "correction": attestation,
                "target_digest": target,
                "replacement_digest": replacement,
            }
        )
    return result


def _compute(
    graph: Mapping[str, Any],
    ledger: Mapping[str, Any],
    context: Mapping[str, Any],
    limits: RelianceMapLimits,
) -> dict[str, Any]:
    graph = _validate_graph(graph, limits)
    context = _validate_context(context)
    graph_digest = canonical_hash(graph)
    if graph_digest not in context["accepted_graph_digests"]:
        raise RelianceMapError("reliance map is not accepted by the external context")
    corrections = _validate_ledger(ledger, context, graph_digest, limits)

    nodes = {node["node_id"]: node for node in graph["nodes"]}
    by_digest = {node["artifact_digest"]: node["node_id"] for node in graph["nodes"]}
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for edge in graph["edges"]:
        outgoing[edge["source"]].append(edge["target"])
        incoming[edge["target"]].append(edge["source"])
    for values in outgoing.values():
        values.sort()

    indegree = {identifier: len(incoming[identifier]) for identifier in nodes}
    queue = deque(sorted(identifier for identifier, degree in indegree.items() if degree == 0))
    topological: list[str] = []
    while queue:
        current = queue.popleft()
        topological.append(current)
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    incomplete: dict[str, bool] = {}
    for identifier in topological:
        incomplete[identifier] = (
            not nodes[identifier]["ancestry_complete"]
            or any(incomplete[parent] for parent in incoming[identifier])
        )

    statuses = {
        identifier: ("UNDETERMINED" if incomplete[identifier] else "NOT_AFFECTED")
        for identifier, node in nodes.items()
        if node["kind"] in RESULT_KINDS
    }
    paths: dict[str, list[dict[str, Any]]] = defaultdict(list)
    path_steps = 0
    for correction in corrections:
        start = by_digest.get(correction["target_digest"])
        if start is None:
            raise RelianceMapError("correction target is absent from the accepted map")
        queue_paths: deque[str] = deque([start])
        predecessor: dict[str, str | None] = {start: None}
        while queue_paths:
            current = queue_paths.popleft()
            if current in statuses:
                path: list[str] = []
                cursor: str | None = current
                while cursor is not None:
                    path.append(cursor)
                    cursor = predecessor[cursor]
                path.reverse()
                path_steps += len(path)
                if path_steps > limits.max_path_steps:
                    raise RelianceMapError("reliance map exceeds path-certificate limit")
                statuses[current] = "AFFECTED"
                paths[current].append(
                    {"correction": correction["correction"], "nodes": path}
                )
            for target in outgoing[current]:
                if target not in predecessor:
                    predecessor[target] = current
                    queue_paths.append(target)

    results = []
    for identifier in sorted(statuses):
        status = statuses[identifier]
        results.append(
            {
                "node_id": identifier,
                "node_kind": nodes[identifier]["kind"],
                "artifact_digest": nodes[identifier]["artifact_digest"],
                "status": status,
                "paths": paths[identifier],
                "conditional_action": (
                    "RECHECK_REQUIRED"
                    if status == "AFFECTED"
                    else "NO_AUTOMATIC_CLEARANCE" if status == "UNDETERMINED" else None
                ),
            }
        )
    counts = {name: sum(item["status"] == name for item in results) for name in ("AFFECTED", "NOT_AFFECTED", "UNDETERMINED")}
    body = {
        "profile": REPORT_PROFILE,
        "graph_id": graph["graph_id"],
        "graph_digest": graph_digest,
        "graph_acceptance": "ACCEPTED",
        "ledger_digest": canonical_hash(ledger),
        "summary": {
            "declared_decisions": sum(node["kind"] in RESULT_KINDS for node in graph["nodes"]),
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["edges"]),
            "affected": counts["AFFECTED"],
            "not_affected": counts["NOT_AFFECTED"],
            "undetermined": counts["UNDETERMINED"],
        },
        "results": results,
        "limitations": [
            "AFFECTED identifies a declared dependency path that requires rechecking; it is not a finding that an action was unsafe.",
            "NOT_AFFECTED is relative to the externally accepted graph and requires declared complete ancestry.",
            "A correction does not roll back an action, establish worldly truth, or authorize a consequence.",
        ],
    }
    return {**body, "report_digest": canonical_hash(body)}


def compute_reliance_map(
    graph_bytes: bytes | bytearray | memoryview,
    ledger_bytes: bytes | bytearray | memoryview,
    context_bytes: bytes | bytearray | memoryview,
    *,
    limits: RelianceMapLimits = RelianceMapLimits(),
) -> dict[str, Any]:
    """Compute exact correction recall under a separately supplied context."""

    graph = _parse(graph_bytes, "reliance map", limits)
    ledger = _parse(ledger_bytes, "correction ledger", limits)
    context = _parse(context_bytes, "verification context", limits)
    return _compute(graph, ledger, context, limits)


def verify_reliance_map_report(
    report_bytes: bytes | bytearray | memoryview,
    graph_bytes: bytes | bytearray | memoryview,
    ledger_bytes: bytes | bytearray | memoryview,
    context_bytes: bytes | bytearray | memoryview,
    *,
    limits: RelianceMapLimits = RelianceMapLimits(),
) -> dict[str, Any]:
    """Recompute and compare a retained report byte-for-byte at the value level."""

    supplied = _parse(report_bytes, "reliance map report", limits)
    expected = compute_reliance_map(
        graph_bytes, ledger_bytes, context_bytes, limits=limits
    )
    if supplied != expected:
        raise RelianceMapError("reliance map report does not match recomputation")
    return expected


__all__ = [
    "CONTEXT_PROFILE",
    "CORRECTION_ACTION",
    "GRAPH_PROFILE",
    "LEDGER_PROFILE",
    "REPORT_PROFILE",
    "RelianceMapError",
    "RelianceMapLimits",
    "canonical_hash",
    "compute_reliance_map",
    "verify_reliance_map_report",
]
