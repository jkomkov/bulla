from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess

import pytest

from bulla.action_receipt import verify_receipt
from bulla.experimental.answerability_network import (
    AnswerabilityNetworkError,
    verify_answerability_network,
    verify_answerability_network_report,
)
from bulla.experimental.witness_covenant import canonical_hash


SPEC = Path(__file__).resolve().parents[1] / "spec" / "answerability-network"
STAGES = ("job", "published", "fork-open", "fork-closed", "fork-authorized", "model-dispute")


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _refresh_witness_manifest(dossier: Path) -> None:
    root = dossier / "witness-covenant"
    core_path = root / "covenant-core.json"
    core = json.loads(core_path.read_text())
    for manifest in (core["receipt_manifest"], core["artifact_manifest"]):
        for item in manifest:
            raw = (root / item["path"]).read_bytes()
            item["sha256"] = _sha(raw); item["byte_length"] = len(raw)
    core_path.write_bytes(_json(core))


def _refresh_network(dossier: Path, source_context: Path, target_context: Path) -> bytes:
    core_path = dossier / "network-core.json"
    core = json.loads(core_path.read_text())
    for item in core["artifact_manifest"]:
        raw = (dossier / item["path"]).read_bytes()
        item["sha256"] = _sha(raw); item["byte_length"] = len(raw)
    core_path.write_bytes(_json(core))
    context = json.loads(source_context.read_text())
    context["accepted_core_hash"] = canonical_hash(core)
    raw = _json(context); target_context.write_bytes(raw); return raw


def _node_report(dossier: Path, context: Path) -> dict:
    completed = subprocess.run(
        ["node", str(SPEC / "check.mjs"), str(dossier), "--context", str(context)],
        check=False, capture_output=True, text=True,
    )
    assert completed.stdout, completed.stderr
    return json.loads(completed.stdout)


def report(stage: str) -> dict:
    return verify_answerability_network(SPEC / "vectors" / stage, (SPEC / "contexts" / f"{stage}.json").read_bytes())


@pytest.mark.parametrize("stage", STAGES)
def test_canonical_report_and_standalone_node_agree(stage: str) -> None:
    expected = json.loads((SPEC / "reports" / f"{stage}.json").read_text())
    assert report(stage) == expected
    completed = subprocess.run(["node", str(SPEC / "check.mjs"), str(SPEC / "vectors" / stage), "--context", str(SPEC / "contexts" / f"{stage}.json")], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == expected


def test_fork_propagates_only_declared_paths_and_keeps_three_states() -> None:
    value = report("fork-open")
    head_a = json.loads((SPEC / "vectors/fork-open/witness-covenant/evidence/head-a.json").read_text())
    head_b = json.loads((SPEC / "vectors/fork-open/witness-covenant/evidence/head-b.json").read_text())
    correction = json.loads((SPEC / "vectors/fork-open/reliance/correction-ledger.json").read_text())["corrections"][0]["action"]["subject"]
    finding = canonical_hash({"predicate": "bulla.same-size-log-equivocation/1", "head_a": head_a["checkpoint_hash"], "head_b": head_b["checkpoint_hash"]})
    assert value["same_size_equivocation"] == "ESTABLISHED"
    assert value["witness_remedy"] == "CHALLENGE_REQUIRED"
    assert value["reliance"]["summary"] == {"declared_decisions": 10_000, "graph_nodes": 10_004, "graph_edges": 10_000, "affected": 2_500, "not_affected": 5_000, "undetermined": 2_500}
    assert value["receipt_inclusion"] == "VERIFIED"
    assert value["history_extension"] == "VERIFIED"
    assert value["reliance"]["selected_status"] == "AFFECTED"
    assert value["reliance"]["selected_receipt_attestation"] == json.loads((SPEC / "vectors/fork-open/procurement/provider-a.json").read_text())["hashes"]["attestation"]
    assert value["reliance"]["selected_checkpoint"] == json.loads((SPEC / "vectors/fork-open/witness-covenant/evidence/head-a.json").read_text())["checkpoint_hash"]
    assert (correction["target_digest"], correction["reason_digest"], correction["replacement_digest"]) == (head_a["checkpoint_hash"], finding, canonical_hash({"recheck_notice": finding}))
    assert value["reliance"]["selected_path"] == ["receipt:provider-a", "source-00", "decision-00000", "decision-00004", "decision-00012", "decision-00032", "decision-00072", "decision-00152", "decision-00308", "decision-00620", "decision-01244", "decision-02492", "decision-04992", "decision-09996"]


def test_remedy_progression_keeps_eligibility_authorization_and_attempt_separate() -> None:
    opened, closed, authorized = (report(name) for name in ("fork-open", "fork-closed", "fork-authorized"))
    assert (opened["witness_remedy"], opened["settlement_authorization"], opened["test_ledger_attempt"]) == ("CHALLENGE_REQUIRED", "NOT_ISSUED", "NOT_REPORTED")
    assert (closed["witness_remedy"], closed["settlement_authorization"], closed["test_ledger_attempt"]) == ("ELIGIBLE", "NOT_ISSUED", "NOT_REPORTED")
    assert (authorized["witness_remedy"], authorized["settlement_authorization"], authorized["test_ledger_attempt"]) == ("ELIGIBLE", "VERIFIED", "REPORTED")
    assert authorized["actual_funds"] == "NOT_ESTABLISHED"


def test_model_identity_does_not_unlock_objective_witness_remedy() -> None:
    value = report("model-dispute")
    assert value["model_identity_control"] == "CHALLENGE_REQUIRED"
    assert value["same_size_equivocation"] == "NOT_ESTABLISHED"
    assert value["witness_remedy"] == "CHALLENGE_REQUIRED"
    assert value["settlement_authorization"] == "NOT_ISSUED"


def test_report_verification_is_type_exact() -> None:
    supplied = json.loads((SPEC / "reports" / "job.json").read_text()); supplied["exit_code"] = False
    with pytest.raises(AnswerabilityNetworkError, match="differs"):
        verify_answerability_network_report(json.dumps(supplied).encode(), SPEC / "vectors" / "job", (SPEC / "contexts" / "job.json").read_bytes())


def test_packet_carried_core_cannot_bootstrap_context(tmp_path: Path) -> None:
    dossier = tmp_path / "job"; shutil.copytree(SPEC / "vectors" / "job", dossier)
    context = json.loads((SPEC / "contexts" / "job.json").read_text()); context["accepted_core_hash"] = "sha256:" + "0" * 64
    value = verify_answerability_network(dossier, json.dumps(context).encode())
    assert value["exit_code"] == 1 and value["network_integrity"] == "FAILED"


def test_accepted_checkpoint_is_exact_reliance_source(tmp_path: Path) -> None:
    dossier = tmp_path / "fork-open"; shutil.copytree(SPEC / "vectors" / "fork-open", dossier)
    graph = json.loads((dossier / "reliance/graph.json").read_text()); graph["nodes"][0]["artifact_digest"] = "sha256:" + "0" * 64
    (dossier / "reliance/graph.json").write_text(json.dumps(graph, sort_keys=True, separators=(",", ":")) + "\n")
    value = verify_answerability_network(dossier, (SPEC / "contexts/fork-open.json").read_bytes())
    assert value["exit_code"] == 1


def test_withheld_second_head_cannot_establish_fork(tmp_path: Path) -> None:
    dossier = tmp_path / "fork-open"; shutil.copytree(SPEC / "vectors" / "fork-open", dossier)
    (dossier / "witness-covenant/evidence/head-b.json").unlink()
    value = verify_answerability_network(dossier, (SPEC / "contexts/fork-open.json").read_bytes())
    assert value["same_size_equivocation"] == "NOT_COMPUTED"
    assert value["witness_remedy"] == "NOT_COMPUTED"


def test_stage_label_cannot_select_a_protected_result(tmp_path: Path) -> None:
    dossier = tmp_path / "relabelled"; shutil.copytree(SPEC / "vectors" / "job", dossier)
    core = json.loads((dossier / "network-core.json").read_text()); core["stage"] = "fork-authorized"
    (dossier / "network-core.json").write_bytes(_json(core))
    context_path = tmp_path / "context.json"
    context_raw = _refresh_network(dossier, SPEC / "contexts/job.json", context_path)
    python_report = verify_answerability_network(dossier, context_raw)
    node_report = _node_report(dossier, context_path)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["witness_remedy"] == node_report["witness_remedy"] == "NOT_COMPUTED"


@pytest.mark.parametrize(("field", "value"), (("price", True), ("price", 12_501), ("unit", "BTC_SAT")))
def test_closed_procurement_terms_fail_with_valid_signatures(field: str, value: object, tmp_path: Path) -> None:
    generator = runpy.run_path(str(SPEC / "generate.py"))
    dossier = tmp_path / f"job-{field}"; shutil.copytree(SPEC / "vectors" / "job", dossier)
    current = json.loads((dossier / "procurement/promise.json").read_text())
    subject = dict(current["action"]["subject"]); subject.pop("profile"); subject.pop("transaction_id"); subject[field] = value
    replacement = generator["_receipt"]("buyer", subject, 1)
    (dossier / "procurement/promise.json").write_bytes(_json(replacement))
    context_path = tmp_path / f"context-{field}.json"
    context_raw = _refresh_network(dossier, SPEC / "contexts/job.json", context_path)
    python_report = verify_answerability_network(dossier, context_raw)
    node_report = _node_report(dossier, context_path)
    assert python_report["exit_code"] == node_report["exit_code"] == 1


def test_signed_model_identity_amplification_is_rejected(tmp_path: Path) -> None:
    generator = runpy.run_path(str(SPEC / "generate.py"))
    dossier = tmp_path / "amplified"; shutil.copytree(SPEC / "vectors" / "job", dossier)
    current = json.loads((dossier / "procurement/provider-a.json").read_text())
    subject = dict(current["action"]["subject"]); subject.pop("profile"); subject.pop("transaction_id"); subject["model_identity"] = "EXECUTION_VERIFIED"
    replacement = generator["_receipt"]("provider-a", subject, 2, evidence_refs=tuple(current["evidence_refs"]))
    (dossier / "procurement/provider-a.json").write_bytes(_json(replacement))
    context_path = tmp_path / "context.json"
    context_raw = _refresh_network(dossier, SPEC / "contexts/job.json", context_path)
    assert verify_answerability_network(dossier, context_raw)["exit_code"] == 1
    assert _node_report(dossier, context_path)["exit_code"] == 1


def test_invalid_append_only_proof_cannot_publish_selected_receipt(tmp_path: Path) -> None:
    dossier = tmp_path / "published"; shutil.copytree(SPEC / "vectors" / "published", dossier)
    path = dossier / "witness-covenant/evidence/external-receipt-consistency.json"
    value = json.loads(path.read_text()); value["proof"][0] = "sha256:" + "0" * 64; path.write_bytes(_json(value))
    _refresh_witness_manifest(dossier)
    context_path = tmp_path / "context.json"
    context_raw = _refresh_network(dossier, SPEC / "contexts/published.json", context_path)
    python_report = verify_answerability_network(dossier, context_raw)
    node_report = _node_report(dossier, context_path)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert python_report["history_extension"] == node_report["history_extension"] == "NOT_COMPUTED"


def test_consistent_witness_history_without_selected_receipt_cannot_be_published(tmp_path: Path) -> None:
    dossier = tmp_path / "published"; shutil.copytree(SPEC / "vectors" / "published", dossier)
    removed = {
        "evidence/external-receipt-inclusion.json",
        "evidence/external-receipt-checkpoint.json",
        "evidence/external-receipt-consistency.json",
    }
    for path in removed:
        (dossier / "witness-covenant" / path).unlink()
    witness_core_path = dossier / "witness-covenant/covenant-core.json"
    witness_core = json.loads(witness_core_path.read_text())
    witness_core["artifact_manifest"] = [item for item in witness_core["artifact_manifest"] if item["path"] not in removed]
    witness_core_path.write_bytes(_json(witness_core))
    network_core_path = dossier / "network-core.json"
    network_core = json.loads(network_core_path.read_text())
    network_core["artifact_manifest"] = [item for item in network_core["artifact_manifest"] if item["path"] not in {f"witness-covenant/{path}" for path in removed}]
    network_core_path.write_bytes(_json(network_core))
    context_path = tmp_path / "context.json"
    context_raw = _refresh_network(dossier, SPEC / "contexts/published.json", context_path)
    python_report = verify_answerability_network(dossier, context_raw)
    node_report = _node_report(dossier, context_path)
    assert python_report["exit_code"] == node_report["exit_code"] == 1
    assert "append-only history" in python_report["errors"][0]


def test_presentation_only_change_does_not_change_semantic_root(tmp_path: Path) -> None:
    generator = runpy.run_path(str(SPEC / "generate.py"))
    candidate = tmp_path / "generated"
    generator["build"](candidate)
    before = json.loads((candidate / "manifest.json").read_text())
    projection_path = candidate / "projections/fork-open.json"
    projection = json.loads(projection_path.read_text())
    projection[0]["x"] += 1
    projection_path.write_text(json.dumps(projection, sort_keys=True, separators=(",", ":")) + "\n")
    after = generator["_tree_manifest"](candidate)
    assert after["semantic_root"] == before["semantic_root"]
    assert after["presentation_root"] != before["presentation_root"]


def test_opaque_provider_evidence_cannot_qualify(tmp_path: Path) -> None:
    generator = runpy.run_path(str(SPEC / "generate.py"))
    dossier = tmp_path / "opaque-evidence"; shutil.copytree(SPEC / "vectors/job", dossier)
    provider = json.loads((dossier / "procurement/provider-a.json").read_text())
    subject = dict(provider["action"]["subject"]); subject.pop("profile"); subject.pop("transaction_id")
    subject["evidence_digest"] = "sha256:" + "0" * 64
    replacement = generator["_receipt"]("provider-a", subject, 2, evidence_refs=tuple(provider["evidence_refs"]))
    (dossier / "procurement/provider-a.json").write_bytes(_json(replacement))
    selection = json.loads((dossier / "procurement/selection.json").read_text())
    selection_subject = dict(selection["action"]["subject"]); selection_subject.pop("profile"); selection_subject.pop("transaction_id")
    selection_subject["selected_attestation"] = replacement["hashes"]["attestation"]
    (dossier / "procurement/selection.json").write_bytes(_json(generator["_receipt"]("buyer-selection", selection_subject, 4)))
    context_path = tmp_path / "context.json"
    context_raw = _refresh_network(dossier, SPEC / "contexts/job.json", context_path)
    assert verify_answerability_network(dossier, context_raw)["exit_code"] == 1
    assert _node_report(dossier, context_path)["exit_code"] == 1


def test_byte_preimage_does_not_upgrade_historical_delivery_grounding() -> None:
    receipt = json.loads((SPEC / "vectors/job/procurement/provider-a.json").read_text())
    verification = verify_receipt(receipt)
    assert verification.ok
    assert verification.effective_grounding == "self_asserted"
    assert report("job")["byte_equality"] == "PROVIDER_DIGESTS_MATCH_SUPPLIED_BYTES"


def test_job_binds_the_external_witness_covenant_before_publication(tmp_path: Path) -> None:
    generator = runpy.run_path(str(SPEC / "generate.py"))
    dossier = tmp_path / "wrong-covenant"; shutil.copytree(SPEC / "vectors/job", dossier)
    promise = json.loads((dossier / "procurement/promise.json").read_text())
    subject = dict(promise["action"]["subject"]); subject.pop("profile"); subject.pop("transaction_id")
    subject["witness_covenant_hash"] = "sha256:" + "0" * 64
    (dossier / "procurement/promise.json").write_bytes(_json(generator["_receipt"]("buyer", subject, 1)))
    context_path = tmp_path / "context.json"
    context_raw = _refresh_network(dossier, SPEC / "contexts/job.json", context_path)
    assert verify_answerability_network(dossier, context_raw)["exit_code"] == 1
    assert _node_report(dossier, context_path)["exit_code"] == 1


def test_formal_fixture_map_is_mechanically_bound_to_reports_and_theorems() -> None:
    mapping = json.loads((SPEC / "formal-fixture-map.json").read_text())
    lean = (SPEC.parents[1] / "papers/interpolant-envelope/lean/InterpolantEnvelope/AssuranceLinker.lean").read_text()

    def resolve(value: object, path: str) -> object:
        for part in path.split("."):
            value = value[part]  # type: ignore[index]
        return value

    for entry in mapping["entries"]:
        assert f"theorem {entry['theorem']}" in lean
        fixture = json.loads((SPEC / "reports" / f"{entry['fixture']}.json").read_text())
        for path, expected in entry["report_assertions"].items():
            assert resolve(fixture, path) == expected


def test_generator_is_byte_deterministic() -> None:
    subprocess.run([str(Path(__import__('sys').executable)), str(SPEC / "generate.py"), "--check"], check=True)
