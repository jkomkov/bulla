"""Routed Inference Profile v0.1 — independent checker and fixture contract."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from zipfile import ZipFile

import pytest

from bulla.action_receipt import (
    ActionReceipt, ActionReceiptError, sign_action_receipt, verify_receipt,
)
from bulla.reliance import PRAGMATIC_RELIANCE_POLICY, verify_reliance


SPEC = Path(__file__).resolve().parents[1] / "spec"
VECTORS = SPEC / "routed-inference-vectors"
EXPECTED = json.loads((VECTORS / "expected.json").read_text())
ROUTED_BUNDLE_AVAILABLE = (
    SPEC / "dist/routed-inference-profile-v0.1-draft.zip"
).is_file() or (
    SPEC.parents[1] / "glyph/public/downloads/routed-inference-profile-v0.1-draft.zip"
).is_file()

module_spec = importlib.util.spec_from_file_location("routed_inference_check", VECTORS / "check.py")
assert module_spec and module_spec.loader
checker = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(checker)

generator_spec = importlib.util.spec_from_file_location(
    "routed_inference_generate", VECTORS / "generate.py"
)
assert generator_spec and generator_spec.loader
generator = importlib.util.module_from_spec(generator_spec)
generator_spec.loader.exec_module(generator)

SIGNERS = {
    signer.verification_method: signer
    for signer in (
        generator.HARNESS, generator.ROUTER, generator.PROVIDER_A,
        generator.PROVIDER_B, generator.RELIER,
    )
}


def _resign(receipt: dict, mutate) -> dict:
    changed = copy.deepcopy(receipt)
    mutate(changed)
    signer = SIGNERS[receipt["signature"]["issuer"]]
    try:
        parsed = ActionReceipt.from_dict(changed)
    except ActionReceiptError:
        # Some malformed actions are rejected before signing. The raw hostile
        # object must still fail closed at the standalone checker boundary.
        return changed
    return sign_action_receipt(
        replace(parsed, signature=None, authorization=None), signer
    ).to_dict()


@pytest.mark.parametrize("name,want", sorted(EXPECTED.items()))
def test_profile_trace_matches_independent_expected(name, want):
    bundle = json.loads((VECTORS / name).read_text())
    assert checker.check_bundle(bundle) == want


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_profile_node_is_an_ordinary_valid_action_receipt(name):
    bundle = json.loads((VECTORS / name).read_text())
    for receipt in bundle["receipts"]:
        verdict = verify_receipt(receipt)
        assert verdict.ok is True, (name, receipt["action"]["type"], verdict.reasons)
        assert verdict.verified_to == "attestation"


@pytest.mark.parametrize("name", [name for name in sorted(EXPECTED) if not name.startswith("10-")])
def test_profile_reliance_receipt_authenticates_and_recomputes(name):
    bundle = json.loads((VECTORS / name).read_text())
    relied = next(r for r in bundle["receipts"] if r["action"]["type"] == "bulla.rely")
    delivery_ref = relied["action"]["subject"]["relied_on"]
    delivery = next(
        r for r in bundle["receipts"]
        if r["action"]["type"] == "inference.delivery"
        and r["hashes"]["event"] == delivery_ref["event"]
        and r["hashes"]["attestation"] == delivery_ref["attestation"]
    )
    report = verify_reliance(relied, delivery, PRAGMATIC_RELIANCE_POLICY)
    assert report.ok is True


def test_profile_manifest_is_evidence_derived():
    status = json.loads((SPEC / "routed-inference-profile-status.json").read_text())
    traces = list(VECTORS.glob("[0-9][0-9]-*.json"))
    assert status["profile_id"] == checker.PROFILE
    assert status["status"] == "draft"
    assert status["schema_version"] == 2
    assert status["trace_count"] == len(traces) == 14
    assert status["traces_local"] is True
    assert status["route_topology"] == "single_route_single_provider"
    assert status["term_disclosure"] == "full"
    assert status["discharge_support"] is False
    assert status["verification_depth"] == "identity"
    assert status["action_chain"] == list(checker.ACTION_ORDER)
    assert status["live_provider_integration"] is False
    assert status["settlement_adapter"] is False
    assert status["external_reproductions"] == 0
    assert status["independent_action_receipt_witnesses"] == 0


def test_receipt_size_report_is_evidence_derived():
    report = json.loads((VECTORS / "size-report.json").read_text())
    traces = sorted(VECTORS.glob("[0-9][0-9]-*.json"))
    compact = []
    pretty = []
    for path in traces:
        for receipt in json.loads(path.read_text())["receipts"]:
            compact.append(len(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()))
            pretty.append(len((json.dumps(receipt, indent=2) + "\n").encode()))

    def summary(values):
        ordered = sorted(values)
        return {
            "min": ordered[0],
            "median": int(statistics.median(ordered)),
            "p95_nearest_rank": ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)],
            "max": ordered[-1],
        }

    assert report["trace_count"] == len(traces) == 14
    assert report["receipt_sample_count"] == len(compact) == 69
    assert report["compact_bytes"] == summary(compact)
    assert report["pretty_printed_bytes"] == summary(pretty)


def test_malformed_settlement_evidence_never_upgrades_depth():
    bundle = json.loads((VECTORS / "01-honest-balanced.json").read_text())
    bundle["settlement_evidence"] = ["self-asserted-placeholder"]
    result = checker.check_bundle(bundle)
    assert result["settlement_depth"] == "SETTLEMENT_UNVERIFIED"


def test_unsigned_log_heads_do_not_establish_equivocation():
    bundle = json.loads((VECTORS / "12-same-size-log-equivocation.json").read_text())
    for head in bundle["witness"]["heads"]:
        head.pop("signature")
        head["authentic"] = True
    result = checker.check_bundle(bundle)
    assert result["outcome"] == "VIOLATES"
    assert "LOG_EQUIVOCATION" not in result["fault_codes"]
    assert "WITNESS_HEAD_INVALID" in result["fault_codes"]


def test_signed_but_non_action_receipt_shape_is_rejected():
    """Fields excluded from the digest still belong to the ActionReceipt wire.

    Removing one must not leave a profile-conforming object merely because the
    existing signatures and four hashes still verify.
    """
    bundle = json.loads((VECTORS / "01-honest-balanced.json").read_text())
    bundle["receipts"][0].pop("stake")
    result = checker.check_bundle(bundle)
    assert result["outcome"] == "VIOLATES"
    assert "RECEIPT_INTEGRITY_INVALID" in result["fault_codes"]


def test_every_action_carries_conserved_slot_and_term_root():
    for name in EXPECTED:
        bundle = json.loads((VECTORS / name).read_text())
        for receipt in bundle["receipts"]:
            action = receipt["action"]
            assert action["slot_id"] == "slot:routed-inference-0001"
            if action["term_root"] != bundle["term_root"]:
                assert "TERM_ROOT_CHANGED" in EXPECTED[name]["fault_codes"]


def test_checker_runs_from_clean_copy_without_bulla(tmp_path):
    copied = tmp_path / "routed-inference-vectors"
    shutil.copytree(VECTORS, copied)
    source = (copied / "check.py").read_text()
    assert "import bulla" not in source and "from bulla" not in source
    run = subprocess.run(
        [sys.executable, "check.py"], cwd=copied, text=True, capture_output=True, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "14/14 routed-inference traces" in run.stdout


def test_single_trace_cli_exit_codes_and_json():
    honest = subprocess.run(
        [sys.executable, str(VECTORS / "check.py"), "verify",
         str(VECTORS / "01-honest-balanced.json"), "--json"],
        text=True, capture_output=True, check=False,
    )
    assert honest.returncode == 0, honest.stdout + honest.stderr
    assert json.loads(honest.stdout)["outcome"] == "CONFORMS"

    hostile = subprocess.run(
        [sys.executable, str(VECTORS / "check.py"), "verify",
         str(VECTORS / "14-attempted-discharge.json"), "--json"],
        text=True, capture_output=True, check=False,
    )
    assert hostile.returncode == 2
    assert json.loads(hostile.stdout)["fault_codes"] == ["DISCHARGE_UNSUPPORTED"]

    bad = subprocess.run(
        [sys.executable, str(VECTORS / "check.py"), "verify"],
        text=True, capture_output=True, check=False,
    )
    assert bad.returncode == 64


def test_missing_optional_crypto_degrades_authority_dimensions(tmp_path):
    copied = tmp_path / "routed-inference-vectors"
    shutil.copytree(VECTORS, copied)
    run = subprocess.run(
        [sys.executable, "-S", "check.py", "verify", "01-honest-balanced.json", "--json"],
        cwd=copied, text=True, capture_output=True, check=False,
    )
    assert run.returncode == 3, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["outcome"] == "UNDETERMINED"
    assert report["verification_depth"] == "digest"
    assert report["answerability_coverage"] == "UNDETERMINED"
    assert report["recourse_conveyance"] == "UNDETERMINED"
    assert report["recourse_reachability"] == "UNVERIFIED"


def test_every_required_profile_action_field_fails_closed_when_resigned():
    original = json.loads((VECTORS / "01-honest-balanced.json").read_text())
    for index, receipt in enumerate(original["receipts"]):
        for field in sorted(receipt["action"]):
            changed = copy.deepcopy(original)
            changed["receipts"][index] = _resign(
                receipt, lambda value, field=field: value["action"].pop(field)
            )
            assert checker.check_bundle(changed)["outcome"] != "CONFORMS", (
                receipt["action"]["type"], field
            )
        for field in sorted(receipt["action"]["subject"]):
            changed = copy.deepcopy(original)
            changed["receipts"][index] = _resign(
                receipt,
                lambda value, field=field: value["action"]["subject"].pop(field),
            )
            assert checker.check_bundle(changed)["outcome"] != "CONFORMS", (
                receipt["action"]["type"], f"subject.{field}"
            )


@pytest.mark.parametrize("half", ["event", "attestation"])
def test_each_receipt_ref_half_is_independently_binding_when_resigned(half):
    original = json.loads((VECTORS / "01-honest-balanced.json").read_text())
    for index, receipt in enumerate(original["receipts"][1:], start=1):
        changed = copy.deepcopy(original)

        def mutate(value):
            value["action"]["parents"][0][half] = "sha256:" + "0" * 64

        changed["receipts"][index] = _resign(receipt, mutate)
        result = checker.check_bundle(changed)
        assert result["outcome"] == "VIOLATES"
        assert "ORPHANED_TRANSITION" in result["fault_codes"]


@pytest.mark.parametrize(
    "action_type",
    ["inference.order", "inference.route", "inference.accept", "inference.delivery", "bulla.rely"],
)
def test_duplicate_constrained_actions_fail_closed(action_type):
    bundle = json.loads((VECTORS / "01-honest-balanced.json").read_text())
    receipt = next(r for r in bundle["receipts"] if r["action"]["type"] == action_type)
    bundle["receipts"].append(copy.deepcopy(receipt))
    assert checker.check_bundle(bundle)["outcome"] == "VIOLATES"


def test_authentic_different_size_heads_are_normal():
    bundle = json.loads((VECTORS / "01-honest-balanced.json").read_text())

    def head(size, label):
        statement = {
            "operator": generator.WITNESS.verification_method,
            "tree_size": size,
            "root": generator._hash(label),
        }
        return {
            **statement,
            "signature": generator.WITNESS.sign(generator.definition_hash(statement)),
        }

    bundle["witness"] = {
        "status": "equivocated",
        "heads": [head(10, "root:ten"), head(11, "root:eleven")],
        "consistency_proofs": [{"verified": True}],
    }
    result = checker.check_bundle(bundle)
    assert result["outcome"] == "CONFORMS"
    assert "LOG_EQUIVOCATION" not in result["fault_codes"]


def test_taxonomy_covers_every_canonical_fault_code():
    taxonomy = json.loads((VECTORS / "violation-taxonomy.json").read_text())
    covered = {
        fault
        for invariant in taxonomy["invariants"]
        for fault in invariant["fault_codes"]
    }
    expected_faults = {
        fault for report in EXPECTED.values() for fault in report["fault_codes"]
    }
    assert expected_faults <= covered


@pytest.mark.parametrize(
    "mutation",
    [
        lambda terms: terms.pop("route_topology"),
        lambda terms: terms.__setitem__("route_topology", "dag"),
        lambda terms: terms.__setitem__("term_disclosure", "selective"),
        lambda terms: terms["process_constraints"].__setitem__("max_route_depth", 2),
        lambda terms: terms.__setitem__("unknown_clause", True),
    ],
)
def test_term_document_boundary_is_closed(mutation):
    bundle = json.loads((VECTORS / "01-honest-balanced.json").read_text())
    mutation(bundle["term_document"])
    result = checker.check_bundle(bundle)
    assert result["outcome"] == "VIOLATES"
    assert "TERM_DOCUMENT_MALFORMED" in result["fault_codes"]


def test_selection_and_ledger_inner_fields_fail_closed_when_resigned():
    original = json.loads((VECTORS / "01-honest-balanced.json").read_text())
    for action_type in ("inference.route", "inference.delivery"):
        index, receipt = next(
            (index, receipt) for index, receipt in enumerate(original["receipts"])
            if receipt["action"]["type"] == action_type
        )
        for field in sorted(receipt["action"]["subject"]["selection"]):
            changed = copy.deepcopy(original)
            changed["receipts"][index] = _resign(
                receipt,
                lambda value, field=field: value["action"]["subject"]["selection"].pop(field),
            )
            result = checker.check_bundle(changed)
            assert result["outcome"] == "VIOLATES"
            assert "SELECTION_MALFORMED" in result["fault_codes"]

    for action_type in ("inference.route", "inference.accept"):
        index, receipt = next(
            (index, receipt) for index, receipt in enumerate(original["receipts"])
            if receipt["action"]["type"] == action_type
        )
        for field in sorted(receipt["action"]["subject"]["budget_ledger"]):
            changed = copy.deepcopy(original)
            changed["receipts"][index] = _resign(
                receipt,
                lambda value, field=field: value["action"]["subject"]["budget_ledger"].pop(field),
            )
            result = checker.check_bundle(changed)
            assert result["outcome"] != "CONFORMS"
            assert result["accounting_depth"] == "ACCOUNTING_UNDETERMINED"


def test_novation_reference_cannot_excuse_discharge():
    bundle = json.loads((VECTORS / "14-attempted-discharge.json").read_text())
    route = next(r for r in bundle["receipts"] if r["action"]["type"] == "inference.route")
    route["action"]["subject"]["novation_ref"] = "sha256:" + "1" * 64
    result = checker.check_bundle(bundle)
    assert result["outcome"] == "VIOLATES"
    assert "DISCHARGE_UNSUPPORTED" in result["fault_codes"]


@pytest.mark.parametrize(
    "fault",
    ["none", "provider-substitution", "budget-overrun"],
)
def test_local_handoff_demo_reproduces_expected_result(fault, tmp_path):
    demo = SPEC.parent / "examples" / "routed-inference" / "run_demo.py"
    output = tmp_path / f"{fault}.json"
    command = [sys.executable, str(demo), "--fixture-keys", "--output", str(output)]
    if fault != "none":
        command.extend(["--fault", fault])
    run = subprocess.run(
        command, cwd=SPEC.parent, text=True, capture_output=True, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(output.read_text())
    expected = "CONFORMS" if fault == "none" else "VIOLATES"
    assert result["report"]["outcome"] == expected
    assert result["report"]["recourse_reachability"] == "UNVERIFIED"


@pytest.mark.skipif(
    not ROUTED_BUNDLE_AVAILABLE,
    reason="the checked distribution bundle is intentionally outside the standalone release inventory",
)
def test_reproduction_bundle_is_deterministic_complete_and_zero_bulla(tmp_path):
    build = SPEC / "build_routed_profile_bundle.py"
    run = subprocess.run(
        [sys.executable, str(build), "--check"],
        cwd=SPEC.parent, text=True, capture_output=True, check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    metadata = json.loads(run.stdout)
    archive_path = Path(metadata["bundle"])
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == metadata["sha256"]

    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert not any("generate.py" in name or "actor.py" in name for name in names)
        assert len([name for name in names if Path(name).name[:2].isdigit()]) == 14
        archive.extractall(tmp_path)

    extracted = tmp_path / "routed-inference-profile-v0.1-draft"
    manifest = (extracted / "MANIFEST.sha256").read_text().splitlines()
    for line in manifest:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((extracted / name).read_bytes()).hexdigest() == digest
    source = (extracted / "check.py").read_text()
    assert "import bulla" not in source and "from bulla" not in source
    verify = subprocess.run(
        [sys.executable, "check.py"], cwd=extracted,
        text=True, capture_output=True, check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "14/14 routed-inference traces" in verify.stdout
