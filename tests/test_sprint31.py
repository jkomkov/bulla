"""Sprint 31 tests: policy enforcement, disposition rules, CLI exit codes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla import verify_receipt_integrity
from bulla.guard import BullaGuard
from bulla.model import (
    BlindSpot,
    Bridge,
    Composition,
    ContradictionReport,
    ContradictionSeverity,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    Edge,
    PolicyProfile,
    SemanticDimension,
    ToolSpec,
)
from bulla.witness import _resolve_disposition, witness


RECEIPTS_DIR = Path(__file__).parent.parent / "examples" / "canonical-demo" / "receipts"
RECEIPT_V029 = RECEIPTS_DIR / "audit_receipt.json"
RECEIPT_V030 = RECEIPTS_DIR / "audit_receipt_v030.json"


# ── Helpers ──────────────────────────────────────────────────────────


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


# ── Phase 1: PolicyProfile serialization ─────────────────────────────


class TestPolicyProfileSerialization:

    def test_new_fields_in_to_dict(self):
        p = PolicyProfile(name="strict", max_unmet_obligations=0, max_contradictions=2)
        d = p.to_dict()
        assert d["max_unmet_obligations"] == 0
        assert d["max_contradictions"] == 2

    def test_default_values_backward_compat(self):
        d = DEFAULT_POLICY_PROFILE.to_dict()
        assert d["max_unmet_obligations"] == -1
        assert d["max_contradictions"] == -1
        assert d["name"] == "witness.default.v1"

    def test_round_trip(self):
        p = PolicyProfile(name="test", max_unmet_obligations=3, max_contradictions=1)
        d = p.to_dict()
        p2 = PolicyProfile(**d)
        assert p2.max_unmet_obligations == 3
        assert p2.max_contradictions == 1
        assert p2.to_dict() == d


# ── Phase 2: Disposition rules ────────────────────────────────────────


class TestDispositionRules:

    def test_strict_unmet_obligations_refuse(self):
        diag = _make_diagnostic()
        policy = PolicyProfile(name="strict", max_unmet_obligations=0, require_bridge=False)
        assert _resolve_disposition(diag, policy, unmet_obligations=1) == (
            Disposition.REFUSE_PENDING_DISCLOSURE
        )

    def test_tolerant_unmet_obligations_proceed(self):
        diag = _make_diagnostic()
        policy = PolicyProfile(name="tolerant", max_unmet_obligations=2, require_bridge=False)
        assert _resolve_disposition(diag, policy, unmet_obligations=1) == (
            Disposition.PROCEED
        )

    def test_strict_contradictions_refuse(self):
        diag = _make_diagnostic()
        policy = PolicyProfile(name="strict", max_contradictions=0, require_bridge=False)
        assert _resolve_disposition(diag, policy, contradiction_count=1) == (
            Disposition.REFUSE_PENDING_DISCLOSURE
        )

    def test_tolerant_contradictions_proceed(self):
        diag = _make_diagnostic()
        policy = PolicyProfile(name="tolerant", max_contradictions=3, require_bridge=False)
        assert _resolve_disposition(diag, policy, contradiction_count=2) == (
            Disposition.PROCEED
        )

    def test_disabled_ignores_both(self):
        """Default -1 disables obligation/contradiction enforcement."""
        diag = _make_diagnostic()
        policy = PolicyProfile(name="default", require_bridge=False)
        assert _resolve_disposition(
            diag, policy, unmet_obligations=100, contradiction_count=50
        ) == Disposition.PROCEED

    def test_backward_compat_no_obligation_data(self):
        """Default policy with no obligation/contradiction data -> same as before."""
        diag = _make_diagnostic()
        assert _resolve_disposition(diag) == Disposition.PROCEED

    def test_unmet_takes_priority_over_bridge(self):
        """Unmet obligation refuse takes priority over proceed_with_bridge."""
        bs = BlindSpot(
            dimension="x", edge="a->b",
            from_field="x", to_field="x",
            from_tool="a", to_tool="b",
            from_hidden=True, to_hidden=False,
        )
        diag = _make_diagnostic(blind_spots=[bs], n_unbridged=1)
        policy = PolicyProfile(
            name="strict",
            max_unmet_obligations=0,
            require_bridge=True,
        )
        assert _resolve_disposition(diag, policy, unmet_obligations=1) == (
            Disposition.REFUSE_PENDING_DISCLOSURE
        )

    def test_contradiction_after_unmet_in_priority(self):
        """Both unmet and contradictions trigger refuse; unmet checked first."""
        diag = _make_diagnostic()
        policy = PolicyProfile(
            name="strict",
            max_unmet_obligations=0,
            max_contradictions=0,
            require_bridge=False,
        )
        result = _resolve_disposition(diag, policy, unmet_obligations=1, contradiction_count=1)
        assert result == Disposition.REFUSE_PENDING_DISCLOSURE


# ── Phase 3: witness() with new parameters ────────────────────────────


class TestWitnessNewParams:

    def test_receipt_with_strict_policy_refuses(self):
        comp = _make_comp()
        from bulla.diagnostic import diagnose
        diag = diagnose(comp)
        policy = PolicyProfile(name="strict", max_unmet_obligations=0, require_bridge=False)
        receipt = witness(diag, comp, policy_profile=policy, unmet_obligations=1)
        assert receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE

    def test_receipt_default_policy_proceeds(self):
        comp = _make_comp()
        from bulla.diagnostic import diagnose
        diag = diagnose(comp)
        receipt = witness(diag, comp, unmet_obligations=5, contradiction_count=3)
        assert receipt.disposition in (Disposition.PROCEED, Disposition.PROCEED_WITH_BRIDGE)

    def test_receipt_integrity_with_policy(self):
        comp = _make_comp()
        from bulla.diagnostic import diagnose
        diag = diagnose(comp)
        policy = PolicyProfile(name="strict", max_unmet_obligations=0, max_contradictions=0)
        receipt = witness(diag, comp, policy_profile=policy, unmet_obligations=1)
        d = receipt.to_dict()
        assert verify_receipt_integrity(d)

    def test_contradiction_count_from_tuple(self):
        """contradiction_count auto-derives from contradictions tuple length."""
        comp = _make_comp()
        from bulla.diagnostic import diagnose
        diag = diagnose(comp)
        contradictions = (
            ContradictionReport(
                dimension="path_convention",
                values=("posix", "windows"),
                sources=("tool_a", "tool_b"),
                severity=ContradictionSeverity.MISMATCH,
            ),
        )
        policy = PolicyProfile(
            name="strict", max_contradictions=0, require_bridge=False,
        )
        receipt = witness(
            diag, comp, policy_profile=policy, contradictions=contradictions,
        )
        assert receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE


# ── Phase 4: BullaGuard.enforce_policy ────────────────────────────────


class TestEnforcePolicy:

    def test_enforce_policy_returns_receipt(self):
        guard = BullaGuard.from_tools(
            {"a": {"fields": ["x"]}, "b": {"fields": ["x"]}}
        )
        receipt = guard.enforce_policy()
        assert receipt.disposition in (
            Disposition.PROCEED,
            Disposition.PROCEED_WITH_BRIDGE,
            Disposition.PROCEED_WITH_RECEIPT,
        )

    def test_enforce_policy_strict_refuses(self):
        guard = BullaGuard.from_tools(
            {"a": {"fields": ["x"]}, "b": {"fields": ["x"]}}
        )
        policy = PolicyProfile(name="strict", max_unmet_obligations=0, require_bridge=False)
        receipt = guard.enforce_policy(policy, unmet_obligations=1)
        assert receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE

    def test_enforce_policy_receipt_integrity(self):
        guard = BullaGuard.from_tools(
            {"a": {"fields": ["x"]}, "b": {"fields": ["x"]}}
        )
        policy = PolicyProfile(name="strict", max_contradictions=0)
        receipt = guard.enforce_policy(policy, contradiction_count=2)
        d = receipt.to_dict()
        assert verify_receipt_integrity(d)

    def test_enforce_policy_with_contradictions(self):
        guard = BullaGuard.from_tools(
            {"a": {"fields": ["x"]}, "b": {"fields": ["x"]}}
        )
        contradictions = (
            ContradictionReport(
                dimension="encoding",
                values=("utf-8", "ascii"),
                sources=("a", "b"),
                severity=ContradictionSeverity.MISMATCH,
            ),
        )
        policy = PolicyProfile(name="strict", max_contradictions=0, require_bridge=False)
        receipt = guard.enforce_policy(
            policy, contradictions=contradictions,
        )
        assert receipt.disposition == Disposition.REFUSE_PENDING_DISCLOSURE
        assert receipt.contradictions is not None
        assert len(receipt.contradictions) == 1


# ── Phase 5: CLI exit codes ───────────────────────────────────────────


class TestCLIExitCodes:

    def test_max_unmet_argument_registered(self):
        result = subprocess.run(
            [sys.executable, "-m", "bulla", "audit", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "--max-unmet" in result.stdout

    def test_max_contradictions_argument_registered(self):
        result = subprocess.run(
            [sys.executable, "-m", "bulla", "audit", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "--max-contradictions" in result.stdout

    def test_check_max_fee_argument_registered(self):
        result = subprocess.run(
            [sys.executable, "-m", "bulla", "check", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "--max-fee" in result.stdout


# ── Backward compatibility ────────────────────────────────────────────


class TestBackwardCompat:

    def test_v029_receipt_still_valid(self):
        data = json.loads(RECEIPT_V029.read_text(encoding="utf-8"))
        assert verify_receipt_integrity(data)

    def test_v030_receipt_still_valid(self):
        data = json.loads(RECEIPT_V030.read_text(encoding="utf-8"))
        assert verify_receipt_integrity(data)

    def test_v029_has_no_new_policy_fields(self):
        data = json.loads(RECEIPT_V029.read_text(encoding="utf-8"))
        pp = data["policy_profile"]
        assert "max_unmet_obligations" not in pp
        assert "max_contradictions" not in pp

    def test_existing_witness_tests_pass(self):
        """Core witness tests still pass (lightweight regression check)."""
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                str(Path(__file__).parent / "test_witness.py"),
                "-x", "-q",
            ],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"Witness tests failed:\n{result.stdout}\n{result.stderr}"
