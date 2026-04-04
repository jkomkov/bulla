"""Tests for MCP manifest inference."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import classify_field, classify_fields
from bulla.infer.mcp import infer_from_manifest


FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_MANIFEST = FIXTURES_DIR / "sample_mcp_manifest.json"


class TestClassifier:
    def test_date_fields(self):
        for name in ["created_at", "due_date", "timestamp", "payment_date"]:
            result = classify_field(name)
            assert result is not None, f"{name} should match"
            assert result.dimension == "date_format"

    def test_amount_fields(self):
        for name in ["total_amount", "price", "tax_amount", "fee"]:
            result = classify_field(name)
            assert result is not None, f"{name} should match"
            assert result.dimension == "amount_unit"

    def test_rate_fields(self):
        for name in ["interest_rate", "tax_rate", "confidence"]:
            result = classify_field(name)
            assert result is not None, f"{name} should match"
            assert result.dimension == "rate_scale"

    def test_score_fields(self):
        for name in ["quality_score", "severity_level", "priority"]:
            result = classify_field(name)
            assert result is not None, f"{name} should match"

    def test_id_fields(self):
        for name in ["transaction_id", "log_id", "page_index"]:
            result = classify_field(name)
            assert result is not None, f"{name} should match"
            assert result.dimension == "id_offset"

    def test_unclassified(self):
        for name in ["document_path", "output_format", "status", "currency"]:
            result = classify_field(name)
            assert result is None, f"{name} should not match"

    def test_classify_fields_batch(self):
        fields = ["total_amount", "status", "due_date", "path"]
        results = classify_fields(fields)
        assert len(results) == 3
        dims = {r.dimension for r in results}
        assert "amount_unit" in dims
        assert "date_format" in dims
        assert "path_convention" in dims


class TestInferFromManifest:
    def test_produces_valid_yaml(self):
        result = infer_from_manifest(SAMPLE_MANIFEST)
        lines = [l for l in result.split("\n") if not l.startswith("#")]
        content = "\n".join(lines)
        data = yaml.safe_load(content)
        assert "tools" in data
        assert "edges" in data
        assert len(data["tools"]) == 3

    def test_tools_have_required_fields(self):
        result = infer_from_manifest(SAMPLE_MANIFEST)
        lines = [l for l in result.split("\n") if not l.startswith("#")]
        data = yaml.safe_load("\n".join(lines))
        for name, spec in data["tools"].items():
            assert "internal_state" in spec, f"{name} missing internal_state"
            assert "observable_schema" in spec, f"{name} missing observable_schema"

    def test_edges_reference_valid_tools(self):
        result = infer_from_manifest(SAMPLE_MANIFEST)
        lines = [l for l in result.split("\n") if not l.startswith("#")]
        data = yaml.safe_load("\n".join(lines))
        tool_names = set(data["tools"].keys())
        for edge in data["edges"]:
            assert edge["from"] in tool_names
            assert edge["to"] in tool_names

    def test_review_comments_present(self):
        result = infer_from_manifest(SAMPLE_MANIFEST)
        assert "# REVIEW" in result

    def test_plain_array_format(self):
        """Test with a plain array of tools (no wrapper object)."""
        data = json.loads(SAMPLE_MANIFEST.read_text())
        tools = data["tools"]
        fd, path = tempfile.mkstemp(suffix=".json")
        os.write(fd, json.dumps(tools).encode())
        os.close(fd)
        try:
            result = infer_from_manifest(Path(path))
            assert "tools:" in result
        finally:
            os.unlink(path)


class TestInferCLI:
    def test_infer_to_stdout(self):
        r = subprocess.run(
            [sys.executable, "-m", "bulla", "infer", str(SAMPLE_MANIFEST)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        assert "tools:" in r.stdout
        assert "# REVIEW" in r.stdout

    def test_infer_to_file(self):
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.close(fd)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "bulla", "infer",
                 str(SAMPLE_MANIFEST), "-o", path],
                capture_output=True, text=True,
            )
            assert r.returncode == 0
            content = Path(path).read_text()
            assert "tools:" in content
        finally:
            os.unlink(path)

    def test_infer_missing_file(self):
        r = subprocess.run(
            [sys.executable, "-m", "bulla", "infer", "/nonexistent.json"],
            capture_output=True, text=True,
        )
        assert r.returncode != 0

    def test_roundtrip_infer_diagnose(self):
        """Inferred YAML should be diagnosable."""
        r1 = subprocess.run(
            [sys.executable, "-m", "bulla", "infer", str(SAMPLE_MANIFEST)],
            capture_output=True, text=True,
        )
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, r1.stdout.encode())
        os.close(fd)
        try:
            r2 = subprocess.run(
                [sys.executable, "-m", "bulla", "diagnose", path],
                capture_output=True, text=True,
            )
            assert r2.returncode == 0
            assert "COHERENCE FEE" in r2.stdout
        finally:
            os.unlink(path)
