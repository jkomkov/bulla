from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
V03 = ROOT / "bench/golden/v0.3"


def verifier_module():
    path = ROOT / "scripts/verify_golden_v03.py"
    spec = importlib.util.spec_from_file_location("verify_golden_v03_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_f9_and_f10_denominators_and_safety() -> None:
    f9 = json.loads((V03 / "f9-oracle-boundary.json").read_text())
    f10 = json.loads((V03 / "f10-complexity-bombs.json").read_text())
    assert f9["case_count"] == len(f9["cases"]) == 48
    assert f9["by_stratum"] == {
        "AUTHORITY": 12,
        "DERIVATION": 12,
        "GROUNDING": 12,
        "SEMANTIC": 12,
    }
    assert f9["unsafe_count"] == 0
    assert f10["case_count"] == len(f10["cases"]) == 128
    assert f10["by_exit"] == {"COMPILED": 72, "PARTIAL": 56}
    assert f10["unsafe_count"] == 0


def test_every_prior_partial_exit_has_coverage() -> None:
    coverage = json.loads((V03 / "partial-coverage.json").read_text())
    assert coverage["source_partial_denominator"] == len(coverage["cases"]) == 70
    assert 0 < coverage["minimum_joint_coverage"] <= coverage["maximum_joint_coverage"] <= 1
    assert all(0 <= case["joint_certified_coverage"] <= 1 for case in coverage["cases"])
    assert set(coverage["coverage_distribution"]) == {"RELY", "REFUSE", "ESCALATE", "CERTIFIED"}
    assert all(
        set(quantiles) == {"p10", "median", "p90"}
        for quantiles in coverage["coverage_distribution"].values()
    )


def test_definition_is_never_treated_as_observation() -> None:
    scout = json.loads((V03 / "definition-observation-scout.json").read_text())
    assert scout["case_count"] == 24
    assert scout["no_definition_ground_conflation"]
    assert all(
        case["exit"] != "FINALIZE"
        for case in scout["cases"]
        if case["definition_available"] and not case["ground_observed"]
    )


def test_zero_import_verifier_replays_v03() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/verify_golden_v03.py"), str(V03)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "external_replay_status": "BLOCKED_MISSING_EXTERNAL_PARTICIPANTS",
        "f10_cases": 128,
        "f9_cases": 48,
        "ok": True,
        "profile": "bulla.golden-suite/0.3-experimental",
    }


def test_zero_import_verifier_passes_static_audit_and_clean_directory_replay() -> None:
    verifier = ROOT / "scripts/verify_golden_v03.py"
    audit = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_zero_import_verifier.py"), str(verifier)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert audit.returncode == 0, audit.stderr
    replay = subprocess.run(
        [sys.executable, str(ROOT / "scripts/replay_golden_v03_clean.py")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["clean_directory"] is True


def test_zero_import_audit_rejects_production_import_and_path_mutation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import bulla\nimport sys\nsys.path.insert(0, 'x')\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_zero_import_verifier.py"), str(bad)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "production package" in completed.stderr
    assert "sys.path.insert" in completed.stderr


def test_v03_verifier_rejects_unknown_fields() -> None:
    report = json.loads((V03 / "f9-oracle-boundary.json").read_text())
    report["cases"][0]["surprise"] = True
    with pytest.raises(ValueError, match="unknown or missing"):
        verifier_module().verify_f9(report)
