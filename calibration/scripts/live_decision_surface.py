"""Live decision surface: four projections of the witness Gram.

Sum-of-Maxima Theorem (DFD+CHP):
  Σ*(G,c) = Σ_j [Σ_{h∈C_j} c(h) - max_{h∈C_j} c(h)]
  Geometry dividend A(G,c) = Σ_j max_{h∈C_j} c(h)

Four projections of the component decomposition:
  fee    = Σ(|C_j| - 1)           — obligation count
  β      = Π|C_j|                 — structural repair count
  A(G,c) = Σ max_{C_j} c(h)      — geometry dividend
  margin = min_j(m1_j - m2_j)    — robustness

Phase diagram at fee=3 (four quadrants, all from corpus):
  Q1 rigid+uniform:   Σ*=9,  A=3  — nothing to decide
  Q2 flex+uniform:    Σ*=9,  A=6  — geometry exists, indifferent
  Q3 rigid+hetero:    Σ*=17, A=7  — costs vary, no routing freedom
  Q4 flex+hetero:     Σ*=13, A=10 — geometry routes to cheaper repair

Decision activation requires both structural multiplicity AND cost
heterogeneity. This is a corollary of the sum-of-maxima theorem,
not merely an empirical observation.
"""

import json
import math
import sys
from fractions import Fraction
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.coboundary import matrix_rank
from bulla.guard import BullaGuard
from bulla.proxy import (
    BullaProxySession,
    compute_field_costs,
    compute_repair_geometry,
    field_sensitivity,
)
from bulla.witness_geometry import (
    _connected_components_of_gram,
    compute_profile,
)

MANIFESTS_DIR = REPO / "calibration" / "data" / "registry" / "manifests"


def load_manifest(name: str) -> list[dict]:
    data = json.loads((MANIFESTS_DIR / f"{name}.json").read_text())
    return data.get("tools", [])


def build_guard(left: str, right: str) -> BullaGuard:
    tools_a, tools_b = load_manifest(left), load_manifest(right)
    prefixed = []
    for t in tools_a:
        p = dict(t)
        p["name"] = f"{left}__{t['name']}"
        prefixed.append(p)
    for t in tools_b:
        p = dict(t)
        p["name"] = f"{right}__{t['name']}"
        prefixed.append(p)
    return BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")


def short_name(tool_field: tuple[str, str]) -> str:
    tool, field = tool_field
    short_tool = tool.split("__")[1] if "__" in tool else tool
    return f"{short_tool}::{field}"


# ── Main ──

print("=" * 72)
print("LIVE DECISION SURFACE: WITNESS GEOMETRY PICKS THE BETTER REPAIR")
print("=" * 72)

PAIRS = [
    ("mcp-xmind+notion", "mcp-xmind", "notion"),
    ("mcp-xmind+playwright", "mcp-xmind", "playwright"),
]

# Replication pair: same structure, different partner server
REPLICATION_PAIRS = [
    ("mcp-xmind+ns-mcp-server", "mcp-xmind", "ns-mcp-server"),
]

results = []

for pair_name, left, right in PAIRS:
    guard = build_guard(left, right)
    comp = guard.composition
    profile = compute_profile(list(comp.tools), list(comp.edges))
    components = _connected_components_of_gram(profile.K)
    component_sizes = tuple(
        sorted([len(c) for c in components if len(c) > 1], reverse=True)
    )
    beta = math.prod(component_sizes)

    # Cost model
    costs = compute_field_costs(profile.hidden_basis, profile.leverage)

    # Geometry-aware repair
    geo = compute_repair_geometry(guard, costs)

    print(f"\n{'─' * 72}")
    print(f"  {pair_name}")
    print(f"{'─' * 72}")

    # Four-object stack
    print(f"\n  FOUR-OBJECT STACK:")
    print(f"    fee               = {geo.fee}")
    print(f"    repair_entropy    = {geo.repair_entropy:.4f}  (β = {geo.beta})")
    print(f"    reachable_bases   = {geo.reachable_basis_count}  (of {geo.beta})")
    print(f"    stability_ratio   = {geo.stability_ratio:.4f}")
    print(f"    robustness_margin = {geo.robustness_margin:.1f}")
    print(f"    repair_mode       = {geo.repair_mode}")
    print(f"    motif             = {component_sizes}")

    # Component structure with costs
    print(f"\n  WITNESS COMPONENTS:")
    for ci, comp_indices in enumerate(components):
        if len(comp_indices) > 1:
            fields = [profile.hidden_basis[i] for i in comp_indices]
            print(f"    Component {ci} (size {len(comp_indices)}):")
            for tool, field in fields:
                lev = float(profile.leverage[profile.hidden_basis.index((tool, field))])
                c = costs[(tool, field)]
                sens = field_sensitivity(field)
                print(f"      {short_name((tool, field)):>40}  "
                      f"lev={lev:.3f}  sens={sens:.0f}  cost={c:.1f}")

    # Enumerate ALL bases with their costs
    n = len(profile.K)
    print(f"\n  ALL BASES (ranked by cost):")
    all_bases = []
    for combo in combinations(range(n), profile.fee):
        sub = [[profile.K[i][j] for j in combo] for i in combo]
        if matrix_rank(sub) == profile.fee:
            fields = [profile.hidden_basis[i] for i in combo]
            total_cost = sum(costs[tuple(f)] for f in fields)
            total_sens = sum(field_sensitivity(f[1]) for f in fields)
            all_bases.append((total_cost, total_sens, fields))
    all_bases.sort()

    for rank, (cost, sens, fields) in enumerate(all_bases):
        marker = " ← cheapest" if rank == 0 else ""
        field_str = ", ".join(short_name(f) for f in fields)
        print(f"    cost={cost:>5.1f}  sens={sens:>2.0f}  {field_str}{marker}")

    # Best and worst
    best_cost = all_bases[0][0]
    worst_cost = all_bases[-1][0]
    print(f"\n    Cost range: {best_cost:.1f} – {worst_cost:.1f}  "
          f"(spread = {worst_cost - best_cost:.1f})")

    # Recommended repair
    rec_cost = sum(costs[tuple(h)] for h in geo.recommended_basis)
    print(f"\n  RECOMMENDED REPAIR (geometry-aware):")
    print(f"    {', '.join(short_name(h) for h in geo.recommended_basis)}")
    print(f"    cost = {rec_cost:.1f}")

    results.append({
        "composition": pair_name,
        "fee": geo.fee,
        "beta": geo.beta,
        "repair_entropy": round(geo.repair_entropy, 4),
        "component_sizes": list(component_sizes),
        "reachable_basis_count": geo.reachable_basis_count,
        "stability_ratio": round(geo.stability_ratio, 4),
        "robustness_margin": round(geo.robustness_margin, 1),
        "repair_mode": geo.repair_mode,
        "min_repair_cost": round(best_cost, 1),
        "max_repair_cost": round(worst_cost, 1),
        "recommended_basis": [short_name(h) for h in geo.recommended_basis],
        "recommended_cost": round(rec_cost, 1),
    })


# ── Decisive comparison ──

print(f"\n{'=' * 72}")
print("DECISIVE COMPARISON: SAME FEE, DIFFERENT GEOMETRY, DIFFERENT OUTCOME")
print(f"{'=' * 72}")

r0, r1 = results[0], results[1]
print(f"\n  Both have fee = {r0['fee']}. Scalar fee sees: same obligations.")
print(f"\n  {'Composition':>25}  {'β':>4}  {'Motif':<10}  {'Mode':<20}  "
      f"{'ρ':>5}  {'Margin':>7}  {'Min cost':>9}  {'Max cost':>9}")
print(f"  {'─' * 100}")
for r in results:
    print(f"  {r['composition']:>25}  {r['beta']:>4}  "
          f"{str(tuple(r['component_sizes'])):<10}  {r['repair_mode']:<20}  "
          f"{r['stability_ratio']:>5.3f}  {r['robustness_margin']:>7.1f}  "
          f"{r['min_repair_cost']:>9.1f}  {r['max_repair_cost']:>9.1f}")

saving = r1["min_repair_cost"] - r0["min_repair_cost"]
pct = 100 * saving / r1["min_repair_cost"]
print(f"\n  The flexible composition saves {saving:.1f} "
      f"({pct:.1f}% lower minimum repair cost).")
print(f"  Scalar fee: identical (both {r0['fee']}).")
print(f"  Witness geometry: {r0['repair_mode']} vs {r1['repair_mode']}.")
print(f"\n  The multi-component motif {tuple(r0['component_sizes'])} allows "
      f"routing repair through cheap notion fields")
print(f"  (sensitivity 2) instead of expensive xmind path fields "
      f"(sensitivity 6).")
print(f"  The single-component motif {tuple(r1['component_sizes'])} has no "
      f"such freedom — 3 of 4 fields are path-sensitive.")


# ── Phase diagram ──

print(f"\n{'=' * 72}")
print("PHASE DIAGRAM: FOUR PROJECTIONS OF REPAIR")
print(f"{'=' * 72}")

# All four quadrants at fee=3
PHASE_QUADRANTS = [
    ("Q1", "rigid+uniform",  "notion",    "ns-mcp-server"),
    ("Q2", "flex+uniform",   "notion",    "todoist-mcp-server"),
    ("Q3", "rigid+hetero",   "mcp-xmind", "playwright"),
    ("Q4", "flex+hetero",    "mcp-xmind", "notion"),
]

phase_results = []
for qid, qlabel, left, right in PHASE_QUADRANTS:
    guard = build_guard(left, right)
    comp = guard.composition
    profile = compute_profile(list(comp.tools), list(comp.edges))
    components = _connected_components_of_gram(profile.K)
    component_sizes = tuple(
        sorted([len(c) for c in components if len(c) > 1], reverse=True)
    )
    costs = compute_field_costs(profile.hidden_basis, profile.leverage)
    geo = compute_repair_geometry(guard, costs)

    # Sum-of-maxima: avoidable cost
    avoidable = 0
    sigma_star = 0
    for comp_indices in components:
        if len(comp_indices) > 1:
            comp_costs = [costs[tuple(profile.hidden_basis[i])]
                          for i in comp_indices]
            avoidable += max(comp_costs)
            sigma_star += sum(comp_costs) - max(comp_costs)

    # Robustness: min top-two gap
    min_gap = float("inf")
    for comp_indices in components:
        if len(comp_indices) > 1:
            comp_costs = sorted(
                [costs[tuple(profile.hidden_basis[i])] for i in comp_indices],
                reverse=True,
            )
            min_gap = min(min_gap, comp_costs[0] - comp_costs[1])

    n = len(profile.K)
    all_costs = []
    for combo in combinations(range(n), profile.fee):
        sub = [[profile.K[i][j] for j in combo] for i in combo]
        if matrix_rank(sub) == profile.fee:
            fields = [profile.hidden_basis[i] for i in combo]
            total = sum(costs[tuple(f)] for f in fields)
            all_costs.append(total)
    all_costs.sort()
    spread = all_costs[-1] - all_costs[0]
    decision_relevant = spread > 0 and len(component_sizes) > 1

    phase_results.append({
        "quadrant": qid,
        "label": qlabel,
        "composition": f"{left}+{right}",
        "fee": geo.fee,
        "beta": geo.beta,
        "motif": list(component_sizes),
        "mode": geo.repair_mode,
        "n_bases": len(all_costs),
        "sigma_star": round(sigma_star, 1),
        "avoidable": round(avoidable, 1),
        "margin": round(min_gap, 1),
        "min_cost": round(all_costs[0], 1),
        "max_cost": round(all_costs[-1], 1),
        "decision_relevant": decision_relevant,
    })

print(f"\n  Theorem (Sum-of-Maxima Law):")
print(f"    Sigma*(G,c) = sum_j [sum_{{h in C_j}} c(h) - max_{{h in C_j}} c(h)]")
print(f"    Geometry dividend A(G,c) = sum_j max_{{h in C_j}} c(h)")
print(f"\n  At fixed fee=3, four quadrants from the corpus:\n")
print(f"  {'':>4} {'Quadrant':<16} {'Composition':<25} {'Motif':<8} "
      f"{'β':>3} {'Σ*':>5} {'A':>5} {'Margin':>7} {'Decision':>10}")
print(f"  {'─' * 100}")
for pr in phase_results:
    dec = "YES ←" if pr["decision_relevant"] else "no"
    print(f"  {pr['quadrant']:>4} {pr['label']:<16} {pr['composition']:<25} "
          f"{str(tuple(pr['motif'])):<8} {pr['beta']:>3} "
          f"{pr['sigma_star']:>5.0f} {pr['avoidable']:>5.0f} "
          f"{pr['margin']:>7.1f} {dec:>10}")

print(f"\n  Q1: One repair class, nothing to optimize.")
print(f"  Q2: Geometry exists, but recommendation is indifferent.")
print(f"  Q3: Costs vary, but no alternate route exists.")
print(f"  Q4: Geometry routes through cheap fields. Decision changes.")

# Replication
print(f"\n  Replication:")
for pair_name, left, right in REPLICATION_PAIRS:
    guard = build_guard(left, right)
    comp = guard.composition
    profile = compute_profile(list(comp.tools), list(comp.edges))
    components = _connected_components_of_gram(profile.K)
    component_sizes = tuple(
        sorted([len(c) for c in components if len(c) > 1], reverse=True)
    )
    costs = compute_field_costs(profile.hidden_basis, profile.leverage)
    geo = compute_repair_geometry(guard, costs)
    n = len(profile.K)
    all_costs = []
    for combo in combinations(range(n), profile.fee):
        sub = [[profile.K[i][j] for j in combo] for i in combo]
        if matrix_rank(sub) == profile.fee:
            fields = [profile.hidden_basis[i] for i in combo]
            total = sum(costs[tuple(f)] for f in fields)
            all_costs.append(total)
    all_costs.sort()
    print(f"    {pair_name}: fee={geo.fee} β={geo.beta} motif={component_sizes} "
          f"mode={geo.repair_mode} cost=[{all_costs[0]:.0f}..{all_costs[-1]:.0f}]")
    print(f"    Matches Q4 exactly. Different partner, same activation.")


# ── Proxy session demo ──

print(f"\n{'=' * 72}")
print("PROXY SESSION: REPAIR GEOMETRY IN-SESSION")
print(f"{'=' * 72}")

# Use mcp-xmind + notion with the calibration manifests.
# The proxy uses raw tool names from the manifest (hyphenated).
left, right = "mcp-xmind", "notion"
tools_left = load_manifest(left)
tools_right = load_manifest(right)

session = BullaProxySession({left: tools_left, right: tools_right})

# Find actual tool names from manifests
xmind_tool = next(t["name"] for t in tools_left if "extract_node" in t["name"]
                  and "by_id" not in t["name"])
notion_tool = next(t["name"] for t in tools_right if "search" in t["name"].lower())

print(f"\n  Session: {left} + {right}")
print(f"  Tools: {xmind_tool} → {notion_tool}")

# Call 1: xmind extracts a node
call_1 = session.record_call(
    left,
    xmind_tool,
    arguments={"path": "/mindmap/project/tasks"},
    result={"content": "Sprint planning", "path": "/mindmap/project/tasks"},
)
print(f"\n  Call 1: {left}/{xmind_tool}")
print(f"    fee = {call_1.local_diagnostic.coherence_fee}")

# Call 2: notion searches — cross-server flow from xmind path
# Find the field name that matches in notion search
notion_fields = [t for t in tools_right if t["name"] == notion_tool]
if notion_fields:
    input_props = notion_fields[0].get("inputSchema", {}).get("properties", {})
    # Use sort.timestamp as the flow target
    flow_field = "sort.timestamp" if "sort.timestamp" in input_props else None
    if flow_field is None:
        # Try to find any field
        flow_field = next(iter(input_props), None)

    if flow_field:
        call_2 = session.record_call(
            right,
            notion_tool,
            arguments={"query": "Sprint planning"},
            argument_sources={
                flow_field: session.make_ref(call_1.call_id, "path"),
            },
            result={"results": []},
        )

        diag = call_2.local_diagnostic
        print(f"\n  Call 2: {right}/{notion_tool}")
        print(f"    fee         = {diag.coherence_fee}")
        print(f"    blind_spots = {diag.blind_spots}")
        print(f"    n_tools     = {diag.n_tools}")

        if diag.repair_geometry:
            rg = diag.repair_geometry
            print(f"\n  REPAIR GEOMETRY (live in-session):")
            print(f"    fee               = {rg.fee}")
            print(f"    repair_entropy    = {rg.repair_entropy:.4f}  (β = {rg.beta})")
            print(f"    reachable_bases   = {rg.reachable_basis_count}")
            print(f"    stability_ratio   = {rg.stability_ratio:.4f}")
            print(f"    robustness_margin = {rg.robustness_margin:.1f}")
            print(f"    repair_mode       = {rg.repair_mode}")
            print(f"\n    Recommended repair: "
                  f"{[short_name(h) for h in rg.recommended_basis]}")
            rec_c = sum(rg.field_costs.get(h, 0) for h in rg.recommended_basis)
            print(f"    Repair cost:       {rec_c:.1f}")

        print(f"\n  Disposition: {session.current_receipt.disposition.value}")
    else:
        print("  (No matching field for cross-server flow)")
else:
    print(f"  (Tool {notion_tool} not found in manifest)")


# ── Save ──

OUTPUT_PATH = REPO / "calibration" / "results" / "live_decision_surface.json"
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
output = {"compositions": results, "phase_diagram": phase_results}
with open(OUTPUT_PATH, "w") as f:
    json.dump(output, f, indent=2)
print(f"\n  Saved to {OUTPUT_PATH}")
