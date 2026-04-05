"""Sprint 30 tests: contradiction detection, receipt integration, backward compat."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla import (
    BoundaryObligation,
    ContradictionReport,
    ContradictionSeverity,
    ObligationVerdict,
    ProbeResult,
    verify_receipt_integrity,
)
from bulla.repair import (
    ConvergenceResult,
    RepairResult,
    detect_contradictions,
    detect_contradictions_across,
    detect_expected_value_contradictions,
)
from bulla.model import Composition, ToolSpec, Edge, SemanticDimension


RECEIPTS_DIR = Path(__file__).parent.parent / "examples" / "canonical-demo" / "receipts"
RECEIPT_V029 = RECEIPTS_DIR / "audit_receipt.json"
RECEIPT_V030 = RECEIPTS_DIR / "audit_receipt_v030.json"


def _make_pack(dims: dict[str, list[str]], sources: dict[str, list[str]] | None = None) -> dict:
    """Build a minimal discovered pack dict for testing."""
    dimensions = {}
    for dim_name, values in dims.items():
        src = (sources or {}).get(dim_name, [])
        dimensions[dim_name] = {
            "known_values": values,
            "provenance": {"source_tools": src},
        }
    return {"dimensions": dimensions}


def _make_probe(
    tool: str, dimension: str, value: str,
    verdict: ObligationVerdict = ObligationVerdict.CONFIRMED,
    expected_value: str = "",
) -> ProbeResult:
    return ProbeResult(
        obligation=BoundaryObligation(
            placeholder_tool=tool,
            dimension=dimension,
            field=f"{dimension}_field",
            expected_value=expected_value,
        ),
        verdict=verdict,
        evidence=f"{tool} uses {value}",
        convention_value=value,
    )


_DUMMY_COMP = Composition(
    name="dummy",
    tools=(ToolSpec("a", ("x",), ("x",)),),
    edges=(),
)


def _make_convergence_result(probes: tuple[ProbeResult, ...]) -> ConvergenceResult:
    """Build a minimal ConvergenceResult with the given probes in one round."""
    return ConvergenceResult(
        rounds=(RepairResult(
            original_fee=1,
            repaired_fee=0,
            fee_delta=1,
            probes=probes,
            confirmed_count=len(probes),
            repaired_comp=_DUMMY_COMP,
            remaining_obligations=(),
        ),),
        converged=True,
        final_comp=_DUMMY_COMP,
        final_fee=0,
        total_confirmed=len(probes),
        total_denied=0,
        total_uncertain=0,
        termination_reason="fee_zero",
    )


# ── detect_contradictions from pack ──────────────────────────────────


class TestDetectContradictions:
    def test_mismatch_detected(self):
        pack = _make_pack(
            {"path": ["absolute", "relative"]},
            {"path": ["fs", "gh"]},
        )
        reports = detect_contradictions(pack)
        assert len(reports) == 1
        assert reports[0].dimension == "path"
        assert reports[0].severity == ContradictionSeverity.MISMATCH
        assert "absolute" in reports[0].values
        assert "relative" in reports[0].values

    def test_no_contradiction_single_value(self):
        pack = _make_pack({"path": ["absolute"]}, {"path": ["fs"]})
        assert detect_contradictions(pack) == ()

    def test_empty_pack(self):
        assert detect_contradictions({"dimensions": {}}) == ()
        assert detect_contradictions({}) == ()

    def test_multiple_dimensions_mixed(self):
        pack = _make_pack(
            {"path": ["absolute", "relative"], "encoding": ["utf8"]},
            {"path": ["fs", "gh"], "encoding": ["fs"]},
        )
        reports = detect_contradictions(pack)
        assert len(reports) == 1
        assert reports[0].dimension == "path"

    def test_canonical_ordering_values(self):
        pack = _make_pack(
            {"path": ["zzz_last", "aaa_first"]},
            {"path": ["server_z", "server_a"]},
        )
        reports = detect_contradictions(pack)
        assert reports[0].values == ("aaa_first", "zzz_last")
        assert reports[0].sources == ("server_a", "server_z")

    def test_three_values(self):
        pack = _make_pack({"enc": ["utf8", "ascii", "latin1"]})
        reports = detect_contradictions(pack)
        assert len(reports) == 1
        assert reports[0].values == ("ascii", "latin1", "utf8")


# ── detect_expected_value_contradictions ─────────────────────────────


class TestExpectedValueContradictions:
    def test_mismatch_with_expected(self):
        probes = (
            _make_probe("fs", "path", "relative", expected_value="absolute"),
        )
        reports = detect_expected_value_contradictions(probes)
        assert len(reports) == 1
        assert reports[0].dimension == "path"
        assert reports[0].values == ("absolute", "relative")
        assert reports[0].severity == ContradictionSeverity.MISMATCH

    def test_no_expected_value(self):
        probes = (_make_probe("fs", "path", "relative"),)
        assert detect_expected_value_contradictions(probes) == ()

    def test_matching_expected_value(self):
        probes = (
            _make_probe("fs", "path", "absolute", expected_value="absolute"),
        )
        assert detect_expected_value_contradictions(probes) == ()

    def test_uncertain_verdict_ignored(self):
        probes = (
            _make_probe(
                "fs", "path", "relative",
                verdict=ObligationVerdict.UNCERTAIN,
                expected_value="absolute",
            ),
        )
        assert detect_expected_value_contradictions(probes) == ()


# ── detect_contradictions_across ─────────────────────────────────────


class TestDetectContradictionsAcross:
    def test_two_convergence_results_conflict(self):
        cr1 = _make_convergence_result((
            _make_probe("fs", "path", "absolute"),
        ))
        cr2 = _make_convergence_result((
            _make_probe("gh", "path", "relative"),
        ))
        reports = detect_contradictions_across(cr1, cr2)
        assert len(reports) == 1
        assert reports[0].dimension == "path"
        assert reports[0].values == ("absolute", "relative")

    def test_two_convergence_results_agree(self):
        cr1 = _make_convergence_result((_make_probe("fs", "path", "absolute"),))
        cr2 = _make_convergence_result((_make_probe("gh", "path", "absolute"),))
        assert detect_contradictions_across(cr1, cr2) == ()


# ── ContradictionReport serialization ────────────────────────────────


class TestContradictionSerialization:
    def test_round_trip(self):
        report = ContradictionReport(
            dimension="path",
            values=("absolute", "relative"),
            sources=("fs", "gh"),
            severity=ContradictionSeverity.MISMATCH,
        )
        d = report.to_dict()
        restored = ContradictionReport.from_dict(d)
        assert restored == report

    def test_to_dict_format(self):
        report = ContradictionReport(
            dimension="enc",
            values=("ascii", "utf8"),
            sources=("s1",),
            severity=ContradictionSeverity.MISMATCH,
        )
        d = report.to_dict()
        assert d["dimension"] == "enc"
        assert d["values"] == ["ascii", "utf8"]
        assert d["sources"] == ["s1"]
        assert d["severity"] == "mismatch"


# ── Receipt with contradictions ──────────────────────────────────────


class TestReceiptWithContradictions:
    def test_receipt_integrity_with_contradictions(self):
        from bulla import witness, diagnose
        from bulla.model import Composition, ToolSpec

        comp = Composition(
            name="test",
            tools=(ToolSpec("a", ("x",), ("x",)),),
            edges=(),
        )
        diag = diagnose(comp)
        contradictions = (ContradictionReport(
            dimension="path",
            values=("absolute", "relative"),
            sources=("fs", "gh"),
            severity=ContradictionSeverity.MISMATCH,
        ),)
        receipt = witness(diag, comp, contradictions=contradictions)
        rd = receipt.to_dict()
        assert "contradictions" in rd
        assert len(rd["contradictions"]) == 1
        assert verify_receipt_integrity(rd)

    def test_receipt_without_contradictions_still_verifies(self):
        from bulla import witness, diagnose

        comp = Composition(
            name="test",
            tools=(ToolSpec("a", ("x",), ("x",)),),
            edges=(),
        )
        diag = diagnose(comp)
        receipt = witness(diag, comp)
        rd = receipt.to_dict()
        assert "contradictions" not in rd
        assert verify_receipt_integrity(rd)


# ── Pre-computed receipt backward compat ─────────────────────────────


class TestPrecomputedReceipts:
    def test_v029_receipt_exists(self):
        assert RECEIPT_V029.exists()

    def test_v029_receipt_integrity(self):
        receipt = json.loads(RECEIPT_V029.read_text())
        assert verify_receipt_integrity(receipt)

    def test_v029_no_contradictions_key(self):
        receipt = json.loads(RECEIPT_V029.read_text())
        assert "contradictions" not in receipt

    def test_v030_receipt_exists(self):
        assert RECEIPT_V030.exists()

    def test_v030_receipt_integrity(self):
        receipt = json.loads(RECEIPT_V030.read_text())
        assert verify_receipt_integrity(receipt)

    def test_v030_has_contradictions(self):
        receipt = json.loads(RECEIPT_V030.read_text())
        assert "contradictions" in receipt
        assert len(receipt["contradictions"]) == 1
        c = receipt["contradictions"][0]
        assert c["dimension"] == "path_convention_match"
        assert "absolute_local" in c["values"]
        assert "relative_repo" in c["values"]
        assert c["severity"] == "mismatch"


# ── Updated demo smoke test ──────────────────────────────────────────


class TestCanonicalDemoV030:
    def test_demo_output_includes_contradictions(self):
        demo_path = (
            Path(__file__).parent.parent
            / "examples" / "canonical-demo" / "run_canonical_demo.py"
        )
        result = subprocess.run(
            [sys.executable, str(demo_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"Demo failed:\n{result.stderr}"
        assert "Contradictions: 1" in result.stdout
        assert "MISMATCH" in result.stdout
        assert "absolute_local vs relative_repo" in result.stdout
