"""Offline consequence analyzer — WS8.

Consumes adversarial probe JSONL (from WS7) and computes:
  PRIMARY:  sign test on discriminating pairs (equal Hamming, different fee_d)
  SECONDARY: beat-the-baseline accuracy (fee_d vs Hamming-only prediction)
  STRATIFIED: consequence breakdown

SCOPE — read before citing any number from this analyzer.

    On the constructed seam_backend, failure labels and fee_d are both
    near-deterministic functions of (mismatch, hidden). The sign test
    therefore validates the pipeline plumbing — that fee computation and
    execution outcomes are correctly connected — NOT that the fee predicts
    failure on real MCP servers. Evidence of prediction requires
    execution-derived labels from servers the fee did not help construct.

    This analyzer is designed to also consume probe JSONL from real backends
    (once available), at which point the sign test becomes a genuine
    non-circular falsification test.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from calibration.harness.adversarial_probe import Consequence, LabelProvenance


@dataclass(frozen=True)
class ProbeRow:
    """One row from the adversarial probe JSONL."""

    label: str
    dimension: str
    visible: bool
    mismatch: bool
    fee: int
    fee_d: int
    observable_distance: int
    consequence: Consequence
    error_message: str | None
    dropped: bool
    # Conservative default: an unstamped row is treated as CONSTRUCTED (circular),
    # so a legacy or hand-written JSONL can never accidentally unlock a citable
    # falsification verdict. Independence must be asserted, never assumed.
    provenance: LabelProvenance = LabelProvenance.CONSTRUCTED

    @property
    def is_failure(self) -> bool:
        return self.consequence.is_failure


def load_jsonl(path: Path) -> list[ProbeRow]:
    """Load probe results from JSONL."""
    rows: list[ProbeRow] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            rows.append(
                ProbeRow(
                    label=d["label"],
                    dimension=d["dimension"],
                    visible=d["visible"],
                    mismatch=d["mismatch"],
                    fee=d["fee"],
                    fee_d=d["fee_d"],
                    observable_distance=d["observable_distance"],
                    consequence=Consequence(d["consequence"]),
                    error_message=d.get("error_message"),
                    dropped=d["dropped"],
                    provenance=LabelProvenance(
                        d.get("provenance", LabelProvenance.CONSTRUCTED.value)
                    ),
                )
            )
    return rows


# ── PRIMARY: Sign test on discriminating pairs ────────────────────────────


@dataclass(frozen=True)
class SignTestResult:
    """Result of the discriminating-pairs sign test.

    ``verdict`` is the load-bearing field. ``significant`` is gated by
    provenance: it can be True only when the labels are
    EXECUTION_INDEPENDENT. On constructed data the p-value is still reported
    (the plumbing must demonstrably work) but ``significant`` is forced False
    and ``verdict`` is ``construct-validity-only`` — so no caller can cite a
    constructed run as falsification, even by reading the raw p-value.
    """

    n_pairs: int
    concordant: int  # higher fee_d → more failures (correct direction)
    discordant: int  # higher fee_d → fewer failures (wrong direction)
    tied: int
    p_value: float  # one-sided binomial p-value under H0: p=0.5
    significant: bool  # True only if p<0.05 AND labels are fee-independent
    verdict: str  # "falsification-test" | "construct-validity-only" | "no-pairs"
    fee_independent: bool  # whether every pair's label is execution-independent

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_pairs": self.n_pairs,
            "concordant": self.concordant,
            "discordant": self.discordant,
            "tied": self.tied,
            "p_value": round(self.p_value, 6),
            "significant": self.significant,
            "verdict": self.verdict,
            "fee_independent": self.fee_independent,
        }


def _binomial_cdf(k: int, n: int, p: float = 0.5) -> float:
    """Cumulative binomial probability P(X <= k) for X ~ Binomial(n, p).

    Uses the exact formula for small n (which is our case — probe grids
    are O(10-100) pairs). No scipy dependency.
    """
    if n <= 0:
        return 1.0
    total = 0.0
    for i in range(k + 1):
        # C(n, i) * p^i * (1-p)^(n-i)
        log_c = (
            sum(math.log(n - j) for j in range(i))
            - sum(math.log(j + 1) for j in range(i))
        )
        total += math.exp(log_c + i * math.log(p) + (n - i) * math.log(1 - p))
    return total


def find_discriminating_pairs(
    rows: list[ProbeRow],
) -> list[tuple[ProbeRow, ProbeRow]]:
    """Find disjoint pairs with equal observable Hamming but different fee_d.

    Each pair (a, b) satisfies:
      a.observable_distance == b.observable_distance
      a.fee_d != b.fee_d
      both are non-dropped mismatch probes

    Matching is disjoint: each row participates in at most one pair.
    This prevents inflating n (and understating the p-value) when one
    high-fee_d row would otherwise pair with k low-fee_d rows.
    """
    active = [r for r in rows if not r.dropped and r.mismatch]
    pairs: list[tuple[ProbeRow, ProbeRow]] = []
    by_hamming: dict[int, list[ProbeRow]] = {}
    for r in active:
        by_hamming.setdefault(r.observable_distance, []).append(r)
    for group in by_hamming.values():
        # Split into high-fee and low-fee pools, then match disjointly.
        high = sorted([r for r in group if r.fee_d > 0], key=lambda r: -r.fee_d)
        low = [r for r in group if r.fee_d == 0]
        for h, l in zip(high, low):
            pairs.append((h, l))
    return pairs


def sign_test(rows: list[ProbeRow]) -> SignTestResult:
    """Run the discriminating-pairs sign test.

    For each pair (high_fee, low_fee) with equal Hamming:
      concordant: high_fee is_failure AND low_fee isn't (fee predicts correctly)
      discordant: low_fee is_failure AND high_fee isn't (fee predicts wrongly)
      tied: same outcome for both
    """
    pairs = find_discriminating_pairs(rows)
    concordant = 0
    discordant = 0
    tied = 0
    for high, low in pairs:
        if high.is_failure == low.is_failure:
            tied += 1
        elif high.is_failure and not low.is_failure:
            concordant += 1
        else:
            discordant += 1
    n_untied = concordant + discordant
    # One-sided p-value: P(concordant >= observed | H0: concordant ~ Bin(n, 0.5))
    # = 1 - P(concordant <= observed - 1)
    if n_untied > 0:
        p_value = 1.0 - _binomial_cdf(concordant - 1, n_untied, 0.5)
    else:
        p_value = 1.0

    # Structural circularity gate. A pair only counts as fee-independent if
    # BOTH its rows carry execution-independent labels. If any pair is
    # constructed, the whole test is plumbing validation, not falsification —
    # and we make that impossible to misreport by forcing significant=False
    # regardless of the p-value.
    fee_independent = bool(pairs) and all(
        high.provenance == LabelProvenance.EXECUTION_INDEPENDENT
        and low.provenance == LabelProvenance.EXECUTION_INDEPENDENT
        for high, low in pairs
    )
    if not pairs:
        verdict = "no-pairs"
    elif fee_independent:
        verdict = "falsification-test"
    else:
        verdict = "construct-validity-only"
    significant = (p_value < 0.05) and fee_independent

    return SignTestResult(
        n_pairs=len(pairs),
        concordant=concordant,
        discordant=discordant,
        tied=tied,
        p_value=p_value,
        significant=significant,
        verdict=verdict,
        fee_independent=fee_independent,
    )


# ── SECONDARY: Beat-the-baseline ΔR² ─────────────────────────────────────


@dataclass(frozen=True)
class BaselineComparison:
    """Hamming-only vs fee_d prediction accuracy."""

    n: int
    hamming_accuracy: float  # fraction correctly predicted by Hamming > 0
    fee_d_accuracy: float  # fraction correctly predicted by fee_d > 0
    delta_accuracy: float  # fee_d_accuracy - hamming_accuracy

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "hamming_accuracy": round(self.hamming_accuracy, 4),
            "fee_d_accuracy": round(self.fee_d_accuracy, 4),
            "delta_accuracy": round(self.delta_accuracy, 4),
        }


def beat_the_baseline(rows: list[ProbeRow]) -> BaselineComparison:
    """Compare Hamming-only vs fee_d prediction of failure.

    Hamming predicts: fail if observable_distance > 0
    fee_d predicts: fail if fee_d > 0
    """
    active = [r for r in rows if not r.dropped and r.mismatch]
    if not active:
        return BaselineComparison(0, 0.0, 0.0, 0.0)
    hamming_correct = 0
    fee_correct = 0
    for r in active:
        hamming_pred = r.observable_distance > 0
        fee_pred = r.fee_d > 0
        actual = r.is_failure
        if hamming_pred == actual:
            hamming_correct += 1
        if fee_pred == actual:
            fee_correct += 1
    n = len(active)
    return BaselineComparison(
        n=n,
        hamming_accuracy=hamming_correct / n,
        fee_d_accuracy=fee_correct / n,
        delta_accuracy=(fee_correct - hamming_correct) / n,
    )


# ── STRATIFIED: Consequence breakdown ────────────────────────────────────


@dataclass(frozen=True)
class ConsequenceStratification:
    """Consequence breakdown."""

    n_guarded_reject: int
    n_silent_corruption: int
    n_correct_pass: int
    n_expected_clean: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "guarded_reject": self.n_guarded_reject,
            "silent_corruption": self.n_silent_corruption,
            "correct_pass": self.n_correct_pass,
            "expected_clean": self.n_expected_clean,
        }


def stratify_by_consequence(rows: list[ProbeRow]) -> ConsequenceStratification:
    """Count probes by consequence category."""
    active = [r for r in rows if not r.dropped]
    return ConsequenceStratification(
        n_guarded_reject=sum(
            1 for r in active if r.consequence == Consequence.GUARDED_REJECT
        ),
        n_silent_corruption=sum(
            1 for r in active if r.consequence == Consequence.SILENT_CORRUPTION
        ),
        n_correct_pass=sum(
            1 for r in active if r.consequence == Consequence.CORRECT_PASS
        ),
        n_expected_clean=sum(
            1 for r in active if r.consequence == Consequence.EXPECTED_CLEAN
        ),
    )


# ── Full report ───────────────────────────────────────────────────────────


@dataclass
class AnalysisReport:
    """Complete analysis report from probe results."""

    sign_test: SignTestResult
    baseline: BaselineComparison
    stratification: ConsequenceStratification
    n_probes: int
    n_dropped: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_probes": self.n_probes,
            "n_dropped": self.n_dropped,
            "sign_test": self.sign_test.to_dict(),
            "baseline_comparison": self.baseline.to_dict(),
            "consequence_stratification": self.stratification.to_dict(),
        }

    def _verdict_prose(self) -> str:
        """Caveat text derived from the machine verdict, so prose cannot drift
        from the structural gate."""
        if self.sign_test.verdict == "falsification-test":
            return (
                "**Scope:** labels are execution-independent of the fee, so this "
                "is a genuine non-circular falsification test — a significant "
                "result is evidence the fee predicts failure."
            )
        if self.sign_test.verdict == "no-pairs":
            return (
                "**Scope:** no discriminating pairs were found, so no claim is "
                "made."
            )
        return (
            "**Scope:** at least one pair's label was produced on a constructed "
            "backend, where the failure label and fee_d are both functions of "
            "(mismatch, hidden). This run is *construct-validity only* — it shows "
            "the pipeline correctly connects fee computation to execution "
            "outcomes. It is NOT evidence the fee predicts failure; "
            "`significant` is held False by the provenance gate regardless of "
            "the p-value. Falsification requires EXECUTION_INDEPENDENT labels."
        )

    def to_markdown(self) -> str:
        lines = [
            "# Consequence Analysis Report",
            "",
            f"**Probes:** {self.n_probes} total, {self.n_dropped} dropped",
            "",
            "## PRIMARY: Discriminating-Pairs Sign Test",
            "",
            f"**Verdict: `{self.sign_test.verdict}`** "
            f"(labels fee-independent: {self.sign_test.fee_independent})",
            "",
            self._verdict_prose(),
            "",
            f"- Pairs (disjoint matching): {self.sign_test.n_pairs}",
            f"- Concordant (fee predicts correctly): {self.sign_test.concordant}",
            f"- Discordant (fee predicts wrongly): {self.sign_test.discordant}",
            f"- Tied: {self.sign_test.tied}",
            f"- p-value: {self.sign_test.p_value:.4f}",
            f"- Significant (p < 0.05 AND fee-independent): "
            f"{self.sign_test.significant}",
            "",
            "## SECONDARY: Beat-the-Baseline",
            "",
            f"- N: {self.baseline.n}",
            f"- Hamming accuracy: {self.baseline.hamming_accuracy:.1%}",
            f"- fee_d accuracy: {self.baseline.fee_d_accuracy:.1%}",
            f"- Δ accuracy: {self.baseline.delta_accuracy:+.1%}",
            "",
            "## Consequence Stratification",
            "",
            f"- Silent corruption: {self.stratification.n_silent_corruption}",
            f"- Correct pass: {self.stratification.n_correct_pass}",
            f"- Expected clean: {self.stratification.n_expected_clean}",
            f"- Guarded reject: {self.stratification.n_guarded_reject}",
        ]
        return "\n".join(lines)


def analyze(rows: list[ProbeRow]) -> AnalysisReport:
    """Run all analyses on probe results."""
    return AnalysisReport(
        sign_test=sign_test(rows),
        baseline=beat_the_baseline(rows),
        stratification=stratify_by_consequence(rows),
        n_probes=len(rows),
        n_dropped=sum(1 for r in rows if r.dropped),
    )


def analyze_jsonl(path: Path) -> AnalysisReport:
    """Load JSONL and run full analysis."""
    return analyze(load_jsonl(path))
