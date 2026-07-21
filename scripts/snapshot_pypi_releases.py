#!/usr/bin/env python3
"""Write the minimal auditable PyPI release record used by Glyph."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bulla.coverage import fetch_pypi_project


def normalized(document: dict) -> dict:
    releases = {}
    for version, files in sorted((document.get("releases") or {}).items()):
        releases[version] = [
            {
                "filename": item.get("filename"),
                "url": item.get("url"),
                "digests": {"sha256": (item.get("digests") or {}).get("sha256")},
                "upload_time_iso_8601": item.get("upload_time_iso_8601"),
                "yanked": bool(item.get("yanked")),
            }
            for item in files
        ]
    info = document.get("info") or {}
    return {
        "_generated": "bulla/scripts/snapshot_pypi_releases.py",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://pypi.org/pypi/bulla/json",
        "info": {"name": info.get("name", "bulla"), "version": info.get("version")},
        "releases": releases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="bulla")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("releases/pypi-project.json"))
    args = parser.parse_args()
    document = json.loads(args.input.read_text()) if args.input else fetch_pypi_project(args.project)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(normalized(document), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
