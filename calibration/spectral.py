"""Sprint A.3 — Hansen-Ghrist eigenvalue interlacing on Bulla's real-schema corpus.

Pre-registration: papers/sheaf/results/sprint_a3_pre_registration.md
  SHA-256: 53ef937fb5cc0cdab4aa77efc24c57dc9c6943535a8a5539b01c80fe8b664dd7

Reconstructs the observable and full coboundary matrices for each composition in
the 703-composition real-schema registry corpus, computes the sheaf Laplacian
spectra L = δᵀδ, and tests the Cauchy interlacing predicate
    λ_k(L_full) ≤ λ_k(L_obs) ≤ λ_{k+t}(L_full)   where t = n_full − n_obs.

The claim under test: Bulla's (δ_obs, δ_full) pair is an empirical instance of a
Hansen-Ghrist sheaf morphism (Hansen-Ghrist 2019, "Toward a Spectral Theory of
Cellular Sheaves").

USAGE
-----
    python -m calibration.spectral \\
        --data-dir bulla/calibration/data/registry \\
        --out papers/sheaf/data/spectral_interlacing_703.jsonl

This script is research-branch only. It imports numpy but does NOT touch the
Bulla kernel — the coboundary construction is still the pure-Fraction version
from bulla/src/bulla/coboundary.py.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Numerical slack for the interlacing inequality. Each eigenvalue test allows
# an absolute error of SLACK_ABS + SLACK_REL * max(|λ|, 1) to absorb FP roundoff
# from the Fraction → float cast and symmetric eigendecomposition.
SLACK_ABS = 1e-9
SLACK_REL = 1e-9

MIN_SCHEMA_FIELDS = 3  # Must match bulla.calibration.index.MIN_SCHEMA_FIELDS


@dataclass
class InterlaceResult:
    """Per-composition result of the Hansen-Ghrist interlacing test."""

    composition: str
    composition_hash: str
    n_obs: int
    n_full: int
    rank_obs: int
    rank_full: int
    fee: int
    eigenvalues_obs: list[float]
    eigenvalues_full: list[float]
    verdict: str  # "licensed" | "partial-left" | "partial-right" | "broken" | "trivial"
    violations: list[dict[str, Any]]  # (k, side, lhs, rhs, slack)


def _frac_matrix_to_np(mat: list[list[Fraction]]) -> np.ndarray:
    """Lift a Fraction matrix to float64. Returns (0,0)-shape for empty rows."""
    if not mat or not mat[0]:
        return np.zeros((len(mat), 0), dtype=np.float64)
    return np.asarray(
        [[float(v) for v in row] for row in mat], dtype=np.float64
    )


def _laplacian_eigs(delta_np: np.ndarray) -> np.ndarray:
    """Compute eigenvalues of L = δᵀδ, sorted ascending. Returns length n_cols."""
    n_rows, n_cols = delta_np.shape
    if n_cols == 0:
        return np.zeros(0, dtype=np.float64)
    L = delta_np.T @ delta_np  # n_cols × n_cols, symmetric PSD
    # Symmetrize to kill any FP asymmetry from the matmul
    L = 0.5 * (L + L.T)
    eigs = np.linalg.eigvalsh(L)
    # Clip tiny negative noise (PSD matrices should have non-negative eigs)
    eigs = np.clip(eigs, 0.0, None)
    return np.sort(eigs)


def _test_interlacing(
    eigs_obs: np.ndarray, eigs_full: np.ndarray
) -> tuple[str, list[dict[str, Any]]]:
    """Test Cauchy interlacing λ_k(L_full) ≤ λ_k(L_obs) ≤ λ_{k+t}(L_full).

    Returns (verdict, violations) where verdict ∈ {"licensed", "partial-left",
    "partial-right", "broken", "trivial"}.
    """
    n_obs = len(eigs_obs)
    n_full = len(eigs_full)

    # Trivial case: obs and full have the same vertex space — no licensing test.
    if n_obs == n_full and n_obs > 0:
        max_abs = max(float(eigs_full.max() if n_full else 0.0), 1.0)
        if np.allclose(eigs_obs, eigs_full, atol=SLACK_ABS + SLACK_REL * max_abs):
            return "trivial", []
        # Different spectra despite same dimension — something is wrong
        # (the two operators are not the same even though n_obs = n_full).
        # This is not a P-interlace violation per se; record as its own class.
        violations = []
        for k in range(n_obs):
            diff = float(abs(eigs_obs[k] - eigs_full[k]))
            slack = SLACK_ABS + SLACK_REL * max(abs(eigs_full[k]), 1.0)
            if diff > slack:
                violations.append({
                    "k": k,
                    "side": "equal-dim-differ",
                    "lhs": float(eigs_obs[k]),
                    "rhs": float(eigs_full[k]),
                    "slack": slack,
                })
        return ("partial-left" if violations else "trivial"), violations

    if n_obs > n_full:
        # n_obs should never exceed n_full (observable ⊆ internal). If it does,
        # the composition has structural anomalies and the test is ill-posed.
        return "broken", [{
            "k": -1,
            "side": "dim-inversion",
            "lhs": n_obs,
            "rhs": n_full,
            "slack": 0.0,
        }]

    t = n_full - n_obs
    violations: list[dict[str, Any]] = []

    for k in range(n_obs):
        # Left inequality: λ_k(L_full) ≤ λ_k(L_obs)
        lhs_left = float(eigs_full[k])
        rhs_left = float(eigs_obs[k])
        slack_left = SLACK_ABS + SLACK_REL * max(abs(lhs_left), abs(rhs_left), 1.0)
        if lhs_left > rhs_left + slack_left:
            violations.append({
                "k": k,
                "side": "left",
                "lhs": lhs_left,
                "rhs": rhs_left,
                "slack": slack_left,
            })

        # Right inequality: λ_k(L_obs) ≤ λ_{k+t}(L_full)
        lhs_right = float(eigs_obs[k])
        rhs_right = float(eigs_full[k + t])
        slack_right = SLACK_ABS + SLACK_REL * max(abs(lhs_right), abs(rhs_right), 1.0)
        if lhs_right > rhs_right + slack_right:
            violations.append({
                "k": k,
                "side": "right",
                "lhs": lhs_right,
                "rhs": rhs_right,
                "slack": slack_right,
            })

    if not violations:
        return "licensed", []

    left_hits = sum(1 for v in violations if v["side"] == "left")
    right_hits = sum(1 for v in violations if v["side"] == "right")
    if left_hits and not right_hits:
        return "partial-left", violations
    if right_hits and not left_hits:
        return "partial-right", violations
    return "broken", violations


def _field_count(tools: list[dict[str, Any]]) -> int:
    total = 0
    for t in tools:
        schema = t.get("inputSchema") or t.get("input_schema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except (ValueError, json.JSONDecodeError):
                schema = {}
        total += len(schema.get("properties", {}))
    return total


def run_sweep(data_dir: Path, out_path: Path, limit: int | None = None) -> None:
    """Run the interlacing sweep on all real-schema pairwise compositions."""
    from bulla.coboundary import build_coboundary, matrix_rank
    from calibration.compute import diagnose_pair
    from calibration.corpus import ManifestStore

    store = ManifestStore(data_dir=data_dir)

    # Real-schema filter — matches Indexer.compute() / _real_schema_servers() at
    # bulla/calibration/index.py:202-208. Keeps Sprint A.3 on the same 703-
    # composition corpus that Sprint 0.4 resolved via Branch X-multi(H2∧H4).
    real_servers: dict[str, list[dict[str, Any]]] = {}
    for name in store.list_servers():
        tools = store.get_tools(name)
        if _field_count(tools) >= MIN_SCHEMA_FIELDS:
            real_servers[name] = tools

    logger.info(
        "Real-schema corpus: %d servers → %d pairwise compositions",
        len(real_servers),
        len(real_servers) * (len(real_servers) - 1) // 2,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tally = {
        "licensed": 0,
        "partial-left": 0,
        "partial-right": 0,
        "broken": 0,
        "trivial": 0,
        "errors": 0,
    }
    processed = 0
    n_obs_equals_n_full = 0

    with open(out_path, "w") as f:
        for a, b in itertools.combinations(sorted(real_servers.keys()), 2):
            if limit is not None and processed >= limit:
                break
            try:
                result = diagnose_pair(a, real_servers[a], b, real_servers[b])
                comp = result.kernel_composition
                if comp is None:
                    tally["errors"] += 1
                    continue

                delta_obs, _, _ = build_coboundary(
                    comp.tools, comp.edges, use_internal=False
                )
                delta_full, _, _ = build_coboundary(
                    comp.tools, comp.edges, use_internal=True
                )
                rank_obs = matrix_rank(delta_obs)
                rank_full = matrix_rank(delta_full)

                delta_obs_np = _frac_matrix_to_np(delta_obs)
                delta_full_np = _frac_matrix_to_np(delta_full)

                eigs_obs = _laplacian_eigs(delta_obs_np)
                eigs_full = _laplacian_eigs(delta_full_np)

                if not np.all(np.isfinite(eigs_obs)) or not np.all(
                    np.isfinite(eigs_full)
                ):
                    tally["errors"] += 1
                    continue

                n_obs = delta_obs_np.shape[1]
                n_full = delta_full_np.shape[1]
                if n_obs == n_full:
                    n_obs_equals_n_full += 1

                verdict, violations = _test_interlacing(eigs_obs, eigs_full)
                tally[verdict] = tally.get(verdict, 0) + 1

                record = InterlaceResult(
                    composition=result.name,
                    composition_hash=result.comp_id,
                    n_obs=n_obs,
                    n_full=n_full,
                    rank_obs=rank_obs,
                    rank_full=rank_full,
                    fee=result.coherence_fee,
                    eigenvalues_obs=[float(x) for x in eigs_obs],
                    eigenvalues_full=[float(x) for x in eigs_full],
                    verdict=verdict,
                    violations=violations,
                )
                f.write(json.dumps(record.__dict__) + "\n")
                processed += 1

                if processed % 50 == 0:
                    logger.info("  processed %d compositions", processed)

            except Exception as e:
                logger.debug("Failed %s+%s: %s", a, b, e)
                tally["errors"] += 1

    summary = {
        "corpus": "registry_real_schema",
        "servers": len(real_servers),
        "processed": processed,
        "n_obs_equals_n_full": n_obs_equals_n_full,
        "tally": tally,
        "pre_registration_sha256": "53ef937fb5cc0cdab4aa77efc24c57dc9c6943535a8a5539b01c80fe8b664dd7",
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2))

    logger.info("Sprint A.3 sweep complete:")
    for k, v in tally.items():
        logger.info("  %-15s %d", k, v)
    logger.info("  n_obs == n_full: %d / %d", n_obs_equals_n_full, processed)
    logger.info("Results: %s", out_path)
    logger.info("Summary: %s", summary_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("bulla/calibration/data/registry"),
        help="Registry data directory (contains manifests/ subdir).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("papers/sheaf/data/spectral_interlacing_703.jsonl"),
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit to first N compositions (for smoke-testing).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run_sweep(args.data_dir, args.out, limit=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
