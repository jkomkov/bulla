"""Statistical analysis for the agent convention-confusion experiment.

Primary hypothesis test:
  H0: error_rate(cyclic) = error_rate(acyclic)
  H1: error_rate(cyclic) > error_rate(acyclic)

Secondary tests:
  - Disambiguation control: does making conventions explicit eliminate the gap?
  - Mechanism classification: are cyclic errors specifically convention confusion?
  - Dimension breakdown: which hidden dimensions drive the most confusion?

Usage:
    python -m bulla.calibration.harness.analysis results_pilot.jsonl
    python -m bulla.calibration.harness.analysis results_full.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ArmStats:
    arm: str
    total: int
    tool_correct: int
    tool_and_params_correct: int
    error_types: Counter
    dimension_errors: Counter  # hidden_dimension → error count

    @property
    def tool_accuracy(self) -> float:
        return self.tool_correct / self.total if self.total > 0 else 0.0

    @property
    def full_accuracy(self) -> float:
        return self.tool_and_params_correct / self.total if self.total > 0 else 0.0

    @property
    def tool_error_rate(self) -> float:
        return 1.0 - self.tool_accuracy


def load_results(path: Path) -> list[dict[str, Any]]:
    """Load JSONL results file."""
    results = []
    with open(path) as f:
        for line in f:
            if line.strip():
                results.append(json.loads(line))
    return results


def compute_arm_stats(results: list[dict[str, Any]], arm: str) -> ArmStats:
    """Compute statistics for one arm."""
    arm_results = [r for r in results if r["arm"] == arm]
    error_types: Counter = Counter()
    dimension_errors: Counter = Counter()

    tool_correct = 0
    full_correct = 0

    for r in arm_results:
        if r["tool_correct"]:
            tool_correct += 1
            if r["params_correct"]:
                full_correct += 1
        error_types[r["error_type"]] += 1
        if r["error_type"] != "correct" and r.get("convention_violated"):
            dimension_errors[r["convention_violated"]] += 1

    return ArmStats(
        arm=arm,
        total=len(arm_results),
        tool_correct=tool_correct,
        tool_and_params_correct=full_correct,
        error_types=error_types,
        dimension_errors=dimension_errors,
    )


def fishers_exact_one_sided(
    errors_a: int, total_a: int,
    errors_b: int, total_b: int,
) -> float:
    """One-sided Fisher's exact test: P(rate_a > rate_b).

    Returns p-value. Uses scipy if available, otherwise falls back
    to a simple approximation.
    """
    try:
        from scipy.stats import fisher_exact
        # Contingency table:
        #              errors   correct
        # arm_a     [errors_a, total_a - errors_a]
        # arm_b     [errors_b, total_b - errors_b]
        table = [
            [errors_a, total_a - errors_a],
            [errors_b, total_b - errors_b],
        ]
        _, p_value = fisher_exact(table, alternative="greater")
        return p_value
    except ImportError:
        # Fallback: normal approximation for difference of proportions
        import math
        p1 = errors_a / total_a if total_a > 0 else 0
        p2 = errors_b / total_b if total_b > 0 else 0
        p_pool = (errors_a + errors_b) / (total_a + total_b)
        se = math.sqrt(p_pool * (1 - p_pool) * (1/total_a + 1/total_b)) if p_pool > 0 else 1
        z = (p1 - p2) / se if se > 0 else 0
        # One-sided p-value from z
        from statistics import NormalDist
        return 1 - NormalDist().cdf(z)


def run_analysis(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the full analysis suite."""
    stats = {
        arm: compute_arm_stats(results, arm)
        for arm in ("cyclic", "acyclic", "disambiguated", "overlap_control")
        if any(r["arm"] == arm for r in results)
    }

    # Primary test: cyclic error rate > acyclic error rate
    cyc = stats["cyclic"]
    acy = stats["acyclic"]
    dis = stats["disambiguated"]

    primary_p = fishers_exact_one_sided(
        cyc.total - cyc.tool_correct, cyc.total,
        acy.total - acy.tool_correct, acy.total,
    )

    # Disambiguation test: does making conventions visible eliminate the gap?
    disambig_p = fishers_exact_one_sided(
        dis.total - dis.tool_correct, dis.total,
        acy.total - acy.tool_correct, acy.total,
    )

    # Overlap control test: does parameter overlap alone explain the gap?
    ovr = stats.get("overlap_control")
    if ovr and ovr.total > 0:
        overlap_p = fishers_exact_one_sided(
            ovr.total - ovr.tool_correct, ovr.total,
            acy.total - acy.tool_correct, acy.total,
        )
    else:
        overlap_p = None

    # Mechanism test: what fraction of cyclic errors are convention_confusion?
    cyclic_errors = cyc.total - cyc.tool_correct
    convention_errors = (
        cyc.error_types.get("convention_confusion", 0)
        + cyc.error_types.get("deprecated_tool_use", 0)
        + cyc.error_types.get("content_type_mismatch", 0)
    )
    mechanism_fraction = convention_errors / cyclic_errors if cyclic_errors > 0 else 0

    return {
        "arm_stats": {arm: _stats_to_dict(s) for arm, s in stats.items()},
        "primary_test": {
            "hypothesis": "error_rate(cyclic) > error_rate(acyclic)",
            "cyclic_error_rate": cyc.tool_error_rate,
            "acyclic_error_rate": acy.tool_error_rate,
            "gap": cyc.tool_error_rate - acy.tool_error_rate,
            "p_value": primary_p,
            "significant_at_05": primary_p < 0.05,
        },
        "disambiguation_test": {
            "hypothesis": "error_rate(disambiguated) ≈ error_rate(acyclic) [gap eliminated]",
            "disambiguated_error_rate": dis.tool_error_rate,
            "acyclic_error_rate": acy.tool_error_rate,
            "gap": dis.tool_error_rate - acy.tool_error_rate,
            "p_value": disambig_p,
            "conventions_are_causal": disambig_p > 0.05,  # NOT significant = gap eliminated
        },
        "overlap_control_test": {
            "hypothesis": "error_rate(overlap_control) ≈ error_rate(acyclic) [overlap alone insufficient]",
            "overlap_control_error_rate": ovr.tool_error_rate if ovr else None,
            "acyclic_error_rate": acy.tool_error_rate,
            "cyclic_error_rate": cyc.tool_error_rate,
            "gap_vs_acyclic": (ovr.tool_error_rate - acy.tool_error_rate) if ovr else None,
            "p_value": overlap_p,
            "overlap_alone_insufficient": overlap_p is not None and overlap_p > 0.05,
        },
        "mechanism_test": {
            "hypothesis": "cyclic errors are specifically convention confusion (>50%)",
            "convention_error_fraction": mechanism_fraction,
            "convention_errors": convention_errors,
            "total_cyclic_errors": cyclic_errors,
            "mechanism_confirmed": mechanism_fraction > 0.5,
        },
        "dimension_breakdown": {
            "cyclic": dict(cyc.dimension_errors),
            "disambiguated": dict(dis.dimension_errors),
        },
    }


def _stats_to_dict(s: ArmStats) -> dict[str, Any]:
    return {
        "total": s.total,
        "tool_correct": s.tool_correct,
        "tool_accuracy": round(s.tool_accuracy, 4),
        "full_accuracy": round(s.full_accuracy, 4),
        "error_types": dict(s.error_types),
        "dimension_errors": dict(s.dimension_errors),
    }


def print_report(analysis: dict[str, Any]) -> None:
    """Print human-readable analysis report."""
    print("=" * 70)
    print("AGENT CONVENTION-CONFUSION EXPERIMENT — ANALYSIS REPORT")
    print("=" * 70)

    print("\n─── ARM ACCURACY ───")
    for arm, stats in analysis["arm_stats"].items():
        print(f"  {arm:15s}: {stats['tool_accuracy']:.1%} tool accuracy "
              f"({stats['tool_correct']}/{stats['total']})")

    print("\n─── PRIMARY TEST ───")
    pt = analysis["primary_test"]
    print(f"  H1: cyclic error rate ({pt['cyclic_error_rate']:.1%}) > "
          f"acyclic error rate ({pt['acyclic_error_rate']:.1%})")
    print(f"  Gap: {pt['gap']:.1%}")
    print(f"  p-value: {pt['p_value']:.4f}")
    print(f"  Significant at α=0.05: {'YES ✓' if pt['significant_at_05'] else 'NO'}")

    print("\n─── DISAMBIGUATION TEST ───")
    dt = analysis["disambiguation_test"]
    print(f"  Disambiguated error rate: {dt['disambiguated_error_rate']:.1%}")
    print(f"  Acyclic error rate: {dt['acyclic_error_rate']:.1%}")
    print(f"  Gap: {dt['gap']:.1%}")
    print(f"  Conventions are causal factor: "
          f"{'YES ✓' if dt['conventions_are_causal'] else 'NO (disambiguation did not help)'}")

    print("\n─── OVERLAP CONTROL TEST ───")
    ot = analysis["overlap_control_test"]
    if ot["overlap_control_error_rate"] is not None:
        print(f"  Overlap-control error rate: {ot['overlap_control_error_rate']:.1%}")
        print(f"  Acyclic error rate: {ot['acyclic_error_rate']:.1%}")
        print(f"  Cyclic error rate: {ot['cyclic_error_rate']:.1%}")
        print(f"  Gap (overlap vs acyclic): {ot['gap_vs_acyclic']:.1%}")
        print(f"  p-value: {ot['p_value']:.4f}")
        print(f"  Overlap alone insufficient: "
              f"{'YES ✓' if ot['overlap_alone_insufficient'] else 'NO (overlap explains confusion)'}")
    else:
        print("  [arm D not present in results]")

    print("\n─── MECHANISM TEST ───")
    mt = analysis["mechanism_test"]
    print(f"  Convention-confusion errors: {mt['convention_errors']}/{mt['total_cyclic_errors']} "
          f"({mt['convention_error_fraction']:.1%})")
    print(f"  Mechanism confirmed (>50% convention): "
          f"{'YES ✓' if mt['mechanism_confirmed'] else 'NO'}")

    print("\n─── DIMENSION BREAKDOWN (cyclic arm errors) ───")
    for dim, count in sorted(
        analysis["dimension_breakdown"]["cyclic"].items(),
        key=lambda x: -x[1],
    ):
        print(f"  {dim:20s}: {count}")

    print("\n" + "=" * 70)

    # Paper-ready verdict
    ot = analysis["overlap_control_test"]
    overlap_ok = ot.get("overlap_alone_insufficient", False)
    if (pt["significant_at_05"] and dt["conventions_are_causal"]
            and mt["mechanism_confirmed"] and overlap_ok):
        print("\n★ FULL PAPER CLAIM SUPPORTED:")
        print("  Hidden convention cycles predict agent tool-selection failures.")
        print("  The mechanism is specifically convention confusion,")
        print("  making conventions explicit eliminates the gap,")
        print("  and parameter overlap alone does NOT explain the effect.")
    elif pt["significant_at_05"] and mt["mechanism_confirmed"]:
        print("\n◐ PARTIAL CLAIM: cyclic > acyclic with convention mechanism,")
        print("  but controls need review — check disambiguation and overlap tests.")
    else:
        print("\n○ PAPER CLAIM NOT YET SUPPORTED — check individual tests above.")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m bulla.calibration.harness.analysis <results.jsonl>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        # Try in the default results directory
        path = Path(__file__).parent.parent / "data" / "agent_confusion" / sys.argv[1]

    results = load_results(path)
    analysis = run_analysis(results)
    print_report(analysis)

    # Also save machine-readable analysis
    out_path = path.with_suffix(".analysis.json")
    with open(out_path, "w") as f:
        json.dump(analysis, f, indent=2)
    print(f"\nMachine-readable analysis: {out_path}")


if __name__ == "__main__":
    main()
