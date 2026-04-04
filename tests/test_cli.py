"""Tests for the CLI: check pass/fail, SARIF output structure."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla import __version__

COMPOSITIONS_DIR = Path(__file__).parent.parent / "compositions"
AUTH = COMPOSITIONS_DIR / "auth_pipeline.yaml"
FINANCIAL = COMPOSITIONS_DIR / "financial_pipeline.yaml"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "bulla", *args],
        capture_output=True,
        text=True,
    )


class TestDiagnoseCommand:
    def test_text_output(self):
        r = _run("diagnose", str(AUTH))
        assert r.returncode == 0
        assert "Auth-Data-Audit Pipeline" in r.stdout
        assert "COHERENCE FEE = 0" in r.stdout

    def test_json_output(self):
        r = _run("diagnose", "--format", "json", str(FINANCIAL))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["coherence_fee"] == 2
        assert data["bulla_version"] == __version__
        assert "composition_sha256" in data
        assert "timestamp" in data

    def test_sarif_output(self):
        r = _run("diagnose", "--format", "sarif", str(FINANCIAL))
        assert r.returncode == 0
        sarif = json.loads(r.stdout)
        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"]) == 1
        run = sarif["runs"][0]
        assert run["tool"]["driver"]["name"] == "bulla"
        results = run["results"]
        blind_spots = [r for r in results if r["ruleId"] == "bulla/blind-spot"]
        bridges = [r for r in results if r["ruleId"] == "bulla/bridge-recommendation"]
        assert len(blind_spots) == 2
        assert len(bridges) == 2

    def test_examples_flag(self):
        r = _run("diagnose", "--examples")
        assert r.returncode == 0
        assert "Summary:" in r.stdout
        assert "10 compositions" in r.stdout

    def test_no_files_error(self):
        r = _run("diagnose")
        assert r.returncode != 0


class TestCheckCommand:
    def test_pass_clean_composition(self):
        r = _run("check", str(AUTH))
        assert r.returncode == 0
        assert "PASS" in r.stdout

    def test_fail_blind_spots(self):
        r = _run("check", str(FINANCIAL))
        assert r.returncode == 1
        assert "FAIL" in r.stderr

    def test_relaxed_threshold_passes(self):
        r = _run("check", "--max-blind-spots", "5", "--max-unbridged", "5", str(FINANCIAL))
        assert r.returncode == 0
        assert "PASS" in r.stdout

    def test_check_json_output(self):
        r = _run("check", "--format", "json", str(AUTH))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["passed"] is True
        assert len(data["compositions"]) == 1

    def test_check_sarif_output(self):
        r = _run("check", "--format", "sarif", str(FINANCIAL))
        assert r.returncode == 1
        sarif = json.loads(r.stdout)
        assert sarif["version"] == "2.1.0"


class TestSarifStructure:
    def test_sarif_schema_fields(self):
        r = _run("diagnose", "--format", "sarif", str(FINANCIAL))
        sarif = json.loads(r.stdout)
        assert "$schema" in sarif
        assert sarif["version"] == "2.1.0"
        run = sarif["runs"][0]
        driver = run["tool"]["driver"]
        assert "name" in driver
        assert "version" in driver
        assert "rules" in driver
        assert len(driver["rules"]) == 2
        for rule in driver["rules"]:
            assert "id" in rule
            assert "shortDescription" in rule
        for result in run["results"]:
            assert "ruleId" in result
            assert "level" in result
            assert "message" in result
            assert "locations" in result
            assert len(result["locations"]) > 0
            loc = result["locations"][0]
            assert "physicalLocation" in loc
            assert "artifactLocation" in loc["physicalLocation"]

    def test_sarif_invocation_metadata(self):
        r = _run("diagnose", "--format", "sarif", str(FINANCIAL))
        sarif = json.loads(r.stdout)
        invocations = sarif["runs"][0]["invocations"]
        assert len(invocations) == 1
        inv = invocations[0]
        assert inv["executionSuccessful"] is True
        assert "bulla_version" in inv["properties"]
        assert "timestamp" in inv["properties"]


class TestGaugeCommand:
    MANIFEST = Path(__file__).parent / "fixtures" / "sample_mcp_manifest.json"

    def test_gauge_text_output(self):
        r = _run("gauge", str(self.MANIFEST))
        assert r.returncode == 0
        assert "Coherence fee:" in r.stdout
        assert "Disclosure set" in r.stdout
        assert "Witness basis:" in r.stdout

    def test_gauge_json_output(self):
        r = _run("gauge", "--format", "json", str(self.MANIFEST))
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "disclosure_set" in data
        assert "witness_basis" in data
        assert "coherence_fee" in data
        assert isinstance(data["disclosure_set"], list)

    def test_gauge_threshold_fail(self):
        r = _run("gauge", "--max-fee", "0", str(self.MANIFEST))
        assert r.returncode == 1
        assert "FAIL" in r.stderr

    def test_gauge_threshold_pass(self):
        r = _run("gauge", "--max-fee", "999", str(self.MANIFEST))
        assert r.returncode == 0

    def test_gauge_blind_spots_threshold(self):
        r = _run("gauge", "--max-blind-spots", "0", str(self.MANIFEST))
        assert r.returncode == 1
        assert "FAIL" in r.stderr

    def test_gauge_output_composition_roundtrip(self, tmp_path):
        out_file = tmp_path / "comp.yaml"
        r = _run("gauge", "-o", str(out_file), str(self.MANIFEST))
        assert r.returncode == 0
        assert out_file.exists()
        from bulla.parser import load_composition
        comp = load_composition(out_file)
        assert comp.name == "inferred-from-sample_mcp_manifest"
        assert len(comp.tools) > 0


MANIFESTS_DIR = Path(__file__).parent.parent / "examples" / "real_world_audit" / "manifests"


class TestAuditManifestsFlag:
    def test_audit_manifests_text(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR))
        assert r.returncode == 0
        assert "Coherence fee:" in r.stdout
        assert "Boundary fee:" in r.stdout

    def test_audit_manifests_json(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--format", "json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["coherence_fee"] > 0
        assert "cross_server_decomposition" in data
        assert data["cross_server_decomposition"]["boundary_fee"] > 0

    def test_audit_manifests_sarif(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--format", "sarif")
        assert r.returncode == 0
        sarif = json.loads(r.stdout)
        assert sarif["$schema"].startswith("https://")
        assert len(sarif["runs"]) == 1

    def test_audit_manifests_threshold_fail(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--max-fee", "0")
        assert r.returncode == 1

    def test_audit_manifests_threshold_pass(self):
        r = _run("audit", "--manifests", str(MANIFESTS_DIR), "--max-fee", "999")
        assert r.returncode == 0

    def test_audit_manifests_nonexistent_dir(self):
        r = _run("audit", "--manifests", "/nonexistent")
        assert r.returncode == 1
        assert "not a directory" in r.stderr


class TestVersionFlag:
    def test_version(self):
        r = _run("--version")
        assert r.returncode == 0
        assert __version__ in r.stdout
