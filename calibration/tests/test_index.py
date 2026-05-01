"""Tests for calibration.index: Indexer orchestration."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

BULLA_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BULLA_ROOT / "src"))
sys.path.insert(0, str(BULLA_ROOT))

from calibration.index import MIN_SCHEMA_FIELDS, Indexer, _field_count


def _make_manifest(name: str, n_fields: int) -> dict:
    """Create a minimal manifest with n_fields in a single tool."""
    props = {f"field_{i}": {"type": "string"} for i in range(n_fields)}
    return {
        "_bulla_provenance": {
            "captured_via": "test",
            "server_package": f"test-{name}",
            "capture_date": "2026-01-01T00:00:00Z",
            "bulla_version": "0.33.0",
            "content_hash": f"sha256:test_{name}",
            "category": "test",
        },
        "tools": [
            {
                "name": f"{name}_tool",
                "description": f"Test tool for {name}",
                "inputSchema": {
                    "type": "object",
                    "properties": props,
                },
            }
        ],
    }


class TestRealSchemaFilter:
    def test_server_below_threshold_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            manifests_dir = data_dir / "manifests"
            manifests_dir.mkdir()

            small = _make_manifest("small", n_fields=2)
            (manifests_dir / "small.json").write_text(json.dumps(small))

            big = _make_manifest("big", n_fields=5)
            (manifests_dir / "big.json").write_text(json.dumps(big))

            index_data = {
                "small": {"manifest": "manifests/small.json", "n_tools": 1, "category": "test"},
                "big": {"manifest": "manifests/big.json", "n_tools": 1, "category": "test"},
            }
            (data_dir / "index.json").write_text(json.dumps(index_data))

            indexer = Indexer(data_dir=data_dir)
            real = indexer._real_schema_servers()
            assert "big" in real
            assert "small" not in real

    def test_min_schema_fields_threshold(self):
        assert MIN_SCHEMA_FIELDS == 3


class TestReceiptsRequireCompute:
    def test_receipts_returns_zero_without_compute(self):
        with tempfile.TemporaryDirectory() as td:
            indexer = Indexer(data_dir=Path(td))
            assert indexer.receipts() == 0

    def test_compute_results_initially_empty(self):
        with tempfile.TemporaryDirectory() as td:
            indexer = Indexer(data_dir=Path(td))
            assert indexer._compute_results == []


class TestIndexerCompute:
    def test_compute_with_two_real_servers(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            manifests_dir = data_dir / "manifests"
            manifests_dir.mkdir()

            server_a = _make_manifest("alpha", n_fields=4)
            server_b = _make_manifest("beta", n_fields=4)
            (manifests_dir / "alpha.json").write_text(json.dumps(server_a))
            (manifests_dir / "beta.json").write_text(json.dumps(server_b))

            index_data = {
                "alpha": {"manifest": "manifests/alpha.json", "n_tools": 1, "category": "test"},
                "beta": {"manifest": "manifests/beta.json", "n_tools": 1, "category": "test"},
            }
            (data_dir / "index.json").write_text(json.dumps(index_data))

            indexer = Indexer(data_dir=data_dir)
            computed = indexer.compute()
            assert computed == 1
            assert len(indexer._compute_results) == 1

    def test_compute_with_one_server_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            manifests_dir = data_dir / "manifests"
            manifests_dir.mkdir()

            server_a = _make_manifest("solo", n_fields=4)
            (manifests_dir / "solo.json").write_text(json.dumps(server_a))

            index_data = {
                "solo": {"manifest": "manifests/solo.json", "n_tools": 1, "category": "test"},
            }
            (data_dir / "index.json").write_text(json.dumps(index_data))

            indexer = Indexer(data_dir=data_dir)
            computed = indexer.compute()
            assert computed == 0
