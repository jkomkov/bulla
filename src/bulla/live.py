"""bulla.LiveSession — online composition proxy with incremental fee tracking.

Combines Session (incremental composition building) with BullaProxySession
(call tracing) into a single object for agent frameworks that discover
MCP servers dynamically.

Lifecycle:
  1. Create a LiveSession
  2. add_server() as servers come online — get delta_fee per addition
  3. record_call() to trace tool invocations — get flow conflict detection
  4. translate() for runtime value translation
  5. diagnose() for the authoritative composition receipt

The Session tracks composition evolution (fee, blind spots, receipt chain).
The proxy tracks runtime behavior (calls, flows, conflicts).
Both are accessible through the LiveSession's unified API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bulla.bridges import TranslationResult
from bulla.guard import BullaGuard
from bulla.model import (
    Composition,
    DEFAULT_POLICY_PROFILE,
    Edge,
    PolicyProfile,
    WitnessReceipt,
)
from bulla.proxy import (
    BullaProxySession,
    FlowReference,
    ProxyCallRecord,
)
from bulla.session import AddToolResult, Session


@dataclass(frozen=True)
class AddServerResult:
    """Outcome of registering a server with a LiveSession.

    ``delta_fee`` is the signed change to the composition's coherence
    fee caused by this server's tools and the cross-server edges they
    introduce.  A server whose tools share no dimensions with existing
    tools contributes ``delta_fee == 0``.
    """

    server: str
    delta_fee: int
    fee_after: int
    new_tools: tuple[str, ...]
    new_edges: int
    new_blind_spots: tuple[tuple[str, str], ...]


class LiveSession:
    """Online composition proxy — add servers, trace calls, see fee deltas.

    Usage::

        live = bulla.LiveSession(name="checkout")

        # Phase 1: build the composition incrementally
        r1 = live.add_server("stripe", stripe_tools)
        r2 = live.add_server("quickbooks", qb_tools)
        # r2.delta_fee shows the cross-server seam cost

        # Phase 2: trace calls through the composition
        call1 = live.record_call("stripe", "create_charge",
            arguments={"currency": "usd"},
            result={"id": "ch_123"})
        call2 = live.record_call("quickbooks", "create_invoice",
            arguments={"currency": "USD"},
            argument_sources={
                "currency": live.make_ref(call1.call_id, "currency")
            })
        # call2.flows detects the convention mismatch

        # Phase 3: get the authoritative receipt
        receipt = live.diagnose()

    Mathematical invariant: ``live.fee`` after all ``add_server`` calls
    equals ``compose_multi(all_server_tools).diagnostic.coherence_fee``.
    This is validated by ``test_live_session.py``.
    """

    def __init__(
        self,
        *,
        policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
        name: str = "live",
    ) -> None:
        self._session = Session(policy=policy, name=name)
        self._server_tools: dict[str, list[dict[str, Any]]] = {}
        self._proxy: BullaProxySession | None = None
        self._known_tool_names: set[str] = set()
        self._known_edge_keys: set[tuple[str, str, tuple[str, ...]]] = set()

    # ── State accessors ───────────────────────────────────────────

    @property
    def fee(self) -> int:
        """Current coherence fee across all registered servers."""
        return self._session.fee

    @property
    def composition(self) -> Composition:
        """Current composition (tools + edges) as a frozen object."""
        return self._session.composition

    @property
    def servers(self) -> tuple[str, ...]:
        """Server names registered so far, in insertion order."""
        return tuple(self._server_tools.keys())

    @property
    def receipt_chain(self) -> tuple[str, ...]:
        """Receipt hashes emitted so far (checkpoints + translations)."""
        return self._session.receipt_chain

    @property
    def hidden_basis(self) -> list[tuple[str, str]]:
        """Current hidden fields contributing to the fee."""
        return self._session.hidden_basis

    @property
    def proxy(self) -> BullaProxySession | None:
        """The underlying proxy, if call tracing has been initiated."""
        return self._proxy

    @property
    def session(self) -> Session:
        """The underlying Session for advanced incremental queries."""
        return self._session

    # ── Server registration ───────────────────────────────────────

    def add_server(
        self,
        server: str,
        tools: list[dict[str, Any]],
    ) -> AddServerResult:
        """Register a server's tools and return the fee delta.

        Runs the full classification + edge-discovery pipeline on
        all accumulated servers, diffs against the existing composition
        to find new tools and cross-server edges, then feeds the diff
        into the Session for incremental fee tracking.

        Raises ``ValueError`` if the server name is already registered
        or the tools list is empty.
        """
        if server in self._server_tools:
            raise ValueError(
                f"server {server!r} already registered; "
                f"create a new LiveSession to recompose"
            )
        if not tools:
            raise ValueError(f"server {server!r} has no tools")

        # Store a copy
        self._server_tools[server] = [dict(t) for t in tools]

        # Run guard pipeline on the full accumulated tool set
        all_prefixed = self._all_prefixed_tools()
        guard = BullaGuard.from_tools_list(
            all_prefixed, name=self._session.name
        )
        comp = guard.composition

        # Diff: find tools and edges not yet in the Session
        new_tools = [
            t for t in comp.tools
            if t.name not in self._known_tool_names
        ]
        new_edges = [
            e for e in comp.edges
            if _edge_key(e) not in self._known_edge_keys
        ]

        # Update tracking sets
        for t in new_tools:
            self._known_tool_names.add(t.name)
        for e in new_edges:
            self._known_edge_keys.add(_edge_key(e))

        # Feed into Session
        result = self._session.add_tools_and_edges(
            tools=new_tools or None,
            edges=new_edges or None,
        )

        # Invalidate the proxy — it needs to be rebuilt with the new
        # server set before the next record_call.
        self._proxy = None

        return AddServerResult(
            server=server,
            delta_fee=result.delta_fee,
            fee_after=result.fee_after,
            new_tools=tuple(t.name for t in new_tools),
            new_edges=len(new_edges),
            new_blind_spots=result.new_hidden_fields,
        )

    # ── Call tracing ──────────────────────────────────────────────

    def record_call(
        self,
        server: str,
        tool: str,
        *,
        arguments: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        argument_sources: dict[str, FlowReference] | None = None,
    ) -> ProxyCallRecord:
        """Record a tool call and return flow analysis.

        The proxy is lazily rebuilt when the server set changes.
        """
        proxy = self._ensure_proxy()
        return proxy.record_call(
            server, tool,
            arguments=arguments,
            result=result,
            argument_sources=argument_sources,
        )

    def make_ref(self, call_id: int, field: str) -> FlowReference:
        """Build a flow reference for use in ``argument_sources``."""
        proxy = self._ensure_proxy()
        return proxy.make_ref(call_id, field)

    def replay_trace(
        self, trace: list[dict[str, Any]]
    ) -> tuple[ProxyCallRecord, ...]:
        """Replay a serialized trace through the proxy."""
        proxy = self._ensure_proxy()
        return proxy.replay_trace(trace)

    # ── Translation ───────────────────────────────────────────────

    def translate(
        self,
        dimension: str,
        *,
        value: str,
        to_convention: str,
        from_convention: str | None = None,
    ) -> TranslationResult:
        """Translate a value across conventions, chained to the session."""
        return self._session.translate(
            dimension,
            value=value,
            to_convention=to_convention,
            from_convention=from_convention,
        )

    # ── Receipt emission ──────────────────────────────────────────

    def checkpoint(self) -> WitnessReceipt:
        """Emit a lightweight receipt for the current composition state."""
        return self._session.checkpoint()

    def diagnose(self) -> WitnessReceipt:
        """Emit the authoritative end-of-session receipt."""
        return self._session.diagnose()

    # ── Convenience constructors ──────────────────────────────────

    @classmethod
    def from_server_tools(
        cls,
        server_tools: dict[str, list[dict[str, Any]]],
        *,
        policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
        name: str = "live",
    ) -> "LiveSession":
        """Create from a server_tools dict, adding all servers at once.

        Equivalent to creating a LiveSession and calling add_server()
        for each entry, but more concise.
        """
        live = cls(policy=policy, name=name)
        for server, tools in server_tools.items():
            live.add_server(server, tools)
        return live

    # ── Internal ──────────────────────────────────────────────────

    def _ensure_proxy(self) -> BullaProxySession:
        """Rebuild the proxy if the server set has changed."""
        if self._proxy is None:
            if not self._server_tools:
                raise ValueError(
                    "no servers registered; call add_server() first"
                )
            self._proxy = BullaProxySession(
                self._server_tools,
                policy=self._session.policy,
            )
        return self._proxy

    def _all_prefixed_tools(self) -> list[dict[str, Any]]:
        """Build the full prefixed tool list for the guard pipeline."""
        all_tools: list[dict[str, Any]] = []
        for server, tools in self._server_tools.items():
            for tool in tools:
                prefixed = dict(tool)
                prefixed["name"] = (
                    f"{server}__{tool.get('name', 'unknown')}"
                )
                all_tools.append(prefixed)
        return all_tools


def _edge_key(e: Edge) -> tuple[str, str, tuple[str, ...]]:
    """Deterministic key for edge deduplication."""
    return (e.from_tool, e.to_tool, tuple(d.name for d in e.dimensions))


__all__ = [
    "AddServerResult",
    "LiveSession",
]
