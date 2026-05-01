"""Tests for witness kernel: constitutional objects, receipts, disposition."""

from __future__ import annotations

import json

import pytest

from bulla.model import (
    BlindSpot,
    Bridge,
    BridgePatch,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    PackRef,
    PolicyProfile,
    WitnessBasis,
    WitnessReceipt,
)
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness import (
    RECEIPT_VERSION,
    _diagnostic_to_patches,
    _resolve_disposition,
    witness,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _make_diagnostic(
    fee: int = 0,
    blind_spots: list[BlindSpot] | None = None,
    bridges: list[Bridge] | None = None,
    n_unbridged: int = 0,
) -> Diagnostic:
    return Diagnostic(
        name="test-composition",
        n_tools=3,
        n_edges=2,
        betti_1=0,
        dim_c0_obs=6,
        dim_c0_full=8,
        dim_c1=4,
        rank_obs=4,
        rank_full=4,
        h1_obs=0,
        h1_full=0,
        coherence_fee=fee,
        blind_spots=blind_spots or [],
        bridges=bridges or [],
        h1_after_bridge=0,
        n_unbridged=n_unbridged,
    )


SAMPLE_BLIND_SPOT = BlindSpot(
    dimension="amount_unit",
    edge="parser → settlement",
    from_field="amount_scale",
    to_field="amount_unit",
    from_hidden=True,
    to_hidden=False,
)

SAMPLE_BRIDGE = Bridge(
    field="amount_scale",
    add_to=["parser"],
    eliminates="amount_unit",
)

SAMPLE_COMPOSITION = Composition(
    name="test-composition",
    tools=(
        ToolSpec("a", ("x", "y"), ("x",)),
        ToolSpec("b", ("x", "z"), ("x",)),
    ),
    edges=(
        Edge("a", "b", (SemanticDimension("d", "y", "z"),)),
    ),
)


# ── Disposition ──────────────────────────────────────────────────────


class TestDisposition:
    def test_proceed_when_clean(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        assert _resolve_disposition(diag) == Disposition.PROCEED

    def test_proceed_with_receipt_when_fee_only(self):
        diag = _make_diagnostic(fee=2, n_unbridged=0)
        assert _resolve_disposition(diag) == Disposition.PROCEED_WITH_RECEIPT

    def test_proceed_with_bridge_when_unbridged_no_fee(self):
        diag = _make_diagnostic(
            fee=0,
            blind_spots=[SAMPLE_BLIND_SPOT],
            bridges=[SAMPLE_BRIDGE],
            n_unbridged=1,
        )
        assert _resolve_disposition(diag) == Disposition.PROCEED_WITH_BRIDGE

    def test_refuse_when_both_unbridged_and_fee(self):
        diag = _make_diagnostic(
            fee=1,
            blind_spots=[SAMPLE_BLIND_SPOT],
            bridges=[SAMPLE_BRIDGE],
            n_unbridged=1,
        )
        assert _resolve_disposition(diag) == Disposition.REFUSE_PENDING_DISCLOSURE

    def test_enum_values(self):
        assert Disposition.PROCEED.value == "proceed"
        assert Disposition.REFUSE_PENDING_HUMAN_REVIEW.value == "refuse_pending_human_review"


# ── BridgePatch ──────────────────────────────────────────────────────


class TestBridgePatch:
    def test_to_bulla_patch(self):
        patch = BridgePatch(
            target_tool="parser",
            dimension="amount_unit",
            field="amount_scale",
            action="expose",
            eliminates_blind_spot="amount_unit",
            expected_fee_delta=0,
        )
        result = patch.to_bulla_patch()
        assert result["bulla_patch_version"] == "0.1.0"
        assert result["action"] == "expose"
        assert result["target_tool"] == "parser"
        assert result["field"] == "amount_scale"
        assert result["path"] == "/observable_schema/amount_scale"
        assert result["dimension"] == "amount_unit"

    def test_frozen(self):
        patch = BridgePatch(
            target_tool="a", dimension="b", field="c",
            action="expose", eliminates_blind_spot="d",
            expected_fee_delta=0,
        )
        with pytest.raises(AttributeError):
            patch.target_tool = "x"  # type: ignore


# ── WitnessReceipt ───────────────────────────────────────────────────


class TestWitnessReceipt:
    def test_receipt_hash_deterministic(self):
        r1 = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc123",
            diagnostic_hash="def456",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
        )
        r2 = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc123",
            diagnostic_hash="def456",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
        )
        assert r1.receipt_hash == r2.receipt_hash

    def test_receipt_hash_changes_with_content(self):
        base = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc123",
            diagnostic_hash="def456",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
        )
        modified = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc123",
            diagnostic_hash="def456",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=1,  # different fee
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
        )
        assert base.receipt_hash != modified.receipt_hash

    def test_anchor_ref_excluded_from_hash(self):
        r1 = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc",
            diagnostic_hash="def",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
            anchor_ref=None,
        )
        r2 = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc",
            diagnostic_hash="def",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
            anchor_ref="ots:abc123",
        )
        assert r1.receipt_hash == r2.receipt_hash

    def test_to_dict_includes_all_fields(self):
        receipt = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc",
            diagnostic_hash="def",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
        )
        d = receipt.to_dict()
        assert d["receipt_version"] == "0.1.0"
        assert d["disposition"] == "proceed"
        assert "receipt_hash" in d
        assert d["patches"] == []

    def test_to_dict_is_json_serializable(self):
        receipt = WitnessReceipt(
            receipt_version="0.1.0",
            kernel_version="0.5.0",
            composition_hash="abc",
            diagnostic_hash="def",
            policy_profile=DEFAULT_POLICY_PROFILE,
            fee=0,
            blind_spots_count=0,
            bridges_required=0,
            unknown_dimensions=0,
            disposition=Disposition.PROCEED,
            timestamp="2026-03-30T00:00:00+00:00",
        )
        # Must not raise
        json.dumps(receipt.to_dict())


# ── Diagnostic.content_hash ──────────────────────────────────────────


class TestDiagnosticContentHash:
    def test_deterministic(self):
        d1 = _make_diagnostic(fee=0)
        d2 = _make_diagnostic(fee=0)
        assert d1.content_hash() == d2.content_hash()

    def test_changes_with_fee(self):
        d1 = _make_diagnostic(fee=0)
        d2 = _make_diagnostic(fee=1)
        assert d1.content_hash() != d2.content_hash()

    def test_includes_blind_spots(self):
        d1 = _make_diagnostic(fee=0)
        d2 = _make_diagnostic(fee=0, blind_spots=[SAMPLE_BLIND_SPOT])
        assert d1.content_hash() != d2.content_hash()


# ── witness() integration ────────────────────────────────────────────


class TestWitnessFunction:
    def test_clean_composition(self):
        diag = _make_diagnostic(fee=0)
        receipt = witness(diag, SAMPLE_COMPOSITION)
        assert receipt.disposition == Disposition.PROCEED
        assert receipt.fee == 0
        assert receipt.blind_spots_count == 0
        assert receipt.patches == ()
        assert receipt.composition_hash == SAMPLE_COMPOSITION.canonical_hash()
        assert receipt.receipt_version == RECEIPT_VERSION

    def test_composition_with_blind_spots(self):
        diag = _make_diagnostic(
            fee=0,
            blind_spots=[SAMPLE_BLIND_SPOT],
            bridges=[SAMPLE_BRIDGE],
            n_unbridged=1,
        )
        receipt = witness(diag, SAMPLE_COMPOSITION)
        assert receipt.disposition == Disposition.PROCEED_WITH_BRIDGE
        assert receipt.blind_spots_count == 1
        assert receipt.bridges_required == 1
        assert len(receipt.patches) == 1
        assert receipt.patches[0].target_tool == "parser"
        assert receipt.patches[0].field == "amount_scale"

    def test_receipt_hash_is_sha256(self):
        diag = _make_diagnostic(fee=0)
        receipt = witness(diag, SAMPLE_COMPOSITION)
        assert len(receipt.receipt_hash) == 64  # SHA-256 hex

    def test_diagnostic_to_patches(self):
        diag = _make_diagnostic(
            bridges=[
                Bridge(field="f1", add_to=["tool_a", "tool_b"], eliminates="dim1"),
                Bridge(field="f2", add_to=["tool_c"], eliminates="dim2"),
            ]
        )
        patches = _diagnostic_to_patches(diag)
        assert len(patches) == 3  # 2 from first bridge + 1 from second
        assert patches[0].target_tool == "tool_a"
        assert patches[1].target_tool == "tool_b"
        assert patches[2].target_tool == "tool_c"


# ── Canonical composition hash ───────────────────────────────────────


class TestCanonicalHash:
    def test_deterministic(self):
        assert SAMPLE_COMPOSITION.canonical_hash() == SAMPLE_COMPOSITION.canonical_hash()

    def test_different_composition(self):
        other = Composition(
            name="other",
            tools=(ToolSpec("z", ("a",), ("a",)),),
            edges=(),
        )
        assert SAMPLE_COMPOSITION.canonical_hash() != other.canonical_hash()

    def test_is_sha256(self):
        assert len(SAMPLE_COMPOSITION.canonical_hash()) == 64

    def test_formatting_independent(self):
        """Two compositions built from different YAML but same structure
        must produce the same canonical hash."""
        from bulla.parser import load_composition

        yaml_v1 = (
            "name: x\n"
            "tools:\n"
            "  a:\n"
            "    internal_state: [p, q]\n"
            "    observable_schema: [p]\n"
            "  b:\n"
            "    internal_state: [p, r]\n"
            "    observable_schema: [p]\n"
            "edges:\n"
            "  - from: a\n"
            "    to: b\n"
            "    dimensions:\n"
            "      - name: d\n"
            "        from_field: q\n"
            "        to_field: r\n"
        )
        # Same structure, different key order and extra whitespace
        yaml_v2 = (
            "name:   x\n"
            "edges:\n"
            "  - to: b\n"
            "    from: a\n"
            "    dimensions:\n"
            "      - from_field: q\n"
            "        to_field: r\n"
            "        name: d\n"
            "tools:\n"
            "  b:\n"
            "    observable_schema: [p]\n"
            "    internal_state: [p, r]\n"
            "  a:\n"
            "    observable_schema: [p]\n"
            "    internal_state: [p, q]\n"
        )
        c1 = load_composition(text=yaml_v1)
        c2 = load_composition(text=yaml_v2)
        assert c1.canonical_hash() == c2.canonical_hash()


# ── v0.8: Disposition with max_unknown ───────────────────────────────


class TestDispositionMaxUnknown:
    def test_over_unknown_causes_refusal(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(name="strict", max_unknown=0)
        assert _resolve_disposition(diag, policy, unknown_dimensions=1) == (
            Disposition.REFUSE_PENDING_DISCLOSURE
        )

    def test_at_unknown_limit_is_ok(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(name="strict", max_unknown=2)
        assert _resolve_disposition(diag, policy, unknown_dimensions=2) == (
            Disposition.PROCEED
        )

    def test_unlimited_unknown_default(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(name="default", max_unknown=-1)
        assert _resolve_disposition(diag, policy, unknown_dimensions=999) == (
            Disposition.PROCEED
        )


# ── v0.8: Parent receipt hash ────────────────────────────────────────


class TestParentReceiptHash:
    def test_default_is_none(self):
        diag = _make_diagnostic(fee=0)
        receipt = witness(diag, SAMPLE_COMPOSITION)
        assert receipt.parent_receipt_hashes is None

    def test_single_parent_convenience(self):
        diag = _make_diagnostic(fee=0)
        receipt = witness(
            diag, SAMPLE_COMPOSITION, parent_receipt_hash="parent_abc"
        )
        assert receipt.parent_receipt_hashes == ("parent_abc",)
        d = receipt.to_dict()
        assert d["parent_receipt_hashes"] == ["parent_abc"]

    def test_dag_parents(self):
        diag = _make_diagnostic(fee=0)
        receipt = witness(
            diag, SAMPLE_COMPOSITION,
            parent_receipt_hashes=("hash_a", "hash_b"),
        )
        assert receipt.parent_receipt_hashes == ("hash_a", "hash_b")
        d = receipt.to_dict()
        assert d["parent_receipt_hashes"] == ["hash_a", "hash_b"]

    def test_mutual_exclusion(self):
        import pytest
        diag = _make_diagnostic(fee=0)
        with pytest.raises(ValueError, match="not both"):
            witness(
                diag, SAMPLE_COMPOSITION,
                parent_receipt_hash="x",
                parent_receipt_hashes=("y",),
            )

    def test_affects_receipt_hash(self):
        diag = _make_diagnostic(fee=0)
        r1 = witness(diag, SAMPLE_COMPOSITION, parent_receipt_hash=None)
        r2 = witness(diag, SAMPLE_COMPOSITION, parent_receipt_hash="abc123")
        assert r1.receipt_hash != r2.receipt_hash


# ── v0.8: WitnessBasis parameter ─────────────────────────────────────


class TestWitnessBasisParam:
    def test_default_is_none(self):
        diag = _make_diagnostic(fee=0)
        receipt = witness(diag, SAMPLE_COMPOSITION)
        assert receipt.witness_basis is None
        assert receipt.to_dict()["witness_basis"] is None

    def test_passed_through(self):
        diag = _make_diagnostic(fee=0)
        basis = WitnessBasis(declared=5, inferred=3, unknown=2)
        receipt = witness(diag, SAMPLE_COMPOSITION, witness_basis=basis)
        assert receipt.witness_basis is basis
        assert receipt.to_dict()["witness_basis"] == {
            "declared": 5, "inferred": 3, "unknown": 2
        }

    def test_affects_receipt_hash(self):
        diag = _make_diagnostic(fee=0)
        r1 = witness(diag, SAMPLE_COMPOSITION, witness_basis=None)
        basis = WitnessBasis(declared=1, inferred=0, unknown=0)
        r2 = witness(diag, SAMPLE_COMPOSITION, witness_basis=basis)
        assert r1.receipt_hash != r2.receipt_hash


# ── v0.8: Active packs parameter ─────────────────────────────────────


class TestActivePacksParam:
    def test_default_is_empty(self):
        diag = _make_diagnostic(fee=0)
        receipt = witness(diag, SAMPLE_COMPOSITION)
        assert receipt.active_packs == ()
        assert receipt.to_dict()["active_packs"] == []

    def test_passed_through(self):
        diag = _make_diagnostic(fee=0)
        packs = (
            PackRef(name="base", version="0.1.0", hash="abc"),
            PackRef(name="financial", version="0.1.0", hash="def"),
        )
        receipt = witness(diag, SAMPLE_COMPOSITION, active_packs=packs)
        assert len(receipt.active_packs) == 2
        d = receipt.to_dict()
        assert len(d["active_packs"]) == 2
        assert d["active_packs"][0]["name"] == "base"
        assert d["active_packs"][1]["name"] == "financial"

    def test_order_affects_receipt_hash(self):
        diag = _make_diagnostic(fee=0)
        packs_ab = (
            PackRef(name="a", version="0.1.0", hash="aaa"),
            PackRef(name="b", version="0.1.0", hash="bbb"),
        )
        packs_ba = (
            PackRef(name="b", version="0.1.0", hash="bbb"),
            PackRef(name="a", version="0.1.0", hash="aaa"),
        )
        r1 = witness(diag, SAMPLE_COMPOSITION, active_packs=packs_ab)
        r2 = witness(diag, SAMPLE_COMPOSITION, active_packs=packs_ba)
        assert r1.receipt_hash != r2.receipt_hash

    def test_json_serializable(self):
        diag = _make_diagnostic(fee=0)
        basis = WitnessBasis(declared=1, inferred=1, unknown=0)
        packs = (PackRef(name="base", version="0.1.0", hash="abc"),)
        receipt = witness(
            diag,
            SAMPLE_COMPOSITION,
            witness_basis=basis,
            active_packs=packs,
            parent_receipt_hash="parent123",
        )
        serialized = json.dumps(receipt.to_dict())
        assert "parent123" in serialized
        assert "base" in serialized


# ── Verification ────────────────────────────────────────────────────


class TestVerifyReceiptConsistency:
    def test_fresh_receipt_is_consistent(self):
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_consistency

        diag = _diag(SAMPLE_COMPOSITION)
        r = witness(diag, SAMPLE_COMPOSITION)
        ok, vs = verify_receipt_consistency(r, SAMPLE_COMPOSITION, diag)
        assert ok
        assert vs == []

    def test_wrong_composition_detected(self):
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_consistency

        diag = _diag(SAMPLE_COMPOSITION)
        r = witness(diag, SAMPLE_COMPOSITION)
        other = Composition(
            name="other",
            tools=(ToolSpec("z", ("a",), ("a",)),),
            edges=(),
        )
        ok, vs = verify_receipt_consistency(r, other, diag)
        assert not ok
        assert "composition_hash mismatch" in vs

    def test_basis_unknown_consistency_enforced(self):
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_consistency

        diag = _diag(SAMPLE_COMPOSITION)
        basis = WitnessBasis(declared=1, inferred=0, unknown=3)
        r = witness(diag, SAMPLE_COMPOSITION, witness_basis=basis)
        assert r.unknown_dimensions == 3
        ok, vs = verify_receipt_consistency(r, SAMPLE_COMPOSITION, diag)
        assert ok


class TestVerifyReceiptIntegrity:
    def test_valid_dict(self):
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_integrity

        diag = _diag(SAMPLE_COMPOSITION)
        r = witness(diag, SAMPLE_COMPOSITION)
        assert verify_receipt_integrity(r.to_dict())

    def test_tampered_fee(self):
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_integrity

        diag = _diag(SAMPLE_COMPOSITION)
        r = witness(diag, SAMPLE_COMPOSITION)
        d = r.to_dict()
        d["fee"] = 9999
        assert not verify_receipt_integrity(d)

    def test_tampered_disposition(self):
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_integrity

        diag = _diag(SAMPLE_COMPOSITION)
        r = witness(diag, SAMPLE_COMPOSITION)
        d = r.to_dict()
        d["disposition"] = "proceed"
        if r.disposition.value != "proceed":
            assert not verify_receipt_integrity(d)

    def test_missing_hash_returns_false(self):
        from bulla.witness import verify_receipt_integrity

        assert not verify_receipt_integrity({})

    def test_unknown_field_breaks_integrity(self):
        """Adding an unknown key to the dict changes the hash,
        proving the verifier isn't ignoring unknown fields."""
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_integrity

        diag = _diag(SAMPLE_COMPOSITION)
        r = witness(diag, SAMPLE_COMPOSITION)
        d = r.to_dict()
        assert verify_receipt_integrity(d)
        d["injected_field"] = "malicious"
        assert not verify_receipt_integrity(d)


# ── 2D disposition (four-quadrant model) ─────────────────────────────


class Test2DDisposition:
    """The disposition should reason over fee and contradiction_score
    as independent axes, not flatten them via addition."""

    # Quadrant 1: fee=0, contradictions=0 -> PROCEED
    def test_quadrant_clean(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        assert _resolve_disposition(
            diag, structural_contradiction_score=0,
        ) == Disposition.PROCEED

    # Quadrant 2: fee>0, contradictions=0 -> opacity only (existing behavior)
    def test_quadrant_opacity_only_receipt(self):
        diag = _make_diagnostic(fee=2, n_unbridged=0)
        assert _resolve_disposition(
            diag, structural_contradiction_score=0,
        ) == Disposition.PROCEED_WITH_RECEIPT

    def test_quadrant_opacity_only_refuse(self):
        diag = _make_diagnostic(
            fee=2,
            blind_spots=[SAMPLE_BLIND_SPOT],
            bridges=[SAMPLE_BRIDGE],
            n_unbridged=1,
        )
        assert _resolve_disposition(
            diag, structural_contradiction_score=0,
        ) == Disposition.REFUSE_PENDING_DISCLOSURE

    # Quadrant 3: fee=0, contradictions>0 -> PROCEED_WITH_CAUTION
    def test_quadrant_incompatibility_only(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        assert _resolve_disposition(
            diag, structural_contradiction_score=5,
        ) == Disposition.PROCEED_WITH_CAUTION

    def test_quadrant_incompatibility_only_score_1(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        assert _resolve_disposition(
            diag, structural_contradiction_score=1,
        ) == Disposition.PROCEED_WITH_CAUTION

    # Quadrant 4: fee>0 + blind_spots, contradictions>0 -> REFUSE
    def test_quadrant_both_axes_hot(self):
        diag = _make_diagnostic(
            fee=2,
            blind_spots=[SAMPLE_BLIND_SPOT],
            bridges=[SAMPLE_BRIDGE],
            n_unbridged=1,
        )
        assert _resolve_disposition(
            diag, structural_contradiction_score=3,
        ) == Disposition.REFUSE_PENDING_DISCLOSURE

    # Policy: max_structural_contradictions threshold
    def test_structural_threshold_refuse(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(
            name="strict", max_structural_contradictions=0, require_bridge=False,
        )
        assert _resolve_disposition(
            diag, policy, structural_contradiction_score=1,
        ) == Disposition.REFUSE_PENDING_DISCLOSURE

    def test_structural_threshold_at_limit_ok(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(
            name="tolerant", max_structural_contradictions=5, require_bridge=False,
        )
        assert _resolve_disposition(
            diag, policy, structural_contradiction_score=5,
        ) == Disposition.PROCEED_WITH_CAUTION

    def test_structural_threshold_over_limit_refuse(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(
            name="strict", max_structural_contradictions=2, require_bridge=False,
        )
        assert _resolve_disposition(
            diag, policy, structural_contradiction_score=3,
        ) == Disposition.REFUSE_PENDING_DISCLOSURE

    # Backward compat: default policy (-1) disables structural threshold
    def test_default_policy_structural_caution_not_refuse(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        assert _resolve_disposition(
            diag, structural_contradiction_score=999,
        ) == Disposition.PROCEED_WITH_CAUTION

    # Convention contradictions are still independent
    def test_convention_contradictions_independent(self):
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(
            name="strict", max_contradictions=0, require_bridge=False,
        )
        assert _resolve_disposition(
            diag, policy,
            contradiction_count=1,
            structural_contradiction_score=0,
        ) == Disposition.REFUSE_PENDING_DISCLOSURE

    def test_axes_not_summed(self):
        """Convention=1 and structural=1 should NOT sum to 2 for threshold checks."""
        diag = _make_diagnostic(fee=0, n_unbridged=0)
        policy = PolicyProfile(
            name="test",
            max_contradictions=1,
            max_structural_contradictions=1,
            require_bridge=False,
        )
        result = _resolve_disposition(
            diag, policy,
            contradiction_count=1,
            structural_contradiction_score=1,
        )
        assert result == Disposition.PROCEED_WITH_CAUTION

    # Enum values
    def test_proceed_with_caution_value(self):
        assert Disposition.PROCEED_WITH_CAUTION.value == "proceed_with_caution"


class Test2DDispositionReceipts:
    """Receipts with PROCEED_WITH_CAUTION verify correctly."""

    def test_receipt_with_caution_integrity(self):
        from bulla.witness import verify_receipt_integrity

        diag = _make_diagnostic(fee=0, n_unbridged=0)
        receipt = witness(
            diag, SAMPLE_COMPOSITION,
            contradiction_score=3,
        )
        assert receipt.disposition == Disposition.PROCEED_WITH_CAUTION
        assert receipt.contradiction_score == 3
        d = receipt.to_dict()
        assert verify_receipt_integrity(d)

    def test_receipt_consistency_caution(self):
        from bulla.diagnostic import diagnose as _diag
        from bulla.witness import verify_receipt_consistency

        diag = _diag(SAMPLE_COMPOSITION)
        receipt = witness(
            diag, SAMPLE_COMPOSITION,
            contradiction_score=5,
        )
        ok, violations = verify_receipt_consistency(
            receipt, SAMPLE_COMPOSITION, diag,
        )
        assert ok, f"Violations: {violations}"

    def test_receipt_consistency_both_axes(self):
        from bulla.witness import verify_receipt_consistency
        from bulla.model import ContradictionReport, ContradictionSeverity

        diag = _make_diagnostic(
            fee=2,
            blind_spots=[SAMPLE_BLIND_SPOT],
            bridges=[SAMPLE_BRIDGE],
            n_unbridged=1,
        )
        contradictions = (
            ContradictionReport(
                dimension="encoding",
                values=("utf-8", "ascii"),
                sources=("a", "b"),
                severity=ContradictionSeverity.MISMATCH,
            ),
        )
        receipt = witness(
            diag, SAMPLE_COMPOSITION,
            contradictions=contradictions,
            contradiction_score=3,
        )
        assert receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE
        ok, violations = verify_receipt_consistency(
            receipt, SAMPLE_COMPOSITION, diag,
        )
        assert ok, f"Violations: {violations}"

    def test_policy_profile_serializes_structural_threshold(self):
        p = PolicyProfile(
            name="strict", max_structural_contradictions=2,
        )
        d = p.to_dict()
        assert d["max_structural_contradictions"] == 2
        p2 = PolicyProfile(**d)
        assert p2.max_structural_contradictions == 2

    def test_default_policy_backward_compat(self):
        d = DEFAULT_POLICY_PROFILE.to_dict()
        assert d["max_structural_contradictions"] == -1
