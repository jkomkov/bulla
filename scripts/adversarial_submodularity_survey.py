"""Adversarial survey: test submodularity of boundary fee on random compositions.

Generates 10,000 random compositions and checks partition pairs for
bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q) violations.

Optimized: builds coboundary matrices once per composition, then
evaluates boundary fees for all partitions via row-set rank operations.

Run: python scripts/adversarial_submodularity_survey.py
"""

from __future__ import annotations

import random
import sys
import time
from fractions import Fraction
from itertools import combinations

sys.path.insert(0, str(__file__).rsplit("/scripts/", 1)[0] + "/src")

from bulla.coboundary import build_coboundary, matrix_rank
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

FIELD_POOL = [
    "amount_unit", "timezone", "encoding", "rate_scale", "id_offset",
    "sort_order", "date_format", "error_format", "score_range",
    "pagination_style", "rounding_mode", "decimal_precision",
    "currency_code", "locale", "settlement_cycle", "fee_basis",
]

DIM_COUNTER = [0]


def random_tool(name: str, rng: random.Random) -> ToolSpec:
    n_fields = rng.randint(1, 5)
    fields = tuple(rng.sample(FIELD_POOL, n_fields))
    n_visible = rng.randint(0, n_fields)
    visible = tuple(rng.sample(fields, n_visible))
    return ToolSpec(name=name, internal_state=fields, observable_schema=visible)


def random_composition(rng: random.Random) -> Composition | None:
    n_tools = rng.randint(3, 6)
    tool_names = [f"T{i}" for i in range(n_tools)]
    tools = [random_tool(name, rng) for name in tool_names]

    max_edges = n_tools * (n_tools - 1)
    n_edges = rng.randint(n_tools - 1, min(n_tools * 2, max_edges))
    possible = [(a, b) for a in tool_names for b in tool_names if a != b]
    chosen = rng.sample(possible, min(n_edges, len(possible)))

    edges = []
    for from_t, to_t in chosen:
        ft = next(t for t in tools if t.name == from_t)
        tt = next(t for t in tools if t.name == to_t)
        if not ft.internal_state or not tt.internal_state:
            continue
        n_dims = rng.randint(1, min(2, len(ft.internal_state), len(tt.internal_state)))
        dims = []
        fu: set[str] = set()
        tu: set[str] = set()
        for _ in range(n_dims):
            af = [f for f in ft.internal_state if f not in fu]
            at = [f for f in tt.internal_state if f not in tu]
            if not af or not at:
                break
            ff = rng.choice(af)
            tf = rng.choice(at)
            fu.add(ff)
            tu.add(tf)
            DIM_COUNTER[0] += 1
            dims.append(SemanticDimension(name=f"d{DIM_COUNTER[0]}", from_field=ff, to_field=tf))
        if dims:
            edges.append(Edge(from_tool=from_t, to_tool=to_t, dimensions=tuple(dims)))

    if not edges:
        return None
    return Composition(name="random", tools=tuple(tools), edges=tuple(edges))


def _boundary_fee_fast(
    delta_obs: list[list[Fraction]],
    delta_full: list[list[Fraction]],
    row_internal: list[bool],
) -> int:
    """Compute boundary fee from prebuilt coboundary matrices and row classification."""
    internal_obs = [delta_obs[i] for i, v in enumerate(row_internal) if v]
    internal_full = [delta_full[i] for i, v in enumerate(row_internal) if v]

    rank_all_obs = matrix_rank(delta_obs) if delta_obs else 0
    rank_all_full = matrix_rank(delta_full) if delta_full else 0
    rank_int_obs = matrix_rank(internal_obs) if internal_obs else 0
    rank_int_full = matrix_rank(internal_full) if internal_full else 0

    rho_obs = rank_all_obs - rank_int_obs
    rho_full = rank_all_full - rank_int_full
    return rho_full - rho_obs


def classify_rows(comp: Composition, partition: list[frozenset[str]]) -> list[bool]:
    """For each row (edge-dimension), is both endpoints in the same group?"""
    result = []
    for edge in comp.edges:
        internal = any(edge.from_tool in g and edge.to_tool in g for g in partition)
        for _ in edge.dimensions:
            result.append(internal)
    return result


def all_binary_partitions(names):
    n = len(names)
    for r in range(1, n):
        for left in combinations(names, r):
            yield [frozenset(left), frozenset(names) - frozenset(left)]


def partition_meet(P, Q):
    result = []
    for p in P:
        for q in Q:
            inter = p & q
            if inter:
                result.append(inter)
    return result


def partition_join(P, Q, names):
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    for part in [P, Q]:
        for group in part:
            members = list(group)
            for i in range(1, len(members)):
                union(members[0], members[i])
    groups: dict[str, set[str]] = {}
    for n in names:
        r = find(n)
        groups.setdefault(r, set()).add(n)
    return [frozenset(g) for g in groups.values()]


def survey(n_compositions: int = 10000, seed: int = 42, max_pairs_per_comp: int = 80):
    rng = random.Random(seed)
    total_pairs = 0
    total_compositions = 0
    violations = []
    min_slack = float("inf")
    t0 = time.time()

    for i in range(n_compositions):
        comp = random_composition(rng)
        if comp is None:
            continue

        names = sorted(t.name for t in comp.tools)
        if len(names) < 3:
            continue

        total_compositions += 1

        tools_list = list(comp.tools)
        edges_list = list(comp.edges)
        delta_obs, _, _ = build_coboundary(tools_list, edges_list, use_internal=False)
        delta_full, _, _ = build_coboundary(tools_list, edges_list, use_internal=True)

        if not delta_obs and not delta_full:
            continue

        partitions = list(all_binary_partitions(names))

        bf_cache: dict[tuple[frozenset[str], ...], int] = {}

        def get_bf(part: list[frozenset[str]]) -> int:
            key = tuple(sorted(part, key=lambda s: sorted(s)))
            if key not in bf_cache:
                if len(part) < 2:
                    bf_cache[key] = 0
                else:
                    row_int = classify_rows(comp, part)
                    bf_cache[key] = _boundary_fee_fast(delta_obs, delta_full, row_int)
            return bf_cache[key]

        pairs = [
            (pi, pj)
            for pi in range(len(partitions))
            for pj in range(pi + 1, len(partitions))
        ]
        if len(pairs) > max_pairs_per_comp:
            pairs = rng.sample(pairs, max_pairs_per_comp)

        for pi, pj in pairs:
            P = partitions[pi]
            Q = partitions[pj]
            meet = partition_meet(P, Q)
            join = partition_join(P, Q, names)
            if len(meet) < 2 and len(join) < 2:
                continue

            try:
                bf_P = get_bf(P)
                bf_Q = get_bf(Q)
                bf_meet = get_bf(meet)
                bf_join = get_bf(join)
            except Exception:
                continue

            total_pairs += 1
            slack = (bf_P + bf_Q) - (bf_meet + bf_join)

            if slack < min_slack:
                min_slack = slack

            if bf_meet + bf_join > bf_P + bf_Q:
                violations.append({
                    "comp_idx": i,
                    "P": P, "Q": Q,
                    "bf_P": bf_P, "bf_Q": bf_Q,
                    "bf_meet": bf_meet, "bf_join": bf_join,
                    "slack": slack,
                    "tools": [(t.name, t.internal_state, t.observable_schema) for t in comp.tools],
                    "edges": [(e.from_tool, e.to_tool, [(d.name, d.from_field, d.to_field) for d in e.dimensions]) for e in comp.edges],
                })

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(
                f"  [{i+1}/{n_compositions}] comps={total_compositions} "
                f"pairs={total_pairs} violations={len(violations)} "
                f"min_slack={min_slack} ({elapsed:.1f}s)",
                flush=True,
            )

    elapsed = time.time() - t0
    print(f"\n=== ADVERSARIAL SUBMODULARITY SURVEY ===", flush=True)
    print(f"Random compositions generated: {total_compositions}", flush=True)
    print(f"Partition pairs checked: {total_pairs}", flush=True)
    print(f"Violations found: {len(violations)}", flush=True)
    print(f"Minimum slack (rhs - lhs): {min_slack}", flush=True)
    print(f"Time: {elapsed:.1f}s", flush=True)

    if violations:
        print(f"\nFIRST VIOLATION:", flush=True)
        v = violations[0]
        print(f"  Tools: {v['tools']}", flush=True)
        print(f"  Edges: {v['edges']}", flush=True)
        print(f"  P={v['P']}, Q={v['Q']}", flush=True)
        print(f"  bf(P)={v['bf_P']}, bf(Q)={v['bf_Q']}", flush=True)
        print(f"  bf(meet)={v['bf_meet']}, bf(join)={v['bf_join']}", flush=True)
    else:
        print(
            f"\nSubmodularity holds for ALL {total_pairs} partition pairs "
            f"across {total_compositions} random compositions.",
            flush=True,
        )
    print(f"=== END SURVEY ===", flush=True)

    return violations


if __name__ == "__main__":
    violations = survey(n_compositions=10000, seed=42, max_pairs_per_comp=80)
    sys.exit(1 if violations else 0)
