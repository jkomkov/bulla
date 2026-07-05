"""Tests for the offline consequence analyzer (WS8).

Validates sign test, baseline comparison, and stratification against
both synthetic data and the live adversarial probe grid.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from calibration.harness.adversarial_probe import Consequence, LabelProvenance
from calibration.consequence_analysis import (
    AnalysisReport,
    BaselineComparison,
    ConsequenceStratification,
    ProbeRow,
    SignTestResult,
    analyze,
    beat_the_baseline,
    find_discriminating_pairs,
    load_jsonl,
    sign_test,
    stratify_by_consequence,
)
from calibration.harness.adversarial_probe import (
    default_probes,
    multi_dimension_probes,
    run_probes,
    write_jsonl,
)


# ── Synthetic data helpers ────────────────────────────────────────────────


def _row(
    label: str,
    fee_d: int = 0,
    obs_dist: int = 0,
    consequence: Consequence = Consequence.EXPECTED_CLEAN,
    mismatch: bool = True,
    provenance: LabelProvenance = LabelProvenance.CONSTRUCTED,
) -> ProbeRow:
    return ProbeRow(
        label=label,
        dimension="encoding",
        visible=(obs_dist > 0),
        mismatch=mismatch,
        fee=fee_d,
        fee_d=fee_d,
        observable_distance=obs_dist,
        consequence=consequence,
        error_message=None,
        dropped=False,
        provenance=provenance,
    )


# ── Sign test ─────────────────────────────────────────────────────────────


def test_sign_test_perfect_concordance():
    """All pairs concordant: high fee_d fails, low doesn't."""
    rows = [
        _row("a", fee_d=1, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION),
        _row("b", fee_d=0, obs_dist=0, consequence=Consequence.CORRECT_PASS),
    ]
    result = sign_test(rows)
    assert result.concordant == 1
    assert result.discordant == 0
    assert result.tied == 0
    assert result.p_value <= 0.5


def test_sign_test_perfect_discordance():
    """All pairs discordant: low fee_d fails, high doesn't."""
    rows = [
        _row("a", fee_d=1, obs_dist=0, consequence=Consequence.CORRECT_PASS),
        _row("b", fee_d=0, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION),
    ]
    result = sign_test(rows)
    assert result.discordant == 1
    assert result.concordant == 0


def test_sign_test_tied():
    """Both fail or both pass: tied."""
    rows = [
        _row("a", fee_d=1, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION),
        _row("b", fee_d=0, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION),
    ]
    result = sign_test(rows)
    assert result.tied == 1
    assert result.concordant == 0
    assert result.discordant == 0


def test_sign_test_no_pairs():
    """No discriminating pairs: p=1, not significant."""
    rows = [_row("a", fee_d=1, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION)]
    result = sign_test(rows)
    assert result.n_pairs == 0
    assert result.p_value == 1.0
    assert not result.significant
    assert result.verdict == "no-pairs"


# ── Provenance gate (structural circularity guard) ────────────────────────


def _concordant_pairs(provenance: LabelProvenance, n: int = 6) -> list[ProbeRow]:
    """n perfectly-concordant discriminating pairs (high fee_d fails, low passes),
    all stamped with the given provenance — enough to drive p < 0.05."""
    rows: list[ProbeRow] = []
    for i in range(n):
        rows.append(_row(f"hi{i}", fee_d=1, obs_dist=0,
                         consequence=Consequence.SILENT_CORRUPTION, provenance=provenance))
        rows.append(_row(f"lo{i}", fee_d=0, obs_dist=0,
                         consequence=Consequence.CORRECT_PASS, provenance=provenance))
    return rows


def test_constructed_labels_cannot_be_significant():
    """The antifragility guarantee: even with perfect concordance and p<0.05,
    constructed labels are held to significant=False and a construct-validity
    verdict — circularity is structurally impossible to misreport."""
    rows = _concordant_pairs(LabelProvenance.CONSTRUCTED)
    result = sign_test(rows)
    assert result.concordant == 6 and result.discordant == 0
    assert result.p_value < 0.05  # the raw statistic IS extreme
    assert result.fee_independent is False
    assert result.significant is False  # ...but the gate holds it False
    assert result.verdict == "construct-validity-only"


def test_execution_independent_labels_unlock_significance():
    """Identical data relabeled EXECUTION_INDEPENDENT flips to a genuine
    falsification verdict — the gate is provenance, not the numbers."""
    rows = _concordant_pairs(LabelProvenance.EXECUTION_INDEPENDENT)
    result = sign_test(rows)
    assert result.p_value < 0.05
    assert result.fee_independent is True
    assert result.significant is True
    assert result.verdict == "falsification-test"


def test_mixed_provenance_defaults_to_construct_validity():
    """A single constructed pair in the set disqualifies the whole run —
    independence must hold for every pair, not on average."""
    rows = _concordant_pairs(LabelProvenance.EXECUTION_INDEPENDENT, n=5)
    rows += [
        _row("c_hi", fee_d=1, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION,
             provenance=LabelProvenance.CONSTRUCTED),
        _row("c_lo", fee_d=0, obs_dist=0, consequence=Consequence.CORRECT_PASS,
             provenance=LabelProvenance.CONSTRUCTED),
    ]
    result = sign_test(rows)
    assert result.fee_independent is False
    assert result.significant is False
    assert result.verdict == "construct-validity-only"


def test_unstamped_jsonl_defaults_to_constructed(tmp_path: Path):
    """A legacy/hand-written JSONL row without a provenance field must default
    to CONSTRUCTED — independence is asserted, never assumed."""
    line = json.dumps({
        "label": "legacy", "dimension": "encoding", "visible": False,
        "mismatch": True, "fee": 1, "fee_d": 1, "observable_distance": 0,
        "consequence": "silent_corruption", "error_message": None, "dropped": False,
        # no "provenance" key
    })
    p = tmp_path / "legacy.jsonl"
    p.write_text(line + "\n")
    rows = load_jsonl(p)
    assert rows[0].provenance == LabelProvenance.CONSTRUCTED


def test_sign_test_filters_non_mismatch():
    """Non-mismatch probes are excluded from pair formation."""
    rows = [
        _row("a", fee_d=1, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION, mismatch=True),
        _row("b", fee_d=0, obs_dist=0, consequence=Consequence.EXPECTED_CLEAN, mismatch=False),
    ]
    result = sign_test(rows)
    assert result.n_pairs == 0


# ── Baseline comparison ──────────────────────────────────────────────────


def test_baseline_fee_d_beats_hamming():
    """fee_d should outperform Hamming on hidden mismatches."""
    rows = [
        # Hidden mismatch: Hamming=0 (predicts no fail), fee_d=1 (predicts fail), actually fails
        _row("hid_mis", fee_d=1, obs_dist=0, consequence=Consequence.SILENT_CORRUPTION),
        # Visible mismatch: Hamming=1 (predicts fail), fee_d=0 (predicts no fail), doesn't fail
        _row("vis_mis", fee_d=0, obs_dist=1, consequence=Consequence.CORRECT_PASS),
    ]
    result = beat_the_baseline(rows)
    assert result.fee_d_accuracy > result.hamming_accuracy
    assert result.delta_accuracy > 0


def test_baseline_empty():
    result = beat_the_baseline([])
    assert result.n == 0


# ── Consequence stratification ──────────────────────────────────────────


def test_stratification_counts():
    rows = [
        _row("a", consequence=Consequence.SILENT_CORRUPTION),
        _row("b", consequence=Consequence.SILENT_CORRUPTION),
        _row("c", consequence=Consequence.GUARDED_REJECT),
        _row("d", consequence=Consequence.CORRECT_PASS),
        _row("e", consequence=Consequence.EXPECTED_CLEAN, mismatch=False),
    ]
    s = stratify_by_consequence(rows)
    assert s.n_silent_corruption == 2
    assert s.n_guarded_reject == 1
    assert s.n_correct_pass == 1
    assert s.n_expected_clean == 1


# ── JSONL round-trip ─────────────────────────────────────────────────────


def test_jsonl_round_trip(tmp_path: Path):
    """Write + load JSONL preserves data."""
    from calibration.harness.adversarial_probe import ProbeResult as APResult

    results = [
        APResult("test_a", "encoding", False, True, 1, 1, 0, Consequence.SILENT_CORRUPTION, "err", False),
        APResult("test_b", "index", True, True, 0, 0, 1, Consequence.CORRECT_PASS, None, False),
    ]
    out = tmp_path / "test.jsonl"
    write_jsonl(results, out)
    loaded = load_jsonl(out)
    assert len(loaded) == 2
    assert loaded[0].label == "test_a"
    assert loaded[0].consequence == Consequence.SILENT_CORRUPTION
    assert loaded[1].label == "test_b"
    assert loaded[1].fee_d == 0
    # Provenance survives the round-trip and defaults to CONSTRUCTED for probes.
    assert loaded[0].provenance == LabelProvenance.CONSTRUCTED
    assert loaded[1].provenance == LabelProvenance.CONSTRUCTED


# ── Full analysis on live probes ─────────────────────────────────────────


@pytest.fixture(scope="module")
def live_report(tmp_path_factory) -> AnalysisReport:
    """Run the full probe grid + analysis pipeline end-to-end."""
    tmp = tmp_path_factory.mktemp("ws8")
    all_probes = default_probes() + multi_dimension_probes()
    results = asyncio.run(run_probes(all_probes))
    out = tmp / "probes.jsonl"
    write_jsonl(results, out)
    rows = load_jsonl(out)
    return analyze(rows)


def test_live_report_no_drops(live_report):
    assert live_report.n_dropped == 0


def test_live_report_fee_d_at_least_ties_hamming(live_report):
    """fee_d accuracy should be >= Hamming accuracy on constructed probes."""
    assert live_report.baseline.delta_accuracy >= 0, (
        f"fee_d underperformed Hamming: delta={live_report.baseline.delta_accuracy}"
    )


def test_live_report_has_consequence_types(live_report):
    """Constructed probes in permissive mode produce silent_corruption,
    correct_pass, and expected_clean. guarded_reject requires strict mode
    (real servers that actually raise)."""
    s = live_report.stratification
    assert s.n_silent_corruption > 0, (
        "silent_corruption must be reachable — it's the dangerous case "
        "the fee exists to flag"
    )
    assert s.n_correct_pass > 0
    assert s.n_expected_clean > 0


def test_live_report_serializes(live_report):
    d = live_report.to_dict()
    assert "sign_test" in d
    assert "baseline_comparison" in d
    assert "consequence_stratification" in d
    json.dumps(d)  # must be JSON-serializable


def test_live_report_markdown(live_report):
    md = live_report.to_markdown()
    assert "Sign Test" in md
    assert "Baseline" in md
    assert "Stratification" in md


def test_live_report_markdown_includes_scope_caveat(live_report):
    """The report must state that on constructed backends the sign test
    is plumbing validation, not prediction evidence."""
    md = live_report.to_markdown()
    assert "not independent" in md.lower() or "NOT" in md, (
        "report must flag that labels are not independent of the fee "
        "on the constructed harness"
    )
