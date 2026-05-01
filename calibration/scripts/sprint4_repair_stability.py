"""Sprint 4: Repair stability under cost perturbation.

For each nonzero-fee corpus composition, compute:
1. Number of distinct optimal bases under random cost vectors
2. Stability margin: minimum cost perturbation that switches the optimal basis
3. Correlation between repair entropy and stability

Central question: when does repair freedom (high β) translate to repair
brittleness (many basis switches under small cost changes)?
"""

import json
import math
import random
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.coboundary import matrix_rank
from bulla.witness_geometry import (
    _connected_components_of_gram,
    compute_profile,
    weighted_greedy_repair,
)

# ── Corpus loading ──

MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"
PAIRS_PATH = REPO / "calibration" / "data" / "registry" / "report" / "schema_structure_pairs.jsonl"


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


def build_composition_from_manifests(server_a, tools_a, server_b, tools_b):
    from bulla.guard import BullaGuard
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


# ── Stability computation ──

def enumerate_bases_brute_force(K, fee, max_bases=500):
    """Enumerate all bases of the column matroid of K, up to max_bases.

    Returns list of frozensets of column indices.
    """
    from itertools import combinations
    n = len(K)
    if fee == 0:
        return [frozenset()]

    bases = []
    for combo in combinations(range(n), fee):
        sub = [[K[i][j] for j in combo] for i in combo]
        if matrix_rank(sub) == fee:
            bases.append(frozenset(combo))
            if len(bases) >= max_bases:
                break
    return bases


def optimal_basis_under_cost(bases, costs):
    """Return the basis with minimum total cost."""
    best = None
    best_cost = None
    for b in bases:
        c = sum(costs[i] for i in b)
        if best_cost is None or c < best_cost:
            best_cost = c
            best = b
    return best, best_cost


def compute_stability_profile(K, hidden_basis, fee, n_trials=50, seed=42):
    """Compute repair stability profile for a composition.

    Returns dict with:
    - beta: total number of bases (up to cap)
    - n_distinct_optimal: how many distinct bases are optimal across cost trials
    - stability_ratio: n_distinct_optimal / beta (0 = perfectly stable, 1 = all bases reachable)
    - margin: minimum cost gap between best and second-best basis (across trials)
    """
    if fee == 0:
        return {
            "beta": 1, "n_distinct_optimal": 1,
            "stability_ratio": 0.0, "margin": float("inf"),
        }

    # Enumerate bases
    bases = enumerate_bases_brute_force(K, fee)
    beta = len(bases)

    if beta <= 1:
        return {
            "beta": beta, "n_distinct_optimal": 1,
            "stability_ratio": 0.0, "margin": float("inf"),
        }

    n = len(K)
    rng = random.Random(seed)
    distinct_optimal = set()
    margins = []

    for trial in range(n_trials):
        # Generate random cost vector: uniform on [1, 10]
        costs = [Fraction(rng.randint(1, 10)) for _ in range(n)]
        best, best_cost = optimal_basis_under_cost(bases, costs)
        distinct_optimal.add(best)

        # Find second-best cost
        second_best_cost = None
        for b in bases:
            if b == best:
                continue
            c = sum(costs[i] for i in b)
            if second_best_cost is None or c < second_best_cost:
                second_best_cost = c
        if second_best_cost is not None:
            margins.append(float(second_best_cost - best_cost))

    # Also try several structured cost models (excluding uniform, which always ties)
    structured_costs = [
        # Index-weighted (later fields more expensive)
        [Fraction(i + 1) for i in range(n)],
        # Reverse index-weighted
        [Fraction(n - i) for i in range(n)],
        # Binary: first half cheap, second half expensive
        [Fraction(1) if i < n // 2 else Fraction(10) for i in range(n)],
        # Exponential: powers of 2
        [Fraction(2 ** (i % 8)) for i in range(n)],
    ]
    for costs in structured_costs:
        best, best_cost = optimal_basis_under_cost(bases, costs)
        distinct_optimal.add(best)
        second_best_cost = None
        for b in bases:
            if b == best:
                continue
            c = sum(costs[i] for i in b)
            if second_best_cost is None or c < second_best_cost:
                second_best_cost = c
        if second_best_cost is not None:
            margins.append(float(second_best_cost - best_cost))

    n_distinct = len(distinct_optimal)
    stability_ratio = n_distinct / beta if beta > 0 else 0.0
    positive_margins = [m for m in margins if m > 0]
    n_ties = sum(1 for m in margins if m == 0)

    return {
        "beta": beta,
        "n_distinct_optimal": n_distinct,
        "stability_ratio": round(stability_ratio, 4),
        "n_ties": n_ties,
        "n_trials": len(margins),
        "tie_rate": round(n_ties / len(margins), 4) if margins else 0.0,
        "median_margin": round(sorted(margins)[len(margins)//2], 4) if margins else 0.0,
        "mean_positive_margin": round(sum(positive_margins) / len(positive_margins), 4) if positive_margins else 0.0,
    }


# ── Main ──

print("=" * 70)
print("SPRINT 4: REPAIR STABILITY UNDER COST PERTURBATION")
print("=" * 70)

manifests = load_manifests()
pairs = []
with open(PAIRS_PATH) as f:
    for line in f:
        line = line.strip()
        if line:
            pairs.append(json.loads(line))

nonzero = [p for p in pairs if p["fee"] > 0]
print(f"  {len(nonzero)} nonzero-fee compositions")

# Skip very high-fee compositions (basis enumeration is exponential)
MAX_FEE_FOR_STABILITY = 14
eligible = [p for p in nonzero if p["fee"] <= MAX_FEE_FOR_STABILITY]
print(f"  {len(eligible)} eligible (fee ≤ {MAX_FEE_FOR_STABILITY})")

results = []
errors = 0
for i, p in enumerate(eligible):
    name = p["pair_name"]
    left, right = p["left_server"], p["right_server"]
    if left not in manifests or right not in manifests:
        continue

    if (i + 1) % 40 == 0 or i == 0:
        print(f"  [{i+1}/{len(eligible)}] {name}...")

    try:
        comp = build_composition_from_manifests(left, manifests[left], right, manifests[right])
        profile = compute_profile(list(comp.tools), list(comp.edges))
        if profile.fee == 0 or profile.fee != p["fee"]:
            continue

        # Compute entropy
        components = _connected_components_of_gram(profile.K)
        component_sizes = sorted([len(c) for c in components], reverse=True)
        beta_formula = 1
        for s in component_sizes:
            beta_formula *= s
        H_repair = math.log(beta_formula) if beta_formula > 0 else 0.0

        # Compute stability
        stab = compute_stability_profile(profile.K, profile.hidden_basis, profile.fee)

        results.append({
            "composition": name,
            "fee": profile.fee,
            "component_sizes": component_sizes,
            "beta_formula": beta_formula,
            "beta_enumerated": stab["beta"],
            "H_repair": round(H_repair, 4),
            "n_distinct_optimal": stab["n_distinct_optimal"],
            "stability_ratio": stab["stability_ratio"],
            "tie_rate": stab["tie_rate"],
            "median_margin": stab["median_margin"],
            "mean_positive_margin": stab["mean_positive_margin"],
        })
    except Exception as e:
        errors += 1
        continue

print(f"\n  Processed: {len(results)} compositions ({errors} errors)")

# ── Verify beta consistency ──
print("\n" + "=" * 70)
print("BETA CONSISTENCY CHECK")
print("=" * 70)
mismatches = 0
for r in results:
    if r["beta_formula"] != r["beta_enumerated"]:
        print(f"  MISMATCH: {r['composition']}: formula={r['beta_formula']}, enumerated={r['beta_enumerated']}")
        mismatches += 1
if mismatches == 0:
    print(f"  ✓ All {len(results)} compositions: β(formula) = β(enumerated)")
else:
    print(f"  ✗ {mismatches} mismatches")

# ── Stability summary ──
print("\n" + "=" * 70)
print("STABILITY SUMMARY BY FEE")
print("=" * 70)

by_fee = defaultdict(list)
for r in results:
    by_fee[r["fee"]].append(r)

print(f"\n  {'Fee':>5} {'N':>5} {'β range':>15} {'Distinct opt':>15} {'Stab ratio':>15} {'Median margin':>15}")
print(f"  {'-'*80}")

for fee in sorted(by_fee):
    group = by_fee[fee]
    n = len(group)
    b_min = min(r["beta_formula"] for r in group)
    b_max = max(r["beta_formula"] for r in group)
    d_min = min(r["n_distinct_optimal"] for r in group)
    d_max = max(r["n_distinct_optimal"] for r in group)
    s_min = min(r["stability_ratio"] for r in group)
    s_max = max(r["stability_ratio"] for r in group)
    m_min = min(r["median_margin"] for r in group)
    m_max = max(r["median_margin"] for r in group)
    print(f"  {fee:>5} {n:>5} {b_min:>6}–{b_max:<6} {d_min:>6}–{d_max:<6} {s_min:>6.3f}–{s_max:<6.3f} {m_min:>6.1f}–{m_max:<6.1f}")


# ── Key finding: entropy vs stability ──
print("\n" + "=" * 70)
print("ENTROPY VS STABILITY")
print("=" * 70)

# For each fee group with variation, show entropy-stability relationship
for fee in sorted(by_fee):
    group = by_fee[fee]
    if len(group) < 2:
        continue
    betas = set(r["beta_formula"] for r in group)
    if len(betas) < 2:
        continue  # no variation at this fee

    print(f"\n  Fee = {fee} ({len(group)} compositions, {len(betas)} distinct β values):")
    for r in sorted(group, key=lambda x: -x["beta_formula"])[:5]:
        motif = tuple(s for s in r["component_sizes"] if s > 1)
        print(f"    β={r['beta_formula']:>6}  distinct_opt={r['n_distinct_optimal']:>3}  "
              f"stab_ratio={r['stability_ratio']:.3f}  tie_rate={r['tie_rate']:.3f}  "
              f"median_margin={r['median_margin']:.1f}  motif={motif}  {r['composition']}")


# ── The headline: does entropy predict stability? ──
print("\n" + "=" * 70)
print("HEADLINE: DOES ENTROPY PREDICT STABILITY?")
print("=" * 70)

# Compare high-entropy vs low-entropy compositions at same fee
for fee in sorted(by_fee):
    group = by_fee[fee]
    if len(group) < 3:
        continue
    betas = set(r["beta_formula"] for r in group)
    if len(betas) < 2:
        continue

    high_beta = [r for r in group if r["beta_formula"] == max(betas)]
    low_beta = [r for r in group if r["beta_formula"] == min(betas)]

    if high_beta and low_beta:
        h_avg_distinct = sum(r["n_distinct_optimal"] for r in high_beta) / len(high_beta)
        l_avg_distinct = sum(r["n_distinct_optimal"] for r in low_beta) / len(low_beta)
        h_avg_margin = sum(r["median_margin"] for r in high_beta) / len(high_beta)
        l_avg_margin = sum(r["median_margin"] for r in low_beta) / len(low_beta)
        print(f"\n  Fee = {fee}:")
        print(f"    High-β (β={max(betas):>4}, n={len(high_beta)}): "
              f"avg distinct optimal = {h_avg_distinct:.1f}, avg min margin = {h_avg_margin:.1f}")
        print(f"    Low-β  (β={min(betas):>4}, n={len(low_beta)}):  "
              f"avg distinct optimal = {l_avg_distinct:.1f}, avg min margin = {l_avg_margin:.1f}")
        if h_avg_distinct > l_avg_distinct:
            print(f"    → High entropy → MORE basis switches (less stable)")
        elif h_avg_distinct < l_avg_distinct:
            print(f"    → High entropy → FEWER basis switches (more stable)")
        else:
            print(f"    → No difference in stability")


# ── Save ──
OUTPUT_PATH = REPO / "calibration" / "results" / "sprint4_repair_stability.json"
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

output = {
    "summary": {
        "n_compositions": len(results),
        "n_errors": errors,
        "max_fee_analyzed": MAX_FEE_FOR_STABILITY,
    },
    "per_composition": results,
}
with open(OUTPUT_PATH, "w") as f:
    json.dump(output, f, indent=2)
print(f"\n  Saved to {OUTPUT_PATH}")
