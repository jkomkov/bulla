"""Sprint 4: Live repair replay.

Shows how two compositions with the same fee but different motifs produce
different optimal repairs under realistic cost models.

The canonical pair: github+gtasks-mcp vs github+playwright (both fee=11).
"""

import sys
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.witness_geometry import (
    _connected_components_of_gram,
    compute_profile,
    weighted_greedy_repair,
)

MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"

import json


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


manifests = load_manifests()

# Cost models
def make_cost_models(hidden_basis):
    """Generate several realistic cost models for hidden fields."""
    models = {}

    # 1. Uniform (baseline)
    models["uniform"] = {h: Fraction(1) for h in hidden_basis}

    # 2. Cross-server fields are expensive (they require coordination)
    cross_server = {}
    for h in hidden_basis:
        tool, field = h
        # Fields that appear in tools from different servers are cross-server
        cross_server[h] = Fraction(1)
    models["cross-server"] = cross_server

    # 3. "state" fields are expensive (they carry runtime semantics)
    state_cost = {}
    for h in hidden_basis:
        tool, field = h
        if "state" in field.lower() or "status" in field.lower():
            state_cost[h] = Fraction(5)
        elif "path" in field.lower() or "file" in field.lower():
            state_cost[h] = Fraction(3)
        elif "page" in field.lower():
            state_cost[h] = Fraction(1)
        elif "direction" in field.lower():
            state_cost[h] = Fraction(2)
        else:
            state_cost[h] = Fraction(2)
    models["semantic-weight"] = state_cost

    # 4. Pagination is cheap, everything else is expensive
    pagination_cheap = {}
    for h in hidden_basis:
        tool, field = h
        if "page" in field.lower():
            pagination_cheap[h] = Fraction(1)
        else:
            pagination_cheap[h] = Fraction(5)
    models["pagination-cheap"] = pagination_cheap

    # 5. Reverse: pagination expensive, others cheap
    pagination_expensive = {}
    for h in hidden_basis:
        tool, field = h
        if "page" in field.lower():
            pagination_expensive[h] = Fraction(5)
        else:
            pagination_expensive[h] = Fraction(1)
    models["pagination-expensive"] = pagination_expensive

    return models


print("=" * 70)
print("LIVE REPAIR REPLAY")
print("=" * 70)

for name, left, right in [
    ("github+gtasks-mcp", "github", "gtasks-mcp"),
    ("github+playwright", "github", "playwright"),
]:
    comp = build_composition_from_manifests(left, manifests[left], right, manifests[right])
    profile = compute_profile(list(comp.tools), list(comp.edges))
    components = _connected_components_of_gram(profile.K)
    sizes = sorted([len(c) for c in components], reverse=True)
    nontrivial = tuple(s for s in sizes if s > 1)

    print(f"\n{'─' * 70}")
    print(f"  {name}  (fee={profile.fee}, β={', '.join(str(s) for s in nontrivial)}={__import__('math').prod(nontrivial)}, motif={nontrivial})")
    print(f"{'─' * 70}")

    # Show component structure
    for ci, comp_indices in enumerate(components):
        if len(comp_indices) > 1:
            fields = [profile.hidden_basis[i] for i in comp_indices]
            dim_label = fields[0][1]  # field name as dimension hint
            print(f"  Component {ci} ({dim_label}, size {len(comp_indices)}):")
            for tool, field in fields:
                lev = profile.leverage[profile.hidden_basis.index((tool, field))]
                print(f"    {tool}::{field}  (leverage={float(lev):.3f})")

    # Run cost models
    cost_models = make_cost_models(profile.hidden_basis)

    print(f"\n  Optimal repair under different cost models:")
    for model_name, costs in cost_models.items():
        basis = weighted_greedy_repair(profile.K, profile.hidden_basis, costs)
        total_cost = sum(costs[h] for h in basis)
        # Format: show which field is OMITTED from each component
        print(f"\n    [{model_name}] total cost = {total_cost}")
        for ci, comp_indices in enumerate(components):
            if len(comp_indices) <= 1:
                continue
            comp_fields = set(profile.hidden_basis[i] for i in comp_indices)
            included = comp_fields & set(basis)
            omitted = comp_fields - set(basis)
            if omitted:
                for tool, field in omitted:
                    print(f"      omit: {tool}::{field} (cost={costs[(tool,field)]})")


# ── The decisive comparison ──

print("\n" + "=" * 70)
print("DECISIVE COMPARISON: same cost model, different repair choice")
print("=" * 70)

for model_name in ["semantic-weight", "pagination-cheap", "pagination-expensive"]:
    print(f"\n  Cost model: {model_name}")
    for name, left, right in [
        ("github+gtasks-mcp", "github", "gtasks-mcp"),
        ("github+playwright", "github", "playwright"),
    ]:
        comp = build_composition_from_manifests(left, manifests[left], right, manifests[right])
        profile = compute_profile(list(comp.tools), list(comp.edges))
        costs = make_cost_models(profile.hidden_basis)[model_name]
        basis = weighted_greedy_repair(profile.K, profile.hidden_basis, costs)
        total_cost = sum(costs[h] for h in basis)
        omitted = set(profile.hidden_basis) - set(basis)
        print(f"    {name}: cost={total_cost}, omitted={[(t.split('__')[1] if '__' in t else t, f) for t, f in omitted]}")
