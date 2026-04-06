"""Phase 4: Report generation for the State of Agent Coherence.

Generates the public report from analysis results + database.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from calibration.analyze import AnalysisResults, CalibrationPoint

logger = logging.getLogger(__name__)


def generate(
    db_path: str | Path,
    analysis: AnalysisResults,
    *,
    output_dir: str | Path = "calibration/report",
    format: str = "md",
) -> Path:
    """Generate the Coherence Index report."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    corpus_stats = _corpus_stats(conn)
    conn.close()

    if format == "md":
        return _generate_markdown(output_dir, analysis, corpus_stats)
    elif format == "json":
        return _generate_json(output_dir, analysis, corpus_stats)
    else:
        raise ValueError(f"Unknown format: {format}")


def _corpus_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    n_comp = conn.execute("SELECT COUNT(*) FROM compositions").fetchone()[0]
    n_servers = conn.execute(
        "SELECT COUNT(DISTINCT value) FROM "
        "(SELECT json_each.value FROM compositions, json_each(compositions.servers))"
    ).fetchone()[0]
    n_bs = conn.execute("SELECT COUNT(*) FROM blind_spots").fetchone()[0]
    fee_dist = conn.execute(
        "SELECT coherence_fee, COUNT(*) FROM diagnostics "
        "GROUP BY coherence_fee ORDER BY coherence_fee"
    ).fetchall()
    return {
        "n_compositions": n_comp,
        "n_servers": n_servers,
        "n_blind_spots": n_bs,
        "fee_distribution": dict(fee_dist),
    }


def _generate_markdown(
    output_dir: Path,
    analysis: AnalysisResults,
    corpus: dict[str, Any],
) -> Path:
    lines: list[str] = []

    # Title
    lines.append("# State of Agent Coherence, Q2 2026")
    lines.append("")
    lines.append(f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}*")
    lines.append("")

    # Executive summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        f"We analyzed **{corpus['n_servers']} MCP servers** across "
        f"**{corpus['n_compositions']:,} pairwise compositions**, computing the "
        f"coherence fee for each. The coherence fee measures the number of "
        f"independent semantic convention dimensions that bilateral verification "
        f"cannot detect."
    )
    lines.append("")
    n_nonzero = sum(
        v for k, v in corpus["fee_distribution"].items() if k > 0
    )
    pct_nonzero = 100 * n_nonzero / corpus["n_compositions"] if corpus["n_compositions"] else 0
    lines.append(f"**Key findings:**")
    lines.append(f"- {pct_nonzero:.0f}% of pairwise compositions have nonzero coherence fee")
    lines.append(f"- {corpus['n_blind_spots']:,} total blind spots identified")
    if analysis.precision > 0:
        lines.append(
            f"- Blind spot precision: {analysis.precision:.0%} "
            f"({analysis.n_real_mismatch} real mismatches out of "
            f"{analysis.n_real_mismatch + analysis.n_false_positive} annotated)"
        )
    if analysis.live_validated > 0:
        lines.append(
            f"- {analysis.live_validated} blind spots validated via live execution"
        )
    lines.append("")

    # Fee distribution
    lines.append("## Coherence Fee Distribution")
    lines.append("")
    lines.append("| Fee | Compositions | % |")
    lines.append("|-----|-------------|---|")
    for fee, count in sorted(corpus["fee_distribution"].items()):
        pct = 100 * count / corpus["n_compositions"] if corpus["n_compositions"] else 0
        lines.append(f"| {fee} | {count:,} | {pct:.1f}% |")
    lines.append("")

    # Top dangerous blind spots
    if analysis.top_dangerous:
        lines.append("## Top Dangerous Blind Spots")
        lines.append("")
        lines.append(
            "Specific server pairs with confirmed or likely semantic mismatches:"
        )
        lines.append("")
        lines.append("| Dimension | Tool A | Tool B | Field A | Field B | Source |")
        lines.append("|-----------|--------|--------|---------|---------|--------|")
        for bs in analysis.top_dangerous:
            lines.append(
                f"| {bs['dimension']} | {bs['from_tool']} | {bs['to_tool']} | "
                f"{bs['from_field']} | {bs['to_field']} | {bs['source'] or 'llm'} |"
            )
        lines.append("")

    # Calibration curve
    if analysis.calibration_curve:
        lines.append("## Calibration Curve: Fee vs Failure Probability")
        lines.append("")
        if analysis.logistic_a is not None:
            lines.append(
                f"Logistic fit: P(mismatch) = sigmoid({analysis.logistic_a:.3f} * fee "
                f"+ {analysis.logistic_b:.3f})"
            )
            lines.append("")
        lines.append("| Fee | Compositions | With Mismatch | P(failure) |")
        lines.append("|-----|-------------|---------------|------------|")
        for pt in analysis.calibration_curve:
            lines.append(
                f"| {pt.fee} | {pt.n_compositions} | "
                f"{pt.n_with_real_mismatch} | {pt.p_failure:.2%} |"
            )
        lines.append("")

    # Boundary fee analysis
    lines.append("## Boundary Fee Analysis")
    lines.append("")
    lines.append(
        f"- Cross-category mean boundary fee: **{analysis.cross_category_mean_boundary:.2f}**"
    )
    lines.append(
        f"- Intra-category mean boundary fee: **{analysis.intra_category_mean_boundary:.2f}**"
    )
    if analysis.boundary_spearman_rho is not None:
        lines.append(
            f"- Spearman rho (boundary fee vs real mismatch count): "
            f"**{analysis.boundary_spearman_rho:.3f}**"
        )
    lines.append("")

    # Dimension landscape
    if analysis.dimension_stats:
        lines.append("## Dimension Landscape")
        lines.append("")
        lines.append("| Dimension | Occurrences | Real Mismatch | False Positive | Precision |")
        lines.append("|-----------|-------------|---------------|----------------|-----------|")
        for ds in analysis.dimension_stats[:15]:
            lines.append(
                f"| {ds.dimension} | {ds.total_occurrences:,} | "
                f"{ds.n_real_mismatch} | {ds.n_false_positive} | "
                f"{ds.precision:.0%} |"
            )
        lines.append("")

    # Server scorecards
    if analysis.server_scores:
        lines.append("## Server Composability Scores")
        lines.append("")
        lines.append(
            "Composability = fraction of pairwise compositions with fee == 0."
        )
        lines.append("")
        lines.append("| Server | Composability | Mean Fee | Compositions | Top Dimensions |")
        lines.append("|--------|--------------|----------|-------------|----------------|")
        for ss in analysis.server_scores[:20]:
            dims = ", ".join(ss.most_common_dimensions) if ss.most_common_dimensions else "-"
            lines.append(
                f"| {ss.server} | {ss.composability:.0%} | "
                f"{ss.mean_fee:.1f} | {ss.n_compositions} | {dims} |"
            )
        lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        "Manifests were collected permissionlessly from the MCP server ecosystem "
        "(official registry, public schema repositories, and local server scanning). "
        "Coherence fees were computed using Bulla v0.28.0+ with the base convention "
        "pack (11 dimensions). Blind spots were annotated via live execution testing "
        "(ground truth) and LLM-assisted classification (extended coverage). "
        "The calibration curve maps coherence fee to empirical failure probability."
    )
    lines.append("")
    lines.append(
        "For full details on the mathematical foundation, see the "
        "[SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf) "
        "and [Bulla Witness Contract](https://github.com/jkomkov/bulla/blob/main/WITNESS-CONTRACT.md)."
    )
    lines.append("")

    path = output_dir / "state-of-agent-coherence.md"
    path.write_text("\n".join(lines))
    logger.info("Report written to %s", path)
    return path


def _generate_json(
    output_dir: Path,
    analysis: AnalysisResults,
    corpus: dict[str, Any],
) -> Path:
    data = {
        "title": "State of Agent Coherence, Q2 2026",
        "generated": datetime.now(timezone.utc).isoformat(),
        "corpus": corpus,
        "precision": {
            "total_annotated": analysis.total_annotated,
            "real_mismatch": analysis.n_real_mismatch,
            "plausible": analysis.n_plausible,
            "false_positive": analysis.n_false_positive,
            "precision": analysis.precision,
            "live_validated": analysis.live_validated,
        },
        "calibration_curve": [
            {
                "fee": pt.fee,
                "n_compositions": pt.n_compositions,
                "n_with_real_mismatch": pt.n_with_real_mismatch,
                "p_failure": pt.p_failure,
            }
            for pt in analysis.calibration_curve
        ],
        "logistic_fit": {
            "a": analysis.logistic_a,
            "b": analysis.logistic_b,
        },
        "boundary_fee": {
            "spearman_rho": analysis.boundary_spearman_rho,
            "cross_category_mean": analysis.cross_category_mean_boundary,
            "intra_category_mean": analysis.intra_category_mean_boundary,
        },
        "dimensions": [
            {
                "dimension": ds.dimension,
                "occurrences": ds.total_occurrences,
                "real_mismatch": ds.n_real_mismatch,
                "precision": ds.precision,
            }
            for ds in analysis.dimension_stats
        ],
        "server_scores": [
            {
                "server": ss.server,
                "composability": ss.composability,
                "mean_fee": ss.mean_fee,
                "n_compositions": ss.n_compositions,
            }
            for ss in analysis.server_scores
        ],
        "top_dangerous": analysis.top_dangerous,
    }

    path = output_dir / "state-of-agent-coherence.json"
    path.write_text(json.dumps(data, indent=2))
    logger.info("JSON report written to %s", path)
    return path


def run(
    *,
    db_path: str | Path = "calibration/data/coherence.db",
    output_dir: str | Path = "calibration/report",
) -> Path:
    """Generate both markdown and JSON reports."""
    from calibration.analyze import analyze

    analysis = analyze(db_path)
    md_path = generate(db_path, analysis, output_dir=output_dir, format="md")
    generate(db_path, analysis, output_dir=output_dir, format="json")
    return md_path
