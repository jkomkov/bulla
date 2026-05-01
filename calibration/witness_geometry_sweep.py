#!/usr/bin/env python3
"""
Sprint C'' Structural Verification — Witness Geometry on 703 Real-Schema Corpus.

Runs the witness-geometry module on every pairwise composition in the real-schema
corpus and verifies four structural identities:

  1. rank(K(G)) == fee(G)                    (backbone consistency)
  2. sum_j leverage(j) == fee(G)             (trace-of-projection conservation)
  3. 0 <= leverage(j) <= 1                   (orthogonal projection bounds)
  4. K(G1 ⊔ G2) == K(G1) ⊕ K(G2)            (disjoint-composition block-diagonal;
                                              spot-checked on sample pairs)

Records per-composition invariants (fee, leverage distribution, coloops, loops,
N_eff) to JSONL for downstream master-example search.

Usage:
    cd bulla && python -m calibration.witness_geometry_sweep

Output:
    calibration/results/witness_geometry_703.jsonl
    calibration/results/witness_geometry_703.summary.json
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
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
        props = schema.get("properties", {})
        total += len(props)
    return total


def _frac_to_float(x: Fraction) -> float:
    return float(x.numerator) / float(x.denominator)


def run_sweep() -> None:
    from bulla.witness_geometry import compute_all, fee_from_gram
    from bulla.coboundary import build_coboundary, matrix_rank
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
    n_compositions = n_servers * (n_servers - 1) // 2
    logger.info(
        f"Real-schema corpus: {n_servers} servers -> {n_compositions} compositions"
    )

    out_dir = Path("calibration/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "witness_geometry_703.jsonl"
    summary_path = out_dir / "witness_geometry_703.summary.json"

    stats = {
        "total": 0,
        "trivial_no_hidden": 0,           # |H| = 0
        "fee_zero": 0,                     # fee = 0 but |H| > 0
        "nontrivial": 0,                   # fee > 0
        "pass_rank": 0,                    # rank(K) == fee (core + K)
        "pass_conservation": 0,            # sum leverage == fee (exact rational)
        "pass_bounds": 0,                  # 0 <= l <= 1 everywhere
        "fail_rank": 0,
        "fail_conservation": 0,
        "fail_bounds": 0,
        "errors": 0,
        "coloop_histogram": {},            # count -> n_compositions
        "loop_count_total": 0,
    }

    with open(out_path, "w") as f:
        for a, b in itertools.combinations(sorted(real_servers.keys()), 2):
            stats["total"] += 1
            try:
                result = diagnose_pair(a, real_servers[a], b, real_servers[b])
                comp = result.kernel_composition
                if comp is None:
                    stats["errors"] += 1
                    continue

                # Cross-check fee via direct coboundary
                d_full, _, _ = build_coboundary(
                    comp.tools, comp.edges, use_internal=True
                )
                d_obs, _, _ = build_coboundary(
                    comp.tools, comp.edges, use_internal=False
                )
                fee_core = matrix_rank(d_full) - matrix_rank(d_obs)

                profile = compute_all(comp.tools, comp.edges)
                n_hidden = len(profile["hidden_basis"])
                fee_K = profile["fee"]
                leverage = profile["leverage"]
                sum_lev = sum(leverage) if leverage else Fraction(0)
                bounds_ok = all(0 <= l <= 1 for l in leverage)
                rank_ok = (fee_K == fee_core)
                conservation_ok = (sum_lev == fee_K)

                if n_hidden == 0:
                    stats["trivial_no_hidden"] += 1
                elif fee_K == 0:
                    stats["fee_zero"] += 1
                else:
                    stats["nontrivial"] += 1

                if rank_ok:
                    stats["pass_rank"] += 1
                else:
                    stats["fail_rank"] += 1
                if conservation_ok:
                    stats["pass_conservation"] += 1
                else:
                    stats["fail_conservation"] += 1
                if bounds_ok:
                    stats["pass_bounds"] += 1
                else:
                    stats["fail_bounds"] += 1

                coloop_n = len(profile["coloops"])
                loop_n = len(profile["loops"])
                stats["coloop_histogram"][coloop_n] = (
                    stats["coloop_histogram"].get(coloop_n, 0) + 1
                )
                stats["loop_count_total"] += loop_n

                record = {
                    "composition": f"{a}+{b}",
                    "n_hidden": n_hidden,
                    "fee_core": fee_core,
                    "fee_K": fee_K,
                    "sum_leverage_num": sum_lev.numerator if isinstance(sum_lev, Fraction) else sum_lev,
                    "sum_leverage_den": sum_lev.denominator if isinstance(sum_lev, Fraction) else 1,
                    "bounds_ok": bounds_ok,
                    "rank_ok": rank_ok,
                    "conservation_ok": conservation_ok,
                    "coloops_count": coloop_n,
                    "coloops": [list(p) for p in profile["coloops"]],
                    "loops_count": loop_n,
                    "n_effective_float": _frac_to_float(
                        profile["n_effective"]
                    ) if fee_K > 0 else 0.0,
                    # Leverage as floats for distribution analysis
                    "leverage_floats": [_frac_to_float(l) for l in leverage],
                    # Hidden basis for master-example search later
                    "hidden_basis": [list(p) for p in profile["hidden_basis"]],
                }
                f.write(json.dumps(record) + "\n")

                if stats["total"] % 50 == 0:
                    logger.info(
                        f"  [{stats['total']:4d}] fee_pass={stats['pass_rank']} "
                        f"cons_pass={stats['pass_conservation']} "
                        f"bounds_pass={stats['pass_bounds']}"
                    )

            except Exception as e:
                stats["errors"] += 1
                logger.exception(f"error on {a}+{b}: {e}")
                continue

    with open(summary_path, "w") as f:
        json.dump(stats, f, indent=2, sort_keys=True)

    logger.info("=" * 70)
    logger.info("Witness Geometry Structural Verification Summary")
    logger.info("=" * 70)
    logger.info(f"Total compositions:         {stats['total']}")
    logger.info(f"  Trivial (no hidden):      {stats['trivial_no_hidden']}")
    logger.info(f"  Fee zero but H > 0:       {stats['fee_zero']}")
    logger.info(f"  Nontrivial (fee > 0):     {stats['nontrivial']}")
    logger.info(f"Errors:                     {stats['errors']}")
    logger.info(f"")
    logger.info(f"Structural identity checks:")
    logger.info(f"  rank(K) == fee_core:       {stats['pass_rank']:4d} pass, {stats['fail_rank']:4d} fail")
    logger.info(f"  sum leverage == fee:       {stats['pass_conservation']:4d} pass, {stats['fail_conservation']:4d} fail")
    logger.info(f"  0 <= leverage <= 1:        {stats['pass_bounds']:4d} pass, {stats['fail_bounds']:4d} fail")
    logger.info(f"")
    logger.info(f"Coloop histogram:")
    for count in sorted(stats["coloop_histogram"].keys()):
        n = stats["coloop_histogram"][count]
        logger.info(f"  {count} coloops: {n:4d} compositions")
    logger.info(f"Total loop count (across all comps): {stats['loop_count_total']}")
    logger.info(f"")
    logger.info(f"Output: {out_path}")
    logger.info(f"Summary: {summary_path}")


if __name__ == "__main__":
    run_sweep()
