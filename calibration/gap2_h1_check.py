#!/usr/bin/env python3
"""Gap-2 harmonization check: is Bulla's fee = dim H^1 (seam complex) the SAME as SHEAF's dim H^1 (nerve)?

Critique (Block 2 gap-2) claimed they are "one H^1 at two granularities." Honest prior (witness_market_prereg
§6): they are cohomology of DIFFERENT complexes and may DIFFER. This computes both on shared scenarios.

SHEAF (sheaf.tex:186, def:nerve): H^1 = first Cech cohomology of the AGENT-OVERLAP NERVE -- vertices=agents,
1-simplices=pairwise overlaps, 2-simplices=triple overlaps. Canonical: 3 agents pairwise-overlap no-triple ->
nerve = S^1 -> H^1 != 0. Over R (generic/constant coefficients) dim H^1 = b_1(nerve) adjusted by filled cells.

Bulla (memory / diagnostic): fee = rank(delta_full) - rank(delta_obs) on the (tool,field) SEAM complex (a
cellular sheaf on the observable-interface complex), NOT the tool-graph b_1.

The mapping (faithful): agent <-> tool; pairwise overlap {A,B} on a shared convention <-> a Bulla edge A-B
carrying that dimension (HIDDEN = held, not declared, so it obstructs); triple overlap <-> the three tools
sharing/agreeing the convention (a fill). We compute SHEAF's nerve H^1 and Bulla's fee on the same overlap
structure and report the relationship. No assumption -- the numbers decide.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bulla.diagnostic import diagnose  # noqa: E402
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec  # noqa: E402


# ── SHEAF side: simplicial H^1 of the nerve over R (H^1 ≅ H_1 over a field) ─────────────────────────────────
def nerve_h1(n: int, edges: list[tuple[int, int]], triangles: list[tuple[int, int, int]]) -> int:
    """dim H_1 = nullity(∂1) - rank(∂2), with ∂1: C1->C0, ∂2: C2->C1 (simplicial boundary, R coefficients)."""
    V = list(range(n))
    E = [tuple(sorted(e)) for e in edges]
    eidx = {e: i for i, e in enumerate(E)}
    # ∂1 : edges -> vertices
    d1 = np.zeros((len(V), len(E)))
    for j, (a, b) in enumerate(E):
        d1[a, j] -= 1.0
        d1[b, j] += 1.0
    # ∂2 : triangles -> edges (oriented boundary a<b<c: +(b,c) -(a,c) +(a,b))
    d2 = np.zeros((len(E), max(len(triangles), 1)))
    for t, (a, b, c) in enumerate(sorted(tuple(sorted(tr)) for tr in triangles)):
        for sign, e in ((+1.0, (b, c)), (-1.0, (a, c)), (+1.0, (a, b))):
            d2[eidx[e], t] += sign
    rank1 = int(np.linalg.matrix_rank(d1)) if len(E) else 0
    rank2 = int(np.linalg.matrix_rank(d2)) if triangles else 0
    nullity1 = len(E) - rank1
    return nullity1 - rank2


# ── Bulla side: fee on the seam complex for the same overlap structure ──────────────────────────────────────
def bulla_fee(n: int, edges: list[tuple[int, int]], n_dims: int = 1, hidden: bool = True) -> int:
    dims = tuple(f"conv{d}" for d in range(n_dims))
    held = dims                          # every tool holds the shared convention(s)
    declared = () if hidden else dims    # hidden => obstructs
    tools = tuple(ToolSpec(f"t{i}", held, declared) for i in range(n))
    es = tuple(Edge(f"t{a}", f"t{b}", tuple(SemanticDimension(d, d, d) for d in dims)) for a, b in edges)
    return diagnose(Composition("nerve", tools, es)).coherence_fee


def cycle(n: int) -> list[tuple[int, int]]:
    return [(i, (i + 1) % n) for i in range(n)]


def path(n: int) -> list[tuple[int, int]]:
    return [(i, i + 1) for i in range(n - 1)]


def main() -> int:
    scenarios = [
        ("3-agent S^1 (pairwise, NO triple) — SHEAF canonical", 3, cycle(3), []),
        ("3-agent FILLED (triple overlap)", 3, cycle(3), [(0, 1, 2)]),
        ("4-agent square S^1 (4-cycle, no fills)", 4, cycle(4), []),
        ("3-agent TREE (path, no cycle)", 3, path(3), []),
        ("4-agent TREE (star: 0-1,0-2,0-3)", 4, [(0, 1), (0, 2), (0, 3)], []),
        ("single edge (2 agents)", 2, [(0, 1)], []),
    ]
    rows = []
    for name, n, edges, tris in scenarios:
        h1 = nerve_h1(n, edges, tris)
        fee1 = bulla_fee(n, edges, n_dims=1)
        fee2 = bulla_fee(n, edges, n_dims=2)
        b1_graph = len(edges) - n + 1            # tool-graph Betti (the "wrong object" per memory)
        rows.append({"scenario": name, "n_agents": n, "n_edges": len(edges), "n_triangles": len(tris),
                     "SHEAF_nerve_H1": h1, "tool_graph_b1": b1_graph,
                     "bulla_fee_k1": fee1, "bulla_fee_k2": fee2,
                     "fee==H1": fee1 == h1, "fee==k*H1(k=2)": fee2 == 2 * h1,
                     "fee==V-1(allhidden conn)": fee1 == (n - 1)})

    # characterize the relationship across scenarios
    all_eq_h1 = all(r["fee==H1"] for r in rows)
    all_eq_kh1 = all(r["fee==k*H1(k=2)"] for r in rows)
    all_eq_conn = all(r["bulla_fee_k1"] == (r["n_agents"] - 1) for r in rows if r["n_edges"] >= r["n_agents"] - 1)
    fee_tracks_b1 = all(r["bulla_fee_k1"] == r["tool_graph_b1"] for r in rows)

    if all_eq_h1:
        verdict = "IDENTICAL — Bulla fee == SHEAF nerve H^1 on every scenario (the critique's unity holds)."
    elif all_eq_conn:
        verdict = ("NOT ONE H^1 — the critique's unity is REFUTED. Bulla fee = dim H^1(SEAM complex) tracks "
                   "CONNECTIVITY/disclosure (V-1); SHEAF dim H^1(NERVE) tracks CYCLES (b_1). They are dim H^1 of "
                   "DIFFERENT complexes -> different numbers (2 vs 1 on the canonical S^1; >0 vs 0 on trees), and "
                   "filling a triple overlap drops SHEAF's H^1 to 0 but leaves fee unmoved. Honest prior confirmed.")
    else:
        verdict = "DIFFERENT — Bulla fee and SHEAF nerve H^1 disagree; relationship is neither identity nor k-multiple (see rows)."

    out = {
        "experiment": "gap2_h1_check (Bulla seam-complex fee vs SHEAF nerve H^1)",
        "sheaf_construction": "Cech H^1 of agent-overlap nerve (sheaf.tex:186 def:nerve); canonical S^1 example line 203",
        "bulla_construction": "fee = rank(delta_full)-rank(delta_obs), seam complex (NOT tool-graph b_1)",
        "rows": rows,
        "summary": {"all_fee==H1": all_eq_h1, "all_fee==2*H1": all_eq_kh1,
                    "all_fee==V-1(connectivity)": all_eq_conn, "fee_tracks_tool_graph_b1": fee_tracks_b1},
        "three_objects_the_critique_fused": {
            "1_bulla_fee": "dim H^1(SEAM complex) = a VALUE-BLIND disclosure/connectivity count (V-1). Schema-only.",
            "2_sheaf_dim_H1": "dim H^1(NERVE) = b_1 of the agent-overlap nerve = a TOPOLOGICAL overlap-cycle count.",
            "3_sheaf_obstruction_class": "[alpha] in H^1(nerve) = the VALUE-AWARE HOLONOMY (does a global reconciliation exist). SHEAF's actual diagnostic.",
            "relation": "1 != 2 (computed: different complexes). 1 is BLIND to 3: fee's signature is schema-only, so "
                        "(8a56a00) it is constant across ALL reconciliation data while [alpha] is a function of that data "
                        "-- mapping-independent. So Bulla fee is neither SHEAF dimension nor SHEAF obstruction.",
        },
        "harmonization": (
            "SHEAF's obstruction class [alpha] (value-aware holonomy) aligns with the program's deferred "
            "PERFORMANCE layer (the premium / cycle-holonomy), NOT the value-blind TYPE-layer fee. So SHEAF slots "
            "into the type/performance split as the performance-side obstruction; the two registers are the program's "
            "TWO LAYERS, not one H^1. The value-blindness foreclosure (8a56a00) is exactly the wedge between them. "
            "Bulla's own holonomy/severity (cycle-holonomy law mu_G, severity doctrine) is the right counterpart to "
            "compare against SHEAF's [alpha] -- a separate, value-aware comparison, not this value-blind fee."),
        "VERDICT": verdict,
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "gap2_h1_check.json").write_text(json.dumps(out, indent=2) + "\n")

    w = max(len(r["scenario"]) for r in rows)
    print(f"{'scenario':<{w}}  SHEAF_H1  tgraph_b1  fee(k=1)  fee(k=2)")
    for r in rows:
        print(f"{r['scenario']:<{w}}  {r['SHEAF_nerve_H1']:>8}  {r['tool_graph_b1']:>9}  {r['bulla_fee_k1']:>8}  {r['bulla_fee_k2']:>8}")
    print(f"\nfee==SHEAF_H1 everywhere: {all_eq_h1}   |   fee==V-1 (connectivity) everywhere: {all_eq_conn}")
    print(f"VERDICT: {verdict}")
    print(f"artifact: {HERE/'results'/'gap2_h1_check.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
