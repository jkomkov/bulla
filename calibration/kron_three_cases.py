#!/usr/bin/env python3
"""Classify each nontrivial composition into the Kron-reduction case structure:

  Case 1 (full CHP):               O_d = empty for every d; K_d = L_d directly.
  Case 2 (trivial Kron):           O_d non-empty but induced L_OO is the zero
                                    matrix (no inter-observable edges within d);
                                    K_d = L_HH (hidden-restricted Laplacian).
  Case 3 (genuine Kron reduction): at least one dimension has L_OO non-zero
                                    (there exist inter-observable edges within d);
                                    K_d is a genuine Schur complement with non-
                                    trivial absorption.

Also check: does any composition exhibit non-uniform leverage within a single
dimension-component? This requires disconnected carrier subgraphs within a
dimension after hidden/observable partitioning.
"""

from __future__ import annotations

import itertools
import json
import logging
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MIN_SCHEMA_FIELDS = 3


def _field_count(tools):
    return sum(
        len(t.get("inputSchema", {}).get("properties", {})) for t in tools
    )


def classify_composition(comp, tool_map):
    """Classify case and gather per-dimension structural data.

    Returns a dict with:
      case: 1 / 2 / 3 / 'trivial' / 'fee_zero'
      dim_details: list of dicts, one per dimension with nonzero rows
      intra_dim_nonuniform: bool — does any dimension have non-uniform leverage
                                     across its hidden vertices?
    """
    from bulla.coboundary import build_coboundary
    from bulla.witness_geometry import compute_all, _observable_indices

    delta, v_basis, e_basis = build_coboundary(
        comp.tools, comp.edges, use_internal=True
    )
    profile = compute_all(comp.tools, comp.edges)
    if len(profile["hidden_basis"]) == 0:
        return {"case": "trivial", "dim_details": [], "intra_dim_nonuniform": False}
    if profile["fee"] == 0:
        return {"case": "fee_zero", "dim_details": [], "intra_dim_nonuniform": False}

    obs_cols, hidden_cols = _observable_indices(v_basis, tool_map)
    obs_set = set(obs_cols)
    hidden_set = set(hidden_cols)

    # Per-dimension carriers and edges
    per_dim_cols: dict[str, set[int]] = defaultdict(set)
    per_dim_edges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row_idx, (edge_label, dim_name) in enumerate(e_basis):
        nonzeros = [c for c in range(len(v_basis)) if delta[row_idx][c] != 0]
        for c in nonzeros:
            per_dim_cols[dim_name].add(c)
        if len(nonzeros) == 2:
            per_dim_edges[dim_name].append(tuple(sorted(nonzeros)))

    dim_details = []
    any_case3 = False
    any_nonuniform = False
    all_full_chp = True

    # Map from hidden col idx to its leverage in profile
    col_to_leverage = {}
    for j, (t, f) in enumerate(profile["hidden_basis"]):
        for c in hidden_cols:
            if v_basis[c] == (t, f):
                col_to_leverage[c] = profile["leverage"][j]
                break

    for dname, cols in per_dim_cols.items():
        H_d = sorted(c for c in cols if c in hidden_set)
        O_d = sorted(c for c in cols if c in obs_set)
        edges = per_dim_edges[dname]
        # inter-observable edges: edges where both endpoints are in O_d
        inter_obs_edges = [
            (u, v) for (u, v) in edges if u in obs_set and v in obs_set
        ]
        dim_case = 1 if not O_d else (3 if inter_obs_edges else 2)
        if O_d:
            all_full_chp = False
        if dim_case == 3:
            any_case3 = True

        # Check uniform leverage on hidden vertices of this dimension
        levs_in_dim = [col_to_leverage.get(c) for c in H_d]
        levs_in_dim = [l for l in levs_in_dim if l is not None]
        is_uniform = (len(set(levs_in_dim)) <= 1)
        if not is_uniform:
            any_nonuniform = True

        # Check disconnected hidden components within this dimension
        # (remove observable vertices, what's the connectivity on H_d?)
        # Build union-find over H_d using edges with both endpoints in H_d
        # (hidden-hidden edges) OR in H_d ∪ O_d (then check via Kron absorption)
        # Simpler: connected components on H_d using edges that stay in
        # H_d after observable absorption — this is the support of the
        # effective Laplacian. But for simplicity: count components in the
        # graph (V_d, edges) restricted to H_d.
        parent = {c: c for c in H_d}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry
        for (u, v) in edges:
            if u in hidden_set and v in hidden_set:
                union(u, v)
        n_components_naive = len({find(c) for c in H_d}) if H_d else 0
        # Observable-aware: two hidden vertices are connected if there's
        # any path between them going through observable vertices as well.
        # That's the carrier-graph-level connectivity.
        parent2 = {c: c for c in cols}
        def find2(x):
            while parent2[x] != x:
                parent2[x] = parent2[parent2[x]]
                x = parent2[x]
            return x
        def union2(x, y):
            rx, ry = find2(x), find2(y)
            if rx != ry:
                parent2[rx] = ry
        for (u, v) in edges:
            union2(u, v)
        hidden_carrier_components = {find2(c) for c in H_d}
        n_components_carrier = len(hidden_carrier_components)

        dim_details.append({
            "dim": dname,
            "n_carriers_total": len(cols),
            "n_hidden": len(H_d),
            "n_obs": len(O_d),
            "n_edges": len(edges),
            "n_inter_obs_edges": len(inter_obs_edges),
            "case": dim_case,
            "hidden_leverages_unique": sorted(set(str(l) for l in levs_in_dim)),
            "leverage_uniform_in_dim": is_uniform,
            "hidden_components_naive": n_components_naive,
            "hidden_components_via_carrier_graph": n_components_carrier,
        })

    overall_case = (1 if all_full_chp else (3 if any_case3 else 2))
    return {
        "case": overall_case,
        "dim_details": dim_details,
        "intra_dim_nonuniform": any_nonuniform,
    }


def run():
    from calibration.compute import diagnose_pair
    from calibration.corpus import ManifestStore

    store = ManifestStore(data_dir=Path("calibration/data/registry"))
    real_servers = {
        name: store.get_tools(name)
        for name in store.list_servers()
        if _field_count(store.get_tools(name)) >= MIN_SCHEMA_FIELDS
    }

    counts = {"trivial": 0, "fee_zero": 0, 1: 0, 2: 0, 3: 0, "errors": 0}
    intra_nonuniform = 0
    case3_compositions = []
    nonuniform_compositions = []
    disconnected_components = 0

    for a, b in itertools.combinations(sorted(real_servers.keys()), 2):
        try:
            res = diagnose_pair(a, real_servers[a], b, real_servers[b])
            comp = res.kernel_composition
            if comp is None:
                counts["errors"] += 1
                continue
            tool_map = {t.name: t for t in comp.tools}
            info = classify_composition(comp, tool_map)
            counts[info["case"]] = counts.get(info["case"], 0) + 1
            if info["intra_dim_nonuniform"]:
                intra_nonuniform += 1
                nonuniform_compositions.append(f"{a}+{b}")
            if info["case"] == 3:
                case3_compositions.append(f"{a}+{b}")
            for dd in info["dim_details"]:
                if dd["hidden_components_via_carrier_graph"] > 1:
                    disconnected_components += 1
        except Exception as e:
            counts["errors"] += 1
            logger.exception(f"error on {a}+{b}: {e}")

    logger.info("=" * 70)
    logger.info("Kron Three-Case Distribution on 703 Corpus")
    logger.info("=" * 70)
    logger.info(f"Trivial (|H|=0):              {counts['trivial']}")
    logger.info(f"Fee zero (|H|>0, fee=0):      {counts['fee_zero']}")
    logger.info(f"Case 1 (full CHP, K=L):       {counts[1]}")
    logger.info(f"Case 2 (trivial Kron, K=L_HH): {counts[2]}")
    logger.info(f"Case 3 (genuine Kron):        {counts[3]}")
    logger.info(f"Errors:                       {counts['errors']}")
    logger.info("")
    logger.info(f"Compositions with intra-dimension non-uniform leverage: {intra_nonuniform}")
    if nonuniform_compositions:
        logger.info("Examples (first 10):")
        for c in nonuniform_compositions[:10]:
            logger.info(f"  {c}")
    logger.info(f"Dimensions with disconnected hidden-carrier components: "
                f"{disconnected_components}")
    logger.info("")
    if case3_compositions:
        logger.info(f"Case 3 compositions (genuine Kron, first 10):")
        for c in case3_compositions[:10]:
            logger.info(f"  {c}")


if __name__ == "__main__":
    run()
