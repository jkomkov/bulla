"""Tests for bulla/src/bulla/compute/a3.py (Phase 6 Track A sweep runner).

Mocked-CSV verdict-logic tests. Each §3c branch has a synthetic input
CSV that produces the right verdict. NO real-map integration tests in
pytest — those run via the actual sweep command.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from bulla.compute.a3 import (
    COMPOSITION_SPECS,
    DEFERRED_MAPS,
    ENABLED_MAPS_FOR_SWEEP,
    GATE_4_CONTROL_MAX,
    GATE_4_MAGNITUDE_BAND_MAX,
    GATE_4_MAGNITUDE_BAND_MIN,
    GATE_5_MAX_REL_DISAGREEMENT,
    GATE_6_RHO_CEILING_NULL,
    GATE_6_RHO_FLOOR_PASS,
    LeafResult,
    LeafSpec,
    enumerate_leaves,
    mechanical_verdict,
)


# ── Locked constants sanity ──────────────────────────────────────────


class TestLockedConstants:
    """Constants must mirror pre-reg §3b/§3c gate thresholds."""

    def test_composition_specs_C1_through_C5(self):
        assert set(COMPOSITION_SPECS.keys()) == {"C1", "C2", "C3", "C4", "C5"}
        assert COMPOSITION_SPECS["C1"]["kind"] == "control_cyclic_observable"
        assert COMPOSITION_SPECS["C1"]["expected_dim_h1_max"] == 5
        for cid in ("C2", "C3", "C4", "C5"):
            assert COMPOSITION_SPECS[cid]["kind"] == "cross_model_2cover"

    def test_locked_n_pairs_per_composition(self):
        # Per pre-reg §3b composition table
        assert COMPOSITION_SPECS["C2"]["n_pairs"] == 2
        assert COMPOSITION_SPECS["C3"]["n_pairs"] == 4
        assert COMPOSITION_SPECS["C4"]["n_pairs"] == 10
        assert COMPOSITION_SPECS["C5"]["n_pairs"] == 20

    def test_kill_switch_2_map_default(self):
        # Phase 6 kill-switch: only Procrustes + Neuronpedia for now
        assert ENABLED_MAPS_FOR_SWEEP == ("procrustes", "neuronpedia")
        assert DEFERRED_MAPS == ("crosscoder", "transcoder")

    def test_gate_thresholds_locked(self):
        assert GATE_4_MAGNITUDE_BAND_MIN == 10
        assert GATE_4_MAGNITUDE_BAND_MAX == 1000
        assert GATE_4_CONTROL_MAX == 5
        assert GATE_5_MAX_REL_DISAGREEMENT == 0.20
        assert GATE_6_RHO_FLOOR_PASS == 0.5
        assert GATE_6_RHO_CEILING_NULL == 0.7


# ── enumerate_leaves ─────────────────────────────────────────────────


class TestEnumerateLeaves:
    def test_default_is_2map_10_leaves(self):
        leaves = enumerate_leaves()
        assert len(leaves) == 10  # 5 compositions × 2 maps
        cids = {leaf.composition_id for leaf in leaves}
        mnames = {leaf.map_name for leaf in leaves}
        assert cids == {"C1", "C2", "C3", "C4", "C5"}
        assert mnames == {"procrustes", "neuronpedia"}

    def test_4map_extension_path(self):
        leaves = enumerate_leaves(
            maps=("procrustes", "crosscoder", "transcoder", "neuronpedia"),
        )
        assert len(leaves) == 20  # 5 compositions × 4 maps

    def test_each_leaf_is_unique(self):
        leaves = enumerate_leaves()
        keys = [(leaf.composition_id, leaf.map_name) for leaf in leaves]
        assert len(keys) == len(set(keys))


# ── Mechanical verdict against synthetic CSVs ─────────────────────────


def _make_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """Helper: write a synthetic sweep CSV to tmp."""
    out = tmp_path / "g23_a3_sweep.csv"
    fieldnames = list(rows[0].keys())
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return out


def _row(cid: str, mname: str, dim: int, b0: float = 1.0) -> dict:
    return {
        "composition_id": cid,
        "map_name": mname,
        "dim_h1": dim,
        "n_edges": dim,
        "n_features_a": 4,
        "n_features_b": 4,
        "b0_value": b0,
        "procrustes_loss": 1.0,
        "cocycle_basis_jaccard_with_3bprime": 1.0,
        "pair_count": 4,
        "deviation_note": "",
    }


class TestMechanicalVerdict_2Map:
    """2-map ablation (kill-switch fallback): Gate 5 always deferred."""

    def test_2map_clean_passes_g4_g6_g7_returns_gate5_deferred(self, tmp_path):
        # All gates that CAN be tested pass, but Gate 5 needs 3 maps.
        # Verdict: A3-GATE-5-DEFERRED.
        rows = [
            # C1 control: dim_h1 = 0 ≤ 5 ✓
            _row("C1", "procrustes", 0, b0=0.1),
            _row("C1", "neuronpedia", 0, b0=0.1),
            # C4 in band [10, 1000] for both maps
            _row("C4", "procrustes", 20, b0=2.0),
            _row("C4", "neuronpedia", 20, b0=2.0),
            # C5 in band
            _row("C5", "procrustes", 40, b0=3.0),
            _row("C5", "neuronpedia", 40, b0=3.0),
            # C2, C3 below band (allowed; gate 4 needs ≥1 in band)
            _row("C2", "procrustes", 4, b0=1.0),
            _row("C2", "neuronpedia", 4, b0=1.0),
            _row("C3", "procrustes", 8, b0=1.5),
            _row("C3", "neuronpedia", 8, b0=1.5),
        ]
        csv_path = _make_csv(tmp_path, rows)
        v = mechanical_verdict(csv_path)
        assert v["verdict"] == "A3-GATE-5-DEFERRED"
        assert v["gates"]["gate_4"]["pass"] is True
        assert v["gates"]["gate_5"]["pass"] is None
        assert "deferred_reason" in v["gates"]["gate_5"]["detail"]
        assert v["gates"]["gate_7"]["pass"] is True

    def test_c1_broken_returns_a3_broken_in_2map(self, tmp_path):
        """C1 dim_h1 > 5 short-circuits to A3-BROKEN regardless of map count."""
        rows = [
            _row("C1", "procrustes", 6),  # > 5; broken
            _row("C1", "neuronpedia", 0),
            _row("C2", "procrustes", 4), _row("C2", "neuronpedia", 4),
            _row("C3", "procrustes", 8), _row("C3", "neuronpedia", 8),
            _row("C4", "procrustes", 20), _row("C4", "neuronpedia", 20),
            _row("C5", "procrustes", 40), _row("C5", "neuronpedia", 40),
        ]
        csv_path = _make_csv(tmp_path, rows)
        v = mechanical_verdict(csv_path)
        assert v["verdict"] == "A3-BROKEN"


class TestMechanicalVerdict_FullPath:
    """4-map sweep path (when Crosscoder + Transcoder loaders land).

    Each §3c branch verified by a synthetic CSV that produces it.
    """

    @staticmethod
    def _4map_rows(
        c1_dim: dict, c2_dim: dict, c3_dim: dict, c4_dim: dict, c5_dim: dict,
        b0_per_comp: dict | None = None,
    ) -> list[dict]:
        """Build 20 rows: 5 compositions × 4 maps."""
        b0_per_comp = b0_per_comp or {"C1": 0.1, "C2": 1.0, "C3": 1.5, "C4": 2.0, "C5": 3.0}
        rows = []
        for cid, dims_per_map in [
            ("C1", c1_dim), ("C2", c2_dim), ("C3", c3_dim),
            ("C4", c4_dim), ("C5", c5_dim),
        ]:
            for mname, d in dims_per_map.items():
                rows.append(_row(cid, mname, d, b0=b0_per_comp[cid]))
        return rows

    def test_a3_pass_clean_4_gates(self, tmp_path):
        # All 4 gates pass cleanly: C1=0, C4 in band agreed across all maps,
        # B0 vs dim_h1 correlation low (gate 6 pass).
        # B0 values designed so |ρ| < 0.5 on each map's (B0, dim_h1) pairs:
        # If b0 perfectly correlates with composition (which it does in real
        # sweeps because compositions have different scales), |ρ| approaches
        # 1.0. To produce |ρ| < 0.5, scramble the dim_h1 ordering relative
        # to b0 across maps. Easier: vary dim_h1 by composition but keep
        # b0 tied to a different ordering.
        c1 = {m: 0 for m in ("procrustes", "crosscoder", "transcoder", "neuronpedia")}
        c2 = {m: 30 for m in ("procrustes", "crosscoder", "transcoder", "neuronpedia")}
        c3 = {m: 30 for m in ("procrustes", "crosscoder", "transcoder", "neuronpedia")}
        c4 = {m: 30 for m in ("procrustes", "crosscoder", "transcoder", "neuronpedia")}
        c5 = {m: 30 for m in ("procrustes", "crosscoder", "transcoder", "neuronpedia")}
        # b0 ordering uncorrelated with dim_h1: dim_h1 constant 30, b0 varies
        b0 = {"C1": 5.0, "C2": 1.0, "C3": 4.0, "C4": 2.0, "C5": 3.0}
        rows = self._4map_rows(c1, c2, c3, c4, c5, b0_per_comp=b0)
        csv_path = _make_csv(tmp_path, rows)
        v = mechanical_verdict(csv_path)
        # constant dim_h1=30 across all (C2-C5, map): Gate 5 ✓; Gate 6
        # |ρ|=0 (constant dim_h1 has zero variance with anything); A3-PASS
        assert v["verdict"] == "A3-PASS"

    def test_a3_map_dependent_procrustes_diverges(self, tmp_path):
        # Gate 5 fails on Procrustes only (dim_h1 differs by > 20%);
        # Crosscoder + Transcoder + Neuronpedia agree.
        # Gates 4, 6, 7 ok → A3-MAP-DEPENDENT.
        c1 = {m: 0 for m in ("procrustes", "crosscoder", "transcoder", "neuronpedia")}
        # C4: Procrustes returns wildly different dim_h1 vs the other 3
        c4_in_band = {"procrustes": 100, "crosscoder": 30, "transcoder": 30, "neuronpedia": 30}
        # Same disagreement pattern across all non-C1
        c2 = {"procrustes": 50, "crosscoder": 4, "transcoder": 4, "neuronpedia": 4}
        c3 = {"procrustes": 80, "crosscoder": 8, "transcoder": 8, "neuronpedia": 8}
        c5 = {"procrustes": 200, "crosscoder": 40, "transcoder": 40, "neuronpedia": 40}
        b0 = {"C1": 5.0, "C2": 1.0, "C3": 4.0, "C4": 2.0, "C5": 3.0}
        rows = self._4map_rows(c1, c2, c3, c4_in_band, c5, b0_per_comp=b0)
        csv_path = _make_csv(tmp_path, rows)
        v = mechanical_verdict(csv_path)
        assert v["verdict"] == "A3-MAP-DEPENDENT"

    def test_a3_broken_via_c1_dim_above_5(self, tmp_path):
        # C1 returns dim_h1 = 7 on one map; A3-BROKEN.
        c1 = {"procrustes": 7, "crosscoder": 0, "transcoder": 0, "neuronpedia": 0}
        c2 = {m: 30 for m in ("procrustes", "crosscoder", "transcoder", "neuronpedia")}
        c3 = c4 = c5 = c2.copy()
        rows = self._4map_rows(c1, c2, c3, c4, c5)
        csv_path = _make_csv(tmp_path, rows)
        v = mechanical_verdict(csv_path)
        assert v["verdict"] == "A3-BROKEN"


# ── LeafSpec / LeafResult dataclasses ────────────────────────────────


class TestLeafDataclasses:
    def test_leaf_spec_frozen(self):
        spec = LeafSpec(composition_id="C2", map_name="procrustes")
        with pytest.raises(Exception):
            spec.composition_id = "C3"  # type: ignore[misc]

    def test_leaf_result_frozen(self):
        r = LeafResult(
            composition_id="C2", map_name="procrustes",
            dim_h1=4, n_edges=4, n_features_a=2, n_features_b=2,
            b0_value=1.0, procrustes_loss=0.5,
            cocycle_basis_jaccard_with_3bprime=1.0,
            pair_count=2, deviation_note="",
        )
        with pytest.raises(Exception):
            r.dim_h1 = 99  # type: ignore[misc]

    def test_leaf_result_to_csv_row(self):
        r = LeafResult(
            composition_id="C2", map_name="procrustes",
            dim_h1=4, n_edges=4, n_features_a=2, n_features_b=2,
            b0_value=1.0, procrustes_loss=0.5,
            cocycle_basis_jaccard_with_3bprime=1.0,
            pair_count=2, deviation_note="",
        )
        d = r.to_csv_row()
        assert d["composition_id"] == "C2"
        assert d["dim_h1"] == 4
        assert isinstance(d["dim_h1"], int)


# ── Module-import smoke ──────────────────────────────────────────────


def test_module_imports_without_torch():
    import bulla.compute.a3 as mod
    assert hasattr(mod, "run_leaf")
    assert hasattr(mod, "run_local_sweep")
    assert hasattr(mod, "mechanical_verdict")
    assert hasattr(mod, "enumerate_leaves")
