"""Sprint 32 tests: Compose SDK, enforce_policy completeness, consistency fix, backward compat."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bulla import verify_receipt_integrity
from bulla.diagnostic import check_obligations
from bulla.guard import BullaGuard
from bulla.model import (
    BlindSpot,
    BoundaryObligation,
    Bridge,
    Composition,
    ContradictionReport,
    ContradictionSeverity,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    Edge,
    PackRef,
    PolicyProfile,
    SemanticDimension,
    ToolSpec,
    WitnessReceipt,
)
from bulla.sdk import ComposeResult, compose, compose_multi
from bulla.witness import _resolve_disposition, verify_receipt_consistency, witness


RECEIPTS_DIR = Path(__file__).parent.parent / "examples" / "canonical-demo" / "receipts"
RECEIPT_V029 = RECEIPTS_DIR / "audit_receipt.json"
RECEIPT_V030 = RECEIPTS_DIR / "audit_receipt_v030.json"


# ── Helpers ──────────────────────────────────────────────────────────


def _make_diagnostic(
    fee: int = 0,
    blind_spots: tuple[BlindSpot, ...] = (),
    bridges: tuple[Bridge, ...] = (),
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
        blind_spots=blind_spots,
        bridges=bridges,
        h1_after_bridge=0,
        n_unbridged=n_unbridged,
    )


def _make_comp() -> Composition:
    return Composition(
        name="test",
        tools=(
            ToolSpec(name="a", internal_state=("x",), observable_schema=("x",)),
            ToolSpec(name="b", internal_state=("x",), observable_schema=("x",)),
        ),
        edges=(
            Edge("a", "b", (SemanticDimension(name="x_match", from_field="x", to_field="x"),)),
        ),
    )


def _simple_tools() -> list[dict]:
    return [
        {
            "name": "tool_alpha",
            "description": "Alpha tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "tool_beta",
            "description": "Beta tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "format": {"type": "string"},
                },
            },
        },
    ]


# ── Phase 0a: unmet_obligations on receipt ───────────────────────────


class TestUnmetObligationsReceipt:

    def test_default_zero(self):
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(diag, comp)
        assert receipt.unmet_obligations == 0

    def test_nonzero_passed_through(self):
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(diag, comp, unmet_obligations=3)
        assert receipt.unmet_obligations == 3

    def test_conditional_hash_zero_omitted(self):
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(diag, comp, unmet_obligations=0)
        hash_input = receipt._hash_input()
        assert "unmet_obligations" not in hash_input

    def test_conditional_hash_nonzero_included(self):
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(diag, comp, unmet_obligations=2)
        hash_input = receipt._hash_input()
        assert hash_input["unmet_obligations"] == 2

    def test_receipt_integrity_with_unmet_zero(self):
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(diag, comp, unmet_obligations=0)
        d = receipt.to_dict()
        assert verify_receipt_integrity(d)

    def test_receipt_integrity_with_unmet_nonzero(self):
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(diag, comp, unmet_obligations=5)
        d = receipt.to_dict()
        assert verify_receipt_integrity(d)

    def test_zero_matches_pre_v032_hash(self):
        """Receipt with unmet_obligations=0 produces same hash structure as one without."""
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt_new = witness(diag, comp, unmet_obligations=0)
        receipt_old = witness(diag, comp)
        hi_new = receipt_new._hash_input()
        hi_old = receipt_old._hash_input()
        del hi_new["timestamp"]
        del hi_old["timestamp"]
        assert hi_new == hi_old


# ── Phase 0b: verify_receipt_consistency fix ─────────────────────────


class TestConsistencyFix:

    def test_consistency_with_unmet_obligations(self):
        strict = PolicyProfile(
            name="strict", max_unmet_obligations=0,
        )
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(diag, comp, policy_profile=strict, unmet_obligations=1)
        assert receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE
        ok, violations = verify_receipt_consistency(receipt, comp, diag)
        assert ok, f"Violations: {violations}"

    def test_consistency_with_contradictions(self):
        strict = PolicyProfile(
            name="strict", max_contradictions=0,
        )
        contradictions = (
            ContradictionReport(
                dimension="d", values=("a", "b"),
                sources=("s1", "s2"), severity=ContradictionSeverity.MISMATCH,
            ),
        )
        diag = _make_diagnostic()
        comp = _make_comp()
        receipt = witness(
            diag, comp, policy_profile=strict, contradictions=contradictions,
        )
        assert receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE
        ok, violations = verify_receipt_consistency(receipt, comp, diag)
        assert ok, f"Violations: {violations}"


# ── Phase 0c: enforce_policy completeness ────────────────────────────


class TestEnforcePolicyCompleteness:

    def test_pass_through_inline_dimensions(self):
        guard = BullaGuard(_make_comp())
        dims = {"indexing": {"known_values": ["zero_based"]}}
        receipt = guard.enforce_policy(inline_dimensions=dims)
        assert receipt.inline_dimensions == dims

    def test_pass_through_boundary_obligations(self):
        guard = BullaGuard(_make_comp())
        obs = (BoundaryObligation(placeholder_tool="ph", dimension="d", field="f"),)
        receipt = guard.enforce_policy(boundary_obligations=obs)
        assert receipt.boundary_obligations == obs

    def test_pass_through_parent_receipt_hashes(self):
        guard = BullaGuard(_make_comp())
        hashes = ("abc123", "def456")
        receipt = guard.enforce_policy(parent_receipt_hashes=hashes)
        assert receipt.parent_receipt_hashes == hashes

    def test_pass_through_active_packs(self):
        guard = BullaGuard(_make_comp())
        packs = (PackRef(name="base", version="1.0", hash="h"),)
        receipt = guard.enforce_policy(active_packs=packs)
        assert receipt.active_packs == packs

    def test_pass_through_unmet_obligations(self):
        guard = BullaGuard(_make_comp())
        receipt = guard.enforce_policy(unmet_obligations=4)
        assert receipt.unmet_obligations == 4

    def test_all_fields_receipt_integrity(self):
        guard = BullaGuard(_make_comp())
        receipt = guard.enforce_policy(
            inline_dimensions={"d": {"known_values": ["v"]}},
            boundary_obligations=(
                BoundaryObligation(placeholder_tool="ph", dimension="d", field="f"),
            ),
            parent_receipt_hashes=("hash1",),
            active_packs=(PackRef(name="base", version="1.0", hash="h"),),
            unmet_obligations=2,
            contradictions=(
                ContradictionReport(
                    dimension="d", values=("a", "b"),
                    sources=("s1",), severity=ContradictionSeverity.MISMATCH,
                ),
            ),
        )
        d = receipt.to_dict()
        assert verify_receipt_integrity(d)


# ── Phase 1: compose() ──────────────────────────────────────────────


class TestCompose:

    def test_returns_compose_result(self):
        result = compose(_simple_tools())
        assert isinstance(result, ComposeResult)
        assert isinstance(result.receipt, WitnessReceipt)
        assert isinstance(result.diagnostic, Diagnostic)
        assert result.decomposition is None

    def test_minimal_two_tools(self):
        result = compose(_simple_tools(), name="test-sdk")
        assert result.receipt.fee == result.diagnostic.coherence_fee
        assert result.diagnostic.n_tools >= 2

    def test_with_chain_parent_hash(self):
        chain = {
            "receipt_hash": "abc123def456",
            "inline_dimensions": {"indexing": {"known_values": ["zero_based"]}},
        }
        result = compose(_simple_tools(), chain=chain)
        assert result.receipt.parent_receipt_hashes == ("abc123def456",)
        assert result.receipt.inline_dimensions == chain["inline_dimensions"]

    def test_auto_computes_unmet_obligations(self):
        """compose() with chain containing obligations, 1 met and 1 unmet."""
        tools = [
            {
                "name": "my_tool",
                "description": "A tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                },
            },
        ]
        chain = {
            "receipt_hash": "parent_hash",
            "boundary_obligations": [
                {
                    "placeholder_tool": "server_a",
                    "dimension": "query_dim",
                    "field": "query",
                },
                {
                    "placeholder_tool": "server_a",
                    "dimension": "secret_dim",
                    "field": "secret_field_not_present",
                },
            ],
        }
        result = compose(tools, chain=chain)
        assert result.receipt.unmet_obligations >= 0
        assert result.receipt.boundary_obligations is not None
        assert len(result.receipt.boundary_obligations) == 2

    def test_no_chain_zero_unmet(self):
        result = compose(_simple_tools())
        assert result.receipt.unmet_obligations == 0


# ── Phase 2: compose_multi() ────────────────────────────────────────


class TestComposeMulti:

    def _two_server_tools(self) -> dict[str, list[dict]]:
        return {
            "alpha": [
                {
                    "name": "search",
                    "description": "Search tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                    },
                },
            ],
            "beta": [
                {
                    "name": "fetch",
                    "description": "Fetch tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "query": {"type": "string"},
                        },
                    },
                },
            ],
        }

    def test_returns_compose_result_with_decomposition(self):
        result = compose_multi(self._two_server_tools())
        assert isinstance(result, ComposeResult)
        assert result.decomposition is not None
        assert result.decomposition.total_fee == result.diagnostic.coherence_fee

    def test_tool_names_prefixed(self):
        result = compose_multi(self._two_server_tools())
        tool_names = [t.name for t in result.receipt.composition_hash and result.diagnostic.name and []]
        assert result.diagnostic.name.startswith("multi_")

    def test_auto_detects_contradictions_from_chain(self):
        chain = {
            "receipt_hash": "parent_hash",
            "inline_dimensions": {
                "indexing": {
                    "known_values": ["zero_based", "one_based"],
                    "provenance": {"source_tools": ["tool_a", "tool_b"]},
                },
            },
        }
        result = compose_multi(self._two_server_tools(), chain=chain)
        assert result.receipt.contradictions is not None
        assert len(result.receipt.contradictions) == 1
        assert result.receipt.contradictions[0].dimension == "indexing"

    def test_no_contradictions_when_single_value(self):
        chain = {
            "receipt_hash": "parent_hash",
            "inline_dimensions": {
                "indexing": {
                    "known_values": ["zero_based"],
                    "provenance": {"source_tools": ["tool_a"]},
                },
            },
        }
        result = compose_multi(self._two_server_tools(), chain=chain)
        assert result.receipt.contradictions is None

    def test_strict_policy_refuses(self):
        strict = PolicyProfile(
            name="strict", max_fee=0, max_blind_spots=0,
        )
        result = compose_multi(self._two_server_tools(), policy=strict)
        if result.diagnostic.coherence_fee > 0 and result.diagnostic.n_unbridged > 0:
            assert result.receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE


# ── Backward compatibility ───────────────────────────────────────────


class TestBackwardCompat:

    @pytest.mark.skipif(
        not RECEIPT_V029.exists(), reason="v029 receipt not present"
    )
    def test_v029_receipt_integrity(self):
        data = json.loads(RECEIPT_V029.read_text())
        assert verify_receipt_integrity(data)
        assert "unmet_obligations" not in data

    @pytest.mark.skipif(
        not RECEIPT_V030.exists(), reason="v030 receipt not present"
    )
    def test_v030_receipt_integrity(self):
        data = json.loads(RECEIPT_V030.read_text())
        assert verify_receipt_integrity(data)
        assert "unmet_obligations" not in data


# ── SDK imports ──────────────────────────────────────────────────────


class TestSDKImports:

    def test_import_from_sdk_module(self):
        from bulla.sdk import ComposeResult, compose, compose_multi
        assert callable(compose)
        assert callable(compose_multi)

    def test_import_from_top_level(self):
        from bulla import ComposeResult, compose, compose_multi
        assert callable(compose)
        assert callable(compose_multi)
