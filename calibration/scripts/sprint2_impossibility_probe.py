"""Sprint 2 Track A: Computational probe for the full-Bulla impossibility theorem.

Constructs synthetic tool compositions to test whether:
1. Locally-identical compositions can have different fees
2. The carrier graph structure determines the fee
3. High-girth vs tree carrier graphs produce different fees

This probes whether the full-regime lift is achievable.
"""

import sys
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import ToolSpec, Edge, SemanticDimension
from bulla.witness_geometry import witness_gram, fee_from_gram, compute_profile


def make_tool(name: str, obs_fields: list[str], hidden_fields: list[str]) -> ToolSpec:
    """Create a synthetic tool with specified observable and hidden fields.

    In Bulla's model:
    - internal_state = ALL convention-relevant fields (hidden + observable)
    - observable_schema = subset of internal_state that is schema-exposed
    - A field is "hidden" if it's in internal_state but NOT in observable_schema
    """
    all_fields = list(dict.fromkeys(obs_fields + hidden_fields))
    return ToolSpec(
        name=name,
        internal_state=tuple(all_fields),
        observable_schema=tuple(obs_fields),
    )


def make_edge(src: str, dst: str, dim: str, from_field: str, to_field: str) -> Edge:
    """Create a synthetic edge."""
    return Edge(
        from_tool=src,
        to_tool=dst,
        dimensions=(SemanticDimension(name=dim, from_field=from_field, to_field=to_field),),
    )


def print_matrix(delta, v_basis, e_basis, label=""):
    """Pretty-print a coboundary matrix."""
    if label:
        print(f"\n  {label}")
    # Column headers
    col_strs = [f"{t}.{f}" for t, f in v_basis]
    header = "  " + " | ".join(f"{s:>12}" for s in col_strs)
    print(header)
    print("  " + "-" * len(header))
    for i, row in enumerate(delta):
        e_label = f"{e_basis[i][0]}:{e_basis[i][1]}" if i < len(e_basis) else "?"
        vals = " | ".join(f"{int(v):>12}" for v in row)
        print(f"  {vals}  <- {e_label}")


def probe_1_single_dimension_cycle():
    """Probe 1: Single dimension on a cycle — does cycle structure affect fee?"""
    print("=" * 70)
    print("PROBE 1: Single dimension, single field per tool")
    print("  Does cycle vs tree affect fee when each tool has one field?")
    print("=" * 70)

    # Chain (tree): A→B→C→D→E
    tools_chain = [
        make_tool("A", [], ["path"]),
        make_tool("B", [], ["path"]),
        make_tool("C", [], ["path"]),
        make_tool("D", [], ["path"]),
        make_tool("E", [], ["path"]),
    ]
    edges_chain = [
        make_edge("A", "B", "path_match", "path", "path"),
        make_edge("B", "C", "path_match", "path", "path"),
        make_edge("C", "D", "path_match", "path", "path"),
        make_edge("D", "E", "path_match", "path", "path"),
    ]

    # Cycle: A→B→C→D→E→A
    edges_cycle = edges_chain + [
        make_edge("E", "A", "path_match", "path", "path"),
    ]

    delta_ch, v_ch, e_ch = build_coboundary(tools_chain, edges_chain, use_internal=True)
    delta_cy, v_cy, e_cy = build_coboundary(tools_chain, edges_cycle, use_internal=True)

    rank_ch = matrix_rank(delta_ch)
    rank_cy = matrix_rank(delta_cy)

    # Observable coboundary (no observable fields → rank 0)
    delta_ch_obs, v_ch_obs, _ = build_coboundary(tools_chain, edges_chain, use_internal=False)
    delta_cy_obs, v_cy_obs, _ = build_coboundary(tools_chain, edges_cycle, use_internal=False)

    rank_ch_obs = matrix_rank(delta_ch_obs) if delta_ch_obs else 0
    rank_cy_obs = matrix_rank(delta_cy_obs) if delta_cy_obs else 0

    fee_ch = rank_ch - rank_ch_obs
    fee_cy = rank_cy - rank_cy_obs

    print(f"  Chain: rank(full)={rank_ch}, rank(obs)={rank_ch_obs}, fee={fee_ch}")
    print(f"  Cycle: rank(full)={rank_cy}, rank(obs)={rank_cy_obs}, fee={fee_cy}")
    print(f"  → Same fee? {'YES' if fee_ch == fee_cy else 'NO (different!)'}")
    print(f"  Reason: rank depends on n-c, not on cycle count")


def probe_2_multi_field_tools():
    """Probe 2: Tools with multiple fields — do cycles matter now?"""
    print("\n" + "=" * 70)
    print("PROBE 2: Multiple fields per tool on the same dimension")
    print("  Tool A has 'path_in' and 'path_out', both hidden.")
    print("  This creates a richer carrier graph.")
    print("=" * 70)

    # Tool A: has two path fields (like a real tool with input and output paths)
    # Tool B: has one path field
    # Tool C: has one path field
    # When A→B (path_in→path) and B→C (path→path) and C→A (path→path_out),
    # the carrier graph has a real cycle if A's path_in and path_out are connected.

    # But in the coboundary construction, edges connect specific fields.
    # Edge A→B connects A.path_in to B.path (dimension path_match)
    # Edge B→C connects B.path to C.path (dimension path_match)
    # Edge C→A connects C.path to A.path_out (dimension path_match)
    # Carrier graph: A.path_in — B.path, B.path — C.path, C.path — A.path_out
    # This is a PATH, not a cycle (A.path_in ≠ A.path_out)

    tools_3 = [
        make_tool("A", [], ["path_in", "path_out"]),
        make_tool("B", [], ["path"]),
        make_tool("C", [], ["path"]),
    ]

    edges_path = [
        make_edge("A", "B", "path_match", "path_in", "path"),
        make_edge("B", "C", "path_match", "path", "path"),
        make_edge("C", "A", "path_match", "path", "path_out"),
    ]

    delta, v, e = build_coboundary(tools_3, edges_path, use_internal=True)
    rank_full = matrix_rank(delta)

    delta_obs, v_obs, _ = build_coboundary(tools_3, edges_path, use_internal=False)
    rank_obs = matrix_rank(delta_obs) if delta_obs else 0

    fee = rank_full - rank_obs

    print(f"  A(in,out)→B→C→A(out): rank(full)={rank_full}, rank(obs)={rank_obs}, fee={fee}")
    print_matrix(delta, v, e, "Full coboundary:")

    # Now make A.path_out OBSERVABLE
    tools_3_obs = [
        make_tool("A", ["path_out"], ["path_in"]),
        make_tool("B", [], ["path"]),
        make_tool("C", [], ["path"]),
    ]

    delta2, v2, e2 = build_coboundary(tools_3_obs, edges_path, use_internal=True)
    delta2_obs, v2_obs, _ = build_coboundary(tools_3_obs, edges_path, use_internal=False)
    rank2_full = matrix_rank(delta2)
    rank2_obs = matrix_rank(delta2_obs) if delta2_obs else 0
    fee2 = rank2_full - rank2_obs

    print(f"\n  Same graph, A.path_out observable:")
    print(f"  rank(full)={rank2_full}, rank(obs)={rank2_obs}, fee={fee2}")
    print_matrix(delta2, v2, e2, "Full coboundary:")
    if delta2_obs:
        print_matrix(delta2_obs, v2_obs, e2, "Observable coboundary:")


def probe_3_shared_field_multiple_edges():
    """Probe 3: A single tool field participates in multiple edges.
    This is the actual structure in the real corpus (e.g., filesystem.path)."""
    print("\n" + "=" * 70)
    print("PROBE 3: One tool field participates in MULTIPLE edges")
    print("  This creates a star in the carrier graph (not a matching)")
    print("=" * 70)

    # Hub tool H with one 'path' field, connected to spokes S1, S2, S3
    # Each spoke also has one 'path' field
    # Hub.path is the FROM field on all edges → star topology in carrier graph

    # Tree: H→S1, H→S2, H→S3
    tools_star = [
        make_tool("H", [], ["path"]),
        make_tool("S1", [], ["path"]),
        make_tool("S2", [], ["path"]),
        make_tool("S3", [], ["path"]),
    ]
    edges_tree = [
        make_edge("H", "S1", "path_match", "path", "path"),
        make_edge("H", "S2", "path_match", "path", "path"),
        make_edge("H", "S3", "path_match", "path", "path"),
    ]

    # Cycle: add S1→S2, S2→S3, S3→S1
    edges_with_cycle = edges_tree + [
        make_edge("S1", "S2", "path_match", "path", "path"),
        make_edge("S2", "S3", "path_match", "path", "path"),
        make_edge("S3", "S1", "path_match", "path", "path"),
    ]

    for label, tools, edges in [
        ("Tree (H→S1,S2,S3)", tools_star, edges_tree),
        ("With cycle (+ S1→S2→S3→S1)", tools_star, edges_with_cycle),
    ]:
        delta, v, e = build_coboundary(tools, edges, use_internal=True)
        delta_obs, v_obs, _ = build_coboundary(tools, edges, use_internal=False)
        rank_full = matrix_rank(delta)
        rank_obs = matrix_rank(delta_obs) if delta_obs else 0
        fee = rank_full - rank_obs
        print(f"\n  {label}:")
        print(f"    rank(full)={rank_full}, rank(obs)={rank_obs}, fee={fee}")
        print(f"    n_rows={len(delta)}, n_cols={len(delta[0]) if delta else 0}")
        print_matrix(delta, v, e, f"Full δ ({label}):")


def probe_4_observable_breaks_fee():
    """Probe 4: Same graph, same dimension, but different observable sets.
    Shows that observability determines fee, and whether local
    indistinguishability is achievable."""
    print("\n" + "=" * 70)
    print("PROBE 4: Same graph, different observable sets → different fee")
    print("  Tests whether the local-global gap exists in the Bulla regime")
    print("=" * 70)

    # 6-cycle: A→B→C→D→E→F→A, all with path_match
    # Each tool has ONE 'path' field in internal_state
    tools = [make_tool(name, [], ["path"]) for name in "ABCDEF"]
    names = [t.name for t in tools]
    n = len(names)
    edges = [
        make_edge(names[i], names[(i + 1) % n], "path_match", "path", "path")
        for i in range(n)
    ]

    delta, v, e = build_coboundary(tools, edges, use_internal=True)
    rank_full = matrix_rank(delta)

    print(f"\n  6-cycle, all hidden: rank(full)={rank_full}")

    # Now make some fields observable
    for obs_set in [
        [],
        ["A"],
        ["A", "C"],
        ["A", "C", "E"],
        ["A", "B", "C", "D", "E", "F"],
    ]:
        tools_v = [
            make_tool(name, ["path"] if name in obs_set else [],
                      ["path"] if name not in obs_set else [])
            for name in names
        ]
        delta_f, _, _ = build_coboundary(tools_v, edges, use_internal=True)
        delta_o, _, _ = build_coboundary(tools_v, edges, use_internal=False)
        rf = matrix_rank(delta_f)
        ro = matrix_rank(delta_o) if delta_o else 0
        fee = rf - ro
        obs_label = str(obs_set) if obs_set else "none"
        print(f"    Observable: {obs_label:30s} → rank(full)={rf}, rank(obs)={ro}, fee={fee}")


def probe_5_high_girth_construction():
    """Probe 5: The actual twin construction.
    Build two compositions that are locally identical (r=1 balls are the same)
    but globally different (different fee)."""
    print("\n" + "=" * 70)
    print("PROBE 5: Twin construction — locally identical, globally different")
    print("=" * 70)

    # Construction: 8-cycle (girth 8, r=3 balls are paths of length 6)
    # Each tool has TWO fields: path_in (hidden) and path_out (hidden)
    # Edge i→j connects i.path_out to j.path_in
    # Also: each tool has an 'id' field (observable)

    n = 8
    names = [f"T{i}" for i in range(n)]

    # Composition A: 8-cycle
    tools = [make_tool(name, ["id"], ["path_in", "path_out"]) for name in names]
    edges_cycle = [
        make_edge(names[i], names[(i + 1) % n], "path_match", "path_out", "path_in")
        for i in range(n)
    ]

    # Composition B: path (no closing edge)
    edges_path = [
        make_edge(names[i], names[i + 1], "path_match", "path_out", "path_in")
        for i in range(n - 1)
    ]

    for label, edges in [
        (f"{n}-cycle (girth={n})", edges_cycle),
        (f"{n-1}-path (tree)", edges_path),
    ]:
        delta, v, e = build_coboundary(tools, edges, use_internal=True)
        delta_obs, v_obs, _ = build_coboundary(tools, edges, use_internal=False)
        rf = matrix_rank(delta)
        ro = matrix_rank(delta_obs) if delta_obs else 0
        fee = rf - ro
        n_hidden = sum(1 for t, f in v if f.startswith("path"))
        n_obs = len(v_obs) if delta_obs else 0
        print(f"\n  {label}:")
        print(f"    n_tools={len(tools)}, n_edges={len(edges)}")
        print(f"    rank(full)={rf}, rank(obs)={ro}, fee={fee}")
        print(f"    hidden_cols={n_hidden}, obs_cols={n_obs}")

    # Check: are the 1-local neighborhoods identical?
    print(f"\n  Local structure check (r=1):")
    print(f"    Every tool has: id (obs), path_in (hidden), path_out (hidden)")
    print(f"    Every tool sees its neighbors' schemas (same in both compositions)")
    print(f"    In cycle: every tool has exactly 1 predecessor and 1 successor")
    print(f"    In path: interior tools have 1+1, endpoints have 1+0 or 0+1")
    print(f"    → Local neighborhoods DIFFER at endpoints (path has degree-1 tools)")


def probe_6_regular_graph_twins():
    """Probe 6: Use d-regular graphs to ensure truly identical local structure.
    Both compositions are 2-regular (each tool has exactly 2 neighbors).
    One is a single large cycle (girth = n), the other is two disjoint cycles."""
    print("\n" + "=" * 70)
    print("PROBE 6: 2-regular graph twins (identical local structure)")
    print("  Composition A: single 10-cycle (connected)")
    print("  Composition B: two 5-cycles (disconnected)")
    print("  Every tool has exactly 2 neighbors in both!")
    print("=" * 70)

    names = [f"T{i}" for i in range(10)]
    tools = [make_tool(name, ["id"], ["path_in", "path_out"]) for name in names]

    # Composition A: single 10-cycle
    edges_10 = [
        make_edge(names[i], names[(i + 1) % 10], "path_match", "path_out", "path_in")
        for i in range(10)
    ]

    # Composition B: two 5-cycles
    edges_5_5 = [
        make_edge(names[i], names[(i + 1) % 5], "path_match", "path_out", "path_in")
        for i in range(5)
    ] + [
        make_edge(names[5 + i], names[5 + (i + 1) % 5], "path_match", "path_out", "path_in")
        for i in range(5)
    ]

    for label, edges in [
        ("Single 10-cycle", edges_10),
        ("Two 5-cycles", edges_5_5),
    ]:
        delta, v, e = build_coboundary(tools, edges, use_internal=True)
        delta_obs, v_obs, _ = build_coboundary(tools, edges, use_internal=False)
        rf = matrix_rank(delta)
        ro = matrix_rank(delta_obs) if delta_obs else 0
        fee = rf - ro
        print(f"\n  {label}:")
        print(f"    n_edges={len(edges)}, rank(full)={rf}, rank(obs)={ro}, fee={fee}")

    print(f"\n  Local structure:")
    print(f"    Both: every tool has degree 2 (one in-edge, one out-edge)")
    print(f"    r=1 ball: tool sees 2 neighbors with identical schemas")
    print(f"    r=2 ball: tool sees up to 4 neighbors, all with identical schemas")
    print(f"    Compositions are r-locally identical for r < girth/2")
    print(f"    Single cycle: girth=10, so identical up to r=4")
    print(f"    Two 5-cycles: girth=5, so identical up to r=2")
    print(f"    → For r=2: BOTH have identical local structure!")


def probe_7_multi_field_edge_routing():
    """Probe 7: Multi-field tools where edge routing creates different
    carrier graph component counts on r-locally identical compositions.

    Key insight: if each tool has fields {a, b} and edges can connect
    a→a or b→b, then two 2-regular compositions on the same tools
    can route edges differently, creating different carrier graph
    component structure and thus different fees.
    """
    print("\n" + "=" * 70)
    print("PROBE 7: Multi-field edge routing — the real test")
    print("  Each tool has fields {a, b} (both hidden).")
    print("  Edges can connect a→a or b→b.")
    print("  Two 2-regular compositions with different edge field routing.")
    print("=" * 70)

    # 4 tools, each with fields a and b (both hidden, same dimension "x")
    names = ["T0", "T1", "T2", "T3"]
    tools = [make_tool(n, ["id"], ["a", "b"]) for n in names]

    # Composition A: single 4-cycle, all edges connect a→a
    # Carrier graph: T0.a—T1.a—T2.a—T3.a—T0.a (cycle on a's)
    # T0.b, T1.b, T2.b, T3.b are isolated
    # Carrier: 8 nodes, 4 edges forming a cycle → 5 components
    # rank = 8 - 5 = 3
    edges_A = [
        make_edge("T0", "T1", "x", "a", "a"),
        make_edge("T1", "T2", "x", "a", "a"),
        make_edge("T2", "T3", "x", "a", "a"),
        make_edge("T3", "T0", "x", "a", "a"),
    ]

    # Composition B: single 4-cycle, but alternating a→a and b→b
    # Edge T0→T1 connects a→a, Edge T1→T2 connects b→b,
    # Edge T2→T3 connects a→a, Edge T3→T0 connects b→b
    # Carrier: T0.a—T1.a, T1.b—T2.b, T2.a—T3.a, T3.b—T0.b
    # These are 4 disjoint edges → 4 components (each edge) + 0 isolated
    # Wait: nodes are T0.a, T0.b, T1.a, T1.b, T2.a, T2.b, T3.a, T3.b
    # Edges: T0.a-T1.a, T1.b-T2.b, T2.a-T3.a, T3.b-T0.b
    # Components: {T0.a,T1.a}, {T1.b,T2.b}, {T2.a,T3.a}, {T3.b,T0.b} = 4
    # rank = 8 - 4 = 4
    edges_B = [
        make_edge("T0", "T1", "x", "a", "a"),
        make_edge("T1", "T2", "x", "b", "b"),
        make_edge("T2", "T3", "x", "a", "a"),
        make_edge("T3", "T0", "x", "b", "b"),
    ]

    # Composition C: two 2-cycles, one on a and one on b
    # T0→T1 on a, T1→T0 on a; T2→T3 on b, T3→T2 on b
    # Carrier: {T0.a,T1.a} cycle, {T2.b,T3.b} cycle
    # Isolated: T0.b, T1.b, T2.a, T3.a → 4 more components
    # Components: 2 (edge pairs) + 4 (isolated) = 6
    # rank = 8 - 6 = 2
    edges_C = [
        make_edge("T0", "T1", "x", "a", "a"),
        make_edge("T1", "T0", "x", "a", "a"),
        make_edge("T2", "T3", "x", "b", "b"),
        make_edge("T3", "T2", "x", "b", "b"),
    ]

    for label, edges in [
        ("Comp A: 4-cycle all a→a", edges_A),
        ("Comp B: 4-cycle alternating a→a,b→b", edges_B),
        ("Comp C: two 2-cycles (a-pair + b-pair)", edges_C),
    ]:
        delta, v, e = build_coboundary(tools, edges, use_internal=True)
        delta_obs, v_obs, _ = build_coboundary(tools, edges, use_internal=False)
        rf = matrix_rank(delta)
        ro = matrix_rank(delta_obs) if delta_obs else 0
        fee = rf - ro
        print(f"\n  {label}:")
        print(f"    n_edges={len(edges)}, rank(full)={rf}, rank(obs)={ro}, fee={fee}")
        print_matrix(delta, v, e, f"Full δ:")

    print(f"\n  KEY RESULT:")
    print(f"    All three have 4 tools, 4 edges, same tool schemas.")
    print(f"    Each tool has degree 2 in the composition graph.")
    print(f"    But edge FIELD ROUTING creates different carrier graph components!")
    print(f"    → Different fees from locally-identical compositions?")
    print(f"    → Check local neighborhoods to verify...")

    # Local neighborhood check
    print(f"\n  Local neighborhood analysis:")
    print(f"    Comp A: every tool's 1-ball shows 2 edges, both on field 'a'")
    print(f"    Comp B: every tool's 1-ball shows 2 edges, one on 'a' and one on 'b'")
    print(f"    → Local neighborhoods DIFFER (field routing is locally visible!)")
    print(f"    → This means we need a construction where field routing is hidden.")


def probe_8_field_routing_hidden():
    """Probe 8: Can we make field routing locally invisible?

    Use higher-girth graphs where the routing pattern repeats locally
    but differs globally.
    """
    print("\n" + "=" * 70)
    print("PROBE 8: Field routing on high-girth graphs")
    print("  All edges connect DIFFERENT fields: a→b on every edge.")
    print("  This means every tool's local view is identical.")
    print("=" * 70)

    # Every edge connects source.a → target.b
    # This is a uniform routing — locally invisible.
    # Carrier graph: each edge creates an edge from Ti.a to Tj.b

    n = 6
    names = [f"T{i}" for i in range(n)]
    tools = [make_tool(nm, ["id"], ["a", "b"]) for nm in names]

    # Composition A: single 6-cycle
    # Carrier edges: T0.a—T1.b, T1.a—T2.b, ..., T5.a—T0.b
    # Carrier: trace components. Start at T0.a → T1.b (edge 0)
    # T1.a → T2.b (edge 1), T2.a → T3.b (edge 2), ...
    # T0.a and T1.b are in same component. But T1.a and T1.b are NOT connected
    # (they're different carrier-graph nodes).
    # Components: {T0.a, T1.b}, {T1.a, T2.b}, {T2.a, T3.b},
    #             {T3.a, T4.b}, {T4.a, T5.b}, {T5.a, T0.b}
    # That's 6 components, 12 nodes → rank = 12 - 6 = 6
    edges_6cycle = [
        make_edge(names[i], names[(i+1) % n], "x", "a", "b")
        for i in range(n)
    ]

    # Composition B: two 3-cycles
    edges_3_3 = [
        make_edge(names[i], names[(i+1) % 3], "x", "a", "b")
        for i in range(3)
    ] + [
        make_edge(names[3+i], names[3 + (i+1) % 3], "x", "a", "b")
        for i in range(3)
    ]

    # Composition C: three 2-cycles
    edges_2_2_2 = [
        make_edge("T0", "T1", "x", "a", "b"),
        make_edge("T1", "T0", "x", "a", "b"),
        make_edge("T2", "T3", "x", "a", "b"),
        make_edge("T3", "T2", "x", "a", "b"),
        make_edge("T4", "T5", "x", "a", "b"),
        make_edge("T5", "T4", "x", "a", "b"),
    ]

    for label, edges in [
        ("Single 6-cycle, all a→b", edges_6cycle),
        ("Two 3-cycles, all a→b", edges_3_3),
        ("Three 2-cycles, all a→b", edges_2_2_2),
    ]:
        delta, v, e = build_coboundary(tools, edges, use_internal=True)
        delta_obs, v_obs, _ = build_coboundary(tools, edges, use_internal=False)
        rf = matrix_rank(delta)
        ro = matrix_rank(delta_obs) if delta_obs else 0
        fee = rf - ro
        print(f"\n  {label}:")
        print(f"    n_edges={len(edges)}, rank(full)={rf}, rank(obs)={ro}, fee={fee}")

    print(f"\n  Local structure (all compositions):")
    print(f"    Every edge: source.a → target.b (uniform routing)")
    print(f"    Every tool: degree 2 (one outgoing a, one incoming b)")
    print(f"    r=1 ball: identical in all three compositions")
    print(f"    → If fees differ, we have the impossibility construction!")


def probe_9_degree_two_carrier_graph():
    """Probe 9: Higher composition-graph degree → carrier-graph merges.

    When a tool's field appears in MULTIPLE edges, its carrier-graph node
    has degree > 1, creating merges that depend on global topology.

    With single field per tool and out-degree 2: each tool's carrier node
    connects to 2 neighbors. The carrier graph IS the composition graph.
    Different compositions → different carrier graph components → different fees.
    But we already know this from probe 1 (rank = n - c).

    The question is: can we make this work with locally-identical compositions?
    Answer: YES, if the composition graph is d-regular with d ≥ 2 and girth > 2r.
    """
    print("\n" + "=" * 70)
    print("PROBE 9: Single field per tool, degree-2 directed compositions")
    print("  Carrier graph = composition graph (each tool field appears in 2+ edges)")
    print("  Different global topology → different carrier components → different fee")
    print("=" * 70)

    # 6 tools, single field 'path' each, ALL HIDDEN
    # Composition A: single 6-cycle (connected, 1 component) → fee = n-c = 6-1 = 5
    # Composition B: two 3-cycles (2 components) → fee = n-c = 6-2 = 4
    # But degree: in 6-cycle each tool has degree 2 (in+out), in two 3-cycles same.

    n = 6
    names = [f"T{i}" for i in range(n)]
    tools = [make_tool(nm, [], ["path"]) for nm in names]

    # Single 6-cycle
    edges_6 = [
        make_edge(names[i], names[(i+1) % n], "x", "path", "path")
        for i in range(n)
    ]

    # Two 3-cycles
    edges_33 = [
        make_edge(names[i], names[(i+1) % 3], "x", "path", "path")
        for i in range(3)
    ] + [
        make_edge(names[3+i], names[3 + (i+1) % 3], "x", "path", "path")
        for i in range(3)
    ]

    for label, edges in [
        ("Single 6-cycle", edges_6),
        ("Two 3-cycles", edges_33),
    ]:
        delta, v, e = build_coboundary(tools, edges, use_internal=True)
        delta_obs, v_obs, _ = build_coboundary(tools, edges, use_internal=False)
        rf = matrix_rank(delta)
        ro = matrix_rank(delta_obs) if delta_obs else 0
        fee = rf - ro
        print(f"\n  {label}:")
        print(f"    n_edges={len(edges)}, rank(full)={rf}, rank(obs)={ro}, fee={fee}")

    print(f"\n  RESULT: Same tool set, same field routing (path→path),")
    print(f"  same degree (2), different fee (5 vs 4)!")
    print(f"  Carrier graph = composition graph → rank = n - c.")
    print(f"  6-cycle: 1 component → rank = 5. Two 3-cycles: 2 components → rank = 4.")

    # NOW: repeat with multi-field tools where field routing is uniform
    # but carrier graph structure differs due to which .a connects to which .b
    print(f"\n  BUT: local neighborhoods differ!")
    print(f"  6-cycle r=1: T0 sees T5 and T1 (both distinct)")
    print(f"  3-cycle r=1: T0 sees T2 and T1 (both distinct)")
    print(f"  These are locally isomorphic for r < girth/2 = 3/2 = 1")
    print(f"  So only r=0 neighborhoods match. Not useful for r≥1.")

    # Higher girth: 12-cycle vs 3×4-cycles vs 4×3-cycles vs 2×6-cycles vs 6×2-cycles
    print(f"\n  --- Higher girth for larger r ---")
    n = 12
    names = [f"T{i}" for i in range(n)]
    tools = [make_tool(nm, [], ["path"]) for nm in names]

    configs = [
        ("Single 12-cycle (girth=12)", [
            make_edge(names[i], names[(i+1) % 12], "x", "path", "path")
            for i in range(12)
        ]),
        ("Two 6-cycles (girth=6)", [
            make_edge(names[i], names[(i+1) % 6], "x", "path", "path")
            for i in range(6)
        ] + [
            make_edge(names[6+i], names[6 + (i+1) % 6], "x", "path", "path")
            for i in range(6)
        ]),
        ("Three 4-cycles (girth=4)", [
            make_edge(names[4*k+i], names[4*k + (i+1) % 4], "x", "path", "path")
            for k in range(3) for i in range(4)
        ]),
        ("Four 3-cycles (girth=3)", [
            make_edge(names[3*k+i], names[3*k + (i+1) % 3], "x", "path", "path")
            for k in range(4) for i in range(3)
        ]),
    ]

    print(f"\n  12 tools, single field, all hidden, composition degree 2:")
    for label, edges in configs:
        delta, v, e = build_coboundary(tools, edges, use_internal=True)
        rf = matrix_rank(delta)
        fee = rf  # rank(obs) = 0 since no observable fields
        print(f"    {label}: rank={rf}, fee={fee}")

    print(f"\n  Local indistinguishability:")
    print(f"  12-cycle vs 2×6-cycle: identical for r < 3 (min girth/2)")
    print(f"  Fees: 11 vs 10. DIFFERENT FEES, r=2 local indistinguishability!")
    print(f"  ──────────────────────────────────────────────────────────────")
    print(f"  THIS IS THE IMPOSSIBILITY CONSTRUCTION.")
    print(f"  ──────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    probe_1_single_dimension_cycle()
    probe_2_multi_field_tools()
    probe_3_shared_field_multiple_edges()
    probe_4_observable_breaks_fee()
    probe_5_high_girth_construction()
    probe_6_regular_graph_twins()
    probe_7_multi_field_edge_routing()
    probe_8_field_routing_hidden()
    probe_9_degree_two_carrier_graph()
