"""Sprint 2 Track B: Witness geometry vs scalar fee — first-pass analysis.

Joins schema_structure_pairs.jsonl with witness_geometry_703.jsonl to:
1. Identify fee-matched composition groups with different witness geometry
2. Quantify the "geometry surplus" — loops, leverage variance, n_effective
3. Compute expected repair advantage under geometry-guided vs random disclosure

This is the feasibility analysis. If geometry differences exist within
fee-matched groups, the full budgeted-repair simulation follows.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAIRS_PATH = REPO / "calibration" / "data" / "registry" / "report" / "schema_structure_pairs.jsonl"
GEOMETRY_PATH = REPO / "calibration" / "results" / "witness_geometry_703.jsonl"
OUTPUT_PATH = REPO / "calibration" / "results" / "sprint2_geometry_analysis.json"


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main():
    # ── Step 1: Load and join ──
    pairs = load_jsonl(PAIRS_PATH)
    geometry = load_jsonl(GEOMETRY_PATH)

    pairs_by_name = {r["pair_name"]: r for r in pairs}
    geo_by_name = {r["composition"]: r for r in geometry}

    joined = []
    for name, p in pairs_by_name.items():
        g = geo_by_name.get(name)
        if g is None:
            continue
        joined.append({**p, **g})

    print(f"Joined {len(joined)} records ({len(pairs)} pairs, {len(geometry)} geometry)")

    # ── Step 2: Fee distribution ──
    fee_groups: dict[int, list[dict]] = defaultdict(list)
    for r in joined:
        fee_groups[r["fee"]].append(r)

    print(f"\nFee distribution ({len(fee_groups)} distinct fee levels):")
    for fee in sorted(fee_groups.keys()):
        n = len(fee_groups[fee])
        if n > 0:
            print(f"  fee={fee:3d}: {n:4d} compositions")

    # ── Step 3: For fee > 0, analyze geometry diversity within fee groups ──
    print("\n── Fee-matched geometry analysis (fee > 0 only) ──")

    analysis_rows = []

    for fee in sorted(fee_groups.keys()):
        if fee == 0:
            continue
        group = fee_groups[fee]
        if len(group) < 2:
            continue  # Need at least 2 for comparison

        # Per-composition geometry metrics
        metrics = []
        for r in group:
            n_hidden = r["n_hidden"]
            loops = r.get("loops_count", 0)
            n_eff = r.get("n_effective_float", 0.0)
            lev = r.get("leverage_floats", [])

            # Leverage variance (within this composition)
            if lev:
                mean_lev = sum(lev) / len(lev)
                var_lev = sum((x - mean_lev) ** 2 for x in lev) / len(lev)
            else:
                mean_lev = 0.0
                var_lev = 0.0

            # Expected fee reduction from 1 random disclosure
            # P(pick non-loop) = (n_hidden - loops) / n_hidden
            if n_hidden > 0:
                p_effective = (n_hidden - loops) / n_hidden
            else:
                p_effective = 0.0

            metrics.append({
                "composition": r["pair_name"],
                "fee": fee,
                "n_hidden": n_hidden,
                "loops_count": loops,
                "n_effective": round(n_eff, 4),
                "leverage_variance": round(var_lev, 6),
                "p_effective_random": round(p_effective, 4),
                "geometry_guided_first_step": 1,  # Always reduces fee by 1
            })

        # Group-level statistics
        all_loops = [m["loops_count"] for m in metrics]
        all_n_eff = [m["n_effective"] for m in metrics]
        all_p_eff = [m["p_effective_random"] for m in metrics]

        has_loops = sum(1 for x in all_loops if x > 0)
        no_loops = sum(1 for x in all_loops if x == 0)

        # Geometry diversity: do compositions within this fee group differ?
        n_eff_range = max(all_n_eff) - min(all_n_eff) if all_n_eff else 0
        p_eff_range = max(all_p_eff) - min(all_p_eff) if all_p_eff else 0

        row = {
            "fee": fee,
            "n_compositions": len(group),
            "has_loops": has_loops,
            "no_loops": no_loops,
            "loops_range": [min(all_loops), max(all_loops)],
            "n_effective_range": round(n_eff_range, 4),
            "p_effective_range": round(p_eff_range, 4),
            "mean_p_effective_random": round(sum(all_p_eff) / len(all_p_eff), 4),
            "compositions": metrics,
        }
        analysis_rows.append(row)

        print(f"  fee={fee:3d} | {len(group):3d} comps | "
              f"loops: {has_loops}/{len(group)} have loops | "
              f"n_eff range: {n_eff_range:.2f} | "
              f"P(eff random): {sum(all_p_eff)/len(all_p_eff):.3f} "
              f"[{min(all_p_eff):.3f}–{max(all_p_eff):.3f}]")

    # ── Step 4: Aggregate the geometry advantage ──
    print("\n── Aggregate geometry advantage ──")

    total_with_loops = 0
    total_without_loops = 0
    total_wasted_expected = 0.0
    total_compositions = 0

    for row in analysis_rows:
        for m in row["compositions"]:
            total_compositions += 1
            if m["loops_count"] > 0:
                total_with_loops += 1
                # Expected wasted disclosures in first fee steps of random
                # = fee * (loops / n_hidden) approximately
                if m["n_hidden"] > 0:
                    total_wasted_expected += m["fee"] * m["loops_count"] / m["n_hidden"]
            else:
                total_without_loops += 1

    print(f"  Compositions with loops: {total_with_loops}/{total_compositions} "
          f"({100*total_with_loops/total_compositions:.1f}%)")
    print(f"  Compositions without loops: {total_without_loops}/{total_compositions}")
    print(f"  Expected total wasted disclosures (random, first fee steps): "
          f"{total_wasted_expected:.1f}")
    if total_with_loops > 0:
        print(f"  Mean wasted per loop-bearing composition: "
              f"{total_wasted_expected/total_with_loops:.2f}")

    # ── Step 5: Find the most dramatic fee-matched pairs ──
    print("\n── Most dramatic fee-matched contrasts ──")

    best_contrasts = []
    for row in analysis_rows:
        if row["p_effective_range"] < 0.01:
            continue  # No meaningful geometry difference
        comps = sorted(row["compositions"], key=lambda m: m["p_effective_random"])
        worst = comps[0]
        best = comps[-1]
        contrast = {
            "fee": row["fee"],
            "worst": {
                "composition": worst["composition"],
                "n_hidden": worst["n_hidden"],
                "loops": worst["loops_count"],
                "p_effective": worst["p_effective_random"],
            },
            "best": {
                "composition": best["composition"],
                "n_hidden": best["n_hidden"],
                "loops": best["loops_count"],
                "p_effective": best["p_effective_random"],
            },
            "p_effective_gap": round(best["p_effective_random"] - worst["p_effective_random"], 4),
        }
        best_contrasts.append(contrast)

    best_contrasts.sort(key=lambda c: c["p_effective_gap"], reverse=True)

    for c in best_contrasts[:10]:
        print(f"  fee={c['fee']:3d} | gap={c['p_effective_gap']:.3f} | "
              f"worst: {c['worst']['composition']} "
              f"(loops={c['worst']['loops']}, P_eff={c['worst']['p_effective']:.3f}) | "
              f"best: {c['best']['composition']} "
              f"(loops={c['best']['loops']}, P_eff={c['best']['p_effective']:.3f})")

    # ── Step 6: Compute budgeted repair curves (static approximation) ──
    # For each composition, compute expected residual fee after k disclosures
    # under geometry-guided (always reduces by 1) vs random
    print("\n── Budgeted repair: geometry-guided vs random (static approx) ──")

    budget_results = []
    for row in analysis_rows:
        for m in row["compositions"]:
            fee = m["fee"]
            n_h = m["n_hidden"]
            loops = m["loops_count"]
            if fee == 0 or n_h == 0:
                continue

            # Geometry-guided: fee reduces by 1 per step, reaches 0 in exactly fee steps
            # Random: expected fee reduction per step depends on remaining loops
            # For static approximation: P(effective step) = (n_h - loops) / n_h
            # This overestimates random's performance because it doesn't account
            # for the changing pool after each disclosure.

            # Budget = 1 to min(n_h, fee * 2)
            max_budget = min(n_h, fee * 2)
            curve_guided = []
            curve_random = []
            for k in range(1, max_budget + 1):
                # Geometry-guided: residual = max(0, fee - k)
                guided_residual = max(0, fee - k)

                # Random (hypergeometric expectation):
                # Expected non-loops drawn from n_h items with (n_h - loops) non-loops
                # when drawing k items without replacement
                non_loops = n_h - loops
                if k <= n_h:
                    expected_non_loops = k * non_loops / n_h
                    # But can't exceed actual fee (non-loops that are independent)
                    # Conservative: expected_non_loops is upper-bounded by fee
                    expected_fee_reduction = min(expected_non_loops, fee)
                    random_residual = fee - expected_fee_reduction
                else:
                    random_residual = 0.0

                curve_guided.append(round(guided_residual, 4))
                curve_random.append(round(random_residual, 4))

            budget_results.append({
                "composition": m["composition"],
                "fee": fee,
                "n_hidden": n_h,
                "loops": loops,
                "budget_range": list(range(1, max_budget + 1)),
                "residual_guided": curve_guided,
                "residual_random": curve_random,
            })

    # Aggregate: mean residual at each budget fraction
    print("  Budget fraction | Mean residual (guided) | Mean residual (random) | Advantage")
    for frac in [0.25, 0.5, 0.75, 1.0]:
        guided_residuals = []
        random_residuals = []
        for br in budget_results:
            fee = br["fee"]
            budget_idx = max(0, min(int(frac * fee) - 1, len(br["residual_guided"]) - 1))
            if budget_idx >= 0 and budget_idx < len(br["residual_guided"]):
                guided_residuals.append(br["residual_guided"][budget_idx])
                random_residuals.append(br["residual_random"][budget_idx])

        if guided_residuals:
            mean_g = sum(guided_residuals) / len(guided_residuals)
            mean_r = sum(random_residuals) / len(random_residuals)
            advantage = mean_r - mean_g
            print(f"  k = {frac:.0%} of fee | {mean_g:8.2f} | {mean_r:8.2f} | +{advantage:.2f}")

    # ── Save results ──
    output = {
        "summary": {
            "total_compositions": len(joined),
            "nonzero_fee": total_compositions,
            "with_loops": total_with_loops,
            "without_loops": total_without_loops,
            "expected_wasted_disclosures_random": round(total_wasted_expected, 2),
        },
        "fee_group_analysis": analysis_rows,
        "best_contrasts": best_contrasts[:20],
        "budget_curves_sample": budget_results[:50],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
