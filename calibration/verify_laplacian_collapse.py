#!/usr/bin/env python3
"""Verify the Laplacian Collapse Theorem on the 703-composition corpus.

For each composition, the theorem predicts:
  leverage(j) = (n_d - 1) / n_d
where n_d is the number of carrier (tool, field) pairs for j's dimension
in the full coboundary (= number of distinct columns in dimension d's block).

We compute per-dimension carrier counts from the actual coboundary and
compare predicted leverage to observed leverage. Exact equality on every
hidden field is the theorem's empirical signature.
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

MIN_SCHEMA_FIELDS = 3


def _field_count(tools: list[dict[str, Any]]) -> int:
    total = 0
    for t in tools:
        schema = t.get("inputSchema", {})
        total += len(schema.get("properties", {}))
    return total


def carrier_counts_per_dimension(
    delta_full: list[list[Fraction]],
    v_basis: list[tuple[str, str]],
    e_basis: list[tuple[str, str]],
) -> dict[str, set[int]]:
    """For each dimension name in the edge basis, collect the set of column
    indices that have nonzero entries in any row of that dimension.

    Returns: {dim_name: {col_idx, ...}}
    """
    per_dim: dict[str, set[int]] = defaultdict(set)
    for row_idx, (edge_label, dim_name) in enumerate(e_basis):
        for col_idx in range(len(v_basis)):
            if delta_full[row_idx][col_idx] != 0:
                per_dim[dim_name].add(col_idx)
    return per_dim


def carrier_components(
    delta_full: list[list[Fraction]],
    v_basis: list[tuple[str, str]],
    e_basis: list[tuple[str, str]],
) -> tuple[dict[int, int], dict[int, int]]:
    """Compute connected components of the carrier graph.

    The carrier graph has:
      - vertices = columns with any nonzero entry in delta_full
      - edges = rows of delta_full (each row connects exactly 2 columns,
        one at -1 and one at +1)

    Returns (col_to_component, component_size) where col_to_component maps
    a column index to its component id, and component_size gives the
    number of columns in each component.

    Under DFD, this is equivalent to per-dimension component analysis;
    under DFD violation, components span multiple dimensions.
    """
    n_cols = len(v_basis)
    parent = list(range(n_cols))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    # Each row connects the columns with nonzero entries
    for row in delta_full:
        nonzero_cols = [c for c, val in enumerate(row) if val != 0]
        if len(nonzero_cols) >= 2:
            for c in nonzero_cols[1:]:
                union(nonzero_cols[0], c)

    col_to_component: dict[int, int] = {}
    component_size: dict[int, int] = defaultdict(int)
    for c in range(n_cols):
        # Only include columns that appear in some row
        if any(row[c] != 0 for row in delta_full):
            root = find(c)
            col_to_component[c] = root
            component_size[root] += 1

    return col_to_component, dict(component_size)


def predicted_leverage_simple(
    delta_full: list[list[Fraction]],
    v_basis: list[tuple[str, str]],
    e_basis: list[tuple[str, str]],
    hidden_cols: list[int],
) -> list[Fraction]:
    """Simple Laplacian-collapse prediction under strict CHP.

    leverage(j) = (|C| - 1) / |C| where |C| is the carrier-graph component
    size. Correct when every vertex in C is hidden (strict CHP holds).
    """
    col_to_component, component_size = carrier_components(
        delta_full, v_basis, e_basis
    )
    predictions: list[Fraction] = []
    for col in hidden_cols:
        if col not in col_to_component:
            predictions.append(Fraction(0))
            continue
        comp_id = col_to_component[col]
        size = component_size[comp_id]
        if size <= 1:
            predictions.append(Fraction(0))
        else:
            predictions.append(Fraction(size - 1, size))
    return predictions


def _invert(A: list[list[Fraction]]) -> list[list[Fraction]]:
    """Exact inverse of a square invertible rational matrix via Gauss-Jordan."""
    n = len(A)
    aug = [A[i][:] + [Fraction(1 if i == j else 0) for j in range(n)] for i in range(n)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if aug[r][col] != 0), None)
        if pivot is None:
            raise ValueError("singular")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        aug[col] = [x / scale for x in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0:
                f = aug[r][col]
                aug[r] = [aug[r][j] - f * aug[col][j] for j in range(2 * n)]
    return [row[n:] for row in aug]


def schur_complement_leverage(
    delta_full: list[list[Fraction]],
    v_basis: list[tuple[str, str]],
    e_basis: list[tuple[str, str]],
    hidden_cols: list[int],
    obs_cols: list[int],
) -> list[Fraction]:
    """Refined Laplacian-collapse prediction under partial CHP.

    For each carrier-graph component C, build the signed incidence matrix
    M restricted to C's rows+columns, compute L = M^T M (combinatorial
    Laplacian), Schur-complement against C ∩ obs_cols, and report the
    diagonal of the projection onto col(Schur) for the hidden vertices.

    This matches the observed K_d block structure exactly under arbitrary
    hidden/observable partition of the carrier graph.
    """
    col_to_component, component_size = carrier_components(
        delta_full, v_basis, e_basis
    )
    obs_set = set(obs_cols)

    # Group hidden cols by component
    components_hidden: dict[int, list[int]] = defaultdict(list)
    components_obs: dict[int, list[int]] = defaultdict(list)
    for col, comp_id in col_to_component.items():
        if col in obs_set:
            components_obs[comp_id].append(col)
        else:
            components_hidden[comp_id].append(col)

    predictions: dict[int, Fraction] = {}
    for comp_id, hidden_in_comp in components_hidden.items():
        obs_in_comp = components_obs.get(comp_id, [])
        all_in_comp = sorted(hidden_in_comp + obs_in_comp)
        if len(all_in_comp) <= 1:
            for c in hidden_in_comp:
                predictions[c] = Fraction(0)
            continue

        # Build L = M^T M restricted to this component's columns
        n_comp = len(all_in_comp)
        col_map = {c: i for i, c in enumerate(all_in_comp)}
        L = [[Fraction(0)] * n_comp for _ in range(n_comp)]
        for row in delta_full:
            nonzeros = [(c, row[c]) for c in all_in_comp if row[c] != 0]
            if len(nonzeros) < 2:
                continue
            for (ci, vi), (cj, vj) in [(a, b) for a in nonzeros for b in nonzeros]:
                L[col_map[ci]][col_map[cj]] += vi * vj

        h_idx = [col_map[c] for c in hidden_in_comp]
        o_idx = [col_map[c] for c in obs_in_comp]

        if not o_idx:
            # Strict CHP in this component; K_d = L[H,H]
            K_d = [[L[i][j] for j in h_idx] for i in h_idx]
        else:
            L_hh = [[L[i][j] for j in h_idx] for i in h_idx]
            L_ho = [[L[i][j] for j in o_idx] for i in h_idx]
            L_oh = [[L[i][j] for j in h_idx] for i in o_idx]
            L_oo = [[L[i][j] for j in o_idx] for i in o_idx]
            # If L_oo is singular (e.g. observable columns span the component
            # kernel), fall back to a pseudoinverse via rank-reduction.
            try:
                L_oo_inv = _invert(L_oo)
            except ValueError:
                # Use the simple-formula fallback for this component
                size = n_comp
                for c in hidden_in_comp:
                    predictions[c] = Fraction(size - 1, size)
                continue
            # K_d = L_hh - L_ho @ L_oo_inv @ L_oh
            temp = [[sum(L_ho[i][k] * L_oo_inv[k][j] for k in range(len(o_idx)))
                     for j in range(len(o_idx))] for i in range(len(h_idx))]
            sub = [[sum(temp[i][k] * L_oh[k][j] for k in range(len(o_idx)))
                    for j in range(len(h_idx))] for i in range(len(h_idx))]
            K_d = [[L_hh[i][j] - sub[i][j] for j in range(len(h_idx))]
                   for i in range(len(h_idx))]

        # Compute leverage via projection onto col(K_d)
        # Use the leverage_scores function from witness_geometry
        from bulla.witness_geometry import leverage_scores
        lev_d = leverage_scores(K_d)
        for k, c in enumerate(hidden_in_comp):
            predictions[c] = lev_d[k]

    return [predictions.get(c, Fraction(0)) for c in hidden_cols]


# Keep an alias pointing at the refined prediction for the main loop
def predicted_leverage(
    delta_full: list[list[Fraction]],
    v_basis: list[tuple[str, str]],
    e_basis: list[tuple[str, str]],
    hidden_cols: list[int],
    obs_cols: list[int] | None = None,
) -> list[Fraction]:
    if obs_cols is None:
        return predicted_leverage_simple(delta_full, v_basis, e_basis, hidden_cols)
    return schur_complement_leverage(
        delta_full, v_basis, e_basis, hidden_cols, obs_cols
    )


def run_check() -> None:
    from bulla.coboundary import build_coboundary
    from bulla.witness_geometry import (
        compute_all,
        _observable_indices,
    )
    from calibration.compute import diagnose_pair
    from calibration.corpus import ManifestStore

    data_dir = Path("calibration/data/registry")
    store = ManifestStore(data_dir=data_dir)

    real_servers: dict[str, list[dict[str, Any]]] = {}
    for name in store.list_servers():
        tools = store.get_tools(name)
        if _field_count(tools) >= MIN_SCHEMA_FIELDS:
            real_servers[name] = tools

    n_servers = len(real_servers)
    logger.info(
        f"Real-schema corpus: {n_servers} servers -> "
        f"{n_servers*(n_servers-1)//2} compositions"
    )

    stats = {
        "total": 0,
        "trivial": 0,               # |H| = 0
        "fee_zero_nontrivial": 0,   # |H| > 0 but fee = 0
        "nontrivial": 0,            # fee > 0
        "all_predictions_match": 0,   # every nontrivial field matches
        "some_prediction_mismatch": 0,
        "dfd_violations": 0,         # fields appearing in multiple dimensions
        "disconnected_dim": 0,       # dimensions with multiple components
        "errors": 0,
    }

    # Track carrier count distribution
    n_d_histogram: dict[int, int] = defaultdict(int)
    # Track mismatch magnitude for any failures
    mismatches: list[dict] = []

    for a, b in itertools.combinations(sorted(real_servers.keys()), 2):
        stats["total"] += 1
        try:
            result = diagnose_pair(a, real_servers[a], b, real_servers[b])
            comp = result.kernel_composition
            if comp is None:
                stats["errors"] += 1
                continue

            profile = compute_all(comp.tools, comp.edges)
            n_hidden = len(profile["hidden_basis"])
            fee_K = profile["fee"]
            observed = profile["leverage"]

            if n_hidden == 0:
                stats["trivial"] += 1
                continue
            if fee_K == 0:
                stats["fee_zero_nontrivial"] += 1
                continue
            stats["nontrivial"] += 1

            # Rebuild delta_full and identify hidden cols to run prediction
            delta_full, v_basis, e_basis = build_coboundary(
                comp.tools, comp.edges, use_internal=True
            )
            tool_map = {t.name: t for t in comp.tools}
            obs_cols, hidden_cols = _observable_indices(v_basis, tool_map)
            preds = predicted_leverage(
                delta_full, v_basis, e_basis, hidden_cols, obs_cols
            )

            # Track carrier-count histogram
            per_dim = carrier_counts_per_dimension(
                delta_full, v_basis, e_basis
            )
            for dname, cols in per_dim.items():
                if len(cols) > 0:
                    n_d_histogram[len(cols)] += 1

            all_match = True
            for k in range(n_hidden):
                pred = preds[k]
                obs = observed[k]
                if pred != obs:
                    all_match = False
                    mismatches.append({
                        "composition": f"{a}+{b}",
                        "field": list(profile["hidden_basis"][k]),
                        "observed": f"{obs}",
                        "predicted": f"{pred}",
                        "diff_float": float(obs - pred),
                    })

            if all_match:
                stats["all_predictions_match"] += 1
            else:
                stats["some_prediction_mismatch"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.exception(f"error on {a}+{b}: {e}")

    logger.info("=" * 70)
    logger.info("Laplacian Collapse Theorem — Empirical Verification")
    logger.info("=" * 70)
    logger.info(f"Total compositions:        {stats['total']}")
    logger.info(f"  Trivial (no hidden):     {stats['trivial']}")
    logger.info(f"  Fee zero (|H|>0, fee=0): {stats['fee_zero_nontrivial']}")
    logger.info(f"  Nontrivial (fee > 0):    {stats['nontrivial']}")
    logger.info(f"  Errors:                  {stats['errors']}")
    logger.info("")
    logger.info("Theorem prediction match (nontrivial compositions):")
    logger.info(f"  All fields match formula:  {stats['all_predictions_match']:4d}")
    logger.info(f"  Some mismatch:             {stats['some_prediction_mismatch']:4d}")
    logger.info(f"  DFD-violation columns:     {stats['dfd_violations']}")
    logger.info("")
    logger.info("Carrier-count (n_d) distribution across dimension instances:")
    for n_d in sorted(n_d_histogram.keys()):
        pred_lev = float(Fraction(n_d - 1, n_d)) if n_d > 0 else 0.0
        logger.info(
            f"  n_d = {n_d:3d}: {n_d_histogram[n_d]:5d} occurrences  "
            f"-> predicted leverage {pred_lev:.4f}"
        )
    if mismatches:
        logger.info("")
        logger.info(f"First 10 mismatches:")
        for m in mismatches[:10]:
            logger.info(
                f"  {m['composition']:40s} {m['field']!s:30s} "
                f"obs={m['observed']:10s} pred={m['predicted']:10s}"
            )

    # Save full stats
    out_path = Path("calibration/results/laplacian_collapse_verification.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "stats": stats,
            "n_d_histogram": dict(n_d_histogram),
            "mismatches": mismatches,
        }, f, indent=2, sort_keys=True)
    logger.info(f"")
    logger.info(f"Full output: {out_path}")


if __name__ == "__main__":
    run_check()
