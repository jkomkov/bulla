from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


BULLA_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = BULLA_ROOT / "scripts" / "aggregate_golden_portability.py"


def observation(backend: str, architecture: str = "x86_64") -> dict[str, object]:
    return {
        "schema_version": "0.2-portability-cell",
        "backend": backend,
        "runner": {
            "os": "Linux",
            "os_release": "test",
            "architecture": architecture,
            "python": "3.12.0",
            "implementation": "CPython",
            "locale": "C",
            "timezone": "UTC",
            "java": None,
            "lean": None,
        },
        "artifacts": {},
        "observed": True,
    }


def run_aggregate(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(tmp_path / "input"),
            "--output",
            str(tmp_path / "report.json"),
            *extra,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_aggregate_counts_only_archived_observations(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "reference.json").write_text(json.dumps(observation("reference")))
    (input_dir / "smt.json").write_text(json.dumps(observation("smtinterpol")))
    (input_dir / "lean.json").write_text(json.dumps(observation("lean")))

    result = run_aggregate(
        tmp_path,
        "--expected-reference", "1",
        "--expected-smt", "1",
        "--expected-lean", "1",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "COMPLETE"
    assert report["expected_cell_count"] == report["observed_cell_count"] == 3
    assert all(cell["observation_hash"].startswith("sha256:") for cell in report["cells"])


def test_aggregate_fails_closed_on_missing_cell(tmp_path: Path) -> None:
    (tmp_path / "input").mkdir()
    result = run_aggregate(
        tmp_path,
        "--expected-reference", "1",
        "--expected-smt", "0",
        "--expected-lean", "0",
    )

    assert result.returncode == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "BLOCKED_INCOMPLETE_MATRIX"
    assert report["blockers"][0]["backend"] == "reference"
