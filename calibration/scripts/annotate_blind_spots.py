#!/usr/bin/env python3
"""Automated blind spot annotation for the Coherence Index.

Applies a conservative heuristic annotation to blind spots in the
coherence database. Annotations are used by the analysis pipeline
to compute precision metrics and calibration curves.

Annotation categories:
  - FALSE_POSITIVE: within-server blind spots (same server prefix on
    both tools). These cannot represent real cross-server mismatches.
  - REAL_MISMATCH: cross-server blind spots on dimensions known to
    produce genuine convention disagreements (path_convention, date_format).
  - PLAUSIBLE: cross-server blind spots that may or may not be real
    (sort_direction, state_filter — similar semantics, unclear values).

Usage:
    python calibration/scripts/annotate_blind_spots.py
    python calibration/scripts/annotate_blind_spots.py --db calibration/data/registry/coherence.db
    python calibration/scripts/annotate_blind_spots.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

BULLA_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BULLA_ROOT / "src"))
sys.path.insert(0, str(BULLA_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("annotate")

# Dimensions where cross-server blind spots are almost always real
REAL_MISMATCH_DIMENSIONS = {
    "path_convention_match",
    "date_format_match",
    "id_offset_match",
}

# Dimensions where cross-server blind spots are plausible but uncertain
PLAUSIBLE_DIMENSIONS = {
    "sort_direction_match",
    "state_filter_match",
    "owner_convention_match",
}


def annotate(db_path: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Apply heuristic annotations to unannotated blind spots.

    Returns counts by annotation category.
    """
    conn = sqlite3.connect(str(db_path))

    total = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE annotation IS NULL"
    ).fetchone()[0]
    logger.info("Unannotated blind spots: %d", total)

    if total == 0:
        logger.info("Nothing to annotate.")
        conn.close()
        return {"FALSE_POSITIVE": 0, "REAL_MISMATCH": 0, "PLAUSIBLE": 0}

    counts = {"FALSE_POSITIVE": 0, "REAL_MISMATCH": 0, "PLAUSIBLE": 0}

    rows = conn.execute(
        "SELECT id, dimension, from_tool, to_tool FROM blind_spots "
        "WHERE annotation IS NULL"
    ).fetchall()

    for bs_id, dimension, from_tool, to_tool in rows:
        from_server = from_tool.split("__")[0] if "__" in from_tool else from_tool
        to_server = to_tool.split("__")[0] if "__" in to_tool else to_tool

        if from_server == to_server:
            annotation = "FALSE_POSITIVE"
            source = "auto:within_server"
        elif dimension in REAL_MISMATCH_DIMENSIONS:
            annotation = "REAL_MISMATCH"
            source = f"auto:cross_server_{dimension}"
        elif dimension in PLAUSIBLE_DIMENSIONS:
            annotation = "PLAUSIBLE"
            source = f"auto:cross_server_{dimension}"
        else:
            annotation = "PLAUSIBLE"
            source = f"auto:cross_server_unknown_{dimension}"

        counts[annotation] += 1

        if not dry_run:
            conn.execute(
                "UPDATE blind_spots SET annotation = ?, annotation_source = ? "
                "WHERE id = ?",
                (annotation, source, bs_id),
            )

    if not dry_run:
        conn.commit()

    conn.close()

    logger.info(
        "Annotated %d blind spots: %d FALSE_POSITIVE, %d REAL_MISMATCH, %d PLAUSIBLE",
        sum(counts.values()),
        counts["FALSE_POSITIVE"],
        counts["REAL_MISMATCH"],
        counts["PLAUSIBLE"],
    )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Annotate blind spots with heuristic labels.",
    )
    parser.add_argument(
        "--db", type=Path,
        default=BULLA_ROOT / "calibration" / "data" / "index" / "coherence.db",
        help="Path to coherence database",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be annotated without writing",
    )
    args = parser.parse_args()

    if not args.db.exists():
        logger.error("Database not found: %s", args.db)
        sys.exit(1)

    counts = annotate(args.db, dry_run=args.dry_run)

    if args.dry_run:
        logger.info("(dry run — no changes written)")


if __name__ == "__main__":
    main()
