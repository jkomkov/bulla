"""Phase 3c: Statistical analysis and calibration curves.

Produces the key metrics:
  1. Blind spot precision (fraction that are real mismatches)
  2. Fee → failure calibration curve
  3. Boundary fee predictiveness
  4. Dimension frequency and information value
  5. Per-server composability score
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CalibrationPoint:
    """One point on the fee → failure curve."""

    fee: int
    n_compositions: int
    n_with_real_mismatch: int
    p_failure: float


@dataclass
class DimensionStats:
    """Statistics for one convention dimension."""

    dimension: str
    total_occurrences: int
    n_real_mismatch: int
    n_plausible: int
    n_false_positive: int
    precision: float  # real / (real + false_positive)


@dataclass
class ServerScore:
    """Composability score for one server."""

    server: str
    n_compositions: int
    n_zero_fee: int
    composability: float  # fraction with fee == 0
    mean_fee: float
    most_common_dimensions: list[str]


@dataclass
class AnalysisResults:
    """Complete analysis output."""

    # Metric 1: Blind spot precision
    total_annotated: int
    n_real_mismatch: int
    n_plausible: int
    n_false_positive: int
    precision: float
    live_validated: int

    # Metric 2: Calibration curve
    calibration_curve: list[CalibrationPoint]
    logistic_a: float | None  # slope
    logistic_b: float | None  # intercept

    # Metric 3: Boundary fee
    boundary_spearman_rho: float | None
    cross_category_mean_boundary: float
    intra_category_mean_boundary: float

    # Metric 4: Dimensions
    dimension_stats: list[DimensionStats]

    # Metric 5: Server scores
    server_scores: list[ServerScore]

    # Top blind spots
    top_dangerous: list[dict[str, Any]]


def _spearman_rho(x: list[float], y: list[float]) -> float | None:
    """Compute Spearman rank correlation."""
    if len(x) < 3 or len(x) != len(y):
        return None

    def _rank(vals: list[float]) -> list[float]:
        indexed = sorted(enumerate(vals), key=lambda p: p[1])
        ranks = [0.0] * len(vals)
        for rank, (orig_idx, _) in enumerate(indexed):
            ranks[orig_idx] = float(rank + 1)
        return ranks

    rx = _rank(x)
    ry = _rank(y)
    n = len(x)
    d_sq = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1.0 - (6.0 * d_sq) / (n * (n * n - 1))


def _logistic_fit(
    fees: list[int], p_failures: list[float], weights: list[int],
) -> tuple[float | None, float | None]:
    """Simple weighted logistic regression via iterative reweighted least squares.

    Returns (slope_a, intercept_b) for P(failure) = 1 / (1 + exp(-(a*fee + b))).
    Falls back to None if convergence fails or data is insufficient.
    """
    if len(fees) < 2:
        return None, None

    # Filter out points with p=0 or p=1 (log-odds undefined)
    valid = [
        (f, p, w) for f, p, w in zip(fees, p_failures, weights)
        if 0 < p < 1 and w > 0
    ]
    if len(valid) < 2:
        return None, None

    # Weighted least squares on log-odds
    xs = [float(f) for f, _, _ in valid]
    ys = [math.log(p / (1 - p)) for _, p, _ in valid]
    ws = [float(w) for _, _, w in valid]

    sum_w = sum(ws)
    mean_x = sum(w * x for w, x in zip(ws, xs)) / sum_w
    mean_y = sum(w * y for w, y in zip(ws, ys)) / sum_w

    num = sum(w * (x - mean_x) * (y - mean_y) for w, x, y in zip(ws, xs, ys))
    den = sum(w * (x - mean_x) ** 2 for w, x in zip(ws, xs))

    if abs(den) < 1e-12:
        return None, None

    a = num / den
    b = mean_y - a * mean_x
    return a, b


def analyze(db_path: str | Path) -> AnalysisResults:
    """Run the full analysis pipeline."""
    conn = sqlite3.connect(str(db_path))

    # ── Metric 1: Blind spot precision ───────────────────────────────

    total_annotated = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE annotation IS NOT NULL"
    ).fetchone()[0]
    n_real = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE annotation = 'REAL_MISMATCH'"
    ).fetchone()[0]
    n_plausible = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE annotation = 'PLAUSIBLE'"
    ).fetchone()[0]
    n_fp = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE annotation = 'FALSE_POSITIVE'"
    ).fetchone()[0]
    live_validated = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE validated = 1"
    ).fetchone()[0]

    precision = n_real / (n_real + n_fp) if (n_real + n_fp) > 0 else 0.0

    # ── Metric 2: Calibration curve ──────────────────────────────────

    # For each fee level, what fraction of compositions have at least
    # one REAL_MISMATCH blind spot?
    fee_rows = conn.execute(
        "SELECT d.coherence_fee, d.comp_id FROM diagnostics d"
    ).fetchall()

    # Build comp_id → has_real_mismatch mapping
    real_mismatch_comps = set()
    for row in conn.execute(
        "SELECT DISTINCT comp_id FROM blind_spots WHERE annotation = 'REAL_MISMATCH'"
    ).fetchall():
        real_mismatch_comps.add(row[0])

    fee_groups: dict[int, list[bool]] = {}
    for fee, comp_id in fee_rows:
        fee_groups.setdefault(fee, []).append(comp_id in real_mismatch_comps)

    calibration_curve: list[CalibrationPoint] = []
    for fee in sorted(fee_groups.keys()):
        group = fee_groups[fee]
        n_with = sum(group)
        calibration_curve.append(CalibrationPoint(
            fee=fee,
            n_compositions=len(group),
            n_with_real_mismatch=n_with,
            p_failure=n_with / len(group) if group else 0.0,
        ))

    # Logistic fit
    logistic_a, logistic_b = _logistic_fit(
        [c.fee for c in calibration_curve],
        [c.p_failure for c in calibration_curve],
        [c.n_compositions for c in calibration_curve],
    )

    # ── Metric 3: Boundary fee analysis ──────────────────────────────

    boundary_rows = conn.execute(
        "SELECT d.boundary_fee, c.pair_type FROM diagnostics d "
        "JOIN compositions c ON d.comp_id = c.id "
        "WHERE d.boundary_fee IS NOT NULL"
    ).fetchall()

    cross_fees = [r[0] for r in boundary_rows if r[1] == "cross_category"]
    intra_fees = [r[0] for r in boundary_rows if r[1] == "intra_category"]

    cross_mean = sum(cross_fees) / len(cross_fees) if cross_fees else 0.0
    intra_mean = sum(intra_fees) / len(intra_fees) if intra_fees else 0.0

    # Spearman rho: boundary_fee vs real_mismatch count per composition
    bf_rm_rows = conn.execute(
        "SELECT d.boundary_fee, "
        "  (SELECT COUNT(*) FROM blind_spots b "
        "   WHERE b.comp_id = d.comp_id AND b.annotation = 'REAL_MISMATCH') "
        "FROM diagnostics d WHERE d.boundary_fee IS NOT NULL"
    ).fetchall()

    bf_vals = [float(r[0]) for r in bf_rm_rows]
    rm_vals = [float(r[1]) for r in bf_rm_rows]
    boundary_rho = _spearman_rho(bf_vals, rm_vals)

    # ── Metric 4: Dimension stats ────────────────────────────────────

    dim_rows = conn.execute(
        "SELECT dimension, "
        "  COUNT(*) as total, "
        "  SUM(CASE WHEN annotation = 'REAL_MISMATCH' THEN 1 ELSE 0 END), "
        "  SUM(CASE WHEN annotation = 'PLAUSIBLE' THEN 1 ELSE 0 END), "
        "  SUM(CASE WHEN annotation = 'FALSE_POSITIVE' THEN 1 ELSE 0 END) "
        "FROM blind_spots "
        "GROUP BY dimension ORDER BY total DESC"
    ).fetchall()

    dimension_stats = []
    for dim, total, real, plaus, fp in dim_rows:
        real = real or 0
        plaus = plaus or 0
        fp = fp or 0
        prec = real / (real + fp) if (real + fp) > 0 else 0.0
        dimension_stats.append(DimensionStats(
            dimension=dim,
            total_occurrences=total,
            n_real_mismatch=real,
            n_plausible=plaus,
            n_false_positive=fp,
            precision=prec,
        ))

    # ── Metric 5: Server composability scores ────────────────────────

    server_rows = conn.execute(
        "SELECT c.servers, d.coherence_fee FROM compositions c "
        "JOIN diagnostics d ON c.id = d.comp_id "
        "WHERE c.strategy = 'pairwise'"
    ).fetchall()

    server_fee_map: dict[str, list[int]] = {}
    for servers_json, fee in server_rows:
        servers = json.loads(servers_json)
        for s in servers:
            server_fee_map.setdefault(s, []).append(fee)

    # Per-server dimension frequency
    server_dim_map: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT c.servers, b.dimension FROM blind_spots b "
        "JOIN compositions c ON b.comp_id = c.id "
        "WHERE b.annotation = 'REAL_MISMATCH'"
    ).fetchall():
        servers = json.loads(row[0])
        for s in servers:
            dims = server_dim_map.setdefault(s, {})
            dims[row[1]] = dims.get(row[1], 0) + 1

    server_scores = []
    for server, fees in sorted(server_fee_map.items()):
        n_zero = sum(1 for f in fees if f == 0)
        dims = server_dim_map.get(server, {})
        top_dims = sorted(dims.keys(), key=lambda d: dims[d], reverse=True)[:3]
        server_scores.append(ServerScore(
            server=server,
            n_compositions=len(fees),
            n_zero_fee=n_zero,
            composability=n_zero / len(fees) if fees else 0.0,
            mean_fee=sum(fees) / len(fees) if fees else 0.0,
            most_common_dimensions=top_dims,
        ))

    server_scores.sort(key=lambda s: s.composability, reverse=True)

    # ── Top dangerous blind spots ────────────────────────────────────

    top_rows = conn.execute(
        "SELECT b.dimension, b.from_tool, b.to_tool, b.from_field, b.to_field, "
        "       b.annotation, b.annotation_source "
        "FROM blind_spots b "
        "WHERE b.annotation = 'REAL_MISMATCH' "
        "ORDER BY b.validated DESC, b.id "
        "LIMIT 10"
    ).fetchall()

    top_dangerous = [
        {
            "dimension": r[0],
            "from_tool": r[1],
            "to_tool": r[2],
            "from_field": r[3],
            "to_field": r[4],
            "annotation": r[5],
            "source": r[6],
        }
        for r in top_rows
    ]

    conn.close()

    return AnalysisResults(
        total_annotated=total_annotated,
        n_real_mismatch=n_real,
        n_plausible=n_plausible,
        n_false_positive=n_fp,
        precision=precision,
        live_validated=live_validated,
        calibration_curve=calibration_curve,
        logistic_a=logistic_a,
        logistic_b=logistic_b,
        boundary_spearman_rho=boundary_rho,
        cross_category_mean_boundary=cross_mean,
        intra_category_mean_boundary=intra_mean,
        dimension_stats=dimension_stats,
        server_scores=server_scores,
        top_dangerous=top_dangerous,
    )


def run(*, db_path: str | Path = "calibration/data/coherence.db") -> AnalysisResults:
    """Run the analysis pipeline."""
    results = analyze(db_path)
    logger.info(
        "Analysis: %d annotated, precision=%.1f%%, %d live-validated, "
        "logistic a=%.3f b=%.3f",
        results.total_annotated,
        results.precision * 100,
        results.live_validated,
        results.logistic_a or 0,
        results.logistic_b or 0,
    )
    return results
