#!/usr/bin/env python3
"""Replay Golden v0.3 from a temporary directory containing no Bulla package."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bulla-golden-v03-clean-") as raw:
        clean = Path(raw)
        verifier = clean / "verify_golden_v03.py"
        shutil.copy2(ROOT / "scripts/verify_golden_v03.py", verifier)
        golden = clean / "golden"
        v02 = golden / "v0.2"
        v03 = golden / "v0.3"
        v02.mkdir(parents=True)
        v03.mkdir(parents=True)
        shutil.copy2(ROOT / "bench/golden/v0.2/scaling-report.json", v02 / "scaling-report.json")
        for source in sorted((ROOT / "bench/golden/v0.3").glob("*.json")):
            shutil.copy2(source, v03 / source.name)
        completed = subprocess.run(
            [sys.executable, "-I", str(verifier), str(v03)],
            cwd=clean,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            print(completed.stderr, file=sys.stderr, end="")
            return completed.returncode
        value = json.loads(completed.stdout)
        value["clean_directory"] = True
        print(json.dumps(value, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
