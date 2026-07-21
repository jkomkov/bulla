from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy
from bulla.experimental.golden import (
    AnytimeEnvelopeCertificate,
    EconomicEvent,
    EconomicPhase,
    EconomicState,
    GoldenCase,
    GoldenRunReport,
    GoldenSuiteManifest,
    MarginCoordinate,
    MarginDirection,
    MarginPrecision,
    MarginVector,
    ModelExpansionNeighborhood,
    OracleClass,
    OracleCommitment,
    SourceCapture,
    WitnessDiversityPolicy,
    WitnessOperatorProfile,
    apply_economic_event,
    assess_witness_diversity,
    anytime_refines,
    economic_invariants,
    merkle_root,
    mint_golden_receipt,
    stress_closure,
)
from bulla.experimental.invention import SeamProblem, SynthesisStatus, synthesize, verify_package


ROOT = Path(__file__).resolve().parents[1]
D = "sha256:" + "11" * 32


def _envelope() -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority("did:example:golden", "policy:golden@1"),
        bounds=Bounds("golden-suite-v0.1"),
        recourse=Recourse(
            "P30D",
            Forum("https://log.example", "sha256:root"),
            (Remedy("recompute", "python -I verify_golden.py", "golden manifest"),),
        ),
        retention_class="authority-permanent",
        disclosure_class="auditor",
    )


def test_oracle_commitments_are_salted_and_merkle_stable() -> None:
    oracle = {"exit": "REFUSE", "certificate_type": "authority_failure"}
    first = OracleCommitment.create("case-1", oracle, "a" * 32)
    second = OracleCommitment.create("case-2", oracle, "b" * 32)
    assert first.verifies(oracle, "a" * 32)
    assert not first.verifies(oracle, "b" * 32)
    assert first.to_dict() == {"case_id": "case-1", "commitment": first.commitment}
    assert merkle_root((first, second)) == merkle_root((second, first))
    with pytest.raises(ValueError, match="32 bytes"):
        OracleCommitment.create("case-3", oracle, "short")


def test_margin_vector_forbids_scalarization_and_roundtrips() -> None:
    vector = MarginVector(
        (
            MarginCoordinate(
                "reserve_shortfall",
                MarginPrecision.EXACT,
                MarginDirection.ZERO_REQUIRED,
                "microunits",
                0,
                D,
            ),
            MarginCoordinate(
                "minimal_countermodel_size",
                MarginPrecision.UNRESOLVED,
                MarginDirection.HIGHER_IS_SAFER,
                "worlds",
            ),
        )
    )
    assert MarginVector.from_dict(vector.to_dict()) == vector
    with pytest.raises(TypeError, match="non-Boolean"):
        bool(vector)


def test_witness_diversity_is_relative_to_profiles_and_fails_correlation() -> None:
    common = dict(
        controlling_entity="Acme",
        key_custodian="Acme KMS",
        infrastructure_provider="Cloud A",
        software_lineage="bulla-checkpoint/1",
        jurisdiction="US-CA",
        anchor_domain="log.acme.example",
        attestation_refs=(D,),
    )
    left = WitnessOperatorProfile(operator_id="did:example:left", **common)
    correlated = WitnessOperatorProfile(operator_id="did:example:right", **common)
    independent = WitnessOperatorProfile(
        operator_id="did:example:other",
        controlling_entity="Other Cooperative",
        key_custodian="Other HSM",
        infrastructure_provider="Cloud B",
        software_lineage="independent-checker/1",
        jurisdiction="DE",
        anchor_domain="witness.other.example",
        attestation_refs=("sha256:" + "22" * 32,),
    )
    policy = WitnessDiversityPolicy(
        ("controlling_entity", "key_custodian", "infrastructure_provider"), 5
    )
    failed = assess_witness_diversity(left, correlated, policy)
    passed = assess_witness_diversity(left, independent, policy)
    assert not failed.passes
    assert passed.passes
    assert passed.claim_boundary == "relative-to-attested-operator-profiles"


def test_closure_stress_distinguishes_declared_expansion_from_epoch_change() -> None:
    neighborhood = ModelExpansionNeighborhood(
        D,
        {"kind": "bounded-outcome-expansion", "version": "1"},
        ("undeclared private state",),
        32,
        {"term": "delivery"},
    )
    inside = stress_closure(
        neighborhood,
        base_outcomes=("CUSTODY",),
        expanded_outcomes=("CUSTODY", "DISPATCH"),
        losses_microunits={"CUSTODY": 0, "DISPATCH": 1_000_000},
        held_reserve_microunits=1_100_000,
        model_risk_buffer_microunits=100_000,
        within_declared_neighborhood=True,
        was_finalized=True,
    )
    outside = dataclasses.replace(
        inside,
        within_declared_neighborhood=False,
        new_epoch_required=True,
        term_stale=True,
    )
    assert inside.closure_breach and inside.reserve_shortfall_microunits == 0
    assert not inside.new_epoch_required
    assert outside.new_epoch_required and outside.term_stale


def test_anytime_envelopes_only_grow_certified_terminal_regions() -> None:
    residual = ("sha256:" + "33" * 32, "sha256:" + "44" * 32)
    prior = AnytimeEnvelopeCertificate(D, (), (), residual, {"budget": 1}, True, "unresolved")
    next_ = AnytimeEnvelopeCertificate(
        D,
        (residual[0],),
        (),
        (residual[1],),
        {"budget": 2},
        True,
        "unresolved",
    )
    assert anytime_refines(prior, next_)
    assert not anytime_refines(next_, prior)
    with pytest.raises(ValueError, match="incomplete model enumeration"):
        AnytimeEnvelopeCertificate(D, (residual[0],), (), (), {}, False, "unresolved")


def test_economic_reference_model_fails_closed_and_finalizes_fair_trace() -> None:
    state = EconomicState(required_reserve_microunits=1_100_000, expiry_step=20)
    short = apply_economic_event(
        state, EconomicEvent("EXECUTE", epoch=0), step=1
    )
    assert not short.accepted and short.next_state == state
    events = (
        EconomicEvent("LOCK", 1_100_000, 0),
        EconomicEvent("EXECUTE", epoch=0),
        EconomicEvent("REFINE", 100_000, 0, authorized=True),
        EconomicEvent("RELEASE", 1_000_000, 0, authorized=True),
        EconomicEvent("REFINE", 0, 0, authorized=True),
        EconomicEvent("RELEASE", 100_000, 0, authorized=True),
        EconomicEvent("FINALIZE", epoch=0, authorized=True, closure_permitted=True),
    )
    for step, event in enumerate(events, start=1):
        transition = apply_economic_event(state, event, step=step)
        assert transition.accepted, transition.cause
        state = transition.next_state
        assert economic_invariants(state) == ()
    assert state.phase is EconomicPhase.FINALIZED
    assert state.released_microunits == state.locked_microunits


def test_full_minterm_fallback_compiles_and_independent_checker_accepts() -> None:
    corpus = json.loads((ROOT / "bench/invention/corpus.json").read_text())
    problem = SeamProblem.from_dict(corpus["instances"][0]["problem"])
    problem = dataclasses.replace(
        problem,
        synthesis_policy=dataclasses.replace(problem.synthesis_policy, max_candidate_atoms=1),
    )
    result = synthesize(problem)
    assert result.status in {SynthesisStatus.COMPILED, SynthesisStatus.PARTIAL, SynthesisStatus.ESCALATE}
    assert result.status is not SynthesisStatus.INDETERMINATE
    if result.package is not None:
        report = verify_package(problem, result.package)
        assert report.receipt_binding.value == "pass"
        assert report.minimality.value == "unresolved"


def test_manifest_case_report_and_receipt_preserve_captive_boundary() -> None:
    case = GoldenCase(
        "F1-0001",
        "F1",
        OracleClass.MACHINE,
        (D,),
        "unsafe acceptance falsifies the suite",
        ("reserve_shortfall",),
        {"time_ms": 1000},
        {"kind": "synthetic-adversarial"},
        "design",
    )
    manifest = GoldenSuiteManifest(
        "0.1",
        "30619618ed74c134aa94cbf7c6f5f8ef440df460",
        "cbaa41da",
        "candidate-uncommitted",
        {"F1": case.case_hash},
        D,
        case.case_hash,
        D,
        {"reference": D},
        ({"os": "linux", "python": "3.12", "backend": "reference"},),
    )
    report = GoldenRunReport(manifest.manifest_hash, "reference", {"os": "linux"}, (), D)
    assert report.to_dict()["evidence_status"] == "internally-verified/captive"
    with pytest.raises(ValueError, match="external validation"):
        GoldenRunReport(manifest.manifest_hash, "reference", {}, (), D, external_validation=True)
    receipt = mint_golden_receipt(
        "bulla.golden.freeze",
        subject={"manifest_hash": manifest.manifest_hash},
        artifact_hash=manifest.manifest_hash,
        envelope=_envelope(),
        timestamp="2026-07-18T00:00:00Z",
        producer={"bulla_version": "0.44.0", "profile": "golden/0.1"},
    )
    assert receipt.action["type"] == "bulla.golden.freeze"


def test_source_capture_rejects_executed_third_party_code() -> None:
    capture = SourceCapture(
        "github",
        "modelcontextprotocol",
        "https://example.invalid/schema.json",
        D,
        "2026-07-18T00:00:00Z",
        "schema-only-http",
        "redistributable",
        "bulla.api_registry/0.1",
        True,
    )
    assert capture.to_dict()["executed_code"] is False
    with pytest.raises(ValueError, match="never execute"):
        dataclasses.replace(capture, executed_code=True)


def test_cleanroom_packet_verifies_without_importing_bulla(tmp_path: Path) -> None:
    packet = ROOT / "bench/golden/v0.1/packets/golden-cleanroom.zip"
    with zipfile.ZipFile(packet) as archive:
        archive.extractall(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-I", str(tmp_path / "verify_golden.py"), str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert json.loads(completed.stdout)["evidence_status"] == "internally-verified/captive"
