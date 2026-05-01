#!/usr/bin/env python3
"""Search the 703-corpus witness-geometry output for a master example:
a pair of compositions with the same fee but materially different K.

Since the initial sweep shows 0 coloops across all 703 compositions, we widen
the search to other discriminators:
  - N_eff (concentration)
  - loop count (redundancy)
  - leverage-score distribution shape (max, min, range)

Reports the best candidate pair for the paper's introduction.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, stdev


def load_records(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            records.append(json.loads(line))
    return records


def main() -> None:
    path = Path("bulla/calibration/results/witness_geometry_703.jsonl")
    records = load_records(path)
    print(f"Loaded {len(records)} records")

    # Filter to nontrivial compositions (fee > 0)
    nontrivial = [r for r in records if r["fee_K"] > 0]
    print(f"Nontrivial (fee > 0): {len(nontrivial)}")

    # Group by fee
    by_fee: dict[int, list[dict]] = {}
    for r in nontrivial:
        by_fee.setdefault(r["fee_K"], []).append(r)

    print()
    print("Fee distribution:")
    for fee in sorted(by_fee.keys()):
        n = len(by_fee[fee])
        print(f"  fee = {fee:3d}: {n:4d} compositions")

    # Summary statistics on leverage concentration
    print()
    print("N_eff distribution across nontrivial:")
    n_effs = [r["n_effective_float"] for r in nontrivial]
    print(f"  mean = {mean(n_effs):.3f}")
    print(f"  stdev = {stdev(n_effs):.3f}")
    print(f"  min = {min(n_effs):.3f}")
    print(f"  max = {max(n_effs):.3f}")

    # Max/min leverage per composition
    max_levs = [max(r["leverage_floats"]) for r in nontrivial
                if r["leverage_floats"]]
    min_nonzero_levs = [
        min(l for l in r["leverage_floats"] if l > 0)
        for r in nontrivial
        if any(l > 0 for l in r["leverage_floats"])
    ]
    print()
    print(f"Max leverage across compositions:")
    print(f"  mean = {mean(max_levs):.3f}")
    print(f"  max = {max(max_levs):.3f} (closest to coloop)")
    print(f"Min nonzero leverage:")
    print(f"  mean = {mean(min_nonzero_levs):.3f}")
    print(f"  min = {min(min_nonzero_levs):.6f}")

    # Loop counts
    loop_counts = [r["loops_count"] for r in nontrivial]
    print()
    print(f"Loops per composition:")
    print(f"  mean = {mean(loop_counts):.2f}")
    print(f"  min = {min(loop_counts)}")
    print(f"  max = {max(loop_counts)}")

    # Find same-fee pairs with maximally different N_eff
    print()
    print("=" * 70)
    print("Master example candidates (same fee, max N_eff gap):")
    print("=" * 70)

    best_candidates: list[tuple] = []
    for fee, group in sorted(by_fee.items()):
        if len(group) < 2:
            continue
        # Find pair with max gap in N_eff
        group_sorted = sorted(group, key=lambda r: r["n_effective_float"])
        low = group_sorted[0]
        high = group_sorted[-1]
        gap = high["n_effective_float"] - low["n_effective_float"]
        if gap > 0:
            best_candidates.append((fee, gap, low, high))

    best_candidates.sort(key=lambda t: -t[1])  # descending gap

    for fee, gap, low, high in best_candidates[:5]:
        print(f"\nfee = {fee}, N_eff gap = {gap:.3f}")
        print(f"  LOW  concentration: {low['composition']:40s} N_eff={low['n_effective_float']:.2f}  "
              f"max_l={max(low['leverage_floats']):.3f}  loops={low['loops_count']}  |H|={low['n_hidden']}")
        print(f"  HIGH concentration: {high['composition']:40s} N_eff={high['n_effective_float']:.2f}  "
              f"max_l={max(high['leverage_floats']):.3f}  loops={high['loops_count']}  |H|={high['n_hidden']}")

    # Also search for pairs with same fee AND same n_hidden but different loops/leverage shape
    print()
    print("=" * 70)
    print("Same-fee same-|H| pairs with max N_eff gap:")
    print("=" * 70)

    pair_candidates: list[tuple] = []
    for fee, group in by_fee.items():
        # Group further by n_hidden
        by_n: dict[int, list[dict]] = {}
        for r in group:
            by_n.setdefault(r["n_hidden"], []).append(r)
        for n_hidden, subgroup in by_n.items():
            if len(subgroup) < 2:
                continue
            subgroup_sorted = sorted(subgroup, key=lambda r: r["n_effective_float"])
            low = subgroup_sorted[0]
            high = subgroup_sorted[-1]
            gap = high["n_effective_float"] - low["n_effective_float"]
            if gap > 0.5:  # only interesting gaps
                pair_candidates.append((fee, n_hidden, gap, low, high))

    pair_candidates.sort(key=lambda t: -t[2])
    for fee, n_h, gap, low, high in pair_candidates[:5]:
        print(f"\nfee={fee}, |H|={n_h}, gap={gap:.3f}")
        print(f"  LOW  : {low['composition']:40s} N_eff={low['n_effective_float']:.2f} "
              f"max_l={max(low['leverage_floats']):.3f} loops={low['loops_count']}")
        print(f"  HIGH : {high['composition']:40s} N_eff={high['n_effective_float']:.2f} "
              f"max_l={max(high['leverage_floats']):.3f} loops={high['loops_count']}")


if __name__ == "__main__":
    main()
