"""Tests for Sprint 23: coordination loop (discover, receipt, chain, refines specificity)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.discover.adapter import MockAdapter
from bulla.discover.engine import discover_dimensions
from bulla.guard import BullaGuard
from bulla.infer.classifier import (
    InferredDimension,
    _reset_taxonomy_cache,
    classify_field_by_name,
    configure_packs,
    get_active_pack_refs,
)
from bulla.model import WitnessBasis, WitnessReceipt
from bulla.witness import verify_receipt_integrity, witness


MOCK_RESPONSE_WITH_REFINES = """\
---BEGIN_PACK---
pack_name: "discovered_test"
pack_version: "0.1.0"
dimensions:
  pagination_base:
    description: "Whether page numbering starts at 0 or 1"
    known_values: ["zero_based", "one_based"]
    field_patterns: ["*_page", "*_offset"]
    description_keywords: ["page number"]
    refines: "id_offset"
  entity_namespace:
    description: "Whether entity IDs share a global sequence"
    known_values: ["global_sequence", "per_type_sequence"]
    field_patterns: ["*_number"]
    description_keywords: ["issue number", "pull request number"]
    refines: "id_offset"
---END_PACK---"""

SAMPLE_TOOLS = [
    {"name": "srv__list_items", "description": "List items",
     "inputSchema": {"type": "object", "properties": {
         "page": {"type": "integer", "description": "Page number"},
         "per_page": {"type": "integer", "description": "Results per page"},
         "owner": {"type": "string"},
     }}},
    {"name": "srv__get_item", "description": "Get a specific item",
     "inputSchema": {"type": "object", "properties": {
         "item_number": {"type": "integer", "description": "Item number"},
         "owner": {"type": "string"},
     }}},
]


@pytest.fixture(autouse=True)
def clean_taxonomy():
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


# ── Phase 2a: inline_dimensions backward compatibility ────────────────


class TestInlineDimensionsBackwardCompat:
    """inline_dimensions=None must not change hash of pre-v0.23.0 receipts."""

    def _make_receipt(self, inline_dims=None):
        from bulla.diagnostic import diagnose
        from bulla.model import Composition, Edge, PackRef, SemanticDimension, ToolSpec

        comp = Composition(
            name="test",
            tools=(
                ToolSpec("A", ("x",), ("x",)),
                ToolSpec("B", ("x",), ()),
            ),
            edges=(Edge("A", "B", (SemanticDimension("x_match", "x", "x"),)),),
        )
        diag = diagnose(comp)
        return witness(diag, comp, inline_dimensions=inline_dims)

    def test_none_inline_dims_omitted_from_dict(self):
        receipt = self._make_receipt(inline_dims=None)
        d = receipt.to_dict()
        assert "inline_dimensions" not in d

    def test_non_none_inline_dims_included_in_dict(self):
        dims = {"pack_name": "test", "dimensions": {"foo": {"description": "x"}}}
        receipt = self._make_receipt(inline_dims=dims)
        d = receipt.to_dict()
        assert "inline_dimensions" in d
        assert d["inline_dimensions"] == dims

    def test_pre_v023_receipt_integrity_unchanged(self):
        """A receipt without inline_dimensions must verify correctly."""
        receipt = self._make_receipt(inline_dims=None)
        d = receipt.to_dict()
        assert "inline_dimensions" not in d
        assert verify_receipt_integrity(d)

    def test_receipt_with_inline_dims_verifies(self):
        dims = {"pack_name": "test", "dimensions": {"foo": {"description": "x"}}}
        receipt = self._make_receipt(inline_dims=dims)
        d = receipt.to_dict()
        assert verify_receipt_integrity(d)

    def test_different_inline_dims_produce_different_hashes(self):
        r1 = self._make_receipt(inline_dims=None)
        r2 = self._make_receipt(inline_dims={"dimensions": {"a": {}}})
        assert r1.receipt_hash != r2.receipt_hash


# ── Phase 3: most-specific-dimension-wins deduplication ───────────────


class TestRefinesSpecificity:
    """When child and parent both match, child wins."""

    def _load_micro_pack(self):
        adapter = MockAdapter(MOCK_RESPONSE_WITH_REFINES)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        assert result.valid
        tmpdir = Path(tempfile.mkdtemp())
        pack_path = tmpdir / "test_pack.yaml"
        pack_path.write_text(yaml.dump(result.pack, default_flow_style=False, sort_keys=False))
        configure_packs(extra_paths=[pack_path])
        return tmpdir

    def test_page_matches_child_when_both_loaded(self):
        tmpdir = self._load_micro_pack()
        hit = classify_field_by_name("page")
        assert hit is not None
        assert hit.dimension == "pagination_base", (
            f"Expected pagination_base (child), got {hit.dimension}"
        )
        import shutil
        shutil.rmtree(tmpdir)

    def test_page_matches_parent_when_only_base(self):
        hit = classify_field_by_name("page")
        assert hit is not None
        assert hit.dimension == "id_offset"

    def test_unrelated_dimensions_both_returned(self):
        from bulla.infer.classifier import _deduplicate_by_specificity
        matches = [
            InferredDimension("f", "date_format", "inferred", ("name",)),
            InferredDimension("f", "timezone", "inferred", ("name",)),
        ]
        result = _deduplicate_by_specificity(matches)
        dims = {m.dimension for m in result}
        assert "date_format" in dims
        assert "timezone" in dims


# ── Phase 4: WitnessBasis.discovered ──────────────────────────────────


class TestWitnessBasisDiscovered:
    def test_default_zero(self):
        basis = WitnessBasis(declared=1, inferred=2, unknown=3)
        assert basis.discovered == 0

    def test_discovered_in_to_dict_when_nonzero(self):
        basis = WitnessBasis(declared=1, inferred=2, unknown=3, discovered=5)
        d = basis.to_dict()
        assert d["discovered"] == 5

    def test_discovered_omitted_when_zero(self):
        basis = WitnessBasis(declared=1, inferred=2, unknown=3, discovered=0)
        d = basis.to_dict()
        assert "discovered" not in d

    def test_guard_counts_discovered_dims_with_micro_pack(self):
        """With a micro-pack loaded, discovered count must be positive."""
        adapter = MockAdapter(MOCK_RESPONSE_WITH_REFINES)
        result = discover_dimensions(SAMPLE_TOOLS, adapter=adapter)
        tmpdir = Path(tempfile.mkdtemp())
        pack_path = tmpdir / "test_pack.yaml"
        pack_path.write_text(yaml.dump(result.pack, default_flow_style=False, sort_keys=False))
        configure_packs(extra_paths=[pack_path])

        guard = BullaGuard.from_tools_list(SAMPLE_TOOLS, name="test")
        basis = guard.witness_basis
        assert basis is not None
        assert basis.discovered > 0, (
            f"Expected discovered > 0 with micro-pack loaded, got {basis.discovered}"
        )

        import shutil
        shutil.rmtree(tmpdir)

    def test_guard_discovered_with_default_pack_stack(self):
        """Default pack stack (base + community) produces a valid discovered count."""
        guard = BullaGuard.from_tools_list(SAMPLE_TOOLS, name="test-default-stack")
        basis = guard.witness_basis
        assert basis is not None
        assert basis.discovered >= 0


# ── Phase 0+1+2: audit --discover --receipt --chain integration ───────


class TestAuditDiscoverReceiptChain:
    """End-to-end: discover -> receipt -> chain."""

    def _create_manifest_dir(self) -> Path:
        tmpdir = Path(tempfile.mkdtemp())
        tools_a = [
            {"name": "list_issues", "description": "List issues",
             "inputSchema": {"type": "object", "properties": {
                 "page": {"type": "integer"}, "per_page": {"type": "integer"}}}},
        ]
        tools_b = [
            {"name": "read_file", "description": "Read a file",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}}}},
        ]
        (tmpdir / "github.json").write_text(json.dumps({"tools": tools_a}))
        (tmpdir / "filesystem.json").write_text(json.dumps({"tools": tools_b}))
        return tmpdir

    def test_discover_receipt_chain_loop(self):
        """Full loop: audit --discover --receipt, then audit --chain --receipt."""
        manifests_dir = self._create_manifest_dir()

        # Step 1: Agent A discovers + produces receipt
        all_tools_a, server_names_a = [], []
        for mf in sorted(manifests_dir.glob("*.json")):
            data = json.loads(mf.read_text())
            server = mf.stem
            server_names_a.append(server)
            for t in data.get("tools", data):
                t["name"] = f"{server}__{t.get('name', 'unknown')}"
            all_tools_a.extend(data.get("tools", data))

        adapter_a = MockAdapter(MOCK_RESPONSE_WITH_REFINES)
        disc_a = discover_dimensions(all_tools_a, adapter=adapter_a)
        assert disc_a.valid
        assert disc_a.n_dimensions > 0

        tmpdir_packs = Path(tempfile.mkdtemp())
        pack_a = tmpdir_packs / "pack_a.yaml"
        pack_a.write_text(yaml.dump(disc_a.pack, default_flow_style=False, sort_keys=False))
        configure_packs(extra_paths=[pack_a])

        guard_a = BullaGuard.from_tools_list(all_tools_a, name="agent-a")
        diag_a = guard_a.diagnose()
        from bulla.diagnostic import diagnose
        receipt_a = witness(
            diag_a, guard_a.composition,
            witness_basis=guard_a.witness_basis,
            active_packs=get_active_pack_refs(),
            inline_dimensions=disc_a.pack,
        )
        receipt_a_dict = receipt_a.to_dict()
        assert verify_receipt_integrity(receipt_a_dict)
        assert "inline_dimensions" in receipt_a_dict

        # Step 2: Agent B chains receipt A
        _reset_taxonomy_cache()
        inherited_dims = receipt_a_dict.get("inline_dimensions")
        assert inherited_dims is not None

        inherited_path = tmpdir_packs / "inherited.yaml"
        inherited_path.write_text(yaml.dump(inherited_dims, default_flow_style=False, sort_keys=False))
        configure_packs(extra_paths=[inherited_path])

        guard_b = BullaGuard.from_tools_list(all_tools_a, name="agent-b")
        diag_b = guard_b.diagnose()
        receipt_b = witness(
            diag_b, guard_b.composition,
            witness_basis=guard_b.witness_basis,
            active_packs=get_active_pack_refs(),
            parent_receipt_hash=receipt_a.receipt_hash,
            inline_dimensions=inherited_dims,
        )
        receipt_b_dict = receipt_b.to_dict()

        assert verify_receipt_integrity(receipt_b_dict)
        assert receipt_b.parent_receipt_hashes == (receipt_a.receipt_hash,)
        assert "inline_dimensions" in receipt_b_dict

        import shutil
        shutil.rmtree(tmpdir_packs)
        shutil.rmtree(manifests_dir)


# ── Phase 5: chain demo smoke test ───────────────────────────────────


class TestChainDemo:
    def test_chain_demo_runs_without_error(self):
        from scripts.run_chain_demo import run_demo
        run_demo(live=False)
