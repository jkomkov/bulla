"""Golden v0.4 zero-import, determinism, and anti-laundering gates."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "bench/golden/v0.4"
VERIFY = ROOT / "scripts/verify_golden_v04.py"
VERIFY_INTERPRETATION = ROOT / "scripts/verify_golden_v04_interpretation.py"


def run_verify(root: Path = GOLDEN):
    return subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(root)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_frozen_v04_replays_without_production_imports() -> None:
    result = run_verify()
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "cases": 240,
        "classification": "INTERNAL_CAPTIVE",
        "f11": 52,
        "holdout": 48,
        "ok": True,
        "profile": "bulla.golden-suite/0.4-experimental",
    }
    audit = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_zero_import_verifier.py"), str(VERIFY)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert audit.returncode == 0, audit.stderr


def test_clean_directory_python_i_replay() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/replay_golden_v04_clean.py")],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["clean_directory"] is True


def test_suite_generation_is_deterministic() -> None:
    before = {path.name: sha(path) for path in GOLDEN.glob("*.json*")}
    result = subprocess.run(
        [sys.executable, str(GOLDEN / "run_precedent_yield.py")],
        cwd=GOLDEN, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    after = {path.name: sha(path) for path in GOLDEN.glob("*.json*")}
    assert before == after


def test_case_mutation_fails_closed(tmp_path: Path) -> None:
    copied = tmp_path / "golden"
    shutil.copytree(ROOT / "bench/golden", copied)
    cases = copied / "v0.4/cases.jsonl"
    lines = cases.read_text(encoding="utf-8").splitlines()
    case = json.loads(lines[0])
    case["expected_decision"] = "REFUSE" if case["expected_decision"] == "RELY" else "RELY"
    lines[0] = json.dumps(case, sort_keys=True, separators=(",", ":"))
    cases.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = run_verify(copied / "v0.4")
    assert result.returncode == 1
    assert "case hash mismatch" in result.stderr


def test_f11_is_exact_thirteen_by_four_and_never_accepts() -> None:
    report = json.loads((GOLDEN / "f11-laundering.json").read_text(encoding="utf-8"))
    assert report["case_count"] == 52
    assert len({case["archetype"] for case in report["cases"]}) == 13
    assert len({case["variant"] for case in report["cases"]}) == 4
    assert all(not case["accepted"] and case["safe"] for case in report["cases"])


def test_prior_golden_roots_are_bound_not_regenerated() -> None:
    manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    paths = {
        "golden_v0.1_manifest": ROOT / "bench/golden/v0.1/manifest.json",
        "golden_v0.2_freeze": ROOT / "bench/golden/v0.2/freeze-manifest.json",
        "golden_v0.3_manifest": ROOT / "bench/golden/v0.3/manifest.json",
        "semantic_boundary_stack_v0.3": ROOT / "bench/golden/v0.3/bulla-semantic-boundary-stack-v0.3.json",
    }
    for name, path in paths.items():
        assert manifest["preserved_roots"][name] == "sha256:" + sha(path)


def test_interpretation_is_additive_and_preserves_frozen_evidence() -> None:
    interpretation = json.loads(
        (GOLDEN / "interpretation.json").read_text(encoding="utf-8")
    )
    assert interpretation["observation"] == "COMPOUNDING_OBSERVED"
    assert interpretation["author_origin"] == "TEAM_AUTHORED"
    assert interpretation["adjudication_origin"] == "MACHINE_PLANTED"
    assert interpretation["replay"] == "INTERNAL"
    assert interpretation["closure"] == "BOUNDED_EXACT"
    assert interpretation["historical_preregistered_threshold_name"] == (
        "DEMONSTRATED_COMPOUNDING"
    )
    assert interpretation["cases_sha256"] == sha(GOLDEN / "cases.jsonl")
    assert interpretation["bounded_report_sha256"] == sha(
        GOLDEN / "precedent-yield-report.json"
    )
    assert interpretation["manifest_sha256"] == sha(GOLDEN / "manifest.json")
    assert {run["conclusion"] for run in interpretation["ci_observations"]} == {
        "SUCCESS"
    }
    result = subprocess.run(
        [sys.executable, "-I", str(VERIFY_INTERPRETATION), str(GOLDEN)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "ci_observations": 2,
        "evidence_class": "INTERNAL_CAPTIVE",
        "observation": "COMPOUNDING_OBSERVED",
        "ok": True,
    }
    audit = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/audit_zero_import_verifier.py"),
            str(VERIFY_INTERPRETATION),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert audit.returncode == 0, audit.stderr
