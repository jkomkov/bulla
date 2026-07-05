"""Real-corpus n-way + HEAD/TAIL measurement (the decisive, falsifiable test).

The synthetic phase-transition sweep proved only a conditional. This measures the
real 38-server registry directly and asks the question the refinement turns on:

  Is the compositional obstruction concentrated in the STANDARDIZED HEAD of the
  convention distribution (path_convention etc. -> big cliques -> girth 3, depth-3
  audit complete), or does the heterogeneous TAIL carry obstruction that
  DELOCALIZES (audit depth > 3) -- i.e. is the impossibility regime reachable in
  reality by the convention tail?

Reports BOTH branches, per real composition and as you compose more servers (n-way):
  (A) tail acyclic                         -> obstruction is ENTIRELY head-driven (strong prior).
  (B) tail cyclic but triangle-generated   -> tail also concentrated; impossibility distant.
  (C) tail cyclic AND delocalizes (ad>3)   -> heterogeneous regime IS reachable by the tail;
                                              the impossibility activates as the tail grows (thesis).

Reuses the real loader + the Stage-0 girth / triangle-span / min-cycle-basis audit-depth
instruments. Deterministic (seed 2026). Read-only; writes one results JSON.
"""
from __future__ import annotations

import collections
import json
import math
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stage0_girth import girth_of, depth3_recovers_full_obstruction  # noqa: E402

MAX_EDGES_FOR_SPAN = 4000   # above this a dense head-clique is skipped (girth-only)


def _edge_index(adj):
    nodes = sorted(adj)
    idx = {n: i for i, n in enumerate(nodes)}
    eid = {}
    for u in nodes:
        for v in adj[u]:
            a, b = sorted((idx[u], idx[v]))
            if (a, b) not in eid:
                eid[(a, b)] = len(eid)
    return nodes, idx, eid


def n_edges_of(adj):
    return sum(len(vs) for vs in adj.values()) // 2


def min_cycle_basis_max_len(adj, cycle_rank):
    """Max cycle length in a MINIMUM cycle basis (Horton candidates + greedy GF(2)).
    This is the audit depth: a radius ~ ad/2 auditor is needed to see the deepest
    independent obstruction. Returns 0 if acyclic."""
    if cycle_rank <= 0:
        return 0
    nodes, idx, eid = _edge_index(adj)
    cands = []  # (length_in_edges, edge-incidence bitmask)
    for s in nodes:
        dist = {s: 0}
        pmask = {s: 0}
        dq = collections.deque([s])
        while dq:
            u = dq.popleft()
            for w in adj[u]:
                if w not in dist:
                    dist[w] = dist[u] + 1
                    pmask[w] = pmask[u] | (1 << eid[tuple(sorted((idx[u], idx[w])))])
                    dq.append(w)
        for x in dist:
            ix = idx[x]
            for y in adj[x]:
                iy = idx[y]
                if y in dist and ix < iy:
                    mask = pmask[x] ^ pmask[y] ^ (1 << eid[(ix, iy)])
                    L = bin(mask).count("1")
                    if L >= 3:
                        cands.append((L, mask))
    cands.sort(key=lambda t: t[0])
    basis = {}
    maxlen = 0
    chosen = 0
    for L, mask in cands:
        cur = mask
        while cur:
            hb = cur.bit_length() - 1
            if hb in basis:
                cur ^= basis[hb]
            else:
                basis[hb] = cur
                chosen += 1
                maxlen = max(maxlen, L)
                break
        if chosen >= cycle_rank:
            break
    return maxlen

ROOT = HERE.parents[2]                      # repo root
BULLA = ROOT / "bulla"
sys.path.insert(0, str(BULLA / "src"))
sys.path.insert(0, str(BULLA))
from calibration.corpus import ManifestStore       # noqa: E402
from calibration.index import MIN_SCHEMA_FIELDS     # noqa: E402
from bulla.guard import BullaGuard                  # noqa: E402

CORPUS_DIR = BULLA / "calibration" / "data" / "registry"
OUT = ROOT / "papers" / "coherence-cliff" / "results" / "dissociation_real_headtail.json"
SEED = 2026


def load_servers():
    store = ManifestStore(data_dir=CORPUS_DIR)
    out = {}
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
            out[name] = tools
    return out


def compose(servers, names):
    pre = []
    for n in names:
        for t in servers[n]:
            c = dict(t); c["name"] = f"{n}__{t['name']}"; pre.append(c)
    return BullaGuard.from_tools_list(pre, name="+".join(names)).composition


def build_adj(comp, exclude_dims=frozenset()):
    """Undirected seam graph; tools u~v if they share a dimension NOT in exclude_dims.
    Also returns dim -> set(tools incident) for the sharing-multiplicity distribution."""
    adj = {t.name: set() for t in comp.tools}
    dim_tools = collections.defaultdict(set)
    for e in comp.edges:
        dims = {d.name for d in e.dimensions}
        for d in dims:
            dim_tools[d].add(e.from_tool); dim_tools[d].add(e.to_tool)
        if e.from_tool == e.to_tool:
            continue
        if dims - set(exclude_dims):
            adj[e.from_tool].add(e.to_tool); adj[e.to_tool].add(e.from_tool)
    return adj, dim_tools


def measure(adj):
    """girth, triangle-generated?, and audit depth (only the expensive Horton pass
    when triangles do NOT already generate the cycle space)."""
    g = girth_of(adj)
    if math.isinf(g):
        return {"cyclic": False, "girth": None, "triangle_generated": None,
                "audit_depth": 0, "dense_skipped": False}
    ne = n_edges_of(adj)
    if ne > MAX_EDGES_FOR_SPAN:
        # dense head-clique: girth 3, triangle-generated by the clique property
        # (a complete graph's cycle space is spanned by triangles) -> audit depth 3.
        return {"cyclic": True, "girth": int(g), "triangle_generated": None,
                "audit_depth": None, "dense_skipped": True, "n_edges": ne}
    spans, tri_rank, cyc_rank = depth3_recovers_full_obstruction(adj)
    ad = 3 if spans else min_cycle_basis_max_len(adj, cyc_rank)
    return {"cyclic": True, "girth": int(g), "triangle_generated": bool(spans),
            "cycle_rank": cyc_rank, "audit_depth": ad, "dense_skipped": False}


def main():
    servers = load_servers()
    names = sorted(servers)
    rng = random.Random(SEED)

    # ---- global convention sharing-multiplicity (head vs tail) from a large composition ----
    big_names = names if len(names) <= 24 else rng.sample(names, 24)
    big = compose(servers, big_names)
    _, big_dim_tools = build_adj(big)
    m_dist = sorted(((d, len(ts)) for d, ts in big_dim_tools.items()), key=lambda x: -x[1])
    head_dims = [d for d, _ in m_dist[:3]]   # the standardized head to peel off

    # ---- n-way sweep: full vs head-removed (top-1/2/3) audit, as composition deepens ----
    nway = {}
    for k in [4, 8, 12, 16]:
        if k > len(names):
            continue
        cells = {"full": [], "tail_top1": [], "tail_top2": [], "tail_top3": []}
        for _ in range(10):
            combo = rng.sample(names, k)
            comp = compose(servers, combo)
            adj_full, _ = build_adj(comp)
            cells["full"].append(measure(adj_full))
            for h, key in [(1, "tail_top1"), (2, "tail_top2"), (3, "tail_top3")]:
                adj_tail, _ = build_adj(comp, exclude_dims=frozenset(head_dims[:h]))
                cells["tail_top1" if h == 1 else key].append(measure(adj_tail))

        def agg(rows):
            cyc = [r for r in rows if r["cyclic"]]
            ads = [r["audit_depth"] for r in cyc if r.get("audit_depth") is not None]
            tg = [r["triangle_generated"] for r in cyc if r.get("triangle_generated") is not None]
            girths = [r["girth"] for r in cyc if r.get("girth") is not None]
            return {
                "n_samples": len(rows),
                "frac_cyclic": round(len(cyc) / len(rows), 3) if rows else None,
                "n_dense_skipped": sum(1 for r in cyc if r.get("dense_skipped")),
                "median_girth": statistics.median(girths) if girths else None,
                "frac_triangle_generated": round(statistics.mean(tg), 3) if tg else None,
                "median_audit_depth": statistics.median(ads) if ads else None,
                "max_audit_depth": max(ads) if ads else None,
            }
        nway[f"k{k}"] = {key: agg(rows) for key, rows in cells.items()}

    # ---- verdict: does peeling the head delocalize the obstruction? ----
    # Use the deepest composition measured.
    deepest = max(nway, key=lambda kk: int(kk[1:])) if nway else None
    branch = "INDETERMINATE"
    if deepest:
        t1 = nway[deepest]["tail_top1"]
        if (t1["frac_cyclic"] or 0) < 0.05:
            branch = "A_TAIL_ACYCLIC: obstruction is entirely head-driven; impossibility regime not reached by the tail."
        elif (t1["frac_triangle_generated"] or 0) >= 0.95 and (t1["max_audit_depth"] or 0) <= 3:
            branch = "B_TAIL_TRIANGLE_GENERATED: the tail too is locally concentrated; depth-3 audit complete; impossibility distant."
        elif (t1["max_audit_depth"] or 0) > 3:
            branch = "C_TAIL_DELOCALIZES: stripping the standardized head leaves tail obstruction needing audit depth > 3 -> the heterogeneous/impossibility regime IS reachable by the real convention tail."
        else:
            branch = "MIXED/INDETERMINATE: see per-k cells."

    result = {
        "experiment": "real_corpus_nway_head_tail",
        "seed": SEED, "n_servers": len(names),
        "convention_sharing_multiplicity_head": m_dist[:8],
        "convention_sharing_multiplicity_tail_sample": m_dist[-8:] if len(m_dist) > 8 else [],
        "head_dims_peeled": head_dims,
        "nway": nway,
        "VERDICT_branch": branch,
        "note": ("audit_depth = max cycle length in a minimum cycle basis (radius ~ad/2 to certify). "
                 "tail_topH = seam graph with the H most-shared (head) convention dimensions removed."),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))

    print("convention sharing-multiplicity (tools sharing each dim), top 8:")
    for d, c in m_dist[:8]:
        print(f"    {d:28s} {c}")
    print(f"\nhead peeled: {head_dims}\n")
    print(f"{'k-way':>6} {'view':>11} {'cyclic':>7} {'girth':>6} {'tri_gen':>8} {'audit_depth(med/max)':>22}")
    for k in sorted(nway, key=lambda kk: int(kk[1:])):
        for view in ["full", "tail_top1", "tail_top2", "tail_top3"]:
            a = nway[k][view]
            print(f"{k:>6} {view:>11} {str(a['frac_cyclic']):>7} {str(a['median_girth']):>6} "
                  f"{str(a['frac_triangle_generated']):>8} {str(a['median_audit_depth'])+'/'+str(a['max_audit_depth']):>22}")
        print()
    print("VERDICT:", branch)
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
