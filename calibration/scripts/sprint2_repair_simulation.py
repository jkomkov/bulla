"""Sprint 2 Track B: Full budgeted repair simulation.

For each nonzero-fee composition in the 703-corpus:
1. Loads manifests and builds the BullaGuard composition
2. Computes witness Gram K(G) and effective resistance
3. Simulates budgeted repair under four strategies:
   - geometry-guided (greedy by leverage — provably optimal for unit costs)
   - random (mean of 50 random permutations)
   - worst-case (loops first, then lowest leverage)
   - cheapest-first (assign realistic costs, pick cheapest fields)
4. Records residual fee at each budget step

Output: sprint2_repair_simulation.json with per-composition repair curves
and aggregate statistics.
"""

import json
import random as rng
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

# Add bulla/src to path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.guard import BullaGuard
from bulla.witness_geometry import (
    witness_gram,
    leverage_scores,
    fee_from_gram,
    effective_resistance,
    WitnessProfile,
    compute_profile,
)
from bulla.incremental import IncrementalDiagnostic

MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"
PAIRS_PATH = REPO / "calibration" / "data" / "registry" / "report" / "schema_structure_pairs.jsonl"
OUTPUT_PATH = REPO / "calibration" / "results" / "sprint2_repair_simulation.json"

# Realistic cost model based on field semantics
COST_MAP = {
    # Low cost (1): generic pagination/filtering
    "page": 1, "limit": 1, "offset": 1, "cursor": 1, "per_page": 1,
    "sort": 1, "order": 1, "direction": 1,
    # Medium cost (3): domain-specific state
    "status": 3, "state": 3, "format": 3, "type": 3, "mode": 3,
    "since": 3, "before": 3, "after": 3,
    # High cost (9): path/location/sensitive
    "path": 9, "filePath": 9, "directory": 9, "url": 9,
    "key": 9, "token": 9, "credentials": 9,
}
DEFAULT_COST = 3


def load_manifests() -> dict[str, list[dict]]:
    """Load all server manifests. Returns {server_name: tools_list}."""
    manifests = {}
    for f in sorted(MANIFESTS_DIR.glob("*.json")):
        if f.name == "coherence.db" or f.stem.startswith("."):
            continue
        try:
            data = json.loads(f.read_text())
            tools = data.get("tools", [])
            if tools:
                manifests[f.stem] = tools
        except (json.JSONDecodeError, KeyError):
            continue
    return manifests


def build_composition(server_a: str, tools_a: list[dict],
                      server_b: str, tools_b: list[dict]):
    """Build a BullaGuard composition from two server tool lists."""
    prefixed = []
    for t in tools_a:
        p = dict(t)
        p["name"] = f"{server_a}__{t['name']}"
        prefixed.append(p)
    for t in tools_b:
        p = dict(t)
        p["name"] = f"{server_b}__{t['name']}"
        prefixed.append(p)

    name = f"{server_a}+{server_b}"
    guard = BullaGuard.from_tools_list(prefixed, name=name)
    return guard.composition


def field_cost(tool_name: str, field_name: str) -> Fraction:
    """Assign a realistic disclosure cost based on field semantics."""
    # Strip nested path (e.g., "sort.direction" -> "direction")
    base = field_name.split(".")[-1]
    return Fraction(COST_MAP.get(base, DEFAULT_COST))


def simulate_repair(comp, profile: WitnessProfile, n_random_trials: int = 50):
    """Run full repair simulation under multiple strategies.

    Returns dict with per-strategy repair curves and summary statistics.
    """
    fee = profile.fee
    hidden = profile.hidden_basis
    n_hidden = len(hidden)

    if fee == 0 or n_hidden == 0:
        return None

    # ── Strategy 1: Geometry-guided (greedy by leverage) ──
    # This is provably optimal for unit costs (matroid greedy theorem)
    incr = IncrementalDiagnostic(comp)
    guided_curve = [fee]  # residual fee at budget=0
    guided_order = []
    for _ in range(n_hidden):
        best = incr.best_next_disclosure()
        if best is None:
            break
        delta = incr.disclose(best[0], best[1])
        guided_curve.append(incr.fee)
        guided_order.append(list(best))

    # ── Strategy 2: Worst-case (loops first, then lowest leverage) ──
    incr_worst = IncrementalDiagnostic(comp)
    lev = incr_worst.leverage()
    # Sort: leverage ascending (loops first, then least impactful)
    worst_order = sorted(lev, key=lambda x: x[1])
    worst_curve = [fee]
    for (tool, field), _ in worst_order:
        try:
            delta = incr_worst.disclose(tool, field)
            worst_curve.append(incr_worst.fee)
        except ValueError:
            continue

    # ── Strategy 3: Random (mean of n_random_trials permutations) ──
    random_curves = []
    for _ in range(n_random_trials):
        incr_rand = IncrementalDiagnostic(comp)
        perm = list(hidden)
        rng.shuffle(perm)
        curve = [fee]
        for tool, field in perm:
            try:
                incr_rand.disclose(tool, field)
                curve.append(incr_rand.fee)
            except ValueError:
                continue
        random_curves.append(curve)

    # Mean random curve (pad shorter curves)
    max_len = max(len(c) for c in random_curves)
    mean_random = []
    for i in range(max_len):
        vals = [c[i] if i < len(c) else 0 for c in random_curves]
        mean_random.append(round(sum(vals) / len(vals), 4))

    # ── Strategy 4: Cost-weighted greedy ──
    incr_cost = IncrementalDiagnostic(comp)
    costs = {h: field_cost(h[0], h[1]) for h in hidden}
    cost_curve = [fee]
    cost_order = []
    cost_total = Fraction(0)
    for _ in range(n_hidden):
        best = incr_cost.best_next_disclosure(costs=costs)
        if best is None:
            break
        c = costs.get(best, Fraction(DEFAULT_COST))
        cost_total += c
        delta = incr_cost.disclose(best[0], best[1])
        cost_curve.append(incr_cost.fee)
        cost_order.append({"field": list(best), "cost": int(c)})

    # ── Strategy 5: Cheapest-first (ignore geometry, pick by cost alone) ──
    incr_cheap = IncrementalDiagnostic(comp)
    cheap_sorted = sorted(hidden, key=lambda h: field_cost(h[0], h[1]))
    cheap_curve = [fee]
    cheap_total = Fraction(0)
    for tool, field in cheap_sorted:
        try:
            c = field_cost(tool, field)
            delta = incr_cheap.disclose(tool, field)
            cheap_curve.append(incr_cheap.fee)
            cheap_total += c
        except ValueError:
            continue

    # ── Compute effective resistance summary ──
    K = profile.K
    R, components = effective_resistance(K)
    # Summarize: mean R_eff within connected components
    r_eff_values = []
    for comp_indices in components:
        if len(comp_indices) < 2:
            continue
        for i in range(len(comp_indices)):
            for j in range(i + 1, len(comp_indices)):
                gi, gj = comp_indices[i], comp_indices[j]
                if R[gi][gj] is not None:
                    r_eff_values.append(float(R[gi][gj]))

    return {
        "fee": fee,
        "n_hidden": n_hidden,
        "n_loops": len(profile.loops),
        "n_coloops": len(profile.coloops),
        "n_effective": round(float(profile.n_effective), 4),
        "leverage_floats": [round(float(l), 6) for l in profile.leverage],
        "n_components": len(components),
        "mean_r_eff": round(sum(r_eff_values) / len(r_eff_values), 6) if r_eff_values else None,
        "max_r_eff": round(max(r_eff_values), 6) if r_eff_values else None,
        "min_r_eff": round(min(r_eff_values), 6) if r_eff_values else None,
        "curves": {
            "guided": guided_curve,
            "worst": worst_curve,
            "random_mean": mean_random,
            "cost_weighted": cost_curve,
            "cheapest_first": cheap_curve,
        },
        "cost_weighted_total": int(cost_total),
        "cheapest_first_total": int(cheap_total),
        "guided_steps_to_zero": guided_curve.index(0) if 0 in guided_curve else len(guided_curve),
    }


def main():
    rng.seed(42)  # Reproducibility

    print("Loading manifests...")
    manifests = load_manifests()
    print(f"  {len(manifests)} servers loaded")

    print("Loading pair metadata...")
    pairs = []
    with open(PAIRS_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    nonzero = [p for p in pairs if p["fee"] > 0]
    print(f"  {len(nonzero)} nonzero-fee pairs to simulate")

    results = []
    errors = []
    skipped = 0

    for i, p in enumerate(nonzero):
        name = p["pair_name"]
        left = p["left_server"]
        right = p["right_server"]

        if left not in manifests or right not in manifests:
            skipped += 1
            continue

        if (i + 1) % 20 == 0 or i == 0:
            print(f"  [{i+1}/{len(nonzero)}] {name} (fee={p['fee']})...")

        try:
            comp = build_composition(left, manifests[left], right, manifests[right])
            profile = compute_profile(list(comp.tools), list(comp.edges))

            if profile.fee != p["fee"]:
                errors.append({
                    "pair": name,
                    "error": f"fee mismatch: computed {profile.fee} vs expected {p['fee']}"
                })
                continue

            sim = simulate_repair(comp, profile)
            if sim is not None:
                sim["composition"] = name
                results.append(sim)

        except Exception as e:
            errors.append({"pair": name, "error": str(e)})
            continue

    print(f"\nCompleted: {len(results)} simulations, {len(errors)} errors, {skipped} skipped")

    # ── Aggregate analysis ──
    print("\n── Aggregate repair analysis ──")

    # Group by fee
    by_fee = defaultdict(list)
    for r in results:
        by_fee[r["fee"]].append(r)

    # For each fee level, compute mean advantage of guided over random
    print("\nFee | N | Guided steps | Random steps | Worst steps | Advantage | Cost-weighted vs Cheapest")
    for fee in sorted(by_fee.keys()):
        group = by_fee[fee]
        n = len(group)

        guided_steps = [r["guided_steps_to_zero"] for r in group]
        # Random steps to zero: first index where mean random curve <= 0.5
        random_steps = []
        for r in group:
            curve = r["curves"]["random_mean"]
            # Steps until fee effectively 0 (< 0.5 for mean)
            steps = len(curve) - 1
            for j, v in enumerate(curve):
                if v < 0.5:
                    steps = j
                    break
            random_steps.append(steps)

        worst_steps = [len(r["curves"]["worst"]) - 1 for r in group]

        cost_w = [r["cost_weighted_total"] for r in group]
        cost_c = [r["cheapest_first_total"] for r in group]

        mg = sum(guided_steps) / n
        mr = sum(random_steps) / n
        mw = sum(worst_steps) / n
        mc_w = sum(cost_w) / n
        mc_c = sum(cost_c) / n

        print(f"  {fee:3d} | {n:3d} | {mg:6.1f} | {mr:6.1f} | {mw:6.1f} | "
              f"+{mr - mg:.1f} steps | cost: {mc_w:.0f} vs {mc_c:.0f} "
              f"({'geometry wins' if mc_w < mc_c else 'cheapest wins' if mc_c < mc_w else 'tie'})")

    # ── Effective resistance analysis ──
    print("\n── Effective resistance within fee-matched groups ──")
    for fee in sorted(by_fee.keys()):
        group = by_fee[fee]
        if len(group) < 2:
            continue
        r_effs = [r["mean_r_eff"] for r in group if r["mean_r_eff"] is not None]
        if not r_effs:
            continue
        print(f"  fee={fee:3d} | N={len(group)} | mean R_eff: "
              f"min={min(r_effs):.4f} max={max(r_effs):.4f} "
              f"range={max(r_effs)-min(r_effs):.4f}")

    # ── Find most dramatic fee-matched contrasts ──
    print("\n── Most dramatic fee-matched repair contrasts ──")
    for fee in sorted(by_fee.keys()):
        group = by_fee[fee]
        if len(group) < 2:
            continue

        # Sort by guided steps (all should be fee, so sort by cost advantage)
        group_sorted = sorted(group, key=lambda r: r["cost_weighted_total"])
        cheapest = group_sorted[0]
        most_expensive = group_sorted[-1]

        if cheapest["cost_weighted_total"] < most_expensive["cost_weighted_total"]:
            print(f"  fee={fee}: cost-weighted repair: "
                  f"{cheapest['composition']}={cheapest['cost_weighted_total']} vs "
                  f"{most_expensive['composition']}={most_expensive['cost_weighted_total']} "
                  f"(Δ={most_expensive['cost_weighted_total'] - cheapest['cost_weighted_total']})")

    # ── Save results ──
    output = {
        "summary": {
            "total_simulated": len(results),
            "errors": len(errors),
            "skipped": skipped,
        },
        "results": results,
        "errors": errors,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
