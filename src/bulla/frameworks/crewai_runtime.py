"""Live-object runtime adapter for CrewAI.

Sibling to ``bulla.frameworks.langgraph_runtime``. Walks a live
``crewai.Crew`` instance and snapshots its agents/tasks/tools into a
``bulla.Session``.

Two public entry points (re-exported via the ``bulla.crewai`` shim):

  - ``bind(crew)`` — pre-execution snapshot.
  - ``BullaCrewCallback`` — execution observer wired into CrewAI's
    ``step_callback`` / ``task_callback`` plumbing.

Imports of ``crewai`` are lazy. The bulla package itself stays
import-clean for users without the ``bulla[crewai]`` extra.

CrewAI shape (verified against crewai>=0.80):
  - ``Crew(agents=[Agent], tasks=[Task], process=Process.sequential, ...)``
  - Each ``Agent`` has ``role: str`` (required, unique by convention)
    and ``tools: list[BaseTool]``.
  - Each ``Task`` has ``description: str``, optional ``agent``, optional
    ``tools`` (overrides agent's), and optional ``context: list[Task]``
    listing prior tasks whose output is consumed.
  - ``Process.sequential`` runs tasks in list order; ``Process.hierarchical``
    delegates through ``Crew.manager_agent``.

Edge inference:
  - Sequential: edges between consecutive tasks' tools.
  - Task.context: explicit dependency edges from each context-task's tools
    to the dependent task's tools.
  - Hierarchical: manager-agent's tools edge to every worker tool.
  - Tool names are namespaced as ``{agent.role}.{tool.name}`` so two
    agents sharing a tool name (e.g. both have a ``search`` tool) are
    distinguished in the composition.
"""

from __future__ import annotations

import warnings
from typing import Any, Iterable

from bulla.frameworks import FrameworkError
from bulla.frameworks._runtime_common import (
    output_fields_from_kwarg,
    shared_field_dimensions,
    tool_input_fields,
)
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    Edge,
    PolicyProfile,
    ToolSpec,
)
from bulla.session import Session


# ── bind() ──────────────────────────────────────────────────────────


def bind(
    crew: Any,
    *,
    name: str | None = None,
    policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
    output_schemas: dict[str, dict] | None = None,
) -> Session:
    """Snapshot a live ``crewai.Crew`` into a Session.

    Args:
        crew: A ``crewai.Crew`` instance with at least one agent or
            one task.
        name: Optional Session name. Defaults to ``"crewai"``.
        policy: PolicyProfile for the Session.
        output_schemas: Optional mapping of namespaced tool name
            (``"{role}.{tool_name}"``) to a JSONSchema dict for output
            fields. CrewAI ``BaseTool`` carries no standardized output
            schema.

    Returns:
        A populated ``bulla.Session``.

    Raises:
        FrameworkError: when ``crew`` lacks the expected attributes.
    """
    agents_attr = getattr(crew, "agents", None)
    tasks_attr = getattr(crew, "tasks", None)
    if agents_attr is None or tasks_attr is None:
        raise FrameworkError(
            "Object passed to bulla.crewai.bind() lacks .agents or "
            ".tasks attributes. Expected a crewai.Crew instance; got "
            "{!r}.".format(type(crew).__name__)
        )

    output_schemas = dict(output_schemas or {})

    # Build a ToolSpec per (agent_role, tool) pair. A single agent
    # contributes one ToolSpec per tool it carries. Sort by namespaced
    # name for determinism.
    spec_by_key: dict[str, ToolSpec] = {}

    # Track which tools each agent has, by namespaced key.
    agent_tools: dict[str, list[str]] = {}  # agent_role -> [namespaced tool names]
    for agent in agents_attr:
        role = _agent_role(agent)
        if role is None:
            continue
        agent_tools[role] = []
        for tool_obj in (getattr(agent, "tools", None) or []):
            key = _namespaced_tool_name(role, tool_obj)
            spec = _tool_to_toolspec(key, tool_obj, output_schemas)
            spec_by_key[key] = spec
            agent_tools[role].append(key)

    # Tasks may declare their own tools (overriding the agent's). Add
    # those as additional ToolSpecs keyed under the task's agent's role.
    task_tools: dict[int, list[str]] = {}  # task index -> [namespaced tool keys]
    for idx, task in enumerate(tasks_attr):
        role = _task_agent_role(task)
        if role is None:
            role = "<unassigned>"
        # Combine task.tools (preferred) with agent.tools when no
        # task-level tools are present.
        explicit_task_tools = getattr(task, "tools", None) or []
        if explicit_task_tools:
            keys: list[str] = []
            for tool_obj in explicit_task_tools:
                key = _namespaced_tool_name(role, tool_obj)
                if key not in spec_by_key:
                    spec_by_key[key] = _tool_to_toolspec(
                        key, tool_obj, output_schemas
                    )
                keys.append(key)
            task_tools[idx] = keys
        else:
            task_tools[idx] = list(agent_tools.get(role, []))

    # Build edges:
    #  1. Sequential mode: edges between consecutive tasks' tools.
    #  2. Task.context: explicit dependencies (one edge per pair of
    #     context-task tool × dependent-task tool).
    #  3. Hierarchical: manager_agent's tools edge to every other tool.
    process = _process_value(crew)
    edge_keys: set[tuple[str, str]] = set()

    if process == "sequential":
        for i in range(len(tasks_attr) - 1):
            for src_key in task_tools.get(i, []):
                for dst_key in task_tools.get(i + 1, []):
                    if src_key != dst_key:
                        edge_keys.add((src_key, dst_key))

    elif process == "hierarchical":
        manager = _hierarchical_manager(crew)
        manager_keys: list[str] = []
        if manager is not None:
            manager_role = _agent_role(manager) or "<manager>"
            for tool_obj in (getattr(manager, "tools", None) or []):
                key = _namespaced_tool_name(manager_role, tool_obj)
                if key not in spec_by_key:
                    spec_by_key[key] = _tool_to_toolspec(
                        key, tool_obj, output_schemas
                    )
                manager_keys.append(key)
        all_worker_keys = [
            k for k in spec_by_key if k not in manager_keys
        ]
        for src in manager_keys:
            for dst in all_worker_keys:
                edge_keys.add((src, dst))

    # 2. Explicit task.context dependencies (apply in BOTH process modes).
    for idx, task in enumerate(tasks_attr):
        ctx = getattr(task, "context", None)
        # CrewAI uses a `_NotSpecified` sentinel as the default for
        # `context` rather than None — it is not iterable. Coerce
        # everything that isn't a real list into an empty iterable.
        if not isinstance(ctx, (list, tuple)):
            continue
        for ctx_task in ctx:
            try:
                ctx_idx = list(tasks_attr).index(ctx_task)
            except ValueError:
                continue
            for src_key in task_tools.get(ctx_idx, []):
                for dst_key in task_tools.get(idx, []):
                    if src_key != dst_key:
                        edge_keys.add((src_key, dst_key))

    # Materialize edges with shared-field dimensions.
    sorted_specs = sorted(spec_by_key.values(), key=lambda s: s.name)
    sorted_edge_keys = sorted(edge_keys)
    edges: list[Edge] = []
    for src, dst in sorted_edge_keys:
        if src not in spec_by_key or dst not in spec_by_key:
            continue
        dims = shared_field_dimensions(spec_by_key[src], spec_by_key[dst])
        if not dims:
            continue
        edges.append(Edge(from_tool=src, to_tool=dst, dimensions=dims))

    session = Session(name=name or "crewai", policy=policy)
    if sorted_specs:
        session.add_tools_and_edges(tools=sorted_specs, edges=edges)
    return session


def _agent_role(agent: Any) -> str | None:
    role = getattr(agent, "role", None)
    return role if isinstance(role, str) and role else None


def _task_agent_role(task: Any) -> str | None:
    agent = getattr(task, "agent", None)
    if agent is None:
        return None
    return _agent_role(agent)


def _process_value(crew: Any) -> str:
    """Resolve `crew.process` to a string ("sequential" / "hierarchical")."""
    proc = getattr(crew, "process", None)
    if proc is None:
        return "sequential"
    val = getattr(proc, "value", None) or str(proc)
    if "hierarchical" in val.lower():
        return "hierarchical"
    return "sequential"


def _hierarchical_manager(crew: Any) -> Any | None:
    return (
        getattr(crew, "manager_agent", None)
        or getattr(crew, "manager_llm", None)
    )


def _namespaced_tool_name(agent_role: str, tool_obj: Any) -> str:
    base = (
        getattr(tool_obj, "name", None)
        or getattr(tool_obj, "__name__", None)
        or type(tool_obj).__name__
    )
    return f"{agent_role}.{base}"


def _tool_to_toolspec(
    key: str, tool_obj: Any, output_schemas: dict[str, dict]
) -> ToolSpec:
    """Build a ToolSpec from a CrewAI BaseTool (or any tool-like object
    exposing ``args_schema`` or ``args``)."""
    input_fields = tool_input_fields(tool_obj)
    output_fields = output_fields_from_kwarg(key, output_schemas)
    fields = list(dict.fromkeys(list(input_fields) + list(output_fields)))
    if not fields and key not in output_schemas:
        warnings.warn(
            f"bulla.crewai.bind: tool {key!r} has no resolvable args_schema. "
            "Pass output_schemas={" + repr(key) + ": {...}} to supply "
            "explicit field hints.",
            UserWarning,
            stacklevel=3,
        )
    observable = list(fields)  # default: all fields observable
    return ToolSpec(
        name=key,
        internal_state=tuple(sorted(set(fields))),
        observable_schema=tuple(sorted(set(observable))),
    )


# ── BullaCrewCallback ──────────────────────────────────────────────


class BullaCrewCallback:
    """Records CrewAI execution events into a Session's receipt chain.

    Wired in via ``Crew(step_callback=handler.on_step,
    task_callback=handler.on_task_complete)`` or pass the methods
    directly to ``Crew.kickoff(callbacks=[...])`` if the version
    supports it.

    The handler stores invocations on ``self.invocations`` and emits
    a terminal receipt via ``handler.finalize()`` after kickoff
    completes. CrewAI does not have a single chain-end signal as
    rich as LangChain's; ``finalize()`` is explicit.
    """

    def __init__(
        self,
        session: Session,
        *,
        emit_checkpoint_per_step: bool = False,
    ) -> None:
        self.session = session
        self.emit_checkpoint_per_step = emit_checkpoint_per_step
        self.invocations: list[dict[str, Any]] = []
        self.terminal_receipt = None

    def on_step(self, step: Any) -> None:
        """CrewAI ``step_callback`` entry point. Called after each
        agent step. ``step`` is a CrewAI internal record; we extract
        what we can defensively."""
        record = {
            "kind": "step",
            "tool": (
                getattr(step, "tool", None)
                or getattr(step, "action", None)
                or "unknown"
            ),
            "log": str(getattr(step, "log", ""))[:500],
        }
        self.invocations.append(record)
        if self.emit_checkpoint_per_step:
            self.session.checkpoint()

    def on_task_complete(self, task: Any) -> None:
        """CrewAI ``task_callback`` entry point. Called after each task
        completes. Records the task summary."""
        record = {
            "kind": "task_complete",
            "task": (
                getattr(task, "description", None)
                or str(task)
            )[:500],
            "agent": _agent_role(getattr(task, "agent", None) or object())
            or "unknown",
        }
        self.invocations.append(record)
        if self.emit_checkpoint_per_step:
            self.session.checkpoint()

    def finalize(self):
        """Emit the terminal receipt covering the entire kickoff. Call
        this after ``crew.kickoff()`` returns."""
        self.terminal_receipt = self.session.diagnose()
        return self.terminal_receipt


__all__ = [
    "BullaCrewCallback",
    "bind",
]
