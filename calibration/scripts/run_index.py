#!/usr/bin/env python3
"""Run the Coherence Index pipeline.

Scans the MCP server ecosystem, computes pairwise coherence fees,
generates witness receipts, and produces reports.

Usage:
    # Full index run (curated scope — scan + compute + receipts + report):
    python calibration/scripts/run_index.py

    # Registry scope (curated + schemas repo + registry crawl):
    python calibration/scripts/run_index.py --scope registry

    # Full scope (deep registry crawl):
    python calibration/scripts/run_index.py --scope full

    # With LLM discovery:
    python calibration/scripts/run_index.py --discover --provider openrouter

    # Scan only (no compute or report):
    python calibration/scripts/run_index.py --scan-only

    # Receipts only (from existing compute data):
    python calibration/scripts/run_index.py --receipts-only

    # Report only (from existing data):
    python calibration/scripts/run_index.py --report-only
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
logger = logging.getLogger("index")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coherence Index: collect, scan, compute, receipt, report.",
    )
    parser.add_argument(
        "--data-dir", type=Path,
        default=BULLA_ROOT / "calibration" / "data" / "index",
        help="Data directory for the index (default: calibration/data/index)",
    )
    parser.add_argument(
        "--scope", default="curated",
        choices=["curated", "registry", "full"],
        help="Collection scope: curated (~26), registry (~200), full (~500+)",
    )
    parser.add_argument(
        "--discover", action="store_true",
        help="Run LLM discovery after scanning (requires API key)",
    )
    parser.add_argument(
        "--provider", default="auto",
        choices=["auto", "openai", "anthropic", "openrouter"],
        help="LLM provider for discovery (default: auto-detect from env)",
    )
    parser.add_argument(
        "--no-receipts", action="store_true",
        help="Skip witness receipt generation",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--scan-only", action="store_true",
        help="Only scan servers, skip compute and report",
    )
    mode.add_argument(
        "--report-only", action="store_true",
        help="Only generate report from existing data",
    )
    mode.add_argument(
        "--receipts-only", action="store_true",
        help="Only generate receipts (requires prior compute run)",
    )
    parser.add_argument(
        "--timeout", type=float, default=20.0,
        help="Scan timeout per server in seconds (default: 20)",
    )
    args = parser.parse_args()

    from calibration.index import Indexer

    indexer = Indexer(
        data_dir=args.data_dir,
        scan_timeout=args.timeout,
        scope=args.scope,
    )

    if args.report_only:
        path = indexer.report()
        if path:
            logger.info("Report: %s", path)
        else:
            logger.error("No data to report on. Run a full index first.")
            sys.exit(1)
        return

    if args.receipts_only:
        # Re-compute to populate kernel objects, then generate receipts
        logger.info("Re-computing to populate kernel objects for receipts...")
        computed = indexer.compute()
        if computed == 0:
            logger.error("No compositions to generate receipts for.")
            sys.exit(1)
        generated = indexer.receipts()
        logger.info("Generated %d receipts from %d compositions.", generated, computed)
        return

    if args.scan_only:
        if args.scope != "curated":
            indexer.collect()
        scanned, failed, skipped = indexer.scan()
        stats = indexer.store.stats()
        logger.info(
            "Scan complete: %d scanned, %d failed, %d skipped. "
            "Corpus: %d servers, %d tools.",
            scanned, failed, skipped, stats["servers"], stats["total_tools"],
        )
        return

    indexer.run(
        discover=args.discover,
        provider=args.provider,
        generate_receipts=not args.no_receipts,
    )


if __name__ == "__main__":
    main()
