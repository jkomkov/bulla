from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from bulla.experimental.inference_clearing import (
    InferenceClearingContext,
    InferenceClearingError,
    InferenceClearingReport,
    parse_inference_clearing_bundle,
    verify_inference_clearing_bundle,
)
from bulla.identity import LocalEd25519Signer


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
SPEC = ROOT / "spec" / "inference-clearing"
VECTORS = SPEC / "vectors"
CONTEXTS = SPEC / "contexts"
SCENARIOS = ("opaque", "recheckable", "bypass")
EXPECTED = json.loads((SPEC / "expected-verdict.json").read_text(encoding="utf-8"))["reports"]


def _context(name: str) -> InferenceClearingContext:
    return InferenceClearingContext.from_dict(
        json.loads((CONTEXTS / f"{name}.json").read_text(encoding="utf-8"))
    )


def _copy(tmp_path: Path, name: str = "recheckable") -> Path:
    target = tmp_path / name
    shutil.copytree(VECTORS / name, target)
    return target


def _load_generator():
    specification = importlib.util.spec_from_file_location(
        "bulla_inference_clearing_generator", SPEC / "generate.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _load_comprehension_scorer():
    specification = importlib.util.spec_from_file_location(
        "bulla_inference_clearing_comprehension_scorer",
        SPEC / "score_comprehension_gate.py",
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> bytes:
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _comprehension_evidence(tmp_path: Path):
    scorer = _load_comprehension_scorer()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    coordinator = LocalEd25519Signer(hashlib.sha256(b"comprehension-coordinator").digest())
    context = tmp_path / "context.json"
    _write_json(context, {
        "profile": "bulla.inference-clearing-comprehension-context/0.1",
        "accepted_coordinator": coordinator.issuer,
    })
    preview = b"<!doctype html><title>Same answer. Different evidence.</title>\n"
    (evidence / "preview-page.html").write_bytes(preview)
    protocol_hash, command_hash, kit_hash = scorer._expected_bindings()
    gate_content = {
        "protocol_sha256": protocol_hash,
        "preview_commit": "1" * 40,
        "immutable_preview_url": "https://preview.example/inference-clearing",
        "route": "/bulla/experimental/inference-clearing",
        "reproduction_command_sha256": command_hash,
        "reproduction_kit_sha256": kit_hash,
        "preview_artifact_path": "preview-page.html",
        "preview_artifact_sha256": scorer._sha(preview),
        "preview_artifact_byte_length": len(preview),
        "preview_content_type": "text/html",
    }
    gate = {
        "profile": "bulla.inference-clearing-comprehension-gate-open/0.1",
        "content": gate_content,
        "proof": coordinator.sign_domain("content", scorer._content_digest(gate_content)),
    }
    gate_raw = _write_json(evidence / "gate-open.json", gate)
    gate_hash = scorer._sha(gate_raw)
    questions = scorer.ALL_QUESTIONS
    response_refs = []
    slots = ["developer-1", "developer-2", "developer-3", "decision-maker-1", "decision-maker-2"]
    for index, slot in enumerate(slots, 1):
        reader = LocalEd25519Signer(hashlib.sha256(f"reader:{index}".encode()).digest())
        answers = {question: f"first response {index} for {question}" for question in questions}
        terminal = (
            {"required": True, "status": "COMPLETED", "duration_seconds": 42.5}
            if slot == "developer-1"
            else {"required": False, "status": "NOT_REQUIRED", "duration_seconds": None}
        )
        content = {
            "attempt_id": "attempt-1",
            "participant_id": f"reader-{index:02d}",
            "reader_key": reader.issuer,
            "slot": slot,
            "participant_role": "DEVELOPER" if slot.startswith("developer") else "TECHNICAL_DECISION_MAKER",
            "eligibility": {
                "identity_commitment": scorer._sha(f"identity:{index}".encode()),
                "no_prior_bulla": True,
                "no_prior_glyph": True,
                "no_prior_res_agentica": True,
                "no_pr_review": True,
                "no_implementation_involvement": True,
            },
            "gate_open_sha256": gate_hash,
            "browser_story_without_help": True,
            "first_responses": answers,
            "first_response_hashes": {question: scorer._sha(text.encode()) for question, text in answers.items()},
            "rubric": {question: "PASS" for question in questions},
            "assistance": [],
            "terminal_reproduction": terminal,
            "corrections": [],
            "limitations": [],
        }
        digest = scorer._content_digest(content)
        response = {
            "profile": "bulla.inference-clearing-comprehension-response/0.1",
            "content": content,
            "reader_proof": reader.sign_domain("content", digest),
            "coordinator_proof": coordinator.sign_domain("content", digest),
        }
        relative = f"responses/attempt-1/reader-{index:02d}.json"
        raw = _write_json(evidence / relative, response)
        response_refs.append({"path": relative, "sha256": scorer._sha(raw)})
    manifest_content = {
        "gate_open_sha256": gate_hash,
        "attempts": [{
            "attempt_id": "attempt-1",
            "scope": "ALL_DIMENSIONS",
            "dimensions": sorted(scorer.ACCEPTANCE_DIMENSIONS),
            "response_refs": response_refs,
        }],
    }
    manifest = {
        "profile": "bulla.inference-clearing-comprehension-attempt-manifest/0.1",
        "content": manifest_content,
        "proof": coordinator.sign_domain("content", scorer._content_digest(manifest_content)),
    }
    _write_json(evidence / "attempt-manifest.json", manifest)
    return scorer, evidence, context


def _resign_comprehension_evidence(scorer, evidence: Path) -> None:
    """Rebind an intentionally coherent hostile fixture after semantic mutation."""

    coordinator = LocalEd25519Signer(hashlib.sha256(b"comprehension-coordinator").digest())
    gate_path = evidence / "gate-open.json"
    gate = json.loads(gate_path.read_text())
    gate["proof"] = coordinator.sign_domain(
        "content", scorer._content_digest(gate["content"])
    )
    gate_digest = scorer._sha(_write_json(gate_path, gate))
    manifest_path = evidence / "attempt-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["content"]["gate_open_sha256"] = gate_digest
    for attempt in manifest["content"]["attempts"]:
        for reference in attempt["response_refs"]:
            response_path = evidence / reference["path"]
            response = json.loads(response_path.read_text())
            response["content"]["gate_open_sha256"] = gate_digest
            index = int(response["content"]["participant_id"].removeprefix("reader-"))
            reader = LocalEd25519Signer(hashlib.sha256(f"reader:{index}".encode()).digest())
            digest = scorer._content_digest(response["content"])
            response["reader_proof"] = reader.sign_domain("content", digest)
            response["coordinator_proof"] = coordinator.sign_domain("content", digest)
            reference["sha256"] = scorer._sha(_write_json(response_path, response))
    manifest["proof"] = coordinator.sign_domain(
        "content", scorer._content_digest(manifest["content"])
    )
    _write_json(manifest_path, manifest)


def _run_checkers(bundle: Path, context: Path) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    python = subprocess.run(
        [sys.executable, str(SPEC / "check.py"), str(bundle), "--context", str(context)],
        check=False,
        capture_output=True,
        text=True,
    )
    node = subprocess.run(
        ["node", str(SPEC / "check.mjs"), str(bundle), "--context", str(context)],
        check=False,
        capture_output=True,
        text=True,
    )
    return python, node


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_python_and_node_match_checked_projection(scenario: str) -> None:
    report = verify_inference_clearing_bundle(VECTORS / scenario, _context(scenario)).to_dict()
    assert report == EXPECTED[scenario]
    python, node = _run_checkers(VECTORS / scenario, CONTEXTS / f"{scenario}.json")
    assert python.returncode == node.returncode == 0
    assert json.loads(python.stdout) == json.loads(node.stdout) == report


def test_same_answer_has_different_retained_evidence() -> None:
    opaque = EXPECTED["opaque"]
    recheckable = EXPECTED["recheckable"]
    bypass = EXPECTED["bypass"]
    assert opaque["output"] == recheckable["output"] == "BACKUP"
    assert opaque["relation_evidence"]["model_binding"] == "UNAVAILABLE"
    assert opaque["relation_evidence"]["relation_reproduction"] == "UNAVAILABLE"
    assert opaque["relation_evidence"]["provider_execution_occurrence"] == "NOT_ESTABLISHED"
    assert recheckable["relation_evidence"]["model_binding"] == "TERM_BOUND"
    assert recheckable["relation_evidence"]["relation_reproduction"] == "REPRODUCED"
    assert recheckable["relation_evidence"]["provider_execution_occurrence"] == "NOT_ESTABLISHED"
    assert (opaque["reliance_decision"], opaque["payment_eligibility"]) == ("REFUSE", "INELIGIBLE")
    assert (recheckable["reliance_decision"], recheckable["payment_eligibility"]) == ("RELY", "ELIGIBLE")
    assert (recheckable["settlement_authorization"], recheckable["settlement_execution"]) == (
        "NOT_ISSUED", "NOT_ATTEMPTED"
    )
    assert recheckable["receipt_integrity"] == bypass["receipt_integrity"] == "VERIFIED"
    assert bypass["receiver_coverage"] == "UNCOVERED"
    assert bypass["uncovered_effects"] == ["effect-bypass-001"]
    assert bypass["payment_eligibility"] == "INELIGIBLE"


def test_term_and_external_policy_bind_the_closed_evidence_requirements() -> None:
    expected = {
        "output_binding": "VERIFIED",
        "model_binding": "TERM_BOUND",
        "relation_reproduction": "REPRODUCED",
        "provider_execution_occurrence": "NOT_REQUIRED",
    }
    for scenario in SCENARIOS:
        term = json.loads((VECTORS / scenario / "terms" / "term-document.json").read_text())
        context = json.loads((CONTEXTS / f"{scenario}.json").read_text())
        assert term["required_evidence"] == context["accepted_evidence_requirements"] == expected
        assert term["expected_model_hash"].startswith("sha256:")
        assert term["output_schema"] == {"type": "enum", "values": ["PRIMARY", "BACKUP"]}


def test_provider_substitution_preserves_template_and_policy() -> None:
    terms = [
        json.loads((VECTORS / scenario / "terms" / "term-document.json").read_text())
        for scenario in ("opaque", "recheckable")
    ]
    transaction_ids = [term.pop("transaction_id") for term in terms]
    assert transaction_ids[0] != transaction_ids[1]
    assert terms[0] == terms[1]
    projection = json.loads((SPEC / "site-projection.json").read_text())
    assert projection["provider_substitution"] == {
        "same_term_template": True,
        "same_buyer_policy": True,
        "separate_transaction_instances": True,
        "verifier": "PROJECT_AUTHORED_LOCAL",
    }


def test_presentation_does_not_infer_real_world_funds_movement() -> None:
    projection = json.loads((SPEC / "site-projection.json").read_text())
    for result in (
        projection["providers"]["opaque"],
        projection["providers"]["recheckable"],
        projection["bypass"],
    ):
        assert result["settlement_attempt"] == "NOT_ATTEMPTED"
        assert result["funds_movement"] == "NOT_ESTABLISHED"


def test_report_rejects_boolean_coercion() -> None:
    report = verify_inference_clearing_bundle(VECTORS / "recheckable", _context("recheckable"))
    assert isinstance(report, InferenceClearingReport)
    with pytest.raises(TypeError):
        bool(report)
    with pytest.raises(TypeError):
        bool(report.relation_evidence)


@pytest.mark.parametrize(
    ("path", "mutation"),
    [
        ("execution/model.json", lambda value: {**value, "output_bias": [0, -5000]}),
        ("execution/report.json", lambda value: {**value, "output": "PRIMARY"}),
        ("terms/term-document.json", lambda value: {**value, "expected_model_hash": "sha256:" + "0" * 64}),
        ("receiver/coverage.json", lambda value: {**value, "receipted_ids": ["effect-phantom-001"]}),
        ("assurance/capital.json", lambda value: {**value, "allocated_amount": 199999}),
    ],
)
def test_uncommitted_artifact_mutation_fails_at_integrity(
    tmp_path: Path, path: str, mutation
) -> None:
    bundle = _copy(tmp_path)
    target = bundle / path
    value = json.loads(target.read_text(encoding="utf-8"))
    target.write_text(json.dumps(mutation(value), sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(InferenceClearingError, match="artifact commitment mismatch"):
        parse_inference_clearing_bundle(bundle)


def test_packet_keys_cannot_bootstrap_provider_trust() -> None:
    value = json.loads((CONTEXTS / "recheckable.json").read_text())
    value["accepted_issuers_by_role"]["provider"] = [
        "did:key:z6MksxBUYPyfXfiV8T9gB5uSshtU32FPu52KyHG9Tc5hW2zL"
    ]
    report = verify_inference_clearing_bundle(
        VECTORS / "recheckable", InferenceClearingContext.from_dict(value)
    )
    assert report.issuer_role_status == "REJECTED"
    assert report.payment_eligibility == "NOT_COMPUTED"
    assert report.exit_code == 1


def test_guarantee_does_not_amplify_historical_execution(tmp_path: Path) -> None:
    generator = _load_generator()
    context, report = generator._scenario("guarantee", tmp_path / "vectors")
    context_path = tmp_path / "guarantee-context.json"
    context_path.write_text(json.dumps(context, sort_keys=True) + "\n")
    python, node = _run_checkers(tmp_path / "vectors" / "guarantee", context_path)
    assert python.returncode == node.returncode == 0
    assert json.loads(node.stdout) == report
    assert report["recourse_conveyance"] == "GUARANTEE_NAMED"
    assert report["relation_evidence"]["provider_execution_occurrence"] == "NOT_ESTABLISHED"
    assert report["relation_evidence"]["relation_reproduction"] == "UNAVAILABLE"


def test_post_hoc_equivalent_model_cannot_become_term_bound(tmp_path: Path) -> None:
    generator = _load_generator()
    context, _ = generator._scenario(
        "signed-post-hoc-equivalent-model",
        tmp_path / "vectors",
        verify_result=False,
        semantic_mutation="post-hoc-equivalent-model",
    )
    bundle = tmp_path / "vectors" / "signed-post-hoc-equivalent-model"
    report = verify_inference_clearing_bundle(bundle, InferenceClearingContext.from_dict(context))
    assert report.output == "BACKUP"
    assert report.relation_evidence.model_binding == "UNBOUND"
    assert report.relation_evidence.relation_reproduction == "FAILED"
    assert report.relation_evidence.provider_execution_occurrence == "NOT_ESTABLISHED"
    assert report.payment_eligibility == "NOT_COMPUTED"
    context_path = tmp_path / "post-hoc-context.json"
    context_path.write_text(json.dumps(context, sort_keys=True) + "\n")
    python, node = _run_checkers(bundle, context_path)
    assert python.returncode == node.returncode == 1


@pytest.mark.parametrize(
    ("case", "mutation", "expected_exit"),
    [
        ("signed-input-mismatch", "input-mismatch", 1),
        ("signed-model-mismatch", "model-mismatch", 1),
        ("signed-coverage-unbound", "coverage-unbound", 1),
        ("signed-capital-unbound", "capital-unbound", 1),
        ("signed-settlement-unbound", "settlement-unbound", 1),
        ("signed-wrong-authority", "wrong-authority", 1),
        ("signed-unsafe-arithmetic", "unsafe-arithmetic", 2),
        ("signed-inclusion-size-mismatch", "inclusion-size-mismatch", 1),
        ("signed-model-string-coercion", "model-string-coercion", 2),
        ("signed-comparison-mismatch", "comparison-mismatch", 1),
        ("signed-order-term-mismatch", "order-term-mismatch", 1),
        ("signed-delivery-anchor-mismatch", "delivery-anchor-mismatch", 1),
        ("signed-witness-proof-substitution", "witness-proof-substitution", 1),
        ("signed-provider-claim-substitution", "provider-claim-substitution", 1),
        ("signed-missing-required-model", "missing-required-model", 2),
        ("signed-malformed-settlement-object", "malformed-settlement-object", 2),
    ],
)
def test_rehashed_hostile_cases_are_generated_transiently(
    tmp_path: Path, case: str, mutation: str, expected_exit: int
) -> None:
    generator = _load_generator()
    context, _ = generator._scenario(
        case, tmp_path / "vectors", verify_result=False, semantic_mutation=mutation
    )
    context_path = tmp_path / f"{case}-context.json"
    context_path.write_text(json.dumps(context, sort_keys=True) + "\n")
    python, node = _run_checkers(tmp_path / "vectors" / case, context_path)
    assert python.returncode == node.returncode == expected_exit
    if expected_exit == 2:
        assert "Traceback" not in python.stderr
        assert "Traceback" not in node.stderr


def test_invalid_authorization_does_not_rewrite_eligibility(tmp_path: Path) -> None:
    generator = _load_generator()
    _, report = generator._scenario("unsafe-settlement", tmp_path / "vectors")
    assert report["payment_eligibility"] == "INELIGIBLE"
    assert report["settlement_authorization"] == "INVALID"
    assert report["settlement_execution"] == "INVALID"
    assert report["exit_code"] == 1


def test_duplicate_json_member_and_symlink_fail_closed(tmp_path: Path) -> None:
    duplicate = _copy(tmp_path, "recheckable")
    core = duplicate / "clearing-core.json"
    core.write_text(core.read_text().replace("{\n", '{\n  "profile": "duplicate",\n', 1))
    with pytest.raises(InferenceClearingError, match="duplicate JSON member"):
        parse_inference_clearing_bundle(duplicate)
    if os.name != "nt":
        linked = _copy(tmp_path, "opaque")
        target = linked / "execution" / "report.json"
        outside = tmp_path / "outside.json"
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
        with pytest.raises(InferenceClearingError, match="symlink"):
            parse_inference_clearing_bundle(linked)


def test_generator_and_kit_are_deterministic(tmp_path: Path) -> None:
    generated = subprocess.run(
        [sys.executable, str(SPEC / "generate.py"), "--check"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    built = subprocess.run(
        [sys.executable, str(SPEC / "build_kit.py"), "--out", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    archive = tmp_path / "inference-clearing-reproduction-kit.tar"
    with tarfile.open(archive, "r:") as kit:
        names = kit.getnames()
        assert not any("hostile-vectors" in name or "hostile-contexts" in name for name in names)
        assert "inference-clearing-kit/site-projection.json" not in names
        kit.extractall(tmp_path / "extracted", filter="data")
    extracted = tmp_path / "extracted" / "inference-clearing-kit"
    python = subprocess.run(
        [sys.executable, "-I", "check.py", "vectors/recheckable", "--context", "contexts/recheckable.json"],
        cwd=extracted,
        check=False,
        capture_output=True,
        text=True,
    )
    node = subprocess.run(
        ["node", "check.mjs", "vectors/recheckable", "--context", "contexts/recheckable.json"],
        cwd=extracted,
        check=False,
        capture_output=True,
        text=True,
    )
    assert python.returncode == node.returncode == 0
    assert json.loads(python.stdout) == json.loads(node.stdout)


def test_public_source_correspondence_tracks_only_declared_git_inputs() -> None:
    correspondence = SPEC / "public-source-correspondence.json"
    if not correspondence.exists():
        pytest.skip("standalone public-source correspondence is not present")
    record = json.loads(correspondence.read_text(encoding="utf-8"))
    members = record["members"]
    compact = json.dumps(members, sort_keys=True, separators=(",", ":")).encode()
    assert record["member_set_sha256"] == "sha256:" + hashlib.sha256(compact).hexdigest()

    member_paths = [item["path"] for item in members]
    local_paths = [item["path"] for item in record["repository_local_files"]]
    assert len(member_paths) == len(set(member_paths))
    assert not set(member_paths) & set(local_paths)
    assert not any("/.lake/" in name for name in member_paths)

    for item in members:
        raw = (ROOT / item["path"]).read_bytes()
        assert item["byte_length"] == len(raw)
        assert item["sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    for item in record["repository_local_files"]:
        raw = (ROOT / item["path"]).read_bytes()
        assert item["mirror_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_exact_published_commands_run_in_a_clean_environment(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    shutil.copytree(
        ROOT,
        checkout / "bulla",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "dist", "build", "*.egg-info"),
    )
    projection = json.loads((SPEC / "site-projection.json").read_text())
    command_block = "\n".join(projection["reproduction"]["commands"])
    completed = subprocess.run(
        ["bash", "-euxo", "pipefail", "-c", command_block],
        cwd=checkout,
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Python and Node reports match" in completed.stdout


def test_runtime_disconnects_providers_then_verifies_offline(tmp_path: Path) -> None:
    output = tmp_path / "runtime"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "inference-clearing" / "run_demo.py"), "--story", "--out", str(output)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Same answer. Different evidence." in completed.stdout
    runtime = json.loads((output / "runtime-report.json").read_text())
    assert runtime["providers_terminated_before_verification"] is True
    report = runtime["reports"]["recheckable"]
    assert report["payment_eligibility"] == "ELIGIBLE"
    assert report["settlement_authorization"] == "NOT_ISSUED"
    assert report["settlement_execution"] == "NOT_ATTEMPTED"
    core = json.loads((output / "recheckable" / "clearing-core.json").read_text())
    assert not any(item["action_type"] == "assurance.settlement.authorize" for item in core["ordered_receipts"])
    python, node = _run_checkers(output / "recheckable", output / "contexts" / "recheckable.json")
    assert python.returncode == node.returncode == 0
    assert json.loads(python.stdout) == json.loads(node.stdout)


@pytest.mark.parametrize(
    "injection",
    [
        "after-provider-response-before-receiver",
        "after-receiver-before-witness",
        "before-coverage-reconciliation",
    ],
)
def test_runtime_failure_injection_never_reports_eligibility(tmp_path: Path, injection: str) -> None:
    output = tmp_path / injection
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples" / "inference-clearing" / "run_demo.py"),
            "--out", str(output), "--format", "json", "--inject-failure", injection,
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads((output / "failure-report.json").read_text())
    assert result["payment_eligibility"] == "NOT_COMPUTED"
    assert result["settlement_authorization"] == "NOT_ISSUED"
    assert result["settlement_execution"] == "NOT_ATTEMPTED"


def test_only_three_canonical_bundles_are_committed() -> None:
    assert {path.name for path in VECTORS.iterdir() if path.is_dir()} == set(SCENARIOS)
    assert not (SPEC / "hostile-vectors").exists()
    assert not (SPEC / "hostile-contexts").exists()
    assert not (SPEC / "inference-clearing-reproduction-kit.tar").exists()


def test_comprehension_protocol_is_frozen_without_fake_human_evidence() -> None:
    completed = subprocess.run(
        [sys.executable, str(SPEC / "check_comprehension_protocol.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    protocol = json.loads((SPEC / "comprehension-protocol.json").read_text())
    assert protocol["state"] == "FROZEN_PENDING_GATE_OPEN"
    assert len(protocol["participant_slots"]) == 5
    assert len(protocol["questions"]) == 9
    assert not (SPEC / "comprehension-gate-open.json").exists()
    assert not (SPEC / "comprehension-result.json").exists()


def test_authenticated_comprehension_scorer_derives_established(tmp_path: Path) -> None:
    scorer, evidence, context = _comprehension_evidence(tmp_path)
    result = scorer.score(evidence, context)
    assert result["status"] == "ESTABLISHED"
    assert len(result["participant_evidence"]) == 5
    assert result["measures"] == {
        "participants": 5,
        "browser_story_passes": 5,
        "role_handoff_passes": 5,
        "all_distinctions_passes": 5,
        "answer_correctness_boundary_passes": 5,
        "funds_movement_boundary_passes": 5,
        "terminal_reproduction_seconds": 42.5,
    }


def test_comprehension_scorer_publishes_atomically(tmp_path: Path) -> None:
    _, evidence, context = _comprehension_evidence(tmp_path)
    output = tmp_path / "result.json"
    command = [
        sys.executable,
        str(SPEC / "score_comprehension_gate.py"),
        "--evidence", str(evidence),
        "--context", str(context),
        "--out", str(output),
    ]
    accepted = subprocess.run(command, check=False, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    original = output.read_bytes()
    assert json.loads(original)["status"] == "ESTABLISHED"
    manifest_path = evidence / "attempt-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["content"]["attempts"][0]["response_refs"] = []
    _write_json(manifest_path, manifest)
    rejected = subprocess.run(command, check=False, capture_output=True, text=True)
    assert rejected.returncode == 2
    assert output.read_bytes() == original


@pytest.mark.parametrize("attack", ["integer-attempt-id", "nonhex-preview-commit"])
def test_comprehension_scorer_enforces_published_schemas(
    tmp_path: Path, attack: str
) -> None:
    scorer, evidence, context = _comprehension_evidence(tmp_path)
    if attack == "integer-attempt-id":
        manifest_path = evidence / "attempt-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["content"]["attempts"][0]["attempt_id"] = 7
        _write_json(manifest_path, manifest)
        for response_path in sorted((evidence / "responses" / "attempt-1").glob("*.json")):
            response = json.loads(response_path.read_text())
            response["content"]["attempt_id"] = 7
            _write_json(response_path, response)
    else:
        gate_path = evidence / "gate-open.json"
        gate = json.loads(gate_path.read_text())
        gate["content"]["preview_commit"] = "x" * 40
        _write_json(gate_path, gate)
    _resign_comprehension_evidence(scorer, evidence)
    with pytest.raises(scorer.GateError):
        scorer.score(evidence, context)


@pytest.mark.parametrize(
    "attack",
    [
        "wrong-gate-bindings",
        "response-hash-substitution",
        "terminal-not-required",
        "missing-participants",
        "packet-carried-context",
    ],
)
def test_comprehension_scorer_rejects_manufactured_evidence(tmp_path: Path, attack: str) -> None:
    scorer, evidence, context = _comprehension_evidence(tmp_path)
    if attack == "wrong-gate-bindings":
        gate_path = evidence / "gate-open.json"
        gate = json.loads(gate_path.read_text())
        gate["content"]["protocol_sha256"] = "sha256:" + "0" * 64
        coordinator = LocalEd25519Signer(hashlib.sha256(b"comprehension-coordinator").digest())
        gate["proof"] = coordinator.sign_domain(
            "content", scorer._content_digest(gate["content"])
        )
        _write_json(gate_path, gate)
    elif attack in {"response-hash-substitution", "terminal-not-required"}:
        response_path = evidence / "responses" / "attempt-1" / "reader-01.json"
        response = json.loads(response_path.read_text())
        if attack == "response-hash-substitution":
            response["content"]["first_response_hashes"]["funds_movement"] = "sha256:" + "0" * 64
        else:
            response["content"]["terminal_reproduction"] = {
                "required": False,
                "status": "NOT_REQUIRED",
                "duration_seconds": None,
            }
        reader = LocalEd25519Signer(hashlib.sha256(b"reader:1").digest())
        coordinator = LocalEd25519Signer(hashlib.sha256(b"comprehension-coordinator").digest())
        digest = scorer._content_digest(response["content"])
        response["reader_proof"] = reader.sign_domain("content", digest)
        response["coordinator_proof"] = coordinator.sign_domain("content", digest)
        response_raw = _write_json(response_path, response)
        manifest_path = evidence / "attempt-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["content"]["attempts"][0]["response_refs"][0]["sha256"] = scorer._sha(response_raw)
        manifest["proof"] = coordinator.sign_domain(
            "content", scorer._content_digest(manifest["content"])
        )
        _write_json(manifest_path, manifest)
    elif attack == "missing-participants":
        manifest_path = evidence / "attempt-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["content"]["attempts"][0]["response_refs"] = []
        coordinator = LocalEd25519Signer(hashlib.sha256(b"comprehension-coordinator").digest())
        manifest["proof"] = coordinator.sign_domain(
            "content", scorer._content_digest(manifest["content"])
        )
        _write_json(manifest_path, manifest)
    elif attack == "packet-carried-context":
        context = evidence / "context.json"
        _write_json(context, {
            "profile": "bulla.inference-clearing-comprehension-context/0.1",
            "accepted_coordinator": "did:key:zpacket-carried",
        })
    with pytest.raises((scorer.GateError, OSError, KeyError, TypeError)):
        scorer.score(evidence, context)


def test_source_only_distribution_policy_is_explicit() -> None:
    policy = json.loads((ROOT / "distribution-policy.json").read_text())
    assert "bulla.experimental.inference_clearing" in policy["source_only_modules"]
    assert "src/bulla/experimental/inference_clearing.py" in policy["forbidden_sdist_prefixes"]
    assert "spec/inference-clearing/" in policy["forbidden_sdist_prefixes"]
    assert "examples/inference-clearing/" in policy["forbidden_sdist_prefixes"]
    assert "tests/test_inference_clearing.py" in policy["forbidden_sdist_prefixes"]
