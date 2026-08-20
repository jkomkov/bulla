#!/usr/bin/env python3
"""Generate the deterministic 10,000-decision Reliance Map corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.identity import LocalEd25519Signer
from bulla.experimental.reliance_map import (
    CONTEXT_PROFILE,
    CORRECTION_ACTION,
    GRAPH_PROFILE,
    LEDGER_PROFILE,
    canonical_hash,
    compute_reliance_map,
)


HERE = Path(__file__).resolve().parent
GENERATED = HERE / "generated"
DECISION_COUNT = 10_000
LANE_COUNT = 4
AUTHORITY_EPOCH = 7
GRAPH_ID = "reliance-map:first-contact-10000"
POLICY_HASH = canonical_hash({"policy": "reliance-map-correction", "revision": 1})
CORRECTION_SIGNER = LocalEd25519Signer(
    seed=hashlib.sha256(b"bulla-reliance-map:correction-authority").digest()
)


def json_bytes(value: object, *, compact: bool = False) -> bytes:
    if compact:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def stable_uuid(label: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(label.encode()).digest()[:16], version=4))


def digest(label: str) -> str:
    return canonical_hash({"reliance-map-artifact": label})


def build_graph() -> tuple[dict, list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    for lane in range(LANE_COUNT):
        nodes.append(
            {
                "node_id": f"source-{lane:02d}",
                "kind": "EVIDENCE",
                "artifact_digest": digest(f"source-{lane:02d}"),
                "ancestry_complete": lane != 2,
            }
        )

    lanes: list[list[str]] = [[] for _ in range(LANE_COUNT)]
    depths: dict[str, int] = {}
    for index in range(DECISION_COUNT):
        lane = index % LANE_COUNT
        identifier = f"decision-{index:05d}"
        lane_position = len(lanes[lane])
        nodes.append(
            {
                "node_id": identifier,
                "kind": "RELIANCE",
                "artifact_digest": digest(identifier),
                "ancestry_complete": True,
            }
        )
        if lane_position == 0:
            source = f"source-{lane:02d}"
            relation = "SUPPORTS"
            depths[identifier] = 1
        else:
            source = lanes[lane][(lane_position - 1) // 2]
            relation = "DERIVES"
            depths[identifier] = depths[source] + 1
        edges.append({"source": source, "target": identifier, "relation": relation})
        lanes[lane].append(identifier)

    nodes.sort(key=lambda item: item["node_id"])
    edges.sort(key=lambda item: (item["source"], item["target"], item["relation"]))
    graph = {"profile": GRAPH_PROFILE, "graph_id": GRAPH_ID, "nodes": nodes, "edges": edges}

    projection = [
        {"node_id": f"source-{lane:02d}", "lane": lane, "x": lane * 250 + 125, "y": 8}
        for lane in range(LANE_COUNT)
    ]
    for lane, identifiers in enumerate(lanes):
        for identifier in identifiers:
            raw = hashlib.sha256(f"projection:{identifier}".encode()).digest()
            x = lane * 250 + 14 + int.from_bytes(raw[:2], "big") % 222
            y = 20 + (depths[identifier] - 1) * 42 + int.from_bytes(raw[2:4], "big") % 34
            projection.append({"node_id": identifier, "lane": lane, "x": x, "y": y})
    projection.sort(key=lambda item: item["node_id"])
    return graph, projection


def correction_receipt(target_digest: str, graph_digest: str) -> dict:
    subject = {
        "profile": GRAPH_PROFILE,
        "sequence": 0,
        "previous_correction": None,
        "target_digest": target_digest,
        "replacement_digest": digest("source-00-corrected"),
        "reason_digest": canonical_hash({"reason": "accepted source record corrected"}),
        "authority_epoch": AUTHORITY_EPOCH,
    }
    envelope = RecourseEnvelope(
        authority=Authority(principal=CORRECTION_SIGNER.issuer, policy=POLICY_HASH),
        bounds=Bounds(scope=f"profile:{GRAPH_PROFILE};action:{CORRECTION_ACTION}"),
        recourse=Recourse(
            challenge_window="checkpoint:reliance-map-correction",
            forum=Forum(
                log_endpoint="https://glyphstandard.com/evidence#reliance-map",
                trusted_root_ref=canonical_hash({"forum": "reliance-map"}),
            ),
            remedies=(
                Remedy(
                    "challenge",
                    "recompute exact declared descendants",
                    "forum:reliance-map",
                ),
            ),
        ),
        retention_class="authority-permanent",
        disclosure_class="public",
    )
    return sign_action_receipt_v04(
        build_action_receipt_v04(
            action={"type": CORRECTION_ACTION, "subject": subject},
            diagnostic_ref={"status": "not_applicable"},
            envelope=envelope,
            event_id=stable_uuid("reliance-map-correction-0000"),
            claimed_at="2026-08-20T12:00:00Z",
            anchor_ref={"relation": "reliance_map", "graph_digest": graph_digest},
            producer={"profile": GRAPH_PROFILE, "fixture": "correction-0000"},
        ),
        CORRECTION_SIGNER,
    ).to_dict()


def generate_artifacts() -> dict[str, object]:
    graph, projection = build_graph()
    graph_digest = canonical_hash(graph)
    context = {
        "profile": CONTEXT_PROFILE,
        "authority_epoch": AUTHORITY_EPOCH,
        "accepted_graph_digests": [graph_digest],
        "correction_authorities": [CORRECTION_SIGNER.issuer],
        "policy_hash": POLICY_HASH,
    }
    source_digest = next(item["artifact_digest"] for item in graph["nodes"] if item["node_id"] == "source-00")
    ledger = {
        "profile": LEDGER_PROFILE,
        "corrections": [correction_receipt(source_digest, graph_digest)],
    }
    report = compute_reliance_map(json_bytes(graph, compact=True), json_bytes(ledger), json_bytes(context))
    expected_summary = {
        "declared_decisions": 10_000,
        "graph_nodes": 10_004,
        "graph_edges": 10_000,
        "affected": 2_500,
        "not_affected": 5_000,
        "undetermined": 2_500,
    }
    if report["summary"] != expected_summary:
        raise RuntimeError(f"frozen corpus summary changed: {report['summary']!r}")
    selected = next(item for item in report["results"] if item["node_id"] == "decision-09996")
    if selected["status"] != "AFFECTED" or len(selected["paths"]) != 1:
        raise RuntimeError("selected path is not one exact affected path")
    facts = {
        "profile": "glyph.reliance-map-facts/0.1",
        "graph_id": GRAPH_ID,
        "graph_digest": graph_digest,
        "ledger_digest": report["ledger_digest"],
        "report_digest": report["report_digest"],
        "summary": report["summary"],
        "corrected_source": "source-00",
        "selected_decision": selected["node_id"],
        "selected_path": selected["paths"][0]["nodes"],
    }
    return {
        "graph.json": graph,
        "context.json": context,
        "correction-ledger.json": ledger,
        "expected-report.json": report,
        "projection.json": projection,
        "facts.json": facts,
    }


def emit(path: Path, raw: bytes, check: bool, failures: list[str]) -> None:
    if check:
        if not path.is_file() or path.read_bytes() != raw:
            failures.append(path.name)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def build_manifest(encoded: dict[str, bytes]) -> dict[str, object]:
    artifact_digests = {
        name: "sha256:" + hashlib.sha256(raw).hexdigest()
        for name, raw in sorted(encoded.items())
    }
    semantic_artifacts = {
        name: value for name, value in artifact_digests.items() if name != "projection.json"
    }
    presentation_artifacts = {"projection.json": artifact_digests["projection.json"]}
    return {
        "profile": "bulla.reliance-map-manifest/0.1-experimental",
        "artifacts": {
            name: {"byte_length": len(raw), "sha256": artifact_digests[name]}
            for name, raw in sorted(encoded.items())
        },
        "semantic_artifacts": semantic_artifacts,
        "semantic_root": canonical_hash(semantic_artifacts),
        "presentation_artifacts": presentation_artifacts,
        "presentation_root": canonical_hash(presentation_artifacts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    artifacts = generate_artifacts()
    compact = {"graph.json", "expected-report.json", "projection.json"}
    encoded = {name: json_bytes(value, compact=name in compact) for name, value in artifacts.items()}
    manifest = build_manifest(encoded)
    encoded["manifest.json"] = json_bytes(manifest)
    failures: list[str] = []
    for name, raw in encoded.items():
        emit(GENERATED / name, raw, args.check, failures)
    if failures:
        print("generated artifact drift: " + ", ".join(sorted(failures)), file=sys.stderr)
        return 1
    print(
        f"reliance map {'checked' if args.check else 'generated'}: "
        f"{manifest['semantic_root']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
