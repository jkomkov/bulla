"""Sprint 2: Oracle (exact minimum-cost repair) vs geometry-guided vs cheapest-first.

Key insight: the minimum-cost full repair is the minimum-weight basis of the
contraction matroid M/O. By the matroid greedy theorem (Rado 1957), the greedy
algorithm — sort hidden fields by cost, add each if independent — computes the
exact minimum. Independence is checked via K[j][j] > 0 (the field has positive
leverage and will reduce the fee).

This script computes the oracle on all 240 nonzero-fee compositions and compares
to geometry-guided (greedy by leverage/cost ratio) and cheapest-first (greedy by
cost alone, without skipping loops).
"""

import json
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.guard import BullaGuard
from bulla.witness_geometry import compute_profile, leverage_scores
from bulla.incremental import IncrementalDiagnostic

MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"
PAIRS_PATH = REPO / "calibration" / "data" / "registry" / "report" / "schema_structure_pairs.jsonl"
OUTPUT_PATH = REPO / "calibration" / "results" / "sprint2_oracle_comparison.json"

SEMANTIC_COST_MAP = {
    "page": 1, "limit": 1, "offset": 1, "cursor": 1, "per_page": 1,
    "sort": 1, "order": 1, "direction": 1,
    "status": 3, "state": 3, "format": 3, "type": 3, "mode": 3,
    "since": 3, "before": 3, "after": 3,
    "path": 9, "filePath": 9, "directory": 9, "url": 9,
    "key": 9, "token": 9, "credentials": 9,
}
DEFAULT_COST = 3


def load_manifests():
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


def build_composition(server_a, tools_a, server_b, tools_b):
    prefixed = []
    for t in tools_a:
        p = dict(t)
        p["name"] = f"{server_a}__{t['name']}"
        prefixed.append(p)
    for t in tools_b:
        p = dict(t)
        p["name"] = f"{server_b}__{t['name']}"
        prefixed.append(p)
    return BullaGuard.from_tools_list(prefixed, name=f"{server_a}+{server_b}").composition


def field_cost(tool, field):
    base = field.split(".")[-1]
    return Fraction(SEMANTIC_COST_MAP.get(base, DEFAULT_COST))


def run_oracle(comp, hidden, costs):
    """Matroid greedy: sort by cost, skip loops. Exact minimum by Rado's theorem."""
    incr = IncrementalDiagnostic(comp)
    sorted_by_cost = sorted(hidden, key=lambda h: (costs[h], h))
    total = Fraction(0)
    steps = []
    for tool, field in sorted_by_cost:
        if incr.fee == 0:
            break
        if incr.preview_disclose(tool, field) == -1:  # independent
            c = costs[(tool, field)]
            incr.disclose(tool, field)
            total += c
            steps.append((tool, field, c))
    return total, len(steps), incr.fee


def run_geometry(comp, hidden, costs):
    """Geometry-guided: greedy by leverage/cost ratio."""
    incr = IncrementalDiagnostic(comp)
    total = Fraction(0)
    steps = []
    for _ in range(len(hidden)):
        if incr.fee == 0:
            break
        best = incr.best_next_disclosure(costs=costs)
        if best is None:
            break
        c = costs[best]
        incr.disclose(best[0], best[1])
        total += c
        steps.append((best[0], best[1], c))
    return total, len(steps), incr.fee


def run_cheapest(comp, hidden, costs):
    """Cheapest-first: sort by cost, disclose ALL (including loops)."""
    incr = IncrementalDiagnostic(comp)
    sorted_by_cost = sorted(hidden, key=lambda h: (costs[h], h))
    total = Fraction(0)
    steps = []
    for tool, field in sorted_by_cost:
        if incr.fee == 0:
            break
        try:
            c = costs[(tool, field)]
            incr.disclose(tool, field)
            total += c
            steps.append((tool, field, c))
        except ValueError:
            continue
    return total, len(steps), incr.fee


def main():
    print("Loading manifests...")
    manifests = load_manifests()

    print("Loading pairs...")
    pairs = []
    with open(PAIRS_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    nonzero = [p for p in pairs if p["fee"] > 0]
    print(f"  {len(nonzero)} nonzero-fee pairs")

    results = []
    oracle_eq_geometry = 0
    oracle_lt_geometry = 0
    geometry_lt_oracle = 0  # Should never happen if oracle is truly optimal

    for i, p in enumerate(nonzero):
        name = p["pair_name"]
        left, right = p["left_server"], p["right_server"]
        if left not in manifests or right not in manifests:
            continue

        if (i + 1) % 40 == 0 or i == 0:
            print(f"  [{i+1}/{len(nonzero)}] {name}...")

        try:
            comp = build_composition(left, manifests[left], right, manifests[right])
            profile = compute_profile(list(comp.tools), list(comp.edges))
            if profile.fee == 0 or profile.fee != p["fee"]:
                continue

            hidden = profile.hidden_basis
            costs = {h: field_cost(h[0], h[1]) for h in hidden}

            o_cost, o_steps, o_fee = run_oracle(comp, hidden, costs)
            g_cost, g_steps, g_fee = run_geometry(comp, hidden, costs)
            c_cost, c_steps, c_fee = run_cheapest(comp, hidden, costs)

            assert o_fee == 0, f"{name}: oracle didn't reach fee=0"
            assert g_fee == 0, f"{name}: geometry didn't reach fee=0"
            assert c_fee == 0, f"{name}: cheapest didn't reach fee=0"

            if o_cost == g_cost:
                oracle_eq_geometry += 1
                comparison = "equal"
            elif o_cost < g_cost:
                oracle_lt_geometry += 1
                comparison = "oracle_wins"
            else:
                geometry_lt_oracle += 1
                comparison = "geometry_wins_UNEXPECTED"

            results.append({
                "composition": name,
                "fee": p["fee"],
                "n_hidden": len(hidden),
                "oracle_cost": float(o_cost),
                "geometry_cost": float(g_cost),
                "cheapest_cost": float(c_cost),
                "oracle_steps": o_steps,
                "geometry_steps": g_steps,
                "cheapest_steps": c_steps,
                "comparison": comparison,
                "geometry_overhead_pct": round(float((g_cost - o_cost) / o_cost * 100), 2) if o_cost > 0 else 0,
                "cheapest_overhead_pct": round(float((c_cost - o_cost) / o_cost * 100), 2) if o_cost > 0 else 0,
            })

        except Exception as e:
            print(f"  ERROR: {name}: {e}")
            continue

    print(f"\nCompleted: {len(results)} compositions")
    print(f"\n{'='*70}")
    print("ORACLE vs GEOMETRY-GUIDED vs CHEAPEST-FIRST (semantic cost model)")
    print(f"{'='*70}")

    print(f"\n  Oracle = Geometry: {oracle_eq_geometry}/{len(results)}")
    print(f"  Oracle < Geometry: {oracle_lt_geometry}/{len(results)} (oracle strictly cheaper)")
    print(f"  Oracle > Geometry: {geometry_lt_oracle}/{len(results)} (should be 0!)")

    # Aggregate costs
    total_oracle = sum(r["oracle_cost"] for r in results)
    total_geometry = sum(r["geometry_cost"] for r in results)
    total_cheapest = sum(r["cheapest_cost"] for r in results)

    print(f"\n  Aggregate cost (all compositions):")
    print(f"    Oracle:          {total_oracle:.0f}")
    print(f"    Geometry-guided: {total_geometry:.0f}  (+{(total_geometry-total_oracle)/total_oracle*100:.1f}% over oracle)")
    print(f"    Cheapest-first:  {total_cheapest:.0f}  (+{(total_cheapest-total_oracle)/total_oracle*100:.1f}% over oracle)")

    # Show cases where oracle beats geometry
    diffs = [r for r in results if r["comparison"] == "oracle_wins"]
    if diffs:
        print(f"\n  Cases where oracle < geometry ({len(diffs)}):")
        diffs.sort(key=lambda r: r["geometry_overhead_pct"], reverse=True)
        for r in diffs[:15]:
            print(f"    {r['composition']}: oracle={r['oracle_cost']:.0f} "
                  f"geo={r['geometry_cost']:.0f} cheap={r['cheapest_cost']:.0f} "
                  f"(+{r['geometry_overhead_pct']:.1f}% over oracle)")

    # Fee-stratified summary
    print(f"\n  Fee-stratified oracle comparison:")
    by_fee = defaultdict(list)
    for r in results:
        by_fee[r["fee"]].append(r)
    for fee in sorted(by_fee):
        group = by_fee[fee]
        n = len(group)
        eq = sum(1 for r in group if r["comparison"] == "equal")
        ow = sum(1 for r in group if r["comparison"] == "oracle_wins")
        mean_overhead = sum(r["geometry_overhead_pct"] for r in group) / n
        mean_cheap_overhead = sum(r["cheapest_overhead_pct"] for r in group) / n
        print(f"    fee={fee:3d}: N={n:3d} | oracle=geo: {eq:3d} | oracle<geo: {ow:3d} | "
              f"mean geo overhead: {mean_overhead:.1f}% | mean cheap overhead: {mean_cheap_overhead:.1f}%")

    # Save
    output = {
        "summary": {
            "n_compositions": len(results),
            "oracle_equals_geometry": oracle_eq_geometry,
            "oracle_beats_geometry": oracle_lt_geometry,
            "geometry_beats_oracle": geometry_lt_oracle,
            "total_oracle_cost": total_oracle,
            "total_geometry_cost": total_geometry,
            "total_cheapest_cost": total_cheapest,
        },
        "results": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
