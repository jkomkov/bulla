from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from bulla.experimental.acceptance_contract import (
    AcceptanceContext,
    AcceptanceContractError,
    AcceptanceContractVerificationError,
    AcceptanceReport,
    canonical_hash,
    evaluate_acceptance_bundle,
    parse_acceptance_bundle,
    parse_acceptance_context,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "acceptance-contract"
GENERATED = SPEC / "generated"
CHECKER = SPEC / "check.py"
PYPROJECT = ROOT / "pyproject.toml"
POLICY = ROOT / "distribution-policy.json"


def _context() -> AcceptanceContext:
    return parse_acceptance_context((GENERATED / "context.json").read_bytes())


def _report(scenario: str) -> AcceptanceReport:
    return evaluate_acceptance_bundle(GENERATED / scenario, _context())


def _generator_module():
    spec = importlib.util.spec_from_file_location(
        "acceptance_contract_generate", SPEC / "generate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _variant(tmp_path: Path, scenario: str) -> tuple[Path, AcceptanceContext]:
    module = _generator_module()
    files, context = module._bundle(scenario)
    bundle = tmp_path / scenario
    for relative, raw in files.items():
        destination = bundle / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    return bundle, AcceptanceContext.from_dict(context)


def test_three_canonical_results_are_distinct_and_staged() -> None:
    missing = _report("missing")
    passing = _report("passing")
    failing = _report("failing")
    assert (missing.decision, missing.consequence_eligibility) == (
        "HOLD_FOR_EVIDENCE",
        "INELIGIBLE",
    )
    assert (passing.decision, passing.consequence_eligibility) == (
        "PROCEED",
        "ELIGIBLE",
    )
    assert (failing.decision, failing.consequence_eligibility) == (
        "REFUSE",
        "INELIGIBLE",
    )
    assert passing.authorization == missing.authorization == "NOT_ISSUED"
    assert passing.execution == missing.execution == "NOT_ATTEMPTED"


def test_claim_and_receiver_observation_do_not_change_across_canonical_bundles() -> None:
    subjects = []
    for scenario in ("missing", "passing", "failing"):
        bundle = GENERATED / scenario
        ready = json.loads((bundle / "records/03-staging-ready.json").read_text())
        observation = json.loads((bundle / "reports/receiver-observation.json").read_text())
        subjects.append((ready["action"]["subject"], observation["action"]["subject"]))
    assert subjects[0] == subjects[1] == subjects[2]


def test_missing_evidence_request_is_conditional_and_catalog_bounded() -> None:
    report = _report("missing")
    assert report.conditional_closure_sets == (("rollback-test-001",),)
    assert report.closure_minimality == "INCLUSION_MINIMAL_WITHIN_DECLARED_CATALOG"
    assert len(report.conditional_evidence_requests) == 1
    request = report.conditional_evidence_requests[0]
    assert request["conditional_statement"].startswith(
        "If a newly supplied and verified artifact"
    )
    assert "contract_hash" not in request["required_bindings"]
    assert request["required_bindings"]["contract_revision"] == 1


def test_negative_evidence_never_becomes_a_hold_request() -> None:
    report = _report("failing")
    assert report.decision == "REFUSE"
    assert report.conditional_evidence_requests == ()
    assert report.conditional_closure_sets == ()


def test_ambiguity_without_a_declared_result_escalates(tmp_path: Path) -> None:
    bundle, context = _variant(tmp_path, "ambiguous")
    report = evaluate_acceptance_bundle(bundle, context)
    assert report.decision == "ESCALATE"
    assert report.consequence_eligibility == "CHALLENGE_REQUIRED"
    assert report.conditional_evidence_requests == ()


@pytest.mark.parametrize("scenario", ["uncovered", "stale", "receiver-mismatch"])
def test_negative_receiver_or_correction_fact_refuses(
    tmp_path: Path, scenario: str
) -> None:
    bundle, context = _variant(tmp_path, scenario)
    report = evaluate_acceptance_bundle(bundle, context)
    assert report.decision == "REFUSE"
    assert report.consequence_eligibility == "INELIGIBLE"


def test_unmatched_receiver_effect_blocks_without_breaking_receipts(
    tmp_path: Path,
) -> None:
    bundle, context = _variant(tmp_path, "uncovered")
    report = evaluate_acceptance_bundle(bundle, context)
    coverage = json.loads(
        (bundle / "reports/receiver-coverage.json").read_text()
    )["action"]["subject"]["result"]
    assert report.record_integrity == "VERIFIED"
    assert report.receiver_coverage == "UNCOVERED"
    assert coverage["unmatched_effect_ids"] == ["effect-bypass-001"]
    assert report.decision == "REFUSE"


def test_evidence_removal_cannot_improve_decision(tmp_path: Path) -> None:
    source = GENERATED / "passing"
    bundle = tmp_path / "passing"
    shutil.copytree(source, bundle)
    (bundle / "evidence/rollback-test.json").unlink()
    with pytest.raises(AcceptanceContractError, match="membership"):
        parse_acceptance_bundle(bundle)
    assert _report("missing").decision != "PROCEED"


def test_adding_a_file_does_not_satisfy_a_requirement(tmp_path: Path) -> None:
    bundle = tmp_path / "missing"
    shutil.copytree(GENERATED / "missing", bundle)
    (bundle / "evidence").mkdir()
    shutil.copy2(
        GENERATED / "passing/evidence/rollback-test.json",
        bundle / "evidence/rollback-test.json",
    )
    with pytest.raises(AcceptanceContractError, match="membership"):
        parse_acceptance_bundle(bundle)


def test_artifact_tampering_fails_manifest_integrity(tmp_path: Path) -> None:
    bundle = tmp_path / "passing"
    shutil.copytree(GENERATED / "passing", bundle)
    target = bundle / "evidence/rollback-test.json"
    target.write_bytes(target.read_bytes().replace(b'"PASS"', b'"FAIL"'))
    with pytest.raises(AcceptanceContractVerificationError, match="commitment"):
        parse_acceptance_bundle(bundle)


def test_packet_carried_or_substituted_trust_cannot_bootstrap() -> None:
    value = _context().to_dict()
    value["accepted_issuers_by_role"]["deploy_agent"] = ["did:key:zRejected"]
    report = evaluate_acceptance_bundle(
        GENERATED / "passing", AcceptanceContext.from_dict(value)
    )
    assert report.decision == "NOT_COMPUTED"
    assert report.consequence_eligibility == "NOT_COMPUTED"


def test_unaccepted_adapter_suppresses_the_decision() -> None:
    value = _context().to_dict()
    value["accepted_adapters"].remove("bulla.rollback-test/0.1")
    report = evaluate_acceptance_bundle(
        GENERATED / "passing", AcceptanceContext.from_dict(value)
    )
    assert report.decision == "NOT_COMPUTED"


def test_policy_revision_changes_identity_and_cannot_rewrite_old_result() -> None:
    original = json.loads((GENERATED / "missing/contract.json").read_text())
    revised = copy.deepcopy(original)
    revised["revision"] = 2
    assert canonical_hash(revised) != canonical_hash(original)
    assert _report("missing").contract_hash == canonical_hash(original)
    assert _report("missing").decision == "HOLD_FOR_EVIDENCE"


def test_reports_reject_boolean_coercion() -> None:
    with pytest.raises(TypeError, match="independent dimensions"):
        bool(_report("passing"))
    with pytest.raises(TypeError, match="named dimensions"):
        bool(_report("passing").requirements[0])


def test_strict_context_parser_rejects_duplicate_keys_and_unsafe_integer() -> None:
    with pytest.raises(AcceptanceContractError, match="duplicate JSON member"):
        parse_acceptance_context(
            b'{"profile":"bulla.acceptance-context/0.1-experimental","profile":"x"}'
        )
    value = _context().to_dict()
    value["authority_epoch"] = 9_007_199_254_740_992
    with pytest.raises(AcceptanceContractError, match="unsafe integer"):
        parse_acceptance_context(json.dumps(value).encode())


def test_bundle_rejects_symlink_and_undeclared_member(tmp_path: Path) -> None:
    bundle = tmp_path / "missing"
    shutil.copytree(GENERATED / "missing", bundle)
    (bundle / "link.json").symlink_to(bundle / "contract.json")
    with pytest.raises(AcceptanceContractError, match="symlink"):
        parse_acceptance_bundle(bundle)
    (bundle / "link.json").unlink()
    (bundle / "extra.json").write_text("{}\n")
    with pytest.raises(AcceptanceContractError, match="membership"):
        parse_acceptance_bundle(bundle)


def test_generated_reports_validate_against_draft_2020_12_schema() -> None:
    schema = json.loads((SPEC / "acceptance-report.schema.json").read_text())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    for scenario in ("missing", "passing", "failing"):
        report = _report(scenario).to_dict()
        assert set(report) == set(schema["required"])
        assert report["decision"] in schema["properties"]["decision"]["enum"]
        assert report["consequence_eligibility"] in schema["properties"][
            "consequence_eligibility"
        ]["enum"]
        expected = json.loads((GENERATED / "expected" / f"{scenario}.json").read_text())
        assert report == expected


def test_contract_and_context_validate_against_published_schemas() -> None:
    contract_schema = json.loads((SPEC / "acceptance-contract.schema.json").read_text())
    context_schema = json.loads((SPEC / "acceptance-context.schema.json").read_text())
    contract_value = json.loads((GENERATED / "missing/contract.json").read_text())
    context_value = _context().to_dict()
    assert contract_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert context_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(contract_value) == set(contract_schema["required"])
    assert set(context_value) == set(context_schema["required"])


def test_checker_completes_for_ineligible_and_eligible_results() -> None:
    context = GENERATED / "context.json"
    for scenario, expected in (
        ("missing", "HOLD_FOR_EVIDENCE"),
        ("passing", "PROCEED"),
        ("failing", "REFUSE"),
    ):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(CHECKER),
                str(GENERATED / scenario),
                "--context",
                str(context),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["decision"] == expected


def test_generator_is_deterministic() -> None:
    result = subprocess.run(
        [sys.executable, str(SPEC / "generate.py"), "--check"],
        check=False,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr


def test_source_only_distribution_boundaries_are_declared() -> None:
    pyproject = PYPROJECT.read_text()
    policy = json.loads(POLICY.read_text())
    assert '"/src/bulla/experimental/acceptance_contract.py"' in pyproject
    assert '"/spec/acceptance-contract"' in pyproject
    assert '"/examples/acceptance-contract"' in pyproject
    assert '"/tests/test_acceptance_contract.py"' in pyproject
    assert "bulla.experimental.acceptance_contract" in policy["source_only_modules"]
    assert "src/bulla/experimental/acceptance_contract.py" in policy["forbidden_sdist_prefixes"]


def test_no_stable_export_or_cli_surface() -> None:
    import bulla
    import bulla.experimental as experimental

    assert not hasattr(bulla, "AcceptanceContract")
    assert not hasattr(experimental, "AcceptanceContract")
    cli = subprocess.run(
        [sys.executable, "-m", "bulla.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "acceptance-contract" not in cli.stdout


def test_version_matches_current_release() -> None:
    import bulla

    assert bulla.__version__ == "0.49.0"
