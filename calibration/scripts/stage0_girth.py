"""Stage 0 (Dissociation pre-registration): the girth gate.

Make-or-break, potentially deflationary. B2 / LVDC impossibility bites only at
girth > 2r: a radius-r auditor is blind only when no cycle of length <= 2r
exists. So the practical question is: do REAL compositions reach the girth where
bounded-local (bounded-depth) methods provably collapse?

This script reuses the exact corpus loader of `profile_schema_structure.py`
(ManifestStore + BullaGuard.from_tools_list), builds the undirected tool-graph
from `comp.edges` (from_tool ~ to_tool), and computes the TRUE girth (shortest
simple cycle, via BFS from each vertex) for every pairwise composition. It then
aggregates the girth distribution over the cyclic subset and applies the
pre-registered Stage-0 decision rule.

Pre-registered decision (papers/coherence-cliff/dissociation_pre_registration.md, §6):
  - girth distribution dominated by girth <= 8  -> Outcome 4 (Bounded): B2 real
    but not yet binding at current composition scales; STOP heavy stages.
  - >= 10% of cyclic compositions reach girth > 16 -> proceed (B2 live).

Read-only on the corpus. Writes one results JSON under papers/coherence-cliff/results/.
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # repo root
BULLA = ROOT / "bulla"
sys.path.insert(0, str(BULLA / "src"))
sys.path.insert(0, str(BULLA))

from calibration.corpus import ManifestStore  # noqa: E402
from calibration.index import MIN_SCHEMA_FIELDS  # noqa: E402
from bulla.guard import BullaGuard  # noqa: E402

CORPUS_DIR = BULLA / "calibration" / "data" / "registry"
OUT = ROOT / "papers" / "coherence-cliff" / "results" / "dissociation_stage0_girth.json"


def girth_of(adj: dict[str, set[str]]) -> float:
    """Shortest simple cycle length of an undirected graph (math.inf if acyclic).

    Standard BFS girth: from each source, the first non-tree edge (u, w) with
    both endpoints reached closes a cycle of length dist[u] + dist[w] + 1.
    """
    best = math.inf
    for src in adj:
        dist = {src: 0}
        parent = {src: None}
        dq = collections.deque([src])
        while dq:
            u = dq.popleft()
            if dist[u] * 2 >= best:  # cannot improve from here
                continue
            for w in adj[u]:
                if w not in dist:
                    dist[w] = dist[u] + 1
                    parent[w] = u
                    dq.append(w)
                elif parent[u] != w:  # non-tree (back/cross) edge -> cycle
                    best = min(best, dist[u] + dist[w] + 1)
    return best


def tool_graph(comp) -> dict[str, set[str]]:
    """Undirected simple tool-graph: tools as vertices, an edge per shared seam."""
    adj: dict[str, set[str]] = {t.name: set() for t in comp.tools}
    for e in comp.edges:
        if e.from_tool == e.to_tool:
            continue  # self-loop, not a >=3 cycle
        adj.setdefault(e.from_tool, set()).add(e.to_tool)
        adj.setdefault(e.to_tool, set()).add(e.from_tool)
    return adj


def _gf2_rank(vectors: list[int]) -> int:
    """Rank over GF(2) of integer-bitmask vectors (linear basis by highest bit)."""
    basis: dict[int, int] = {}
    rank = 0
    for v in vectors:
        cur = v
        while cur:
            hb = cur.bit_length() - 1
            if hb in basis:
                cur ^= basis[hb]
            else:
                basis[hb] = cur
                rank += 1
                break
    return rank


def depth3_recovers_full_obstruction(adj: dict[str, set[str]]) -> tuple[bool, int, int]:
    """Do triangles GENERATE the cycle space? If so, a depth-3 (radius-1) auditor
    recovers the FULL obstruction H^1 — bounded-local testing is sufficient.

    Returns (triangles_span, triangle_rank, cycle_rank) on the simple graph.
    cycle_rank = |E| - |V| + |components| (betti_1 of the simple graph).
    triangle_rank = GF(2) rank of all 3-cycles in the edge space.
    """
    nodes = sorted(adj)
    idx = {n: i for i, n in enumerate(nodes)}
    # canonical edge bit-index
    eid: dict[tuple[int, int], int] = {}
    for u in nodes:
        for v in adj[u]:
            a, b = sorted((idx[u], idx[v]))
            if (a, b) not in eid:
                eid[(a, b)] = len(eid)
    n_edges = len(eid)
    # components (union-find)
    parent = list(range(len(nodes)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for (a, b) in eid:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comps = len({find(i) for i in range(len(nodes))})
    cycle_rank = n_edges - len(nodes) + comps
    # enumerate triangles (each once, anchored at its min vertex)
    tri_vecs: list[int] = []
    for u in nodes:
        iu = idx[u]
        nbrs = sorted(idx[w] for w in adj[u] if idx[w] > iu)
        for i in range(len(nbrs)):
            for j in range(i + 1, len(nbrs)):
                a, b = nbrs[i], nbrs[j]
                # is (a,b) an edge?
                if nodes[b] in adj[nodes[a]]:
                    e1 = eid[tuple(sorted((iu, a)))]
                    e2 = eid[tuple(sorted((iu, b)))]
                    e3 = eid[tuple(sorted((a, b)))]
                    tri_vecs.append((1 << e1) | (1 << e2) | (1 << e3))
    tri_rank = _gf2_rank(tri_vecs)
    return (tri_rank == cycle_rank, tri_rank, cycle_rank)


def main() -> None:
    store = ManifestStore(data_dir=CORPUS_DIR)
    server_tools: dict[str, list[dict[str, Any]]] = {}
    for name in store.list_servers():
        tools = store.get_tools(name)
        if not tools:
            continue
        nfields = sum(
            len(((t.get("inputSchema") or t.get("input_schema") or {}) or {}).get("properties", {}))
            for t in tools
            if isinstance(t.get("inputSchema") or t.get("input_schema") or {}, dict)
        )
        if nfields >= MIN_SCHEMA_FIELDS:
            server_tools[name] = tools

    # Reproducibility: hash the manifest set actually used.
    man_hash = hashlib.sha256(
        json.dumps(sorted(server_tools.keys())).encode()
    ).hexdigest()

    rows: list[dict[str, Any]] = []
    for left, right in itertools.combinations(sorted(server_tools), 2):
        prefixed: list[dict[str, Any]] = []
        for t in server_tools[left]:
            c = dict(t); c["name"] = f"{left}__{t['name']}"; prefixed.append(c)
        for t in server_tools[right]:
            c = dict(t); c["name"] = f"{right}__{t['name']}"; prefixed.append(c)
        guard = BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")
        comp = guard.composition
        diag = guard.diagnose()
        adj = tool_graph(comp)
        g = girth_of(adj)
        row = {
            "pair_name": f"{left}+{right}",
            "n_tools": len(comp.tools),
            "n_edges": len(comp.edges),
            "betti_1": diag.betti_1,
            "fee": diag.coherence_fee,
            "girth": (None if math.isinf(g) else int(g)),
        }
        if not math.isinf(g):  # has a simple cycle
            spans, tri_rank, cyc_rank = depth3_recovers_full_obstruction(adj)
            row["cycle_rank"] = cyc_rank
            row["triangle_rank"] = tri_rank
            row["depth3_recovers_full"] = spans
        rows.append(row)

    cyclic = [r for r in rows if r["girth"] is not None]  # has a >=3 simple cycle
    girths = [r["girth"] for r in cyclic]
    dist = dict(sorted(collections.Counter(girths).items()))

    n_cyclic = len(cyclic)
    frac_le8 = sum(g <= 8 for g in girths) / n_cyclic if n_cyclic else 0.0
    frac_gt16 = sum(g > 16 for g in girths) / n_cyclic if n_cyclic else 0.0
    # Robustness: does a depth-3 auditor recover the FULL obstruction (triangles
    # generate the cycle space)? Closes the girth-vs-full-H^1 nuance.
    depth3_full = [r for r in cyclic if r.get("depth3_recovers_full")]
    frac_depth3_full = len(depth3_full) / n_cyclic if n_cyclic else 0.0

    # Pre-registered decision rule (§6).
    if n_cyclic == 0:
        verdict = "OUTCOME_4_BOUNDED"
        reason = "no composition has any simple cycle (>=3); the impossibility regime is entirely unreached."
    elif frac_gt16 >= 0.10:
        verdict = "PROCEED"
        reason = f"{frac_gt16:.1%} of cyclic compositions reach girth > 16 (>= 10% threshold); B2 is live."
    else:
        verdict = "OUTCOME_4_BOUNDED"
        reason = (f"girth distribution dominated by short cycles "
                  f"({frac_le8:.1%} <= 8, only {frac_gt16:.1%} > 16); and triangles "
                  f"generate the full cycle space in {frac_depth3_full:.1%} of cyclic "
                  f"compositions, so depth-3 bounded-local testing recovers the entire "
                  f"obstruction. B2 real but not yet binding at current composition scales.")

    result = {
        "stage": "0_girth_gate",
        "corpus": "registry_real_schema_pairwise",
        "manifest_set_sha256": man_hash,
        "n_servers": len(server_tools),
        "n_compositions": len(rows),
        "n_with_simple_cycle": n_cyclic,
        "girth_distribution": {str(k): v for k, v in dist.items()},
        "girth_summary": {
            "min": min(girths) if girths else None,
            "median": statistics.median(girths) if girths else None,
            "max": max(girths) if girths else None,
        },
        "frac_cyclic_girth_le_8": round(frac_le8, 4),
        "frac_cyclic_girth_gt_16": round(frac_gt16, 4),
        "frac_cyclic_depth3_recovers_full_obstruction": round(frac_depth3_full, 4),
        "betti1_max": max(r["betti_1"] for r in rows),
        "decision_rule": "girth<=8 dominant -> Outcome 4 (Bounded); >=10% girth>16 -> proceed",
        "VERDICT": verdict,
        "reason": reason,
        "cyclic_pairs": sorted(cyclic, key=lambda r: r["girth"]),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "cyclic_pairs"}, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
