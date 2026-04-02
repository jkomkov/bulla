"""Mathematical invariant tests.

These tests verify cross-cutting properties that must hold for all
compositions. They are the executable specification: if any invariant
breaks, either the code or the theory is wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bulla.diagnostic import diagnose
from bulla.model import (
    Composition,
    Edge,
    PackRef,
    PolicyProfile,
    SemanticDimension,
    ToolSpec,
    WitnessBasis,
)
from bulla.parser import load_composition
from bulla.witness import (
    verify_receipt_consistency,
    verify_receipt_integrity,
    witness,
)

COMPOSITIONS_DIR = Path(__file__).resolve().parent.parent / "src" / "bulla" / "compositions"

BUNDLED_YAMLS = sorted(COMPOSITIONS_DIR.glob("*.yaml"))


@pytest.fixture(params=BUNDLED_YAMLS, ids=[p.stem for p in BUNDLED_YAMLS])
def comp_diag(request):
    comp = load_composition(path=request.param)
    diag = diagnose(comp)
    return comp, diag


# ── Rank monotonicity ────────────────────────────────────────────────


class TestCoherenceFee:
    def test_nonnegative(self, comp_diag):
        """coherence_fee = h1_obs - h1_full >= 0 (rank monotonicity)."""
        _, diag = comp_diag
        assert diag.coherence_fee >= 0

    def test_fee_decomposition(self, comp_diag):
        """Fee is exactly h1_obs - h1_full."""
        _, diag = comp_diag
        assert diag.coherence_fee == diag.h1_obs - diag.h1_full


# ── Bridging monotonicity ────────────────────────────────────────────


class TestBridgingMonotonicity:
    def test_h1_after_bridge_le_h1_obs(self, comp_diag):
        """Bridging never increases obstruction."""
        _, diag = comp_diag
        assert diag.h1_after_bridge <= diag.h1_obs

    def test_h1_after_bridge_nonneg(self, comp_diag):
        """h1_after_bridge >= 0 (dimensionality)."""
        _, diag = comp_diag
        assert diag.h1_after_bridge >= 0


# ── WitnessBasis / unknown_dimensions consistency ────────────────────


class TestBasisUnknownConsistency:
    def test_basis_overrides_unknown(self):
        """When basis is provided, unknown_dimensions == basis.unknown."""
        comp = Composition(
            name="inv",
            tools=(
                ToolSpec("a", ("x",), ("x",)),
                ToolSpec("b", ("x",), ("x",)),
            ),
            edges=(
                Edge("a", "b", (SemanticDimension("d", "x", "x"),)),
            ),
        )
        diag = diagnose(comp)
        basis = WitnessBasis(declared=1, inferred=0, unknown=7)
        r = witness(diag, comp, unknown_dimensions=0, witness_basis=basis)
        assert r.unknown_dimensions == 7
        assert r.witness_basis.unknown == r.unknown_dimensions

    def test_no_basis_uses_explicit(self):
        """Without basis, explicit unknown_dimensions is used as-is."""
        comp = Composition(
            name="inv",
            tools=(
                ToolSpec("a", ("x",), ("x",)),
                ToolSpec("b", ("x",), ("x",)),
            ),
            edges=(
                Edge("a", "b", (SemanticDimension("d", "x", "x"),)),
            ),
        )
        diag = diagnose(comp)
        r = witness(diag, comp, unknown_dimensions=3)
        assert r.unknown_dimensions == 3
        assert r.witness_basis is None


# ── Verification round-trips ─────────────────────────────────────────


class TestVerificationRoundTrips:
    def test_consistency_roundtrip(self, comp_diag):
        """verify_receipt_consistency passes for any fresh receipt."""
        comp, diag = comp_diag
        r = witness(diag, comp)
        ok, violations = verify_receipt_consistency(r, comp, diag)
        assert ok, f"Violations: {violations}"

    def test_integrity_roundtrip(self, comp_diag):
        """verify_receipt_integrity passes for any fresh receipt dict."""
        comp, diag = comp_diag
        r = witness(diag, comp)
        assert verify_receipt_integrity(r.to_dict())

    def test_tamper_detection(self, comp_diag):
        """Modifying any field in the dict breaks integrity."""
        comp, diag = comp_diag
        r = witness(diag, comp)
        d = r.to_dict()
        d["fee"] = d["fee"] + 999
        assert not verify_receipt_integrity(d)


# ── Receipt hash determinism ─────────────────────────────────────────


class TestReceiptHashDeterminism:
    def test_same_inputs_same_hash(self):
        """Fixed inputs + fixed timestamp => deterministic hash."""
        comp = Composition(
            name="det",
            tools=(
                ToolSpec("a", ("x", "y"), ("x",)),
                ToolSpec("b", ("x", "y"), ("x",)),
            ),
            edges=(
                Edge("a", "b", (SemanticDimension("d", "y", "y"),)),
            ),
        )
        diag = diagnose(comp)
        r1 = witness(diag, comp)
        r2 = witness(diag, comp)
        d1 = r1.to_dict()
        d2 = r2.to_dict()
        d1["timestamp"] = "fixed"
        d2["timestamp"] = "fixed"
        d1.pop("receipt_hash")
        d2.pop("receipt_hash")
        import hashlib, json
        h1 = hashlib.sha256(json.dumps(d1, sort_keys=True).encode()).hexdigest()
        h2 = hashlib.sha256(json.dumps(d2, sort_keys=True).encode()).hexdigest()
        assert h1 == h2


# ── Pack order hash sensitivity ──────────────────────────────────────


class TestPackOrderSensitivity:
    def test_pack_order_changes_receipt_hash(self):
        """Receipts with same packs in different order have different hashes."""
        comp = Composition(
            name="po",
            tools=(
                ToolSpec("a", ("x",), ("x",)),
                ToolSpec("b", ("x",), ("x",)),
            ),
            edges=(
                Edge("a", "b", (SemanticDimension("d", "x", "x"),)),
            ),
        )
        diag = diagnose(comp)
        p1 = PackRef(name="alpha", version="0.1.0", hash="aaa")
        p2 = PackRef(name="beta", version="0.1.0", hash="bbb")

        r_ab = witness(diag, comp, active_packs=(p1, p2))
        r_ba = witness(diag, comp, active_packs=(p2, p1))

        d_ab = r_ab.to_dict()
        d_ba = r_ba.to_dict()
        d_ab["timestamp"] = d_ba["timestamp"] = "fixed"

        import hashlib, json
        h_ab = hashlib.sha256(json.dumps({k: v for k, v in d_ab.items() if k != "receipt_hash"}, sort_keys=True).encode()).hexdigest()
        h_ba = hashlib.sha256(json.dumps({k: v for k, v in d_ba.items() if k != "receipt_hash"}, sort_keys=True).encode()).hexdigest()
        assert h_ab != h_ba
