#!/usr/bin/env python3
"""Replay Golden v0.4 in a clean directory under isolated Python."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bulla-golden-v04-clean-") as raw:
        clean = Path(raw)
        verifier = clean / "verify_golden_v04.py"
        shutil.copy2(ROOT / "scripts/verify_golden_v04.py", verifier)
        golden = clean / "golden"
        for version, names in {
            "v0.1": ("manifest.json",),
            "v0.2": ("freeze-manifest.json",),
            "v0.3": (
                "manifest.json", "bulla-semantic-boundary-stack-v0.3.json",
                "partial-coverage.json",
            ),
        }.items():
            target = golden / version
            target.mkdir(parents=True, exist_ok=True)
            for name in names:
                shutil.copy2(ROOT / "bench/golden" / version / name, target / name)
        shutil.copytree(ROOT / "bench/golden/v0.4", golden / "v0.4")
        completed = subprocess.run(
            [sys.executable, "-I", str(verifier), str(golden / "v0.4")],
            cwd=clean, text=True, capture_output=True, check=False,
        )
        if completed.returncode:
            print(completed.stderr, file=sys.stderr, end="")
            return completed.returncode
        value = json.loads(completed.stdout)
        value["clean_directory"] = True
        value["isolated_python"] = True
        print(json.dumps(value, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
