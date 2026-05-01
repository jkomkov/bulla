"""Sprint 2: Cost-model stress test for witness geometry repair.

Defends against the critique "your geometry result is a cost-model artifact"
by running the geometry-guided vs cheapest-first comparison under four
independent cost models:

  1. Uniform:     all fields cost 1
  2. Semantic:    pagination=1, status=3, path/key=9 (the existing model)
  3. Random:      Fibonacci-ish costs {1,2,3,5,8,13}, seeded
  4. Adversarial: costs inversely proportional to leverage (high-leverage
                  fields cost MORE, trying to defeat geometry)

For each model x composition, we compare total disclosure cost to reach
fee=0 under geometry-guided (best_next_disclosure with costs) vs
cheapest-first (disclose in ascending cost order, breaking ties arbitrarily).

Output: summary table + sprint2_cost_model_stress_test.json
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
from bulla.witness_geometry import compute_profile
from bulla.incremental import IncrementalDiagnostic

MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"
PAIRS_PATH = REPO / "calibration" / "data" / "registry" / "report" / "schema_structure_pairs.jsonl"
OUTPUT_PATH = REPO / "calibration" / "results" / "sprint2_cost_model_stress_test.json"

# ── Semantic cost map (same as sprint2_repair_simulation.py) ──
SEMANTIC_COST_MAP = {
    "page": 1, "limit": 1, "offset": 1, "cursor": 1, "per_page": 1,
    "sort": 1, "order": 1, "direction": 1,
    "status": 3, "state": 3, "format": 3, "type": 3, "mode": 3,
    "since": 3, "before": 3, "after": 3,
    "path": 9, "filePath": 9, "directory": 9, "url": 9,
    "key": 9, "token": 9, "credentials": 9,
}
SEMANTIC_DEFAULT = 3

# Fibonacci-ish cost set for random model
FIBONACCI_COSTS = [1, 2, 3, 5, 8, 13]


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


# ── Cost model builders ──

def make_uniform_costs(hidden: list[tuple[str, str]]) -> dict[tuple[str, str], Fraction]:
    """All fields cost 1."""
    return {h: Fraction(1) for h in hidden}


def make_semantic_costs(hidden: list[tuple[str, str]]) -> dict[tuple[str, str], Fraction]:
    """Pagination=1, status=3, path/key=9."""
    costs = {}
    for tool, field in hidden:
        base = field.split(".")[-1]
        costs[(tool, field)] = Fraction(SEMANTIC_COST_MAP.get(base, SEMANTIC_DEFAULT))
    return costs


def make_random_costs(hidden: list[tuple[str, str]], seed: int) -> dict[tuple[str, str], Fraction]:
    """Each field gets a random Fibonacci-ish cost, seeded for reproducibility."""
    r = rng.Random(seed)
    return {h: Fraction(r.choice(FIBONACCI_COSTS)) for h in hidden}


def make_adversarial_costs(
    hidden: list[tuple[str, str]],
    leverage_map: dict[tuple[str, str], Fraction],
) -> dict[tuple[str, str], Fraction]:
    """Costs inversely proportional to leverage: high-leverage fields cost MORE.

    This is the worst case for geometry-guided: the fields geometry most
    wants to disclose first are the most expensive.

    Scale: leverage in [0, 1], so cost = 1 + 12 * leverage, giving range [1, 13].
    Fields with zero leverage (loops) get cost 1.
    """
    costs = {}
    for h in hidden:
        lev = leverage_map.get(h, Fraction(0))
        # Linear scale: cost = 1 + 12 * leverage (so coloop at lev=1 costs 13)
        cost = Fraction(1) + Fraction(12) * lev
        costs[h] = cost
    return costs


# ── Simulation core ──

def run_strategy(comp, hidden: list[tuple[str, str]],
                 costs: dict[tuple[str, str], Fraction],
                 strategy: str) -> dict:
    """Run a single strategy and return total cost + per-step trace.

    strategy: 'geometry' or 'cheapest'
    """
    incr = IncrementalDiagnostic(comp)
    n_hidden = len(hidden)
    total_cost = Fraction(0)
    steps = []

    if strategy == "geometry":
        for _ in range(n_hidden):
            best = incr.best_next_disclosure(costs=costs)
            if best is None:
                break
            c = costs.get(best, Fraction(1))
            total_cost += c
            incr.disclose(best[0], best[1])
            steps.append({
                "field": list(best),
                "cost": str(c),
                "residual_fee": incr.fee,
            })
            if incr.fee == 0:
                break

    elif strategy == "cheapest":
        # Sort by cost ascending, break ties by field name for determinism
        cheap_sorted = sorted(hidden, key=lambda h: (costs.get(h, Fraction(1)), h))
        for tool, field in cheap_sorted:
            try:
                c = costs.get((tool, field), Fraction(1))
                incr.disclose(tool, field)
                total_cost += c
                steps.append({
                    "field": [tool, field],
                    "cost": str(c),
                    "residual_fee": incr.fee,
                })
                if incr.fee == 0:
                    break
            except ValueError:
                continue

    return {
        "total_cost": total_cost,
        "final_fee": incr.fee,
        "n_steps": len(steps),
        "steps": steps,
    }


def simulate_composition(comp, profile, pair_name: str, random_seed: int) -> dict:
    """Run all four cost models on one composition."""
    hidden = profile.hidden_basis
    fee = profile.fee

    if fee == 0 or len(hidden) == 0:
        return None

    # Build leverage map for adversarial model
    leverage_map = dict(zip(hidden, profile.leverage))

    # Build all four cost dicts
    cost_models = {
        "uniform": make_uniform_costs(hidden),
        "semantic": make_semantic_costs(hidden),
        "random": make_random_costs(hidden, seed=random_seed),
        "adversarial": make_adversarial_costs(hidden, leverage_map),
    }

    results = {"composition": pair_name, "fee": fee, "n_hidden": len(hidden)}

    for model_name, costs in cost_models.items():
        geo = run_strategy(comp, hidden, costs, "geometry")
        cheap = run_strategy(comp, hidden, costs, "cheapest")

        geo_cost = geo["total_cost"]
        cheap_cost = cheap["total_cost"]

        if cheap_cost > 0:
            savings_pct = float((cheap_cost - geo_cost) / cheap_cost * 100)
        else:
            savings_pct = 0.0

        results[model_name] = {
            "geometry_cost": str(geo_cost),
            "cheapest_cost": str(cheap_cost),
            "geometry_cost_float": float(geo_cost),
            "cheapest_cost_float": float(cheap_cost),
            "geometry_wins": geo_cost <= cheap_cost,
            "savings_pct": round(savings_pct, 2),
            "geometry_steps": geo["n_steps"],
            "cheapest_steps": cheap["n_steps"],
        }

    return results


def main():
    rng.seed(42)

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

    all_results = []
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

            result = simulate_composition(comp, profile, name, random_seed=42 + i)
            if result is not None:
                all_results.append(result)

        except Exception as e:
            errors.append({"pair": name, "error": str(e)})
            continue

    print(f"\nCompleted: {len(all_results)} compositions, {len(errors)} errors, {skipped} skipped")

    # ── Aggregate analysis per cost model ──
    MODEL_NAMES = ["uniform", "semantic", "random", "adversarial"]

    print("\n" + "=" * 90)
    print("COST-MODEL STRESS TEST: geometry-guided vs cheapest-first")
    print("=" * 90)

    summary = {}

    for model in MODEL_NAMES:
        geo_wins = 0
        cheap_wins = 0
        ties = 0
        savings = []
        max_saving = 0.0
        max_saving_comp = ""
        cheap_win_cases = []

        for r in all_results:
            m = r[model]
            g = m["geometry_cost_float"]
            c = m["cheapest_cost_float"]

            if g < c:
                geo_wins += 1
                s = m["savings_pct"]
                savings.append(s)
                if s > max_saving:
                    max_saving = s
                    max_saving_comp = r["composition"]
            elif g > c:
                cheap_wins += 1
                cheap_win_cases.append({
                    "composition": r["composition"],
                    "geometry_cost": g,
                    "cheapest_cost": c,
                    "loss_pct": round((g - c) / c * 100, 2),
                })
            else:
                ties += 1
                savings.append(0.0)

        n = len(all_results)
        mean_savings = sum(savings) / len(savings) if savings else 0.0

        summary[model] = {
            "n_compositions": n,
            "geometry_wins": geo_wins,
            "cheapest_wins": cheap_wins,
            "ties": ties,
            "geometry_win_rate_pct": round(geo_wins / n * 100, 1) if n > 0 else 0,
            "mean_savings_pct": round(mean_savings, 2),
            "max_savings_pct": round(max_saving, 2),
            "max_savings_composition": max_saving_comp,
            "cheapest_win_cases": cheap_win_cases[:10],  # Cap detail
        }

        print(f"\n{'─' * 70}")
        print(f"  {model.upper()} cost model")
        print(f"{'─' * 70}")
        print(f"  Geometry wins : {geo_wins:4d} / {n}  ({geo_wins/n*100:.1f}%)")
        print(f"  Cheapest wins : {cheap_wins:4d} / {n}  ({cheap_wins/n*100:.1f}%)")
        print(f"  Ties          : {ties:4d} / {n}  ({ties/n*100:.1f}%)")
        print(f"  Mean savings  : {mean_savings:.2f}%")
        print(f"  Max savings   : {max_saving:.2f}%  ({max_saving_comp})")
        if cheap_win_cases:
            print(f"  Cheapest-first wins ({len(cheap_win_cases)} cases):")
            for cw in cheap_win_cases[:5]:
                print(f"    {cw['composition']}: geo={cw['geometry_cost']:.1f} "
                      f"cheap={cw['cheapest_cost']:.1f} (loss={cw['loss_pct']:.1f}%)")

    # ── Cross-model summary table ──
    print("\n" + "=" * 90)
    print(f"{'Model':<15} {'Geo wins':>10} {'Cheap wins':>12} {'Ties':>8} "
          f"{'Win rate':>10} {'Mean save':>11} {'Max save':>10}")
    print("-" * 90)
    for model in MODEL_NAMES:
        s = summary[model]
        print(f"{model:<15} {s['geometry_wins']:>10} {s['cheapest_wins']:>12} "
              f"{s['ties']:>8} {s['geometry_win_rate_pct']:>9.1f}% "
              f"{s['mean_savings_pct']:>10.2f}% {s['max_savings_pct']:>9.2f}%")
    print("=" * 90)

    # ── Save results ──
    output = {
        "description": "Cost-model stress test: geometry-guided vs cheapest-first under 4 cost models",
        "cost_models": {
            "uniform": "all fields cost 1",
            "semantic": "pagination=1, status=3, path/key=9",
            "random": "Fibonacci {1,2,3,5,8,13}, seeded per composition",
            "adversarial": "cost = 1 + 12*leverage (high-leverage costs MORE)",
        },
        "summary": summary,
        "n_compositions": len(all_results),
        "n_errors": len(errors),
        "n_skipped": skipped,
        "per_composition": all_results,
        "errors": errors[:20],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
