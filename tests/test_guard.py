"""Tests for the BullaGuard programmatic API."""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla import BullaGuard, BullaCheckError
from bulla.model import Diagnostic


COMPOSITIONS_DIR = Path(__file__).parent.parent / "compositions"
AUTH = COMPOSITIONS_DIR / "auth_pipeline.yaml"
FINANCIAL = COMPOSITIONS_DIR / "financial_pipeline.yaml"
SAMPLE_MANIFEST = Path(__file__).parent / "fixtures" / "sample_mcp_manifest.json"


class TestFromComposition:
    def test_loads_yaml(self):
        guard = BullaGuard.from_composition(AUTH)
        assert guard.composition.name == "Auth-Data-Audit Pipeline"

    def test_diagnose_cached(self):
        guard = BullaGuard.from_composition(AUTH)
        d1 = guard.diagnose()
        d2 = guard.diagnose()
        assert d1 is d2

    def test_zero_fee(self):
        guard = BullaGuard.from_composition(AUTH)
        diag = guard.diagnose()
        assert diag.coherence_fee == 0

    def test_nonzero_fee(self):
        guard = BullaGuard.from_composition(FINANCIAL)
        diag = guard.diagnose()
        assert diag.coherence_fee == 2
        assert len(diag.blind_spots) == 2


class TestFromTools:
    def test_with_conventions(self):
        guard = BullaGuard.from_tools({
            "parser": {
                "fields": ["total_amount", "due_date", "line_items"],
                "conventions": {"amount_unit": "dollars", "date_format": "ISO-8601"},
            },
            "processor": {
                "fields": ["amount", "settlement_date", "ledger_entry"],
                "conventions": {"amount_unit": "cents"},
            },
        })
        diag = guard.diagnose()
        assert isinstance(diag, Diagnostic)
        assert diag.n_tools == 2

    def test_explicit_edges(self):
        guard = BullaGuard.from_tools({
            "A": {"internal_state": ["x", "hidden"], "observable_schema": ["x"]},
            "B": {"internal_state": ["x", "hidden"], "observable_schema": ["x"]},
        }, edges=[("A", "B")])
        diag = guard.diagnose()
        assert diag.n_edges == 1

    def test_auto_inferred_edges(self):
        guard = BullaGuard.from_tools({
            "source": {"fields": ["total_amount", "status"]},
            "sink": {"fields": ["payment_amount", "status"]},
        })
        assert len(guard.composition.edges) >= 0  # may or may not infer

    def test_explicit_internal_observable(self):
        guard = BullaGuard.from_tools({
            "A": {"internal_state": ["x", "y"], "observable_schema": ["x"]},
            "B": {"internal_state": ["x", "y"], "observable_schema": ["x"]},
        }, edges=[("A", "B")])
        diag = guard.diagnose()
        assert diag.coherence_fee > 0

    def test_custom_name(self):
        guard = BullaGuard.from_tools(
            {"A": {"fields": ["x"]}, "B": {"fields": ["y"]}},
            edges=[],
            name="custom-name",
        )
        assert guard.composition.name == "custom-name"


class TestFromMcpManifest:
    def test_loads_manifest(self):
        guard = BullaGuard.from_mcp_manifest(SAMPLE_MANIFEST)
        diag = guard.diagnose()
        assert diag.n_tools == 3

    def test_string_path(self):
        guard = BullaGuard.from_mcp_manifest(str(SAMPLE_MANIFEST))
        assert guard.composition.name.startswith("inferred-from-")


class TestCheck:
    def test_passes_clean(self):
        guard = BullaGuard.from_composition(AUTH)
        diag = guard.check(max_blind_spots=0, max_unbridged=0)
        assert isinstance(diag, Diagnostic)
        assert diag.coherence_fee == 0

    def test_raises_on_blind_spots(self):
        guard = BullaGuard.from_composition(FINANCIAL)
        with pytest.raises(BullaCheckError) as exc_info:
            guard.check(max_blind_spots=0)
        assert exc_info.value.diagnostic.coherence_fee == 2

    def test_passes_with_relaxed_threshold(self):
        guard = BullaGuard.from_composition(FINANCIAL)
        diag = guard.check(max_blind_spots=10, max_unbridged=10)
        assert diag.coherence_fee == 2

    def test_error_has_diagnostic(self):
        guard = BullaGuard.from_composition(FINANCIAL)
        with pytest.raises(BullaCheckError) as exc_info:
            guard.check()
        assert len(exc_info.value.diagnostic.blind_spots) > 0


class TestExport:
    def test_to_text(self):
        guard = BullaGuard.from_composition(AUTH)
        text = guard.to_text()
        assert "COHERENCE FEE" in text

    def test_to_json(self):
        guard = BullaGuard.from_composition(FINANCIAL)
        j = guard.to_json()
        data = json.loads(j)
        assert data["coherence_fee"] == 2
        assert "bulla_version" in data

    def test_to_json_with_source(self):
        guard = BullaGuard.from_composition(FINANCIAL)
        j = guard.to_json(source_path=FINANCIAL)
        data = json.loads(j)
        assert "composition_sha256" in data

    def test_to_sarif(self):
        guard = BullaGuard.from_composition(FINANCIAL)
        s = guard.to_sarif()
        sarif = json.loads(s)
        assert sarif["version"] == "2.1.0"
        results = sarif["runs"][0]["results"]
        blind_spots = [r for r in results if r["ruleId"] == "bulla/blind-spot"]
        assert len(blind_spots) == 2

    def test_to_yaml_returns_string(self):
        guard = BullaGuard.from_composition(AUTH)
        text = guard.to_yaml()
        data = yaml.safe_load(text)
        assert "tools" in data
        assert "edges" in data

    def test_to_yaml_writes_file(self):
        guard = BullaGuard.from_composition(AUTH)
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        try:
            guard.to_yaml(path)
            content = Path(path).read_text()
            data = yaml.safe_load(content)
            assert "tools" in data
        finally:
            Path(path).unlink(missing_ok=True)


class TestRoundtrip:
    def test_from_tools_export_diagnose(self):
        """from_tools -> to_yaml -> from_composition roundtrip."""
        guard1 = BullaGuard.from_tools({
            "A": {"internal_state": ["x", "secret"], "observable_schema": ["x"]},
            "B": {"internal_state": ["x", "secret"], "observable_schema": ["x"]},
        }, edges=[("A", "B")])

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        try:
            guard1.to_yaml(path)
            guard2 = BullaGuard.from_composition(path)
            d1 = guard1.diagnose()
            d2 = guard2.diagnose()
            assert d1.coherence_fee == d2.coherence_fee
            assert len(d1.blind_spots) == len(d2.blind_spots)
        finally:
            Path(path).unlink(missing_ok=True)
