from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import time

import pytest

from bulla.experimental.reliance_map import (
    RelianceMapError,
    RelianceMapLimits,
    canonical_hash,
    compute_reliance_map,
    verify_reliance_map_report,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "reliance-map"
GENERATED = SPEC / "generated"


def raw(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def load(name: str):
    return json.loads((GENERATED / name).read_text())


def compute(*, graph=None, ledger=None, context=None):
    return compute_reliance_map(
        raw(load("graph.json") if graph is None else graph),
        raw(load("correction-ledger.json") if ledger is None else ledger),
        raw(load("context.json") if context is None else context),
    )


def resign_malformed_receipt(receipt: dict, **changes: object) -> dict:
    generator = runpy.run_path(str(SPEC / "generate.py"))
    signer = generator["CORRECTION_SIGNER"]
    receipt = copy.deepcopy(receipt)
    receipt.update(changes)
    event = canonical_hash(
        {
            "content_hash": receipt["hashes"]["content"],
            "event_id": receipt["event_id"],
            "claimed_at": receipt["claimed_at"],
        }
    )
    issuer = receipt["signature"]["issuer"]
    receipt["occurrence"] = signer.sign_domain("occurrence", event, schema="0.4")
    envelope = {
        "deed_schema": receipt["mandate"].get("deed_schema", "0.2"),
        "authority": receipt["mandate"]["authority"],
        "bounds": receipt["mandate"]["bounds"],
        "recourse": receipt["remedy"],
        "retention_class": receipt["retention"]["record"],
        "disclosure_class": receipt["retention"]["disclosure"],
    }
    authorization_digest = canonical_hash(
        {"event_hash": event, "envelope_hash": canonical_hash(envelope)}
    )
    receipt["authorization"] = signer.sign_domain(
        "authorization", authorization_digest, schema="0.4"
    )
    attestation = canonical_hash(
        {
            "content_hash": receipt["hashes"]["content"],
            "signature": receipt["signature"],
            "event_hash": event,
            "occurrence": receipt["occurrence"],
            "recourse_envelope": envelope,
            "authorization": receipt["authorization"],
        }
    )
    receipt["hashes"] = {
        "content": receipt["hashes"]["content"],
        "event": event,
        "attestation": attestation,
        "log_leaf": "sha256:"
        + hashlib.sha256(b"\x00" + attestation.encode("utf-8")).hexdigest(),
    }
    assert receipt["signature"]["issuer"] == issuer
    return receipt


def standalone_status(tmp_path: Path, graph_bytes: bytes, ledger_bytes: bytes, context_bytes: bytes) -> int:
    graph_path = tmp_path / "graph.json"
    ledger_path = tmp_path / "ledger.json"
    context_path = tmp_path / "context.json"
    graph_path.write_bytes(graph_bytes)
    ledger_path.write_bytes(ledger_bytes)
    context_path.write_bytes(context_bytes)
    return subprocess.run(
        [
            "node",
            str(SPEC / "check.mjs"),
            str(graph_path),
            "--ledger",
            str(ledger_path),
            "--context",
            str(context_path),
        ],
        capture_output=True,
        check=False,
    ).returncode


def test_frozen_corpus_has_exact_tri_state_closure_and_paths() -> None:
    report = compute()
    assert report["summary"] == {
        "declared_decisions": 10_000,
        "graph_nodes": 10_004,
        "graph_edges": 10_000,
        "affected": 2_500,
        "not_affected": 5_000,
        "undetermined": 2_500,
    }
    by_id = {item["node_id"]: item for item in report["results"]}
    assert by_id["decision-09996"]["conditional_action"] == "RECHECK_REQUIRED"
    assert by_id["decision-09996"]["paths"][0]["nodes"][0] == "source-00"
    assert by_id["decision-09996"]["paths"][0]["nodes"][-1] == "decision-09996"
    assert by_id["decision-09998"]["status"] == "UNDETERMINED"
    assert by_id["decision-09998"]["conditional_action"] == "NO_AUTOMATIC_CLEARANCE"
    assert by_id["decision-09999"]["status"] == "NOT_AFFECTED"


def test_presentation_coordinate_cannot_change_semantic_root() -> None:
    generator = runpy.run_path(str(SPEC / "generate.py"))
    artifacts = generator["generate_artifacts"]()
    compact = {"graph.json", "expected-report.json", "projection.json"}
    encoded = {
        name: generator["json_bytes"](value, compact=name in compact)
        for name, value in artifacts.items()
    }
    original = generator["build_manifest"](encoded)
    changed_projection = copy.deepcopy(artifacts["projection.json"])
    changed_projection[0]["x"] += 1
    changed = dict(encoded)
    changed["projection.json"] = generator["json_bytes"](changed_projection, compact=True)
    revised = generator["build_manifest"](changed)
    assert revised["semantic_root"] == original["semantic_root"]
    assert revised["presentation_root"] != original["presentation_root"]


def test_standalone_node_deep_equals_python() -> None:
    completed = subprocess.run(
        [
            "node",
            str(SPEC / "check.mjs"),
            str(GENERATED / "graph.json"),
            "--ledger",
            str(GENERATED / "correction-ledger.json"),
            "--context",
            str(GENERATED / "context.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == compute()


def test_proto_member_is_rejected_by_python_and_standalone_node(tmp_path: Path) -> None:
    graph = (GENERATED / "graph.json").read_bytes().replace(
        b'{"edges":', b'{"__proto__":{"attacker":true},"edges":', 1
    )
    ledger = (GENERATED / "correction-ledger.json").read_bytes()
    context = (GENERATED / "context.json").read_bytes()
    with pytest.raises(RelianceMapError, match=r"extra=\['__proto__'\]"):
        compute_reliance_map(graph, ledger, context)
    assert standalone_status(tmp_path, graph, ledger, context) != 0


@pytest.mark.parametrize("changes", [{"event_id": "not-a-uuid"}, {"claimed_at": ""}])
def test_validly_resigned_malformed_metadata_fails_both_kernels(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    ledger = load("correction-ledger.json")
    ledger["corrections"][0] = resign_malformed_receipt(
        ledger["corrections"][0], **changes
    )
    graph_bytes = (GENERATED / "graph.json").read_bytes()
    ledger_bytes = raw(ledger)
    context_bytes = (GENERATED / "context.json").read_bytes()
    with pytest.raises(RelianceMapError, match="receipt verification"):
        compute_reliance_map(graph_bytes, ledger_bytes, context_bytes)
    assert standalone_status(tmp_path, graph_bytes, ledger_bytes, context_bytes) != 0


def test_multibyte_string_byte_limit_matches_standalone_node(tmp_path: Path) -> None:
    graph = load("graph.json")
    graph["graph_id"] = "é" * 4_096
    graph_bytes = raw(graph)
    ledger_bytes = (GENERATED / "correction-ledger.json").read_bytes()
    context_bytes = (GENERATED / "context.json").read_bytes()
    with pytest.raises(RelianceMapError, match="oversized string"):
        compute_reliance_map(graph_bytes, ledger_bytes, context_bytes)
    assert standalone_status(tmp_path, graph_bytes, ledger_bytes, context_bytes) != 0


def test_retained_report_recomputes_exactly() -> None:
    verified = verify_reliance_map_report(
        (GENERATED / "expected-report.json").read_bytes(),
        (GENERATED / "graph.json").read_bytes(),
        (GENERATED / "correction-ledger.json").read_bytes(),
        (GENERATED / "context.json").read_bytes(),
    )
    assert verified["report_digest"] == load("facts.json")["report_digest"]


def test_forged_path_certificate_is_rejected() -> None:
    report = load("expected-report.json")
    report["results"][0]["paths"][0]["nodes"] = ["source-00", "decision-09996"]
    with pytest.raises(RelianceMapError, match="does not match recomputation"):
        verify_reliance_map_report(
            raw(report),
            (GENERATED / "graph.json").read_bytes(),
            (GENERATED / "correction-ledger.json").read_bytes(),
            (GENERATED / "context.json").read_bytes(),
        )


def test_graph_root_substitution_fails_external_acceptance() -> None:
    graph = load("graph.json")
    graph["nodes"][0]["ancestry_complete"] = not graph["nodes"][0]["ancestry_complete"]
    with pytest.raises(RelianceMapError, match="not accepted"):
        compute(graph=graph)


def test_correction_cannot_replay_into_another_accepted_graph() -> None:
    graph = load("graph.json")
    graph["nodes"].append(
        {
            "node_id": "source-99",
            "kind": "EVIDENCE",
            "artifact_digest": canonical_hash({"other": "accepted graph"}),
            "ancestry_complete": True,
        }
    )
    graph["nodes"].sort(key=lambda item: item["node_id"])
    context = load("context.json")
    context["accepted_graph_digests"] = [canonical_hash(graph)]
    with pytest.raises(RelianceMapError, match="order, authority, profile"):
        compute(graph=graph, context=context)


def test_packet_carried_trust_cannot_replace_context() -> None:
    context = load("context.json")
    context["accepted_graph_digests"] = [canonical_hash({"attacker": "graph"})]
    with pytest.raises(RelianceMapError, match="not accepted"):
        compute(context=context)


def test_stale_authority_epoch_fails_even_with_accepted_graph() -> None:
    context = load("context.json")
    context["authority_epoch"] += 1
    with pytest.raises(RelianceMapError, match="fails order, authority"):
        compute(context=context)


def test_noop_replacement_is_rejected_even_when_signed() -> None:
    graph = load("graph.json")
    graph_digest = canonical_hash(graph)
    target = next(
        item["artifact_digest"] for item in graph["nodes"] if item["node_id"] == "source-00"
    )
    generator = runpy.run_path(str(SPEC / "generate.py"))
    # Rebuild through the generator's signed helper by temporarily using a
    # distinct target, then target the receipt at that same replacement.
    # A stale edit alone would only test digest verification.
    correction_receipt = generator["correction_receipt"]
    original_digest = correction_receipt.__globals__["digest"]
    correction_receipt.__globals__["digest"] = lambda _label: target
    try:
        receipt = correction_receipt(target, graph_digest)
    finally:
        correction_receipt.__globals__["digest"] = original_digest
    ledger = {"profile": "bulla.reliance-correction-ledger/0.1-experimental", "corrections": [receipt]}
    with pytest.raises(RelianceMapError, match="replacement must differ"):
        compute(ledger=ledger)


def test_correction_target_must_exist_in_accepted_graph() -> None:
    graph = load("graph.json")
    graph_digest = canonical_hash(graph)
    generator = runpy.run_path(str(SPEC / "generate.py"))
    ledger = {
        "profile": "bulla.reliance-correction-ledger/0.1-experimental",
        "corrections": [
            generator["correction_receipt"](
                canonical_hash({"absent": "target"}), graph_digest
            )
        ],
    }
    with pytest.raises(RelianceMapError, match="target is absent"):
        compute(graph=graph, ledger=ledger)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda graph: graph["nodes"].append(copy.deepcopy(graph["nodes"][0])), "duplicate graph node"),
        (lambda graph: graph["edges"].append(copy.deepcopy(graph["edges"][0])), "duplicate edge"),
        (lambda graph: graph["edges"].__setitem__(0, {"source": "decision-00000", "target": "source-00", "relation": "DERIVES"}), "typed signature"),
        (lambda graph: graph["nodes"].reverse(), "sorted by node_id"),
        (lambda graph: graph["edges"].reverse(), "edges must be sorted"),
    ],
)
def test_graph_structure_fails_closed(mutate, match: str) -> None:
    graph = load("graph.json")
    mutate(graph)
    context = load("context.json")
    context["accepted_graph_digests"] = [canonical_hash(graph)]
    with pytest.raises(RelianceMapError, match=match):
        compute(graph=graph, context=context)


def test_cycle_fails_before_recall() -> None:
    graph = load("graph.json")
    graph["edges"].append(
        {"source": "decision-09996", "target": "decision-00000", "relation": "DERIVES"}
    )
    graph["edges"].sort(key=lambda item: (item["source"], item["target"], item["relation"]))
    context = load("context.json")
    context["accepted_graph_digests"] = [canonical_hash(graph)]
    with pytest.raises(RelianceMapError, match="acyclic"):
        compute(graph=graph, context=context)


def test_graph_depth_is_bounded_before_path_emission() -> None:
    nodes = [
        {
            "node_id": "source-00",
            "kind": "EVIDENCE",
            "artifact_digest": canonical_hash({"depth": "source"}),
            "ancestry_complete": True,
        }
    ]
    edges = []
    parent = "source-00"
    for index in range(65):
        node_id = f"decision-{index:05d}"
        nodes.append(
            {
                "node_id": node_id,
                "kind": "RELIANCE",
                "artifact_digest": canonical_hash({"depth": index}),
                "ancestry_complete": True,
            }
        )
        edges.append(
            {
                "source": parent,
                "target": node_id,
                "relation": "SUPPORTS" if index == 0 else "DERIVES",
            }
        )
        parent = node_id
    graph = {
        "profile": "bulla.reliance-map/0.1-experimental",
        "graph_id": "depth-hostile",
        "nodes": sorted(nodes, key=lambda item: item["node_id"]),
        "edges": sorted(
            edges, key=lambda item: (item["source"], item["target"], item["relation"])
        ),
    }
    context = load("context.json")
    context["accepted_graph_digests"] = [canonical_hash(graph)]
    with pytest.raises(RelianceMapError, match="graph depth limit"):
        compute(graph=graph, context=context)


def test_duplicate_json_members_are_rejected() -> None:
    with pytest.raises(RelianceMapError, match="duplicate JSON member"):
        compute_reliance_map(
            b'{"profile":"bulla.reliance-map/0.1-experimental","profile":"x","graph_id":"x","nodes":[],"edges":[]}',
            (GENERATED / "correction-ledger.json").read_bytes(),
            (GENERATED / "context.json").read_bytes(),
        )


def test_resource_limit_is_caller_tightenable() -> None:
    with pytest.raises(RelianceMapError, match="over limit"):
        compute_reliance_map(
            (GENERATED / "graph.json").read_bytes(),
            (GENERATED / "correction-ledger.json").read_bytes(),
            (GENERATED / "context.json").read_bytes(),
            limits=RelianceMapLimits(max_graph_nodes=100),
        )
    with pytest.raises(RelianceMapError, match="path-certificate limit"):
        compute_reliance_map(
            (GENERATED / "graph.json").read_bytes(),
            (GENERATED / "correction-ledger.json").read_bytes(),
            (GENERATED / "context.json").read_bytes(),
            limits=RelianceMapLimits(max_path_steps=10),
        )


def test_ten_thousand_decisions_complete_within_the_frozen_budget() -> None:
    started = time.perf_counter()
    report = compute()
    assert time.perf_counter() - started < 10
    assert report["summary"]["declared_decisions"] == 10_000


def test_reliance_map_remains_source_only() -> None:
    policy = json.loads((ROOT / "distribution-policy.json").read_text())
    assert "bulla.experimental.reliance_map" in policy["source_only_modules"]
    for path in (
        "spec/reliance-map/",
        "src/bulla/experimental/reliance_map.py",
        "tests/test_reliance_map.py",
    ):
        assert path in policy["forbidden_sdist_prefixes"]
