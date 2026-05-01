"""Sprint 4: Canonical motif pair — same fee, similar entropy, different stability.

Builds the decisive example: github+gtasks-mcp (β=112, ρ=0.375) vs
github+playwright (β=126, ρ=0.309).

Both have fee=11. The one with LOWER entropy has MORE reachable bases.
This proves motif geometry matters beyond the scalar β.

Also constructs synthetic pairs with exactly equal β but different motifs,
to demonstrate the separation more cleanly.
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
)
from bulla.model import ToolSpec, Edge, SemanticDimension

# ── Corpus pair analysis ──

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


def enumerate_bases(K, fee, max_bases=1000):
    from itertools import combinations
    n = len(K)
    bases = []
    for combo in combinations(range(n), fee):
        sub = [[K[i][j] for j in combo] for i in combo]
        if matrix_rank(sub) == fee:
            bases.append(frozenset(combo))
            if len(bases) >= max_bases:
                break
    return bases


def optimal_basis_under_cost(bases, costs):
    best = None
    best_cost = None
    for b in bases:
        c = sum(costs[i] for i in b)
        if best_cost is None or c < best_cost:
            best_cost = c
            best = b
    return best, best_cost


def stability_sweep(K, fee, n_trials=200, seed=42):
    """Detailed stability analysis with more trials."""
    bases = enumerate_bases(K, fee)
    beta = len(bases)
    if beta <= 1:
        return {"beta": beta, "details": []}

    n = len(K)
    rng = random.Random(seed)
    distinct_optimal = set()
    basis_opt_counts = defaultdict(int)
    margins = []

    for trial in range(n_trials):
        costs = [Fraction(rng.randint(1, 10)) for _ in range(n)]
        best, best_cost = optimal_basis_under_cost(bases, costs)
        distinct_optimal.add(best)
        basis_opt_counts[best] += 1

        # Margin
        second_best = None
        for b in bases:
            if b == best:
                continue
            c = sum(costs[i] for i in b)
            if second_best is None or c < second_best:
                second_best = c
        if second_best is not None:
            margins.append(float(second_best - best_cost))

    return {
        "beta": beta,
        "n_distinct": len(distinct_optimal),
        "rho": len(distinct_optimal) / beta,
        "top_basis_frequency": max(basis_opt_counts.values()) / n_trials,
        "margins": margins,
        "median_margin": sorted(margins)[len(margins)//2] if margins else 0,
        "mean_margin": sum(margins)/len(margins) if margins else 0,
        "pct_zero_margin": sum(1 for m in margins if m == 0) / len(margins) if margins else 0,
    }


# ── Part 1: Corpus canonical pair ──

print("=" * 70)
print("CANONICAL MOTIF PAIR: github+gtasks-mcp vs github+playwright")
print("=" * 70)

manifests = load_manifests()

for name, left, right in [
    ("github+gtasks-mcp", "github", "gtasks-mcp"),
    ("github+playwright", "github", "playwright"),
]:
    comp = build_composition_from_manifests(left, manifests[left], right, manifests[right])
    profile = compute_profile(list(comp.tools), list(comp.edges))
    components = _connected_components_of_gram(profile.K)
    sizes = sorted([len(c) for c in components], reverse=True)
    nontrivial = [s for s in sizes if s > 1]

    print(f"\n  {name}:")
    print(f"    fee = {profile.fee}")
    print(f"    motif = {tuple(nontrivial)}")
    print(f"    β = {math.prod(nontrivial)}")
    print(f"    hidden fields = {len(profile.hidden_basis)}")

    stab = stability_sweep(profile.K, profile.fee, n_trials=200)
    print(f"    β (enumerated) = {stab['beta']}")
    print(f"    reachable bases = {stab['n_distinct']}")
    print(f"    ρ = {stab['rho']:.3f}")
    print(f"    top basis frequency = {stab['top_basis_frequency']:.3f}")
    print(f"    median margin = {stab['median_margin']:.1f}")
    print(f"    mean margin = {stab['mean_margin']:.2f}")
    print(f"    zero-margin rate = {stab['pct_zero_margin']:.3f}")

    # Show component structure
    for ci, comp_indices in enumerate(components):
        if len(comp_indices) > 1:
            fields = [profile.hidden_basis[i] for i in comp_indices]
            print(f"    component {ci} (size {len(comp_indices)}):")
            for tool, field in fields:
                print(f"      ({tool}, {field})")


# ── Part 2: Synthetic same-β different-motif pair ──

print("\n" + "=" * 70)
print("SYNTHETIC PAIR: same β, different motif, different stability")
print("=" * 70)

# β = 12 can come from:
#   motif (12,) — single component of 12 vertices, fee=11
#   motif (6, 2) — two components (6, 2), fee=6
#   motif (4, 3) — two components (4, 3), fee=5
#   motif (3, 2, 2) — three components (3, 2, 2), fee=4
#   motif (2, 2, 3) — same as above
#
# β = 6 is simpler:
#   motif (6,) — single component, fee=5
#   motif (3, 2) — two components, fee=3

def make_single_block_composition(n):
    """Single dimension with n tools in a chain, all hidden."""
    tools = []
    edges = []
    field = "f0"
    for i in range(n):
        tools.append(ToolSpec(
            name=f"t{i}",
            internal_state=(field,),
            observable_schema=(),
        ))
    for i in range(n - 1):
        edges.append(Edge(
            from_tool=f"t{i}",
            to_tool=f"t{i+1}",
            dimensions=(SemanticDimension(name="dim0", from_field=field, to_field=field),),
        ))
    return tools, edges


def make_multi_component_composition(component_sizes):
    """Multiple dimensions, each with a chain of given size."""
    tools = []
    edges = []
    for d, size in enumerate(component_sizes):
        field = f"f{d}"
        for i in range(size):
            tools.append(ToolSpec(
                name=f"t{d}_{i}",
                internal_state=(field,),
                observable_schema=(),
            ))
        for i in range(size - 1):
            edges.append(Edge(
                from_tool=f"t{d}_{i}",
                to_tool=f"t{d}_{i+1}",
                dimensions=(SemanticDimension(name=f"dim{d}", from_field=field, to_field=field),),
            ))
    return tools, edges


# Test β=12 with two different motifs
pairs = [
    ("single (12,)", [12]),
    ("split (6, 2)", [6, 2]),
    ("split (4, 3)", [4, 3]),
    ("split (3, 2, 2)", [3, 2, 2]),
    ("split (2, 2, 3)", [2, 2, 3]),
]

print(f"\n  All with β = 12:\n")

for label, sizes in pairs:
    if len(sizes) == 1:
        tools, edges = make_single_block_composition(sizes[0])
    else:
        tools, edges = make_multi_component_composition(sizes)

    profile = compute_profile(tools, edges)
    beta_check = math.prod(sizes)
    fee = sum(s - 1 for s in sizes)

    stab = stability_sweep(profile.K, profile.fee, n_trials=200)

    print(f"  {label:>20}  fee={fee:>2}  β={beta_check:>3}  "
          f"reachable={stab['n_distinct']:>3}  ρ={stab['rho']:.3f}  "
          f"median_margin={stab['median_margin']:.1f}  "
          f"zero_margin={stab['pct_zero_margin']:.3f}")


# Also test β=24
print(f"\n  All with β = 24:\n")

pairs24 = [
    ("single (24,)", [24]),
    ("split (12, 2)", [12, 2]),
    ("split (8, 3)", [8, 3]),
    ("split (6, 4)", [6, 4]),
    ("split (6, 2, 2)", [6, 2, 2]),
    ("split (4, 3, 2)", [4, 3, 2]),
    ("split (3, 2, 2, 2)", [3, 2, 2, 2]),
    ("split (2, 2, 2, 3)", [2, 2, 2, 3]),
]

for label, sizes in pairs24:
    if len(sizes) == 1:
        tools, edges = make_single_block_composition(sizes[0])
    else:
        tools, edges = make_multi_component_composition(sizes)

    profile = compute_profile(tools, edges)
    beta_check = math.prod(sizes)
    fee = sum(s - 1 for s in sizes)

    stab = stability_sweep(profile.K, profile.fee, n_trials=200)

    print(f"  {label:>20}  fee={fee:>2}  β={beta_check:>3}  "
          f"reachable={stab['n_distinct']:>3}  ρ={stab['rho']:.3f}  "
          f"median_margin={stab['median_margin']:.1f}  "
          f"zero_margin={stab['pct_zero_margin']:.3f}")
