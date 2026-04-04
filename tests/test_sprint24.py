"""Tests for Sprint 24: receipt DAG, vocabulary merge, convergence."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from bulla.merge import OverlapReport, merge_receipt_vocabularies
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    Disposition,
    WitnessBasis,
    WitnessReceipt,
)
from bulla.witness import verify_receipt_integrity, witness


@pytest.fixture(autouse=True)
def clean_taxonomy():
    from bulla.infer.classifier import _reset_taxonomy_cache
    _reset_taxonomy_cache()
    yield
    _reset_taxonomy_cache()


def _make_diagnostic(fee: int = 0):
    from bulla.diagnostic import diagnose
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    comp = Composition(
        name="test",
        tools=(
            ToolSpec("A", ("x",), ("x",)),
            ToolSpec("B", ("x",), ()),
        ),
        edges=(Edge("A", "B", (SemanticDimension("x_match", "x", "x"),)),),
    )
    return diagnose(comp), comp


# ── Phase 1: Receipt DAG ─────────────────────────────────────────────


class TestReceiptDAG:
    def test_single_parent_as_1tuple(self):
        diag, comp = _make_diagnostic()
        receipt = witness(diag, comp, parent_receipt_hash="hash_a")
        assert receipt.parent_receipt_hashes == ("hash_a",)

    def test_dag_parents(self):
        diag, comp = _make_diagnostic()
        receipt = witness(
            diag, comp,
            parent_receipt_hashes=("hash_a", "hash_b"),
        )
        assert receipt.parent_receipt_hashes == ("hash_a", "hash_b")

    def test_no_parent(self):
        diag, comp = _make_diagnostic()
        receipt = witness(diag, comp)
        assert receipt.parent_receipt_hashes is None

    def test_mutual_exclusion(self):
        diag, comp = _make_diagnostic()
        with pytest.raises(ValueError, match="not both"):
            witness(
                diag, comp,
                parent_receipt_hash="x",
                parent_receipt_hashes=("y",),
            )

    def test_1tuple_verifies(self):
        diag, comp = _make_diagnostic()
        receipt = witness(diag, comp, parent_receipt_hash="hash_a")
        d = receipt.to_dict()
        assert "parent_receipt_hashes" in d
        assert d["parent_receipt_hashes"] == ["hash_a"]
        assert verify_receipt_integrity(d)

    def test_dag_verifies(self):
        diag, comp = _make_diagnostic()
        receipt = witness(
            diag, comp,
            parent_receipt_hashes=("hash_a", "hash_b"),
        )
        d = receipt.to_dict()
        assert d["parent_receipt_hashes"] == ["hash_a", "hash_b"]
        assert verify_receipt_integrity(d)

    def test_none_parent_omitted_from_dict(self):
        diag, comp = _make_diagnostic()
        receipt = witness(diag, comp)
        d = receipt.to_dict()
        assert "parent_receipt_hashes" not in d
        assert verify_receipt_integrity(d)

    def test_different_parents_different_hashes(self):
        diag, comp = _make_diagnostic()
        r1 = witness(diag, comp, parent_receipt_hash="a")
        r2 = witness(diag, comp, parent_receipt_hash="b")
        r3 = witness(diag, comp, parent_receipt_hashes=("a", "b"))
        assert r1.receipt_hash != r2.receipt_hash
        assert r1.receipt_hash != r3.receipt_hash

    def test_pre_v024_receipt_still_verifies(self):
        """Pre-v0.24.0 receipt dict with parent_receipt_hash (singular) should
        verify correctly since verify_receipt_integrity is key-name-agnostic."""
        diag, comp = _make_diagnostic()
        receipt = witness(diag, comp)
        d = receipt.to_dict()
        d["parent_receipt_hash"] = "legacy_parent"
        import hashlib
        obj = {k: v for k, v in d.items() if k not in {"receipt_hash", "anchor_ref"}}
        new_hash = hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()
        d["receipt_hash"] = new_hash
        assert verify_receipt_integrity(d)

    def test_precedence_order_preserved(self):
        diag, comp = _make_diagnostic()
        receipt = witness(
            diag, comp,
            parent_receipt_hashes=("first", "second", "third"),
        )
        d = receipt.to_dict()
        assert d["parent_receipt_hashes"] == ["first", "second", "third"]


# ── Phase 2: Vocabulary Merge ────────────────────────────────────────


class TestVocabularyMerge:
    def _make_receipt_dict(self, dims: dict, pack_name: str = "test") -> dict:
        diag, comp = _make_diagnostic()
        inline = {"pack_name": pack_name, "pack_version": "0.1.0", "dimensions": dims}
        receipt = witness(diag, comp, inline_dimensions=inline)
        return receipt.to_dict()

    def test_basic_union(self):
        r1 = self._make_receipt_dict({"dim_a": {"description": "A"}})
        r2 = self._make_receipt_dict({"dim_b": {"description": "B"}})
        merged, overlaps = merge_receipt_vocabularies([r1, r2])
        assert merged is not None
        assert set(merged["dimensions"].keys()) == {"dim_a", "dim_b"}
        assert len(overlaps) == 0

    def test_later_wins_on_collision(self):
        r1 = self._make_receipt_dict({"dim_x": {"description": "from_r1"}})
        r2 = self._make_receipt_dict({"dim_x": {"description": "from_r2"}})
        merged, overlaps = merge_receipt_vocabularies([r1, r2])
        assert merged["dimensions"]["dim_x"]["description"] == "from_r2"
        assert len(overlaps) == 1
        assert overlaps[0].shared_patterns == ("(same name)",)

    def test_no_inline_dimensions(self):
        merged, overlaps = merge_receipt_vocabularies([{}, {}])
        assert merged is None
        assert overlaps == []

    def test_field_patterns_overlap(self):
        r1 = self._make_receipt_dict({
            "pagination_base": {
                "description": "Page numbering",
                "field_patterns": ["*_page", "*_offset"],
            }
        })
        r2 = self._make_receipt_dict({
            "page_index_origin": {
                "description": "Page index",
                "field_patterns": ["*_page", "*_index"],
            }
        })
        merged, overlaps = merge_receipt_vocabularies([r1, r2])
        assert len(merged["dimensions"]) == 2
        assert len(overlaps) == 1
        assert "*_page" in overlaps[0].shared_patterns

    def test_deep_copy_prevents_mutation(self):
        r1 = self._make_receipt_dict({"dim_a": {"description": "A"}})
        r2 = self._make_receipt_dict({"dim_b": {"description": "B"}})
        r1_hash_before = r1.get("receipt_hash")
        merged, _ = merge_receipt_vocabularies([r1, r2])
        assert r1.get("receipt_hash") == r1_hash_before
        assert "dim_b" not in r1.get("inline_dimensions", {}).get("dimensions", {})

    def test_three_way_merge(self):
        r1 = self._make_receipt_dict({"dim_a": {"description": "A"}})
        r2 = self._make_receipt_dict({"dim_b": {"description": "B"}})
        r3 = self._make_receipt_dict({"dim_c": {"description": "C"}})
        merged, overlaps = merge_receipt_vocabularies([r1, r2, r3])
        assert set(merged["dimensions"].keys()) == {"dim_a", "dim_b", "dim_c"}


# ── Phase 3: bulla merge CLI ────────────────────────────────────────


class TestMergeCLI:
    def _write_receipt(self, tmpdir: Path, name: str, dims: dict) -> Path:
        diag, comp = _make_diagnostic()
        inline = {"pack_name": name, "pack_version": "0.1.0", "dimensions": dims}
        receipt = witness(diag, comp, inline_dimensions=inline)
        path = tmpdir / f"{name}.json"
        path.write_text(json.dumps(receipt.to_dict(), indent=2))
        return path

    def test_merge_text_output(self, capsys):
        import subprocess
        tmpdir = Path(tempfile.mkdtemp())
        p1 = self._write_receipt(tmpdir, "agent_a", {"dim_a": {"description": "A", "field_patterns": ["*_a"]}})
        p2 = self._write_receipt(tmpdir, "agent_c", {"dim_c": {"description": "C", "field_patterns": ["*_c"]}})

        from bulla.cli import _cmd_merge
        import argparse
        args = argparse.Namespace(
            receipts=[p1, p2],
            receipt=None,
            format="text",
        )
        _cmd_merge(args)
        captured = capsys.readouterr()
        assert "2 receipts" in captured.out
        assert "2 dimensions merged" in captured.out

        import shutil
        shutil.rmtree(tmpdir)

    def test_merge_with_receipt_output(self):
        tmpdir = Path(tempfile.mkdtemp())
        p1 = self._write_receipt(tmpdir, "agent_a", {"dim_a": {"description": "A"}})
        p2 = self._write_receipt(tmpdir, "agent_c", {"dim_c": {"description": "C"}})
        merged_path = tmpdir / "merged.json"

        from bulla.cli import _cmd_merge
        import argparse
        args = argparse.Namespace(
            receipts=[p1, p2],
            receipt=merged_path,
            format="text",
        )
        _cmd_merge(args)

        assert merged_path.exists()
        merged_dict = json.loads(merged_path.read_text())
        assert "parent_receipt_hashes" in merged_dict
        assert len(merged_dict["parent_receipt_hashes"]) == 2
        assert "inline_dimensions" in merged_dict
        assert set(merged_dict["inline_dimensions"]["dimensions"].keys()) == {"dim_a", "dim_c"}
        assert verify_receipt_integrity(merged_dict)

        import shutil
        shutil.rmtree(tmpdir)


# ── Phase 4: Diamond demo ───────────────────────────────────────────


class TestDiamondDemo:
    def test_diamond_demo_runs_without_error(self):
        from scripts.run_diamond_demo import run_demo
        run_demo(live=False)
