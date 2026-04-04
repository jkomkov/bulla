"""Sprint 26 tests: bridge-guided discovery, repair, collective invariant."""
from __future__ import annotations

import subprocess
import sys

import pytest

from bulla.diagnostic import (
    boundary_obligations_from_decomposition,
    decompose_fee,
    diagnose,
    repair_composition,
    repair_step,
    RepairResult,
)
from bulla.discover.adapter import MockAdapter
from bulla.discover.engine import guided_discover, GuidedDiscoveryResult
from bulla.discover.prompt import (
    build_guided_prompt,
    parse_guided_response,
)
from bulla.model import (
    BoundaryObligation,
    Composition,
    Edge,
    ObligationVerdict,
    ProbeResult,
    SemanticDimension,
    ToolSpec,
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _two_server_comp() -> tuple[Composition, list[frozenset[str]]]:
    """Composition with boundary_fee > 0 for testing repairs."""
    tools = (
        ToolSpec("storage__read", ("path", "offset"), ("path",)),
        ToolSpec("storage__write", ("path", "content"), ("path", "content")),
        ToolSpec("api__list", ("endpoint", "offset"), ("endpoint",)),
        ToolSpec("api__get", ("endpoint", "id"), ("endpoint", "id")),
    )
    edges = (
        Edge("storage__read", "api__list", (
            SemanticDimension("path_resolve", "path", "endpoint"),
            SemanticDimension("pagination", "offset", "offset"),
        )),
    )
    comp = Composition("test-comp", tools, edges)
    partition = [
        frozenset(["storage__read", "storage__write"]),
        frozenset(["api__list", "api__get"]),
    ]
    return comp, partition


def _mock_tool_dicts(tools: tuple[ToolSpec, ...]) -> list[dict]:
    result = []
    for t in tools:
        props = {}
        for f in t.internal_state:
            props[f] = {"type": "string"}
            if f in t.observable_schema:
                props[f]["description"] = f"Observable: {f}"
        result.append({
            "name": t.name,
            "description": f"Tool {t.name}",
            "inputSchema": {"type": "object", "properties": props},
        })
    return result


# ── ObligationVerdict + ProbeResult ──────────────────────────────────


class TestObligationVerdict:
    def test_enum_values(self):
        assert ObligationVerdict.CONFIRMED.value == "confirmed"
        assert ObligationVerdict.DENIED.value == "denied"
        assert ObligationVerdict.UNCERTAIN.value == "uncertain"

    def test_from_string(self):
        assert ObligationVerdict("confirmed") == ObligationVerdict.CONFIRMED
        assert ObligationVerdict("denied") == ObligationVerdict.DENIED


class TestProbeResult:
    def test_to_dict(self):
        obl = BoundaryObligation("api", "pagination", "offset", "a -> b")
        pr = ProbeResult(obl, ObligationVerdict.CONFIRMED, "field present", "zero_based")
        d = pr.to_dict()
        assert d["verdict"] == "confirmed"
        assert d["evidence"] == "field present"
        assert d["convention_value"] == "zero_based"
        assert d["obligation"]["field"] == "offset"

    def test_to_dict_minimal(self):
        obl = BoundaryObligation("api", "pagination", "offset")
        pr = ProbeResult(obl, ObligationVerdict.DENIED)
        d = pr.to_dict()
        assert d["verdict"] == "denied"
        assert "evidence" not in d
        assert "convention_value" not in d


# ── Guided prompt construction + parsing ─────────────────────────────


class TestGuidedPrompt:
    def test_build_single_obligation(self):
        obls = [{"placeholder_tool": "api", "dimension": "pagination", "field": "offset"}]
        tools = [{"name": "api__list", "description": "List items",
                  "inputSchema": {"type": "object", "properties": {"offset": {"type": "integer"}}}}]
        prompt = build_guided_prompt(obls, tools)
        assert "OBLIGATION 1:" in prompt
        assert "pagination" in prompt
        assert "offset" in prompt
        assert "BEGIN_VERDICT_1" in prompt

    def test_build_batched_prompt(self):
        obls = [
            {"placeholder_tool": "api", "dimension": "pagination", "field": "offset"},
            {"placeholder_tool": "render", "dimension": "auth", "field": "token"},
        ]
        prompt = build_guided_prompt(obls, [])
        assert "OBLIGATION 1:" in prompt
        assert "OBLIGATION 2:" in prompt
        assert "BEGIN_VERDICT_1" in prompt
        assert "BEGIN_VERDICT_2" in prompt

    def test_build_with_pack_context(self):
        obls = [{"placeholder_tool": "api", "dimension": "pagination", "field": "offset"}]
        pack = {"dimensions": {"pagination": {"known_values": ["zero_based", "one_based"]}}}
        prompt = build_guided_prompt(obls, [], pack_context=pack)
        assert "zero_based" in prompt

    def test_parse_single_verdict(self):
        raw = (
            "---BEGIN_VERDICT_1---\n"
            "verdict: CONFIRMED\n"
            "evidence: field is present\n"
            "convention_value: zero_based\n"
            "---END_VERDICT_1---"
        )
        results = parse_guided_response(raw, 1)
        assert len(results) == 1
        assert results[0]["verdict"] == "CONFIRMED"
        assert results[0]["evidence"] == "field is present"
        assert results[0]["convention_value"] == "zero_based"

    def test_parse_batched_verdicts(self):
        raw = (
            "---BEGIN_VERDICT_1---\n"
            "verdict: CONFIRMED\n"
            "evidence: found it\n"
            "convention_value: standard\n"
            "---END_VERDICT_1---\n"
            "\n"
            "---BEGIN_VERDICT_2---\n"
            "verdict: DENIED\n"
            "evidence: not available\n"
            "convention_value:\n"
            "---END_VERDICT_2---"
        )
        results = parse_guided_response(raw, 2)
        assert len(results) == 2
        assert results[0]["verdict"] == "CONFIRMED"
        assert results[1]["verdict"] == "DENIED"
        assert results[1]["convention_value"] == ""

    def test_parse_missing_verdict_defaults_uncertain(self):
        raw = "Some unrelated text"
        results = parse_guided_response(raw, 2)
        assert len(results) == 2
        assert results[0]["verdict"] == "UNCERTAIN"
        assert results[1]["verdict"] == "UNCERTAIN"


# ── guided_discover engine ───────────────────────────────────────────


class TestGuidedDiscover:
    def test_empty_obligations(self):
        result = guided_discover((), [], MockAdapter(""))
        assert result.probes == ()
        assert result.n_confirmed == 0

    def test_single_confirmed(self):
        obl = BoundaryObligation("api", "pagination", "offset", "a -> b")
        mock_response = (
            "---BEGIN_VERDICT_1---\n"
            "verdict: CONFIRMED\n"
            "evidence: yes\n"
            "convention_value: zero_based\n"
            "---END_VERDICT_1---"
        )
        adapter = MockAdapter(mock_response)
        tools = [{"name": "api__list", "description": "List",
                  "inputSchema": {"type": "object", "properties": {"offset": {"type": "int"}}}}]
        result = guided_discover((obl,), tools, adapter)
        assert result.n_confirmed == 1
        assert result.probes[0].verdict == ObligationVerdict.CONFIRMED
        assert result.probes[0].convention_value == "zero_based"

    def test_mixed_verdicts(self):
        obls = (
            BoundaryObligation("api", "pagination", "offset"),
            BoundaryObligation("render", "auth", "token"),
        )
        mock_response = (
            "---BEGIN_VERDICT_1---\n"
            "verdict: CONFIRMED\n"
            "evidence: found\n"
            "convention_value: standard\n"
            "---END_VERDICT_1---\n"
            "---BEGIN_VERDICT_2---\n"
            "verdict: DENIED\n"
            "evidence: not found\n"
            "convention_value:\n"
            "---END_VERDICT_2---"
        )
        result = guided_discover(obls, [], MockAdapter(mock_response))
        assert result.n_confirmed == 1
        assert result.n_denied == 1
        assert result.n_uncertain == 0
        assert len(result.confirmed) == 1

    def test_prompt_includes_tool_schema(self):
        obl = BoundaryObligation("api", "pagination", "offset")
        tools = [{"name": "api__list", "description": "List items",
                  "inputSchema": {"type": "object", "properties": {"offset": {"type": "int"}}}}]
        adapter = MockAdapter("---BEGIN_VERDICT_1---\nverdict: DENIED\nevidence: no\nconvention_value:\n---END_VERDICT_1---")
        guided_discover((obl,), tools, adapter)
        assert "api__list" in adapter.last_prompt
        assert "pagination" in adapter.last_prompt


# ── repair_composition ───────────────────────────────────────────────


class TestRepairComposition:
    def test_pure_no_mutation(self):
        comp, _ = _two_server_comp()
        original_obs = comp.tools[0].observable_schema

        obl = BoundaryObligation("storage", "pagination", "offset", "storage__read -> api__list")
        probe = ProbeResult(obl, ObligationVerdict.CONFIRMED, "yes")
        repaired = repair_composition(comp, (probe,))

        assert comp.tools[0].observable_schema == original_obs
        assert repaired is not comp

    def test_field_added_to_observable(self):
        comp, _ = _two_server_comp()
        obl = BoundaryObligation("storage", "pagination", "offset", "storage__read -> api__list")
        probe = ProbeResult(obl, ObligationVerdict.CONFIRMED, "yes")
        repaired = repair_composition(comp, (probe,))

        storage_read = next(t for t in repaired.tools if t.name == "storage__read")
        assert "offset" in storage_read.observable_schema

    def test_idempotent(self):
        comp, _ = _two_server_comp()
        obl = BoundaryObligation("storage", "pagination", "offset", "storage__read -> api__list")
        probe = ProbeResult(obl, ObligationVerdict.CONFIRMED, "yes")

        repaired1 = repair_composition(comp, (probe,))
        repaired2 = repair_composition(repaired1, (probe,))
        assert repaired1.canonical_hash() == repaired2.canonical_hash()

    def test_denied_probes_ignored(self):
        comp, _ = _two_server_comp()
        obl = BoundaryObligation("storage", "pagination", "offset")
        probe = ProbeResult(obl, ObligationVerdict.DENIED, "no")
        repaired = repair_composition(comp, (probe,))
        assert repaired.canonical_hash() == comp.canonical_hash()

    def test_collective_invariant_fee_drops(self):
        """Collective invariant: at least one confirmed -> fee strictly decreases."""
        comp, partition = _two_server_comp()
        diag = diagnose(comp)
        assert diag.coherence_fee > 0

        obls = boundary_obligations_from_decomposition(comp, partition, diag)
        probes = tuple(
            ProbeResult(obl, ObligationVerdict.CONFIRMED, "yes")
            for obl in obls
        )

        repaired = repair_composition(comp, probes)
        repaired_diag = diagnose(repaired)
        assert repaired_diag.coherence_fee < diag.coherence_fee


# ── repair_step integration ──────────────────────────────────────────


class TestRepairStep:
    def test_full_round(self):
        comp, partition = _two_server_comp()
        tool_dicts = _mock_tool_dicts(comp.tools)

        diag = diagnose(comp)
        obls = boundary_obligations_from_decomposition(comp, partition, diag)
        assert len(obls) > 0

        blocks = []
        for idx, obl in enumerate(obls, 1):
            blocks.append(
                f"---BEGIN_VERDICT_{idx}---\n"
                f"verdict: CONFIRMED\n"
                f"evidence: field is observable\n"
                f"convention_value: standard\n"
                f"---END_VERDICT_{idx}---"
            )
        adapter = MockAdapter("\n\n".join(blocks))

        result = repair_step(comp, partition, tool_dicts, adapter)
        assert isinstance(result, RepairResult)
        assert result.fee_delta > 0
        assert result.repaired_fee < result.original_fee
        assert result.confirmed_count > 0

    def test_no_obligations(self):
        tools = (
            ToolSpec("a__x", ("f",), ("f",)),
            ToolSpec("b__y", ("g",), ("g",)),
        )
        edges = (Edge("a__x", "b__y", (SemanticDimension("d", "f", "g"),)),)
        comp = Composition("no-fee", tools, edges)
        partition = [frozenset(["a__x"]), frozenset(["b__y"])]

        result = repair_step(comp, partition, [], MockAdapter(""))
        assert result.fee_delta == 0
        assert result.confirmed_count == 0
        assert result.repaired_comp is comp


# ── GuidedDiscoveryResult ────────────────────────────────────────────


class TestGuidedDiscoveryResult:
    def test_summary_stats(self):
        probes = (
            ProbeResult(BoundaryObligation("a", "d", "f"), ObligationVerdict.CONFIRMED),
            ProbeResult(BoundaryObligation("b", "d", "g"), ObligationVerdict.DENIED),
            ProbeResult(BoundaryObligation("c", "d", "h"), ObligationVerdict.UNCERTAIN),
        )
        result = GuidedDiscoveryResult(probes, "raw", "prompt")
        assert result.n_confirmed == 1
        assert result.n_denied == 1
        assert result.n_uncertain == 1
        assert len(result.confirmed) == 1


# ── Demo smoke test ──────────────────────────────────────────────────


class TestGuidedDiscoveryDemo:
    def test_demo_runs_successfully(self):
        result = subprocess.run(
            [sys.executable, "scripts/run_guided_discovery_demo.py"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Demo failed:\n{result.stderr}\n{result.stdout}"
        assert "Guided Discovery Demo" in result.stdout
        assert "Collective invariant" in result.stdout
        assert "VALID" in result.stdout
