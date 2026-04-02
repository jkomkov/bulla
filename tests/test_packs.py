"""Tests for convention pack overlays, pack loading, and pack-aware classification."""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path

import pytest
import yaml

from bulla.infer.classifier import (
    _hash_pack,
    _load_base_pack,
    _merge_packs,
    _reset_taxonomy_cache,
    configure_packs,
    get_active_pack_refs,
    load_pack_stack,
)
from bulla.model import PackRef, WitnessBasis


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset taxonomy/pack caches before and after each test."""
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


@pytest.fixture
def financial_pack_path():
    """Path to the built-in financial pack."""
    import importlib.resources
    pkg = importlib.resources.files("bulla")
    return Path(str(pkg / "packs" / "financial.yaml"))


@pytest.fixture
def tmp_custom_pack(tmp_path):
    """Create a temporary custom pack YAML."""
    pack = {
        "pack_version": "0.1.0",
        "pack_name": "custom_test",
        "dimensions": {
            "custom_dim": {
                "description": "A custom test dimension",
                "known_values": ["alpha", "beta"],
                "field_patterns": ["*_custom"],
                "description_keywords": ["custom thing"],
                "domains": ["test"],
            }
        },
    }
    path = tmp_path / "custom_test.yaml"
    path.write_text(yaml.dump(pack))
    return path


@pytest.fixture
def tmp_colliding_pack(tmp_path):
    """Create a pack that collides with base on date_format."""
    pack = {
        "pack_version": "0.2.0",
        "pack_name": "collider",
        "dimensions": {
            "date_format": {
                "description": "Override date format",
                "known_values": ["custom-date-fmt"],
                "field_patterns": ["*_date"],
                "description_keywords": ["custom date"],
                "domains": ["test"],
            }
        },
    }
    path = tmp_path / "collider.yaml"
    path.write_text(yaml.dump(pack))
    return path


# ── Base pack loading ────────────────────────────────────────────────


class TestBasePackLoading:
    def test_base_pack_loads(self):
        parsed, ref = _load_base_pack()
        assert "dimensions" in parsed
        assert ref.name == "base"
        assert ref.version == "0.1.0"
        assert len(ref.hash) == 64

    def test_base_pack_has_10_dimensions(self):
        parsed, _ = _load_base_pack()
        dims = parsed.get("dimensions", {})
        assert len(dims) == 10

    def test_base_pack_ref_is_frozen(self):
        _, ref = _load_base_pack()
        with pytest.raises(AttributeError):
            ref.name = "mutated"  # type: ignore


# ── Pack hash determinism ────────────────────────────────────────────


class TestPackHash:
    def test_same_content_same_hash(self):
        data = {"pack_name": "test", "dimensions": {"a": {"known_values": ["x"]}}}
        assert _hash_pack(data) == _hash_pack(data)

    def test_different_content_different_hash(self):
        d1 = {"pack_name": "test", "dimensions": {"a": {"known_values": ["x"]}}}
        d2 = {"pack_name": "test", "dimensions": {"a": {"known_values": ["y"]}}}
        assert _hash_pack(d1) != _hash_pack(d2)

    def test_key_order_does_not_affect_hash(self):
        d1 = {"a": 1, "b": 2}
        d2 = {"b": 2, "a": 1}
        assert _hash_pack(d1) == _hash_pack(d2)


# ── Pack stack loading ───────────────────────────────────────────────


class TestPackStack:
    def test_base_only_returns_one_ref(self):
        merged, refs = load_pack_stack()
        assert len(refs) == 1
        assert refs[0].name == "base"
        assert len(merged["dimensions"]) == 10

    def test_financial_overlay_adds_dimensions(self, financial_pack_path):
        merged, refs = load_pack_stack(extra_paths=[financial_pack_path])
        assert len(refs) == 2
        assert refs[0].name == "base"
        assert refs[1].name == "financial"
        assert "day_count_convention" in merged["dimensions"]
        assert "settlement_cycle" in merged["dimensions"]
        assert "fee_basis" in merged["dimensions"]
        assert len(merged["dimensions"]) > 10

    def test_custom_pack_merges(self, tmp_custom_pack):
        merged, refs = load_pack_stack(extra_paths=[tmp_custom_pack])
        assert len(refs) == 2
        assert refs[1].name == "custom_test"
        assert "custom_dim" in merged["dimensions"]

    def test_multiple_overlays(self, financial_pack_path, tmp_custom_pack):
        merged, refs = load_pack_stack(
            extra_paths=[financial_pack_path, tmp_custom_pack]
        )
        assert len(refs) == 3
        assert refs[0].name == "base"
        assert refs[1].name == "financial"
        assert refs[2].name == "custom_test"
        assert "day_count_convention" in merged["dimensions"]
        assert "custom_dim" in merged["dimensions"]


# ── Pack merge semantics ─────────────────────────────────────────────


class TestPackMerge:
    def test_later_pack_overrides_dimension(self, tmp_colliding_pack):
        merged, refs = load_pack_stack(extra_paths=[tmp_colliding_pack])
        date_dim = merged["dimensions"]["date_format"]
        assert "custom-date-fmt" in date_dim["known_values"]

    def test_collision_emits_warning(self, tmp_colliding_pack, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            load_pack_stack(extra_paths=[tmp_colliding_pack])
        assert any("overrides dimension" in r.message for r in caplog.records)
        assert any("date_format" in r.message for r in caplog.records)


# ── Pack precedence order ────────────────────────────────────────────


class TestPackPrecedenceOrder:
    def test_order_is_preserved_in_refs(self, financial_pack_path, tmp_custom_pack):
        _, refs = load_pack_stack(
            extra_paths=[financial_pack_path, tmp_custom_pack]
        )
        names = [r.name for r in refs]
        assert names == ["base", "financial", "custom_test"]

    def test_reversed_order_produces_different_refs(
        self, financial_pack_path, tmp_custom_pack
    ):
        _, refs_a = load_pack_stack(
            extra_paths=[financial_pack_path, tmp_custom_pack]
        )
        _, refs_b = load_pack_stack(
            extra_paths=[tmp_custom_pack, financial_pack_path]
        )
        assert [r.name for r in refs_a] != [r.name for r in refs_b]


# ── configure_packs ──────────────────────────────────────────────────


class TestConfigurePacks:
    def test_configure_returns_refs(self, financial_pack_path):
        refs = configure_packs(extra_paths=[financial_pack_path])
        assert len(refs) == 2
        assert refs[1].name == "financial"

    def test_get_active_pack_refs_after_configure(self, financial_pack_path):
        configure_packs(extra_paths=[financial_pack_path])
        refs = get_active_pack_refs()
        assert len(refs) == 2

    def test_get_active_pack_refs_lazy_loads_base(self):
        refs = get_active_pack_refs()
        assert len(refs) == 1
        assert refs[0].name == "base"


# ── PackRef model ────────────────────────────────────────────────────


class TestPackRefModel:
    def test_to_dict(self):
        ref = PackRef(name="test", version="1.0.0", hash="abc123")
        d = ref.to_dict()
        assert d == {"name": "test", "version": "1.0.0", "hash": "abc123"}

    def test_frozen(self):
        ref = PackRef(name="test", version="1.0.0", hash="abc")
        with pytest.raises(AttributeError):
            ref.name = "mutated"  # type: ignore


# ── WitnessBasis model ───────────────────────────────────────────────


class TestWitnessBasisModel:
    def test_to_dict(self):
        basis = WitnessBasis(declared=5, inferred=3, unknown=2)
        assert basis.to_dict() == {"declared": 5, "inferred": 3, "unknown": 2}

    def test_frozen(self):
        basis = WitnessBasis(declared=1, inferred=0, unknown=0)
        with pytest.raises(AttributeError):
            basis.declared = 10  # type: ignore


# ── Financial pack content ───────────────────────────────────────────


class TestFinancialPackContent:
    def test_has_expected_dimensions(self, financial_pack_path):
        data = yaml.safe_load(financial_pack_path.read_text())
        dims = data["dimensions"]
        assert "day_count_convention" in dims
        assert "settlement_cycle" in dims
        assert "fee_basis" in dims
        assert "rounding_mode" in dims

    def test_has_version_field(self, financial_pack_path):
        data = yaml.safe_load(financial_pack_path.read_text())
        assert data["pack_version"] == "0.1.0"
        assert data["pack_name"] == "financial"
