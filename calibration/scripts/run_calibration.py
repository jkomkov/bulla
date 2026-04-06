#!/usr/bin/env python3
"""Run the calibration study pipeline.

Usage:
    # Quick QA on existing 4-server manifests:
    python calibration/scripts/run_calibration.py --qa

    # Full Tier 1 collection + computation:
    python calibration/scripts/run_calibration.py --tier 1
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add bulla/src (for bulla package) and bulla/ (for calibration package) to path
BULLA_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BULLA_ROOT / "src"))
sys.path.insert(0, str(BULLA_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("calibration")


def run_qa(data_dir: Path) -> None:
    """QA checkpoint: run full pipeline on existing 4-server manifests."""
    from calibration.corpus import ManifestStore, import_from_directory
    from calibration.compute import run_pairwise
    from calibration.analyze import analyze
    from calibration.report import generate

    manifests_dir = BULLA_ROOT / "examples" / "real_world_audit" / "manifests"
    if not manifests_dir.exists():
        logger.error("Cannot find %s", manifests_dir)
        sys.exit(1)

    logger.info("=== QA: Importing 4-server manifests ===")
    store = ManifestStore(data_dir=data_dir)
    import_from_directory(store, manifests_dir)
    stats = store.stats()
    logger.info("Imported %d servers, %d total tools", stats["servers"], stats["total_tools"])

    logger.info("=== QA: Pairwise computation ===")
    db_path = data_dir / "coherence.db"
    db = run_pairwise(data_dir, db_path)

    summary = db.summary()
    logger.info("Fee distribution: %s", summary["fee_distribution"])
    logger.info("Avg fee: %.1f", summary["avg_fee"])
    logger.info("Nonzero fee: %d/%d (%.0f%%)",
                summary["nonzero_fee_count"], summary["compositions"],
                summary["nonzero_fee_pct"])
    logger.info("Nonzero boundary: %d", summary["nonzero_boundary_count"])
    logger.info("Total blind spots in DB: %d", summary["blind_spots"])

    # Sanity checks
    assert summary["compositions"] == 6, (
        f"Expected 6 pairs from 4 servers, got {summary['compositions']}"
    )
    assert summary["blind_spots"] > 0, "Expected nonzero blind spots"
    assert summary["nonzero_fee_count"] > 0, "Expected at least some nonzero fees"

    logger.info("=== QA: Analysis (no annotations yet) ===")
    results = analyze(db_path)
    logger.info("Dimension stats: %d dimensions found", len(results.dimension_stats))
    for ds in results.dimension_stats[:5]:
        logger.info("  %s: %d occurrences", ds.dimension, ds.total_occurrences)

    logger.info("Server scores:")
    for ss in results.server_scores:
        logger.info("  %s: composability=%.0f%%, mean_fee=%.1f",
                     ss.server, ss.composability * 100, ss.mean_fee)

    logger.info("=== QA: Report generation ===")
    report_dir = data_dir / "report"
    md_path = generate(db_path, results, output_dir=report_dir, format="md")
    generate(db_path, results, output_dir=report_dir, format="json")
    logger.info("Markdown report: %s", md_path)

    db.close()
    logger.info("=== QA PASSED ===")


def run_full(tier: int, data_dir: Path, scan_local: bool) -> None:
    """Run the full collection + computation pipeline."""
    from calibration.corpus import collect
    from calibration.compute import run as run_compute
    from calibration.analyze import analyze
    from calibration.report import generate

    existing = BULLA_ROOT / "examples" / "real_world_audit" / "manifests"

    logger.info("=== Phase 1: Corpus collection (tier %d) ===", tier)
    store = collect(
        tier=tier,
        output_dir=data_dir,
        scan_local=scan_local,
        import_existing=existing if existing.exists() else None,
    )
    stats = store.stats()
    logger.info("Corpus: %d servers, %d tools", stats["servers"], stats["total_tools"])

    logger.info("=== Phase 2: Pairwise computation ===")
    db = run_compute(corpus_dir=data_dir, strategy="pairwise")
    summary = db.summary()
    logger.info("Computed: %d compositions", summary["compositions"])

    logger.info("=== Phase 3c: Analysis ===")
    db_path = data_dir / "coherence.db"
    results = analyze(db_path)

    logger.info("=== Phase 4: Report ===")
    report_dir = data_dir / "report"
    generate(db_path, results, output_dir=report_dir, format="md")
    generate(db_path, results, output_dir=report_dir, format="json")

    db.close()
    logger.info("=== Pipeline complete ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulla Calibration Study")
    parser.add_argument("--qa", action="store_true",
                        help="QA checkpoint on existing 4-server manifests")
    parser.add_argument("--tier", type=int, default=1, choices=[1, 2, 3],
                        help="Collection tier (default: 1)")
    parser.add_argument("--data-dir", type=Path,
                        default=BULLA_ROOT / "calibration" / "data",
                        help="Data directory")
    parser.add_argument("--no-scan", action="store_true",
                        help="Skip local server scanning")
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)

    if args.qa:
        qa_dir = args.data_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        run_qa(qa_dir)
    else:
        run_full(args.tier, args.data_dir, scan_local=not args.no_scan)


if __name__ == "__main__":
    main()
