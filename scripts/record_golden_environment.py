#!/usr/bin/env python3
"""Emit one machine-readable Golden portability observation."""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import subprocess
import sys
from pathlib import Path


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (completed.stdout or completed.stderr).strip().splitlines()
    return value[0] if completed.returncode == 0 and value else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("reference", "smtinterpol", "lean"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    args = parser.parse_args()
    observation = {
        "schema_version": "0.2-portability-cell",
        "backend": args.backend,
        "runner": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "locale": locale.setlocale(locale.LC_ALL, None),
            "timezone": os.environ.get("TZ", "runner-default"),
            "java": version(["java", "-version"]),
            "lean": version(["lean", "--version"]),
        },
        "artifacts": {
            str(path): file_hash(path)
            for path in sorted(args.artifact)
            if path.is_file()
        },
        "observed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(observation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(observation, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
