"""Tests for the live-object CrewAI runtime adapter.

Gated by ``pytest.importorskip("crewai")`` so a clean checkout without
``bulla[crewai]`` skips this file rather than failing.

Coverage matches the sprint plan's 12 tests:
  1     empty crew → fee=0 session
  2     sequential two-task crew with shared field → fee=0, edge present
  3     hierarchical crew → manager edges
  4     two agents with same-named tool → namespacing disambiguates
  5     task.context → explicit dependency edges
  6     LOAD-BEARING: bind() insertion-order independence
  7     output_schemas kwarg propagates
  8     warning on tool without args_schema
  9–10  on_step / on_task_complete record into invocations
  11    chained checkpoints when emit_checkpoint_per_step=True
  12    finalize() emits terminal_receipt
"""

from __future__ import annotations

import random
import warnings
from typing import Any

import pytest

pytest.importorskip("crewai", reason="bulla[crewai] extra not installed")

from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

import bulla
from bulla.crewai import BullaCrewCallback, bind
from bulla.model import Disposition, WitnessReceipt


# ── Tool fixtures ──────────────────────────────────────────────────


class _SearchInput(BaseModel):
    query: str = Field(description="The search query")


class _SearchTool(BaseTool):
    name: str = "search"
    description: str = "Search the web"
    args_schema: type = _SearchInput

    def _run(self, query: str) -> str:
        return "search-result"


class _SummarizeInput(BaseModel):
    query: str = Field(description="Source query")
    text: str = Field(description="Text to summarize")


class _SummarizeTool(BaseTool):
    name: str = "summarize"
    description: str = "Summarize text"
    args_schema: type = _SummarizeInput

    def _run(self, query: str, text: str) -> str:
        return "summary"


def _researcher() -> Agent:
    return Agent(
        role="researcher",
        goal="find things",
        backstory="researcher",
        tools=[_SearchTool()],
    )


def _writer() -> Agent:
    return Agent(
        role="writer",
        goal="write",
        backstory="writer",
        tools=[_SummarizeTool()],
    )


# ── 1: empty crew ──────────────────────────────────────────────────


def test_bind_minimal_crew_returns_zero_fee_session():
    """A Crew with one toolless agent / task produces an empty
    composition (no tools = no edges = fee 0). CrewAI rejects truly
    empty Crews at construction (`agents=[]` triggers a validation
    error), so this is the closest legal minimal case."""
    a = Agent(role="x", goal="g", backstory="b")  # no tools
    t = Task(description="d", agent=a, expected_output="o")
    crew = Crew(agents=[a], tasks=[t], process=Process.sequential)
    session = bind(crew)
    assert session.fee == 0
    assert session.composition.tools == ()


# ── 2: sequential two-task pipeline ────────────────────────────────


def test_bind_sequential_crew_two_tasks():
    r, w = _researcher(), _writer()
    t1 = Task(description="Research", agent=r, expected_output="notes")
    t2 = Task(
        description="Write summary",
        agent=w,
        expected_output="summary",
        context=[t1],
    )
    crew = Crew(agents=[r, w], tasks=[t1, t2], process=Process.sequential)

    session = bind(crew, name="research-flow")
    tool_names = {t.name for t in session.composition.tools}
    assert tool_names == {"researcher.search", "writer.summarize"}
    edge_pairs = {(e.from_tool, e.to_tool) for e in session.composition.edges}
    assert ("researcher.search", "writer.summarize") in edge_pairs


# ── 3: hierarchical crew ───────────────────────────────────────────


def test_bind_hierarchical_crew_emits_manager_edges():
    """When process=hierarchical, the manager_agent's tools edge to
    every worker tool that shares fields."""

    class _DelegateInput(BaseModel):
        query: str = Field(description="Subtask query")

    class _DelegateTool(BaseTool):
        name: str = "delegate"
        description: str = "Delegate a subtask"
        args_schema: type = _DelegateInput

        def _run(self, query: str) -> str:
            return ""

    manager = Agent(
        role="manager",
        goal="orchestrate",
        backstory="manager",
        tools=[_DelegateTool()],
        allow_delegation=True,
    )
    worker = _researcher()
    t = Task(description="Run subtask", agent=worker, expected_output="output")

    crew = Crew(
        agents=[worker],
        tasks=[t],
        process=Process.hierarchical,
        manager_agent=manager,
    )

    session = bind(crew)
    edge_pairs = {(e.from_tool, e.to_tool) for e in session.composition.edges}
    assert ("manager.delegate", "researcher.search") in edge_pairs


# ── 4: namespacing disambiguates same-named tools ──────────────────


def test_bind_disambiguates_tool_names_via_agent_role():
    """Two agents with a tool named `search` should produce two
    distinct ToolSpecs, namespaced as `{role}.search`."""

    class _OtherSearchTool(BaseTool):
        name: str = "search"
        description: str = "Different search backend"
        args_schema: type = _SearchInput

        def _run(self, query: str) -> str:
            return ""

    a = Agent(role="alpha", goal="g", backstory="b", tools=[_SearchTool()])
    b = Agent(role="beta", goal="g", backstory="b", tools=[_OtherSearchTool()])
    t = Task(description="x", agent=a, expected_output="x")
    crew = Crew(agents=[a, b], tasks=[t], process=Process.sequential)
    session = bind(crew)
    tool_names = {t.name for t in session.composition.tools}
    assert "alpha.search" in tool_names
    assert "beta.search" in tool_names


# ── 5: task.context emits dependency edges ─────────────────────────


def test_bind_task_context_emits_explicit_dependency_edges():
    r, w = _researcher(), _writer()
    t1 = Task(description="Research", agent=r, expected_output="notes")
    t2 = Task(
        description="Write summary",
        agent=w,
        expected_output="summary",
        context=[t1],
    )
    # NOTE: process=sequential already emits this edge from the linear
    # ordering. We isolate context by using process=hierarchical with
    # manager but no manager tools — so only context-derived edges
    # remain.
    manager = Agent(
        role="lead",
        goal="lead",
        backstory="lead",
        allow_delegation=False,
    )
    crew = Crew(
        agents=[r, w],
        tasks=[t1, t2],
        process=Process.hierarchical,
        manager_agent=manager,
    )
    session = bind(crew)
    edge_pairs = {(e.from_tool, e.to_tool) for e in session.composition.edges}
    # context=[t1] explicitly says t1's tools feed t2.
    assert ("researcher.search", "writer.summarize") in edge_pairs


# ── 6: LOAD-BEARING property test ──────────────────────────────────


@pytest.mark.parametrize("seed", range(50))
def test_bind_order_independence(seed: int):
    """bind() must produce the same composition_hash regardless of the
    order in which agents/tasks were added. Mirrors the LangGraph
    adapter's load-bearing property test."""
    rng = random.Random(seed)
    n = rng.randint(2, 4)
    roles = ["alpha", "beta", "gamma", "delta"][:n]

    class _Inp(BaseModel):
        query: str = Field(description="x")

    def _make_tool(role_idx: int):
        class _T(BaseTool):
            name: str = f"tool_{role_idx}"
            description: str = "tool"
            args_schema: type = _Inp

            def _run(self, query: str) -> str:
                return ""
        return _T()

    agents = [
        Agent(role=r, goal="g", backstory="b", tools=[_make_tool(i)])
        for i, r in enumerate(roles)
    ]
    tasks = [
        Task(description=f"t{i}", agent=agents[i], expected_output="o")
        for i in range(n)
    ]

    def build(agent_order: list[Agent], task_order: list[Task]) -> Crew:
        return Crew(
            agents=agent_order,
            tasks=task_order,
            process=Process.sequential,
        )

    a_a = list(agents)
    a_b = list(agents)
    rng.shuffle(a_b)
    t_a = list(tasks)
    t_b = list(tasks)  # task order has semantic effect on sequential edges,
                      # so don't shuffle: only agent insertion order varies.

    s_a = bind(build(a_a, t_a))
    s_b = bind(build(a_b, t_b))
    assert s_a.composition.canonical_hash() == s_b.composition.canonical_hash(), (
        f"seed={seed}: composition_hash diverged across agent orderings"
    )


# ── 7: output_schemas kwarg ────────────────────────────────────────


def test_bind_uses_output_schemas_kwarg():
    r = _researcher()
    t = Task(description="Research", agent=r, expected_output="notes")
    crew = Crew(agents=[r], tasks=[t], process=Process.sequential)

    session = bind(
        crew,
        output_schemas={
            "researcher.search": {
                "type": "object",
                "properties": {"results_url": {"type": "string"}},
            },
        },
    )
    spec = session.composition.tools[0]
    assert "results_url" in spec.internal_state


# ── 8: warning on tool without args_schema ─────────────────────────


def test_bind_warns_on_unschematized_tool():
    """A tool with no args_schema and no output_schemas entry triggers
    a warning."""

    class _Bare(BaseTool):
        name: str = "bare"
        description: str = "no args schema"

        def _run(self) -> str:
            return ""

    a = Agent(role="alpha", goal="g", backstory="b", tools=[_Bare()])
    t = Task(description="x", agent=a, expected_output="x")
    crew = Crew(agents=[a], tasks=[t], process=Process.sequential)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = bind(crew)
    msgs = [str(w.message) for w in caught]
    assert any("alpha.bare" in m and "output_schemas" in m for m in msgs), (
        f"expected a warning naming the bare tool; got: {msgs}"
    )


# ── 9–10: callback records invocations ─────────────────────────────


def test_callback_records_step_invocations():
    session = bulla.Session()
    handler = BullaCrewCallback(session)
    step = type("Step", (), {"tool": "search", "log": "running search"})()
    handler.on_step(step)
    assert len(handler.invocations) == 1
    assert handler.invocations[0]["kind"] == "step"
    assert handler.invocations[0]["tool"] == "search"


def test_callback_records_task_completions():
    session = bulla.Session()
    handler = BullaCrewCallback(session)
    r = _researcher()
    t = Task(description="research", agent=r, expected_output="x")
    handler.on_task_complete(t)
    assert len(handler.invocations) == 1
    assert handler.invocations[0]["kind"] == "task_complete"
    assert handler.invocations[0]["agent"] == "researcher"


# ── 11: chained checkpoints ────────────────────────────────────────


def test_callback_chains_receipts_per_step():
    session = bulla.Session()
    handler = BullaCrewCallback(session, emit_checkpoint_per_step=True)
    chain_before = len(session.receipt_chain)
    step1 = type("Step", (), {"tool": "a", "log": "1"})()
    step2 = type("Step", (), {"tool": "b", "log": "2"})()
    handler.on_step(step1)
    handler.on_step(step2)
    chain_after = len(session.receipt_chain)
    assert chain_after - chain_before == 2


# ── 12: finalize() emits terminal_receipt ──────────────────────────


def test_callback_finalize_emits_terminal_receipt():
    session = bulla.Session()
    session.add_tool(bulla.ToolSpec("a", ("x",), ("x",)))
    handler = BullaCrewCallback(session)
    receipt = handler.finalize()
    assert receipt is not None
    assert isinstance(receipt, WitnessReceipt)
    assert handler.terminal_receipt is receipt
    assert receipt.disposition in {
        Disposition.PROCEED, Disposition.PROCEED_WITH_BRIDGE
    }
