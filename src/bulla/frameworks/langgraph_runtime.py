"""Live-object runtime adapter for LangGraph.

The static adapter at ``bulla/frameworks/langgraph.py`` parses ``.py``
source files via ``ast`` without importing langgraph. This runtime
adapter takes a live ``langgraph.graph.StateGraph`` instance and
snapshots it into a ``bulla.Session``.

Two public entry points (re-exported via the ``bulla.langgraph`` shim):

  - ``bind(graph)`` — pre-execution snapshot. Walks
    ``graph.nodes / edges / branches / channels`` and builds a Session
    whose composition_hash is deterministic with respect to insertion
    order. This is the load-bearing prescriptive surface: an agent
    framework gets the fee + bridge requirements *before* anything
    runs.
  - ``BullaCallbackHandler`` — execution observer. A
    ``langchain_core.callbacks.BaseCallbackHandler`` subclass that
    records actual tool invocations into the Session's receipt chain
    during ``.invoke()`` / ``.stream()``.

Imports of ``langgraph`` and ``langchain_core`` are lazy (inside
function bodies). The bulla package itself stays import-clean for
users without the ``bulla[langgraph]`` extra.

Output-schema gap: ``BaseTool.args_schema`` covers inputs; LangChain
has no standardized output schema. The integration accepts a
``output_schemas={tool_name: jsonschema_dict}`` kwarg on ``bind()``
and falls back to input-only when not supplied. A one-line warning is
logged per unschematized node so the user sees the gap.
"""

from __future__ import annotations

import warnings
from typing import Any, Iterable, Literal

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
    graph: Any,
    *,
    name: str | None = None,
    policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
    output_schemas: dict[str, dict] | None = None,
    on_unknown_branch: Literal["fan_out", "skip"] = "fan_out",
) -> Session:
    """Snapshot a live ``StateGraph`` (compiled or not) into a Session.

    The function never mutates ``graph`` and never imports langgraph at
    module load — only when called.

    Args:
        graph: A ``langgraph.graph.StateGraph`` instance. Both
            uncompiled (`graph.compiled is False`) and compiled forms
            are accepted; we read the same attributes either way.
        name: Optional Session name. Defaults to ``"langgraph"``.
        policy: PolicyProfile for the Session. Defaults to bulla's
            default profile.
        output_schemas: Optional mapping of ``tool_name`` →
            JSONSchema dict for output fields. LangChain BaseTool has
            no standardized output schema; supply this when you want
            output-side fee detection.
        on_unknown_branch: How to handle ``add_conditional_edges``
            calls without a ``path_map``. ``"fan_out"`` (default) treats
            them as edges to every node — what LangGraph itself does
            at runtime — and is conservative for fee math.
            ``"skip"`` records nothing.

    Returns:
        A ``bulla.Session`` populated with one ToolSpec per node and
        one Edge per declared / discovered link. ``session.fee`` is
        immediately available; ``session.diagnose()`` produces a full
        WitnessReceipt covering the entire graph.

    Raises:
        FrameworkError: when ``graph`` lacks the expected attributes
            (likely a LangGraph version drift).
    """
    if on_unknown_branch not in ("fan_out", "skip"):
        raise ValueError(
            f"on_unknown_branch must be 'fan_out' or 'skip', "
            f"got {on_unknown_branch!r}"
        )

    nodes_attr = getattr(graph, "nodes", None)
    edges_attr = getattr(graph, "edges", None)
    if nodes_attr is None or edges_attr is None:
        raise FrameworkError(
            "Object passed to bulla.langgraph.bind() lacks .nodes or "
            ".edges attributes. Expected a langgraph.graph.StateGraph "
            "instance; got {!r}.".format(type(graph).__name__)
        )

    output_schemas = dict(output_schemas or {})

    # Build ToolSpecs in sorted order by node name. Determinism is the
    # load-bearing property — bind(g_a) and bind(g_b) on graphs built
    # in different orders must produce identical composition_hashes.
    node_names = sorted(nodes_attr.keys())
    specs: list[ToolSpec] = []
    spec_by_node: dict[str, ToolSpec] = {}
    for node_name in node_names:
        spec = _node_to_toolspec(
            node_name, nodes_attr[node_name], graph, output_schemas
        )
        specs.append(spec)
        spec_by_node[node_name] = spec

    # Edges: collect explicit edges + branches, dedupe, sort.
    edge_keys: set[tuple[str, str]] = set()
    for from_node, to_node in edges_attr:
        if not isinstance(from_node, str) or not isinstance(to_node, str):
            continue  # langgraph's START / END sentinels — skip
        edge_keys.add((from_node, to_node))

    # Branches (conditional edges).
    branches_attr = getattr(graph, "branches", None)
    if isinstance(branches_attr, dict):
        for source, branch_dict in branches_attr.items():
            if not isinstance(source, str) or not isinstance(branch_dict, dict):
                continue
            for branch_name, branch_spec in branch_dict.items():
                targets = _branch_targets(branch_spec, node_names, on_unknown_branch)
                for tgt in targets:
                    if isinstance(tgt, str):
                        edge_keys.add((source, tgt))

    # Sort edges deterministically by (from, to).
    sorted_edge_keys = sorted(edge_keys)
    edges: list[Edge] = []
    for from_node, to_node in sorted_edge_keys:
        if from_node not in spec_by_node or to_node not in spec_by_node:
            # Edge endpoints not in nodes (likely START/END sentinels).
            continue
        dims = shared_field_dimensions(
            spec_by_node[from_node], spec_by_node[to_node]
        )
        if not dims:
            # No shared fields — the edge still exists but the
            # composition's δ₀ row would be all zeros. Skip rather than
            # emit a degenerate Edge with empty dimensions, which the
            # coboundary builder treats specially.
            continue
        edges.append(
            Edge(from_tool=from_node, to_tool=to_node, dimensions=dims)
        )

    session = Session(name=name or "langgraph", policy=policy)
    if specs:
        session.add_tools_and_edges(tools=specs, edges=edges)
    return session


def _node_to_toolspec(
    node_name: str,
    node_spec: Any,
    graph: Any,
    output_schemas: dict[str, dict],
) -> ToolSpec:
    """Build a ToolSpec for one StateGraph node.

    Strategy:
      1. If the node's runnable is a ToolNode, walk ``tools_by_name``.
         For each BaseTool, take ``args_schema.model_json_schema()`` for
         inputs and the user-supplied output schema (if any).
      2. Else, fall back to the graph's channel set as internal_state
         with empty observable_schema. Emit a warning naming the node.
    """
    runnable = (
        getattr(node_spec, "runnable", None)
        or getattr(node_spec, "node", None)
        or node_spec  # last-ditch — some node specs are themselves the runnable
    )

    # ToolNode detection — lazy import to avoid a hard langgraph dep.
    try:
        from langgraph.prebuilt import ToolNode as _ToolNode  # type: ignore
    except ImportError:
        _ToolNode = None  # type: ignore

    fields: list[str] = []
    observable: list[str] = []

    if _ToolNode is not None and isinstance(runnable, _ToolNode):
        tools_by_name = getattr(runnable, "tools_by_name", {})
        for tool_name, tool_obj in sorted(tools_by_name.items()):
            input_fields = tool_input_fields(tool_obj)
            output_fields = output_fields_from_kwarg(
                tool_name, output_schemas
            )
            # Namespace input fields with the tool name so two tools
            # in the same node don't collide on field names.
            for f in input_fields:
                qf = f if len(tools_by_name) == 1 else f"{tool_name}.{f}"
                fields.append(qf)
                observable.append(qf)
            for f in output_fields:
                qf = f if len(tools_by_name) == 1 else f"{tool_name}.{f}"
                fields.append(qf)
                # Output fields are observable by definition (consumers
                # see them via the node's state update).
                observable.append(qf)
    else:
        # Non-ToolNode runnable: best-effort use channel names.
        channels_attr = getattr(graph, "channels", None)
        if isinstance(channels_attr, dict):
            fields = sorted(channels_attr.keys())
        if not output_schemas.get(node_name):
            warnings.warn(
                f"bulla.langgraph.bind: node {node_name!r} runnable is not a "
                "ToolNode; falling back to channel names for internal_state. "
                "Pass output_schemas={" + repr(node_name) + ": {...}} to "
                "supply explicit field hints for output-side fee detection.",
                UserWarning,
                stacklevel=3,
            )
        # No output_schemas case — observable defaults to empty.

    # Dedupe while preserving order. Sort observables for determinism.
    seen: set[str] = set()
    deduped: list[str] = []
    for f in fields:
        if f in seen:
            continue
        seen.add(f)
        deduped.append(f)
    return ToolSpec(
        name=node_name,
        internal_state=tuple(sorted(deduped)),
        observable_schema=tuple(sorted(set(observable))),
    )


def _branch_targets(
    branch_spec: Any,
    all_node_names: list[str],
    on_unknown_branch: Literal["fan_out", "skip"],
) -> Iterable[str]:
    """Resolve the destination set for one BranchSpec.

    LangGraph's BranchSpec carries an optional ``path_map`` (or
    ``ends`` in some versions) listing the explicit target nodes. When
    absent, LangGraph itself fans out to every node at runtime; we
    mirror that conservatively unless the caller asks us to skip.
    """
    # Try common attribute names across LangGraph versions.
    for attr in ("path_map", "ends"):
        path_map = getattr(branch_spec, attr, None)
        if path_map is None:
            continue
        if isinstance(path_map, dict):
            return list(path_map.values())
        if isinstance(path_map, (list, tuple, set)):
            return list(path_map)
    # No explicit map: fall back per policy.
    if on_unknown_branch == "skip":
        return []
    return list(all_node_names)


# ── BullaCallbackHandler ────────────────────────────────────────────


def _import_base_callback_handler() -> type:
    """Lazy import to keep bulla import-clean without the langgraph extra."""
    try:
        from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise FrameworkError(
            "BullaCallbackHandler requires langchain-core. "
            "Install via `pip install bulla[langgraph]`."
        ) from e
    return BaseCallbackHandler


class _BullaCallbackHandlerImpl:
    """Implementation body. Wrapped by ``BullaCallbackHandler`` after the
    BaseCallbackHandler base class is resolved at first instantiation.

    Thread-safety: ``self._open_calls`` is a plain dict. LangGraph's
    callback dispatch is sequential within a single ``invoke()`` /
    ``stream()`` call, so this is safe for normal use. If you share a
    single handler across multiple concurrent graph runs, wrap each
    handler method in a lock or use one handler per run.
    """

    def __init__(
        self,
        session: Session,
        *,
        emit_checkpoint_per_call: bool = False,
    ) -> None:
        self.session = session
        self.emit_checkpoint_per_call = emit_checkpoint_per_call
        self.invocations: list[dict[str, Any]] = []
        self.terminal_receipt = None
        self._open_calls: dict[Any, dict[str, Any]] = {}

    # ── BaseCallbackHandler API surface ─────────────────────────────

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        import datetime as _dt
        tool_name = (serialized or {}).get("name") or "unknown_tool"
        self._open_calls[run_id] = {
            "tool": tool_name,
            "ts_start": _dt.datetime.now(_dt.timezone.utc),
            "input_str": input_str,
        }

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        import datetime as _dt
        opened = self._open_calls.pop(run_id, None)
        if opened is None:
            return
        ts_end = _dt.datetime.now(_dt.timezone.utc)
        duration_ms = int(
            (ts_end - opened["ts_start"]).total_seconds() * 1000
        )
        record = {
            "run_id": str(run_id),
            "tool": opened["tool"],
            "ts_start": opened["ts_start"].isoformat(),
            "ts_end": ts_end.isoformat(),
            "duration_ms": duration_ms,
        }
        self.invocations.append(record)
        if self.emit_checkpoint_per_call:
            self.session.checkpoint()

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # Record the error as a failed invocation but don't checkpoint —
        # fee math doesn't care about runtime exceptions.
        opened = self._open_calls.pop(run_id, None)
        if opened is None:
            return
        self.invocations.append({
            "run_id": str(run_id),
            "tool": opened["tool"],
            "error": str(error),
        })

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: Any,
        parent_run_id: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # Only the OUTERMOST chain's end signals graph completion.
        if parent_run_id is not None:
            return
        self.terminal_receipt = self.session.diagnose()


def _build_callback_handler_class() -> type:
    """Construct ``BullaCallbackHandler`` as a real BaseCallbackHandler
    subclass at first reference. Defers the langchain_core import until
    the user actually instantiates the handler — keeping bulla.langgraph
    importable even when langchain-core isn't installed (so static
    inspection of the module shape still works)."""
    base = _import_base_callback_handler()
    # The implementation body is a non-subclass class for testability;
    # we splice its methods into a real subclass here.
    cls = type(
        "_BullaCallbackHandlerReal",
        (base,),
        {
            "__init__": _BullaCallbackHandlerImpl.__init__,
            "on_tool_start": _BullaCallbackHandlerImpl.on_tool_start,
            "on_tool_end": _BullaCallbackHandlerImpl.on_tool_end,
            "on_tool_error": _BullaCallbackHandlerImpl.on_tool_error,
            "on_chain_end": _BullaCallbackHandlerImpl.on_chain_end,
            "__doc__": (
                "LangChain BaseCallbackHandler that records tool invocations "
                "into a bulla.Session's receipt chain. Attach via "
                "graph.invoke(input, config={'callbacks': [handler]})."
            ),
        },
    )
    return cls


class _BullaCallbackHandlerMeta(type):
    """Metaclass that makes ``isinstance(handler, BullaCallbackHandler)``
    return True for instances of the dynamically-built real subclass.

    The real subclass is constructed lazily inside ``__new__`` so that
    ``import bulla.langgraph`` doesn't pull in ``langchain_core`` —
    keeping the optional-dep boundary visible. Without this metaclass,
    user code doing ``isinstance(h, BullaCallbackHandler)`` after
    instantiation would silently return False because the instance's
    actual class is the dynamically-built one.
    """

    def __instancecheck__(cls, instance: Any) -> bool:
        real = cls.__dict__.get("_real_cls")
        if real is not None and isinstance(instance, real):
            return True
        return super().__instancecheck__(instance)


class BullaCallbackHandler(metaclass=_BullaCallbackHandlerMeta):
    """Public façade. Constructs the real subclass on first __new__.

    The lazy-subclass dance keeps ``import bulla.langgraph`` clean for
    users without ``langchain-core`` installed (they hit a clear
    FrameworkError only when they actually try to instantiate).
    ``isinstance(handler, BullaCallbackHandler)`` works correctly via
    the metaclass __instancecheck__ override.
    """

    _real_cls: type | None = None

    def __new__(cls, *args: Any, **kwargs: Any):
        if cls._real_cls is None:
            cls._real_cls = _build_callback_handler_class()
        return cls._real_cls(*args, **kwargs)


__all__ = [
    "BullaCallbackHandler",
    "bind",
]
