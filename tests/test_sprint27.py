"""Sprint 27 tests: iterative convergence loop, module split, demo smoke."""
from __future__ import annotations

import re
import subprocess
import sys

import pytest

from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    decompose_fee,
    diagnose,
)
from bulla.discover.adapter import MockAdapter
from bulla.discover.engine import guided_discover
from bulla.model import (
    BoundaryObligation,
    Composition,
    Edge,
    ObligationVerdict,
    ProbeResult,
    SemanticDimension,
    ToolSpec,
)
from bulla.repair import (
    ConvergenceResult,
    RepairResult,
    coordination_step,
    repair_composition,
    repair_step,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _fee2_composition() -> tuple[Composition, list[frozenset[str]], list[dict]]:
    """Build a composition with fee=2 from two independent hidden dimensions."""
    alpha = (
        ToolSpec("alpha__read", ("path", "encoding"), ("path",)),
        ToolSpec("alpha__write", ("path", "mode"), ("path",)),
    )
    beta = (
        ToolSpec("beta__fetch", ("url", "timeout"), ("url",)),
        ToolSpec("beta__post", ("url", "payload"), ("url",)),
    )
    edges = (
        Edge("alpha__read", "beta__fetch", (
            SemanticDimension("transport", "encoding", "timeout"),
        )),
        Edge("alpha__write", "beta__post", (
            SemanticDimension("protocol", "mode", "payload"),
        )),
    )
    comp = Composition("test-fee2", alpha + beta, edges)
    partition = [
        frozenset(t.name for t in alpha),
        frozenset(t.name for t in beta),
    ]
    tool_dicts = []
    for t in alpha + beta:
        props = {}
        for f in t.internal_state:
            props[f] = {"type": "string"}
            if f in t.observable_schema:
                props[f]["description"] = f"Observable: {f}"
        tool_dicts.append({
            "name": t.name,
            "description": f"Tool {t.name}",
            "inputSchema": {"type": "object", "properties": props},
        })
    return comp, partition, tool_dicts


def _fee0_composition() -> tuple[Composition, list[frozenset[str]], list[dict]]:
    """Build a composition with fee=0 (all fields observable)."""
    tools = (
        ToolSpec("x__a", ("f1", "f2"), ("f1", "f2")),
        ToolSpec("x__b", ("f3", "f4"), ("f3", "f4")),
    )
    edges = (
        Edge("x__a", "x__b", (
            SemanticDimension("dim1", "f2", "f3"),
        )),
    )
    comp = Composition("test-fee0", tools, edges)
    partition = [frozenset(t.name for t in tools)]
    tool_dicts = [
        {"name": t.name, "description": f"Tool {t.name}",
         "inputSchema": {"type": "object", "properties": {
             f: {"type": "string"} for f in t.internal_state
         }}}
        for t in tools
    ]
    return comp, partition, tool_dicts


def _confirm_all_response(n: int) -> str:
    """Build a mock response that confirms all N obligations."""
    blocks = []
    for idx in range(1, n + 1):
        blocks.append(
            f"---BEGIN_VERDICT_{idx}---\n"
            f"verdict: CONFIRMED\n"
            f"evidence: field is observable\n"
            f"convention_value: standard\n"
            f"---END_VERDICT_{idx}---"
        )
    return "\n\n".join(blocks)


def _deny_all_response(n: int) -> str:
    """Build a mock response that denies all N obligations."""
    blocks = []
    for idx in range(1, n + 1):
        blocks.append(
            f"---BEGIN_VERDICT_{idx}---\n"
            f"verdict: DENIED\n"
            f"evidence: field not exposed\n"
            f"convention_value:\n"
            f"---END_VERDICT_{idx}---"
        )
    return "\n\n".join(blocks)


def _uncertain_all_response(n: int) -> str:
    """Build a mock response that returns UNCERTAIN for all N obligations."""
    blocks = []
    for idx in range(1, n + 1):
        blocks.append(
            f"---BEGIN_VERDICT_{idx}---\n"
            f"verdict: UNCERTAIN\n"
            f"evidence: cannot determine\n"
            f"convention_value:\n"
            f"---END_VERDICT_{idx}---"
        )
    return "\n\n".join(blocks)


class StagedMockAdapter:
    """Returns different responses per call for multi-round testing."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    def complete(self, prompt: str) -> str:
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        return self._responses[idx]


class DimensionAwareMockAdapter:
    """Confirms one new dimension per round (matches demo StagedMockAdapter)."""

    def __init__(self) -> None:
        self._confirmed_dims: set[str] = set()

    def complete(self, prompt: str) -> str:
        n_obls = len(re.findall(r"OBLIGATION \d+:", prompt))
        if n_obls == 0:
            return ""

        dims: list[str] = []
        for idx in range(1, n_obls + 1):
            pattern = rf"OBLIGATION {idx}:.*?Dimension:\s*(\S+)"
            match = re.search(pattern, prompt, re.DOTALL)
            dims.append(match.group(1) if match else "")

        confirmed_this_round = False
        blocks = []
        for idx in range(1, n_obls + 1):
            dim = dims[idx - 1]
            should_confirm = not confirmed_this_round and dim not in self._confirmed_dims
            if should_confirm:
                self._confirmed_dims.add(dim)
                confirmed_this_round = True
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\n"
                    f"verdict: CONFIRMED\n"
                    f"evidence: field is observable\n"
                    f"convention_value: standard\n"
                    f"---END_VERDICT_{idx}---"
                )
            else:
                blocks.append(
                    f"---BEGIN_VERDICT_{idx}---\n"
                    f"verdict: UNCERTAIN\n"
                    f"evidence: cannot determine\n"
                    f"convention_value:\n"
                    f"---END_VERDICT_{idx}---"
                )
        return "\n\n".join(blocks)


# ── TestConvergenceResult ─────────────────────────────────────────────

class TestConvergenceResult:
    def test_dataclass_fields(self):
        rr = RepairResult(
            original_fee=2, repaired_fee=1, fee_delta=1,
            probes=(), confirmed_count=1,
            repaired_comp=Composition("x", (), ()),
            remaining_obligations=(),
        )
        cr = ConvergenceResult(
            rounds=(rr,),
            converged=True,
            final_comp=Composition("x", (), ()),
            final_fee=1,
            total_confirmed=1,
            total_denied=0,
            total_uncertain=0,
            termination_reason="fee_zero",
        )
        assert cr.converged is True
        assert cr.final_fee == 1
        assert len(cr.rounds) == 1
        assert cr.termination_reason == "fee_zero"
        assert cr.total_confirmed == 1

    def test_frozen(self):
        cr = ConvergenceResult(
            rounds=(), converged=False,
            final_comp=Composition("x", (), ()),
            final_fee=0, total_confirmed=0,
            total_denied=0, total_uncertain=0,
            termination_reason="fixpoint",
        )
        with pytest.raises(AttributeError):
            cr.converged = True  # type: ignore[misc]


# ── TestCoordinationStep ─────────────────────────────────────────────

class TestCoordinationStep:
    def test_convergence_1_round(self):
        """All obligations confirmed in round 1 -> fee_zero."""
        comp, partition, tool_dicts = _fee2_composition()
        adapter = MockAdapter(_confirm_all_response(10))
        result = coordination_step(comp, partition, tool_dicts, adapter, max_rounds=5)
        assert isinstance(result, ConvergenceResult)
        assert result.converged is True
        assert result.termination_reason == "fee_zero"
        assert result.final_fee == 0
        assert len(result.rounds) == 1
        assert result.total_confirmed >= 1

    def test_convergence_2_rounds(self):
        """Dimension-aware adapter resolves one edge per round -> 2 rounds."""
        comp, partition, tool_dicts = _fee2_composition()
        assert diagnose(comp).coherence_fee == 2

        adapter = DimensionAwareMockAdapter()
        result = coordination_step(comp, partition, tool_dicts, adapter, max_rounds=5)
        assert result.converged is True
        assert result.termination_reason == "fee_zero"
        assert result.final_fee == 0
        assert len(result.rounds) == 2
        assert result.rounds[0].fee_delta == 1
        assert result.rounds[1].fee_delta == 1

    def test_fixpoint_all_denied(self):
        """All obligations denied -> fixpoint in 1 round."""
        comp, partition, tool_dicts = _fee2_composition()
        adapter = MockAdapter(_deny_all_response(10))
        result = coordination_step(comp, partition, tool_dicts, adapter, max_rounds=5)
        assert result.converged is True
        assert result.termination_reason == "fixpoint"
        assert result.final_fee == 2
        assert len(result.rounds) == 1

    def test_max_rounds_cutoff(self):
        """Adapter makes progress each round but budget runs out first."""
        comp, partition, tool_dicts = _fee2_composition()
        assert diagnose(comp).coherence_fee == 2
        adapter = DimensionAwareMockAdapter()
        result = coordination_step(comp, partition, tool_dicts, adapter, max_rounds=1)
        assert result.converged is False
        assert result.termination_reason == "max_rounds"
        assert len(result.rounds) == 1
        assert result.final_fee == 1

    def test_zero_obligations(self):
        """Composition with fee=0 -> immediate fee_zero."""
        comp, partition, tool_dicts = _fee0_composition()
        assert diagnose(comp).coherence_fee == 0
        adapter = MockAdapter("")
        result = coordination_step(comp, partition, tool_dicts, adapter, max_rounds=5)
        assert result.converged is True
        assert result.termination_reason == "fee_zero"
        assert result.final_fee == 0

    def test_obligation_carry_forward_triage(self):
        """After round 1, CONFIRMED obligations (repaired) don't reappear.

        Round 1: confirm encoding (fee drops), deny timeout, uncertain mode+payload.
        Carry-forward: only mode+payload (UNCERTAIN). DENIED timeout is excluded
        from carry-forward but may be re-derived by repair_step's own decomposition.
        CONFIRMED encoding is both excluded AND no longer derivable (now observable).
        """
        comp, partition, tool_dicts = _fee2_composition()
        round1_response = (
            "---BEGIN_VERDICT_1---\n"
            "verdict: CONFIRMED\n"
            "evidence: encoding is observable\n"
            "convention_value: standard\n"
            "---END_VERDICT_1---\n\n"
            "---BEGIN_VERDICT_2---\n"
            "verdict: DENIED\n"
            "evidence: timeout not exposed\n"
            "convention_value:\n"
            "---END_VERDICT_2---\n\n"
            "---BEGIN_VERDICT_3---\n"
            "verdict: UNCERTAIN\n"
            "evidence: cannot determine\n"
            "convention_value:\n"
            "---END_VERDICT_3---\n\n"
            "---BEGIN_VERDICT_4---\n"
            "verdict: UNCERTAIN\n"
            "evidence: cannot determine\n"
            "convention_value:\n"
            "---END_VERDICT_4---"
        )
        round2_response = _confirm_all_response(10)
        adapter = StagedMockAdapter([round1_response, round2_response])
        result = coordination_step(comp, partition, tool_dicts, adapter, max_rounds=5)
        assert len(result.rounds) >= 2

        r1_confirmed_keys = {
            (p.obligation.placeholder_tool, p.obligation.dimension, p.obligation.field)
            for p in result.rounds[0].probes
            if p.verdict == ObligationVerdict.CONFIRMED
        }
        assert len(r1_confirmed_keys) > 0
        r2_keys = {
            (p.obligation.placeholder_tool, p.obligation.dimension, p.obligation.field)
            for p in result.rounds[1].probes
        }
        for key in r1_confirmed_keys:
            assert key not in r2_keys, (
                f"Repaired obligation {key} should not reappear in round 2"
            )
        assert len(result.rounds[1].probes) < len(result.rounds[0].probes), (
            "Round 2 should have fewer probes (CONFIRMED was repaired)"
        )

    def test_convergence_invariant_monotonic(self):
        """Fee is monotonically non-increasing across rounds."""
        comp, partition, tool_dicts = _fee2_composition()
        adapter = DimensionAwareMockAdapter()
        result = coordination_step(comp, partition, tool_dicts, adapter, max_rounds=10)
        fees = [result.rounds[0].original_fee]
        for rnd in result.rounds:
            fees.append(rnd.repaired_fee)
        for i in range(1, len(fees)):
            assert fees[i] <= fees[i - 1], (
                f"Fee increased at step {i}: {fees[i-1]} -> {fees[i]}"
            )


# ── TestRepairModuleSplit ────────────────────────────────────────────

class TestRepairModuleSplit:
    def test_import_from_repair_module(self):
        from bulla.repair import repair_composition, repair_step, coordination_step, ConvergenceResult, RepairResult
        assert callable(repair_composition)
        assert callable(repair_step)
        assert callable(coordination_step)

    def test_import_from_bulla_package(self):
        from bulla import repair_composition, repair_step, coordination_step, ConvergenceResult, RepairResult
        assert callable(repair_composition)
        assert callable(repair_step)
        assert callable(coordination_step)

    def test_diagnostic_no_longer_exports_repair(self):
        import bulla.diagnostic as d
        assert not hasattr(d, "repair_composition")
        assert not hasattr(d, "repair_step")
        assert not hasattr(d, "RepairResult")

    def test_diagnostic_still_exports_measurement(self):
        from bulla.diagnostic import (
            diagnose,
            decompose_fee,
            boundary_obligations_from_decomposition,
            check_obligations,
            FeeDecomposition,
        )
        assert callable(diagnose)
        assert callable(decompose_fee)


# ── TestPhase0Fixes ──────────────────────────────────────────────────

class TestPhase0Fixes:
    def test_match_tool_sorted_prefix(self):
        """_match_tool_for_obligation uses sorted iteration for determinism."""
        from bulla.discover.prompt import _match_tool_for_obligation
        tool_by_name = {
            "alpha__write": {"name": "alpha__write"},
            "alpha__read": {"name": "alpha__read"},
        }
        obl = {"placeholder_tool": "alpha", "dimension": "d", "field": "f"}
        result = _match_tool_for_obligation(obl, tool_by_name)
        assert result is not None
        assert result["name"] == "alpha__read"

    def test_match_tool_source_edge_preferred(self):
        """source_edge match takes priority over prefix."""
        from bulla.discover.prompt import _match_tool_for_obligation
        tool_by_name = {
            "alpha__read": {"name": "alpha__read"},
            "alpha__write": {"name": "alpha__write"},
        }
        obl = {
            "placeholder_tool": "alpha",
            "dimension": "d",
            "field": "f",
            "source_edge": "alpha__write -> beta__post",
        }
        result = _match_tool_for_obligation(obl, tool_by_name)
        assert result is not None
        assert result["name"] == "alpha__write"

    def test_mock_adapter_docstring(self):
        """MockAdapter has docstring mentioning last_prompt."""
        assert "last_prompt" in (MockAdapter.__doc__ or "")

    def test_convention_value_accepts_any_nonempty(self):
        """parse_guided_response accepts any non-empty convention_value."""
        from bulla.discover.prompt import parse_guided_response
        raw = (
            "---BEGIN_VERDICT_1---\n"
            "verdict: CONFIRMED\n"
            "evidence: yes\n"
            "convention_value: none\n"
            "---END_VERDICT_1---"
        )
        results = parse_guided_response(raw, 1)
        assert results[0]["convention_value"] == "none"


# ── TestConvergenceDemo ──────────────────────────────────────────────

class TestConvergenceDemo:
    def test_demo_runs_successfully(self):
        result = subprocess.run(
            [sys.executable, "scripts/run_convergence_demo.py"],
            capture_output=True, text=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, f"Demo failed:\n{result.stderr}\n{result.stdout}"
        assert "Convergence Demo" in result.stdout
        assert "fee_zero" in result.stdout
        assert "VALID" in result.stdout
