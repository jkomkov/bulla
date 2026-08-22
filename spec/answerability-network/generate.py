#!/usr/bin/env python3
"""Generate the deterministic Answerability Network corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import uuid
from typing import Any

from bulla.action_receipt import build_action_receipt_v04, sign_action_receipt_v04
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.answerability_network import (
    PROFILE, CONTEXT_PROFILE, EVIDENCE_CLASS, EVIDENCE_PROFILE,
    WITNESS_COVENANT_HASH, verify_answerability_network,
)
from bulla.experimental.reliance_map import GRAPH_PROFILE, LEDGER_PROFILE, canonical_hash
from bulla.identity import LocalEd25519Signer


HERE = Path(__file__).resolve().parent
STAGES = ("job", "published", "fork-open", "fork-closed", "fork-authorized", "model-dispute")
NETWORK_ID = "answerability-network:one-job-one-fork-10000"
TRANSACTION_ID = "inference-job-001"
TASK_DIGEST = "sha256:" + hashlib.sha256(b"fixed delegated inference task").hexdigest()
ARTIFACT_DIGEST = "sha256:" + hashlib.sha256(b"identical delivered inference bytes").hexdigest()
ROLE_ACTIONS = {
    "buyer": "inference.promise.accept",
    "provider-a": "inference.delivery",
    "provider-b": "inference.delivery",
    "buyer-selection": "inference.selection",
}
SOURCE_FILES = ("PROFILE.md", "WIRE-FORMAT.md", "PREREGISTRATION.md", "THREAT-MODEL.md", "formal-fixture-map.json", "benchmark.json", "check.py", "check.mjs", "kernel.mjs")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WITNESS = _load("answerability_witness_generator", HERE.parent / "witness-covenant" / "generate.py")
RELIANCE = _load("answerability_reliance_generator", HERE.parent / "reliance-map" / "generate.py")


def _json(value: Any, *, compact: bool = False) -> bytes:
    kwargs = {"sort_keys": True, "ensure_ascii": False}
    text = json.dumps(value, separators=(",", ":"), **kwargs) if compact else json.dumps(value, indent=2, **kwargs)
    return (text + "\n").encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _uuid(label: str) -> str:
    raw = bytearray(hashlib.sha256(label.encode()).digest()[:16]); raw[6] = (raw[6] & 15) | 64; raw[8] = (raw[8] & 63) | 128
    return str(uuid.UUID(bytes=bytes(raw)))


def _signer(role: str) -> LocalEd25519Signer:
    return LocalEd25519Signer(seed=hashlib.sha256(f"answerability-network:{role}".encode()).digest())


def _envelope(role: str, action: str) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(principal=_signer(role).issuer, policy=f"policy://answerability-network/{action}"),
        bounds=Bounds(scope=f"profile:{PROFILE};transaction:{TRANSACTION_ID}"),
        recourse=Recourse(
            challenge_window="checkpoint:answerability-network-5",
            forum=Forum(log_endpoint="https://glyphstandard.com/bulla/answerable-computing", trusted_root_ref=_sha(b"answerability-network-forum")),
            remedies=(Remedy("challenge", "verify the retained network under supplied context", "forum:answerability-network"),),
        ),
        retention_class="operational", disclosure_class="public",
    )


def _receipt(
    role: str,
    subject: dict[str, Any],
    index: int,
    *,
    evidence_refs: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    action = ROLE_ACTIONS[role]
    receipt = build_action_receipt_v04(
        action={"type": action, "subject": {"profile": PROFILE, "transaction_id": TRANSACTION_ID, **subject}},
        diagnostic_ref={"status": "not_applicable"}, envelope=_envelope(role, action),
        evidence_refs=evidence_refs,
        event_id=_uuid(f"{role}:{index}"), claimed_at=f"2026-08-21T14:{index:02d}:00Z",
        producer={"profile": PROFILE, "fixture": "one-job-one-fork"},
    )
    return sign_action_receipt_v04(receipt, _signer(role)).to_dict()


def _procurement() -> dict[str, bytes]:
    covenant_hash = WITNESS._covenant()["covenant_hash"]
    if covenant_hash != WITNESS_COVENANT_HASH:
        raise RuntimeError("closed Witness Covenant hash changed")
    delivered = b"identical delivered inference bytes"
    evidence = _json({
        "profile": EVIDENCE_PROFILE,
        "transaction_id": TRANSACTION_ID,
        "provider_id": "provider-a",
        "artifact_digest": ARTIFACT_DIGEST,
        "artifact_path": "procurement/provider-a-delivery.bin",
    })
    delivery_assertion = _json({
        "profile": PROFILE,
        "transaction_id": TRANSACTION_ID,
        "provider_id": "provider-a",
        "artifact_digest": ARTIFACT_DIGEST,
        "claim": "HISTORICAL_DELIVERY_REPORTED",
    })
    promise = _receipt("buyer", {
        "task_digest": TASK_DIGEST, "expected_artifact_digest": ARTIFACT_DIGEST,
        "accepted_evidence_classes": [EVIDENCE_CLASS],
        "price": 12500, "unit": "USD_CENTS", "witness_covenant_hash": covenant_hash,
    }, 1)
    provider_a = _receipt("provider-a", {
        "provider_id": "provider-a", "artifact_digest": ARTIFACT_DIGEST,
        "evidence_class": EVIDENCE_CLASS, "evidence_digest": _sha(evidence),
        "model_identity": "SELF_ASSERTED",
    }, 2, evidence_refs=(
        {"name": "delivery_report", "hash": _sha(delivery_assertion), "grounding": "self_asserted"},
        {"name": "delivered_bytes", "hash": ARTIFACT_DIGEST, "grounding": "execution_verified"},
    ))
    provider_b = _receipt("provider-b", {
        "provider_id": "provider-b", "artifact_digest": ARTIFACT_DIGEST,
        "evidence_class": "SELF_ASSERTED", "evidence_digest": _sha(b"provider-b-assertion"),
        "model_identity": "SELF_ASSERTED",
    }, 3)
    policy = canonical_hash({"accepted_evidence_classes": [EVIDENCE_CLASS], "rule": "REQUIRE_SHA256_PREIMAGE/1"})
    selection = _receipt("buyer-selection", {
        "selected_provider": "provider-a", "selected_attestation": provider_a["hashes"]["attestation"], "policy_digest": policy,
    }, 4)
    return {
        "procurement/promise.json": _json(promise), "procurement/provider-a.json": _json(provider_a),
        "procurement/provider-b.json": _json(provider_b), "procurement/selection.json": _json(selection),
        "procurement/provider-a-evidence.json": evidence,
        "procurement/provider-a-delivery-assertion.json": delivery_assertion,
        "procurement/provider-a-delivery.bin": delivered,
    }


def _reliance(checkpoint_hash: str, finding_digest: str) -> tuple[dict[str, bytes], dict[str, Any], list[dict[str, Any]]]:
    graph, projection = RELIANCE.build_graph()
    source = next(item for item in graph["nodes"] if item["node_id"] == "source-00")
    source["artifact_digest"] = checkpoint_hash
    graph["graph_id"] = "answerability-network:declared-reliance-10000"
    selected_edge = next(item for item in graph["edges"] if item["target"] == "decision-09996")
    selected_edge["source"] = "decision-04992"
    graph["edges"].sort(key=lambda item: (item["source"], item["target"], item["relation"]))
    projection = [{"node_id": item["node_id"], "x": item["x"], "y": item["y"]} for item in projection]
    projection.append({"node_id": "receipt:provider-a", "x": 125, "y": 0})
    projection.sort(key=lambda item: item["node_id"])
    graph_digest = canonical_hash(graph)
    policy_hash = RELIANCE.POLICY_HASH
    signer = RELIANCE.CORRECTION_SIGNER
    subject = {
        "profile": GRAPH_PROFILE, "sequence": 0, "previous_correction": None,
        "target_digest": checkpoint_hash,
        "replacement_digest": canonical_hash({"recheck_notice": finding_digest}),
        "reason_digest": finding_digest, "authority_epoch": RELIANCE.AUTHORITY_EPOCH,
    }
    envelope = RecourseEnvelope(
        authority=Authority(principal=signer.issuer, policy=policy_hash),
        bounds=Bounds(scope=f"profile:{GRAPH_PROFILE};action:reliance.correct"),
        recourse=Recourse(challenge_window="checkpoint:reliance-map-correction", forum=Forum(log_endpoint="https://glyphstandard.com/evidence#reliance-map", trusted_root_ref=canonical_hash({"forum": "reliance-map"})), remedies=(Remedy("challenge", "recompute exact declared descendants", "forum:reliance-map"),)),
        retention_class="authority-permanent", disclosure_class="public",
    )
    correction = sign_action_receipt_v04(build_action_receipt_v04(
        action={"type": "reliance.correct", "subject": subject}, diagnostic_ref={"status": "not_applicable"}, envelope=envelope,
        event_id=RELIANCE.stable_uuid("answerability-network-correction"), claimed_at="2026-08-21T15:00:00Z",
        anchor_ref={"relation": "reliance_map", "graph_digest": graph_digest}, producer={"profile": GRAPH_PROFILE, "fixture": "answerability-network-recall"},
    ), signer).to_dict()
    context = {
        "profile": "bulla.reliance-map-context/0.1-experimental", "authority_epoch": RELIANCE.AUTHORITY_EPOCH,
        "accepted_graph_digests": [graph_digest], "correction_authorities": [signer.issuer], "policy_hash": policy_hash,
    }
    return {
        "reliance/graph.json": _json(graph, compact=True),
        "reliance/correction-ledger.json": _json({"profile": LEDGER_PROFILE, "corrections": [correction]}),
    }, context, projection


def _artifact(path: str, raw: bytes) -> dict[str, Any]:
    media_type = "application/octet-stream" if path == "procurement/provider-a-delivery.bin" else "application/json"
    return {"path": path, "sha256": _sha(raw), "byte_length": len(raw), "media_type": media_type}


def _build_stage(root: Path, stage: str) -> tuple[Path, Path, list[dict[str, Any]]]:
    files = _procurement()
    provider_a = json.loads(files["procurement/provider-a.json"])
    witness_context = None
    reliance_context = None
    projection: list[dict[str, Any]] = []
    if stage != "job":
        witness_scenario = "consistent" if stage == "published" else stage
        temp_root = root / ".witness" / stage
        context_path = WITNESS._build_scenario(
            temp_root, witness_scenario,
            external_history_receipt=None if stage == "model-dispute" else provider_a,
        )
        witness_context = json.loads(context_path.read_bytes())
        source = temp_root / "vectors" / witness_scenario
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            files[f"witness-covenant/{path.relative_to(source).as_posix()}"] = path.read_bytes()
        if stage.startswith("fork-"):
            head_a = json.loads(files["witness-covenant/evidence/head-a.json"])
            head_b = json.loads(files["witness-covenant/evidence/head-b.json"])
            finding = canonical_hash({"predicate": WITNESS.PREDICATE_PROFILE, "head_a": head_a["checkpoint_hash"], "head_b": head_b["checkpoint_hash"]})
            reliance_files, reliance_context, projection = _reliance(head_a["checkpoint_hash"], finding)
            files.update(reliance_files)
    core = {
        "profile": PROFILE, "revision": 1, "stage": stage, "network_id": NETWORK_ID,
        "transaction_id": TRANSACTION_ID,
        "artifact_manifest": [_artifact(path, raw) for path, raw in sorted(files.items())],
    }
    files["network-core.json"] = _json(core)
    dossier = root / "vectors" / stage
    for path, raw in files.items():
        target = dossier / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    context = {
        "profile": CONTEXT_PROFILE, "accepted_core_hash": canonical_hash(core),
        "accepted_issuers": {role: _signer(role).issuer for role in sorted(ROLE_ACTIONS)},
        "accepted_evidence_verifier": EVIDENCE_PROFILE,
        "accepted_witness_covenant_hash": WITNESS_COVENANT_HASH,
        "witness_context": witness_context, "reliance_context": reliance_context,
    }
    destination = root / "contexts" / f"{stage}.json"; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(_json(context))
    return dossier, destination, projection


def _tree_manifest(root: Path) -> dict[str, Any]:
    semantic = []
    presentation = []
    for group in ("contexts", "vectors", "reports", "projections"):
        for path in sorted(item for item in (root / group).rglob("*") if item.is_file()):
            entry = _artifact(path.relative_to(root).as_posix(), path.read_bytes())
            (presentation if entry["path"].startswith("projections/") else semantic).append(entry)
    return {
        "profile": "bulla.answerability-network-manifest/0.1-experimental",
        "semantic_root": canonical_hash(semantic), "presentation_root": canonical_hash(presentation),
        "semantic_entries": semantic, "presentation_entries": presentation,
        "source_entries": [_artifact(name, (HERE / name).read_bytes()) for name in SOURCE_FILES],
    }


def build(root: Path) -> None:
    for group in ("contexts", "vectors", "reports", "projections"):
        shutil.rmtree(root / group, ignore_errors=True)
    shutil.rmtree(root / ".witness", ignore_errors=True)
    for stage in STAGES:
        dossier, context, projection = _build_stage(root, stage)
        report = verify_answerability_network(dossier, context.read_bytes())
        if report["exit_code"] != 0:
            raise RuntimeError(f"{stage}: {report['errors']}")
        target = root / "reports" / f"{stage}.json"; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(_json(report))
        projection_target = root / "projections" / f"{stage}.json"
        projection_target.parent.mkdir(parents=True, exist_ok=True)
        projection_target.write_bytes(_json(projection, compact=True))
    shutil.rmtree(root / ".witness", ignore_errors=True)
    (root / "manifest.json").write_bytes(_json(_tree_manifest(root)))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--check", action="store_true"); args = parser.parse_args()
    if not args.check:
        build(HERE); return 0
    with tempfile.TemporaryDirectory(prefix="answerability-network-check-") as raw:
        candidate = Path(raw); build(candidate)
        generated = {path.relative_to(candidate).as_posix(): path.read_bytes() for path in candidate.rglob("*") if path.is_file()}
        current = {path.relative_to(HERE).as_posix(): path.read_bytes() for group in ("contexts", "vectors", "reports", "projections") for path in (HERE / group).rglob("*") if path.is_file()}
        current["manifest.json"] = (HERE / "manifest.json").read_bytes() if (HERE / "manifest.json").is_file() else b""
        if generated != current:
            print("answerability network generated corpus drift", file=__import__('sys').stderr); return 1
    print("answerability network corpus checked"); return 0


if __name__ == "__main__": raise SystemExit(main())
