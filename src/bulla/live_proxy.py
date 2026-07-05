"""Bulla live MCP proxy — agent-facing safety co-pilot.

Sits between an MCP client (Claude Code, Cursor, an agent framework) and
N backend MCP servers. Forwards JSON-RPC traffic transparently AND
injects a set of ``bulla__*`` meta-tools the agent can call to get a
mathematically-grounded safety verdict on the composition it is
building.

The product framing (revised plan, 2026-05-17): this is NOT a logger
for humans watching agents. It is a participant agents query as part
of their own reasoning. The agent asks "is this call safe?"; the proxy
answers with a formally-verified recommendation; the agent chooses.

The meta-tools, drafted in lockstep with the system-prompt fragment at
``bulla/agents/system_prompt_v1.md`` so the API surface matches what the
prompt asks agents to do:

  * ``bulla__fee``           — current witness rank (incremental)
  * ``bulla__blind_spots``   — enumerated obstruction dimensions
  * ``bulla__bridge``        — value- or schema-level repair advice
  * ``bulla__should_proceed``— ternary verdict for a pending call
  * ``bulla__why``           — Aristotle-stamped formal provenance
  * ``bulla__deed_emit``     — sign + log the current composition's deed
  * ``bulla__deed_verify``   — demand a counterparty's deed is logged
  * ``bulla__deed_lookup``   — deeds-by-composition (factual)

The diagnostic meta-tools never modify agent tool traffic. The deed tools
(``--key`` + ``--registry``, both optional) DO write a signed, non-repudiable
record: ``bulla__deed_emit`` appends to the deed registry and emits telemetry.
They do not alter the agent's calls — the agent invokes them deliberately, at a
coherence checkpoint or a trust boundary.

Transport: newline-delimited JSON-RPC 2.0 over stdio (the MCP standard).
HTTP/SSE is a future B2 stretch goal.
"""

from __future__ import annotations

import asyncio
from bulla._subproc import session_kwargs, terminate_tree_async
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bulla import __version__
from bulla.bridge_kinds import (
    BridgeAdvice,
    classify_for_call,
    summarize_verdict,
)
from bulla.diagnostic import decompose_fee_by_dimension, diagnose
from bulla.live import LiveSession


# ── Aristotle provenance table ────────────────────────────────────
#
# Mirrors the In-scope list in
# ``papers/composition-doctrine/lean/CompositionDoctrine.lean``.
# Updated when new stamps land.

ARISTOTLE_STAMPS: dict[str, dict[str, str]] = {
    "disclosure_characterization": {
        "aristotle_run": "ad67beb2-9f8e-48d6-9e5e-4cce51520afa",
        "lean_module": "CompositionDoctrine.Characterization",
        "theorem": "disclosure_characterization",
        "status": "verified_sorry_free",
        "carrier": "abstract DoctrineCarrier",
    },
    "sheaf_realization": {
        "aristotle_run": "fdf8fb06-aa2a-475a-82de-0f787b1fd5c1",
        "lean_module": "CompositionDoctrine.RealizationSheafPhase5",
        "theorem": "sheaf_realization_characterization_via_cohomology",
        "status": "verified_sorry_free",
        "carrier": "concrete cellular-sheaf SheafComplex (matches Bulla)",
    },
    "axiom_independence": {
        "aristotle_run": "e20a4d00-c052-4cb9-a913-7321e452773d",
        "lean_module": "CompositionDoctrine.AxiomIndependence",
        "theorem": "A4a_independent_of_A2_A3_A4b, A4b_independent_of_A2_A3_A4a",
        "status": "verified_sorry_free",
        "carrier": "PairComplex (toy carrier)",
    },
}

AXIOMS_USED = ("propext", "Classical.choice", "Quot.sound")
MATHLIB_PIN = "8f9d9cff6bd728b17a24e163c9402775d9e6a365"


# ── Telemetry ─────────────────────────────────────────────────────


@dataclass
class TelemetrySink:
    """Append JSON-Lines telemetry events for downstream analysis.

    Each event captures one observable moment: a tool call forwarded,
    a meta-tool consultation, an advice returned. The file is the
    primary evidence for whether agents actually use the meta-tools
    (the empirical loop the revised plan calls out).
    """

    path: Path | None
    _f: Any = None

    def open(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None

    def emit(self, event: dict[str, Any]) -> None:
        if self._f is None:
            return
        event = {**event, "ts": time.time()}
        self._f.write(json.dumps(event) + "\n")
        self._f.flush()


# ── Backend MCP servers ───────────────────────────────────────────


DEFAULT_BACKEND_CALL_TIMEOUT_S = 30.0
DEFAULT_BACKEND_INIT_TIMEOUT_S = 10.0


class BackendCallTimeout(RuntimeError):
    """Raised when a backend doesn't respond within the per-call deadline."""


@dataclass
class BackendServer:
    """One backend MCP subprocess the proxy fronts.

    Concurrency model: a single ``_reader_loop`` task per backend
    reads stdout, parses one JSON-RPC message per line, and resolves
    the waiter registered for ``msg["id"]``. Multiple in-flight
    requests are correlated by id rather than by stdout order, so
    parallel ``tools/call`` requests from the agent (LangChain async,
    Cursor's parallel tool batches) don't race on the same readline.

    ``alive`` becomes False on stdout-close or stop. A dead backend's
    tools are still listed (so agents see what they were) but
    ``tools/call`` returns a JSON-RPC error -32000.
    """

    name: str
    command: str
    env: dict[str, str] | None = None
    call_timeout_s: float = DEFAULT_BACKEND_CALL_TIMEOUT_S
    process: asyncio.subprocess.Process | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 1
    alive: bool = False
    response_waiters: dict[int, asyncio.Future] = field(default_factory=dict)
    reader_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None

    async def start(self) -> None:
        import os
        import shlex

        spawn_env = None
        if self.env:
            spawn_env = {**os.environ, **self.env}
        args = shlex.split(self.command)
        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=spawn_env,
            **session_kwargs(),
        )
        self.alive = True
        self.reader_task = asyncio.create_task(self._reader_loop())
        # Drain the backend's stderr continuously. Real MCP servers
        # log to stderr (server-filesystem, server-github, ...); when
        # the OS pipe buffer fills (~64 KB on Linux, less on macOS)
        # the backend's next write blocks and stops servicing stdin.
        # We forward each line to the proxy's stderr with a [name]
        # prefix so operators see what backends are saying without
        # mixing it into the JSON-RPC stream.
        self.stderr_task = asyncio.create_task(self._stderr_drain())

    async def _stderr_drain(self) -> None:
        """Forward backend stderr to the proxy's stderr with a prefix."""
        assert self.process is not None and self.process.stderr is not None
        prefix = f"[{self.name}] ".encode()
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    return
                try:
                    sys.stderr.buffer.write(prefix + line)
                    sys.stderr.buffer.flush()
                except (ValueError, BrokenPipeError):
                    return  # stderr closed; backend output discarded
        except asyncio.CancelledError:
            return

    async def initialize(self) -> None:
        """Send the MCP initialize handshake.

        Initialize uses a shorter timeout than `call_tool` because an
        unresponsive backend at handshake is almost always a config /
        spawn failure, not a slow tool.
        """
        await self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bulla-proxy", "version": __version__},
            },
            timeout=DEFAULT_BACKEND_INIT_TIMEOUT_S,
        )
        await self._send_notification("notifications/initialized")

    async def list_tools(self) -> list[dict[str, Any]]:
        resp = await self._send_request(
            "tools/list", {}, timeout=DEFAULT_BACKEND_INIT_TIMEOUT_S,
        )
        result = resp.get("result", {})
        tools = result.get("tools", [])
        if not tools and isinstance(result, list):
            tools = result
        self.tools = tools
        return tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError(f"backend {self.name!r} not started")
        if timeout is None:
            timeout = self.call_timeout_s
        msg_id = self.next_id
        self.next_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": msg_id,
            "params": params,
        }
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        self.response_waiters[msg_id] = waiter
        try:
            line = json.dumps(request).encode() + b"\n"
            self.process.stdin.write(line)
            await self.process.stdin.drain()
            return await asyncio.wait_for(waiter, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise BackendCallTimeout(
                f"backend {self.name!r} did not respond to "
                f"{method!r} within {timeout}s"
            ) from exc
        finally:
            self.response_waiters.pop(msg_id, None)

    async def _send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError(f"backend {self.name!r} not started")
        notif: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            notif["params"] = params
        self.process.stdin.write(json.dumps(notif).encode() + b"\n")
        await self.process.stdin.drain()

    async def _reader_loop(self) -> None:
        """Dispatch backend stdout to the matching waiter by msg id.

        Runs for the lifetime of the backend. Exits when stdout is
        closed. Failing any pending waiters with a ``RuntimeError``
        on stdout-close prevents callers from hanging on a dead
        backend.
        """
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    return
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_id = msg.get("id")
                if msg_id is None:
                    continue  # server-initiated notification, ignore
                waiter = self.response_waiters.pop(msg_id, None)
                if waiter is not None and not waiter.done():
                    waiter.set_result(msg)
        finally:
            self.alive = False
            for waiter in list(self.response_waiters.values()):
                if not waiter.done():
                    waiter.set_exception(
                        RuntimeError(f"backend {self.name!r} closed stdout")
                    )
            self.response_waiters.clear()

    async def stop(self) -> None:
        self.alive = False
        for task_name in ("reader_task", "stderr_task"):
            task = getattr(self, task_name)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                # Other exceptions surface — they're real bugs in the
                # reader / drain loops we want to see, not silent
                # shutdown noise.
                setattr(self, task_name, None)
        if self.process is None:
            return
        await terminate_tree_async(self.process)


# ── Proxy core ────────────────────────────────────────────────────


class BullaLiveProxy:
    """Stdio JSON-RPC dispatcher fronting N backend MCP servers.

    Reads from stdin (the upstream MCP client), dispatches each request
    either to a backend (``tools/call``, namespaced as ``server__tool``)
    or to an in-process meta-tool handler (``bulla__*``). Writes
    responses to stdout. Telemetry is emitted to a JSON-Lines file if
    configured.

    LiveSession is the source of truth for fee, blind spots, and
    composition state. It is updated whenever ``tools/call`` produces
    flow conflicts.
    """

    def __init__(
        self,
        backends: list[BackendServer],
        *,
        telemetry: TelemetrySink | None = None,
        signer: Any = None,
        registry: Any = None,
        enforce: bool = False,
        gate_policy: Any = None,
        trusted_root: str | None = None,
        shadow: bool = False,
        mandate: dict | None = None,
        gate_reads: bool = False,
    ) -> None:
        self.backends = {b.name: b for b in backends}
        self.telemetry = telemetry or TelemetrySink(path=None)
        self.session: LiveSession | None = None
        self._namespaced_tools: list[dict[str, Any]] = []
        self._latencies_ms: list[float] = []
        self._last_advices: list[BridgeAdvice] = []
        self._initialized = False
        self._call_seq: int = 0
        # The deed surface. `signer` (a LocalEd25519Signer) enables bulla__deed_emit;
        # `registry` (a DeedLog locally, or a read-only HttpRegistry) is the log this
        # proxy trusts — emit appends to it, verify/lookup read from it. Both optional:
        # without them the proxy still serves fee/blind_spots/bridge as before.
        self._signer = signer
        self._registry = registry
        # The recourse GATE (OBSERVE -> ENFORCE). `enforce=False` is the identity
        # transform — today's advisory behaviour, nothing refused. With `enforce=True`
        # and a registry, a cross-owner tools/call must carry a counterparty deed that
        # is authentic + included under a root we trust independently + certifies fee=0,
        # or the proxy REFUSES it (JSON-RPC -32001 with a contestable refusal cert)
        # before the backend is ever touched. `trusted_root` is the root we pin.
        self._enforce = enforce
        self._gate_policy = gate_policy
        self._gate_trusted_root = trusted_root
        # The GATEWAY surface (shadow -> enforce). `shadow=True` emits a signed
        # per-call deed — carrying the v0.2 recourse envelope — for every
        # SIDE-EFFECTING call, and never blocks (emission failures are telemetry,
        # not errors). Effect classes come from MCP ToolAnnotations where the
        # server declares them, else a conservative default: unknown ⇒ write
        # (`bulla.side_effects`). Reads are exempt from both shadow receipts and
        # the enforce gate unless `gate_reads=True`. `mandate` optionally names
        # the envelope's authority: {"principal": …, "policy": …, "delegation": […]}
        # — the delegation chain to a surviving principal.
        self._shadow = shadow
        self._mandate = mandate
        self._gate_reads = gate_reads
        self._effect_class: dict[str, str] = {}

    async def start_backends(self) -> None:
        """Spawn each backend, initialize, and list its tools.

        Rejects a backend named ``bulla`` because its namespaced tools
        (``bulla__*``) would collide with the proxy's injected
        meta-tools — the meta-tool would always win at dispatch time
        and the agent would silently lose access to the backend's
        actual tools.
        """
        for name in self.backends:
            if name == "bulla":
                raise ValueError(
                    "backend name 'bulla' collides with the proxy's "
                    "injected bulla__* meta-tool namespace; rename it "
                    "(e.g., 'bulla_app', 'bulla_backend')."
                )
        self.session = LiveSession(name="live-proxy")
        for name, backend in list(self.backends.items()):
            try:
                await backend.start()
                await backend.initialize()
                tools = await backend.list_tools()
            except Exception as exc:
                self._log_stderr(
                    f"[bulla] backend {name!r} failed to start: {exc}"
                )
                backend.alive = False
                continue
            if not tools:
                self._log_stderr(
                    f"[bulla] backend {name!r} returned 0 tools"
                )
                continue
            self.session.add_server(name, tools)
            from bulla.side_effects import classify_tool

            for t in tools:
                ns = dict(t)
                ns["name"] = f"{name}__{t.get('name', 'unknown')}"
                self._namespaced_tools.append(ns)
                self._effect_class[ns["name"]] = classify_tool(t)
        # Meta-tools first so the LLM encounters them before the
        # backend tools when reading tools/list. Position bias in
        # large tool lists is real — agents are more likely to invoke
        # what they see early.
        self._namespaced_tools = (
            _meta_tool_definitions() + self._namespaced_tools
        )
        self.telemetry.emit({
            "event": "backends_started",
            "backends": list(self.backends.keys()),
            "n_tools": len(self._namespaced_tools),
            "starting_fee": self.session.fee,
        })

    async def run(self) -> None:
        """Read JSON-RPC lines from stdin, dispatch, write responses.

        Stdin is wrapped with the public ``StreamReader`` /
        ``connect_read_pipe`` pair. Stdout is written synchronously —
        Python's ``sys.stdout`` is fine for line-rate JSON-RPC and
        avoids depending on ``asyncio.streams.FlowControlMixin``, a
        private API whose signature has moved between releases.
        """
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            line = await reader.readline()
            if not line:
                return
            start = time.perf_counter()
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                self._log_stderr(f"[bulla] malformed JSON: {exc}")
                continue
            resp = await self.dispatch(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
            latency_ms = (time.perf_counter() - start) * 1000.0
            self._latencies_ms.append(latency_ms)

    async def dispatch(
        self, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Route a single JSON-RPC message to backend or meta-tool."""
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params", {})
        if method == "initialize":
            return _ok_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "bulla-proxy",
                    "version": __version__,
                },
            })
        if method == "notifications/initialized":
            self._initialized = True
            return None
        if method == "tools/list":
            return _ok_response(msg_id, {"tools": self._namespaced_tools})
        if method == "tools/call":
            return await self._handle_tool_call(msg_id, params)
        if msg_id is not None:
            return _err_response(
                msg_id, -32601, f"method not found: {method}"
            )
        return None

    async def _handle_tool_call(
        self, msg_id: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        name = params.get("name", "")
        arguments = params.get("arguments", {}) or {}
        if name.startswith("bulla__"):
            return await self._handle_meta_tool(msg_id, name, arguments)
        if "__" not in name:
            return _err_response(
                msg_id, -32602,
                f"tool name {name!r} not namespaced as server__tool",
            )
        server, _, tool = name.partition("__")
        backend = self.backends.get(server)
        if backend is None:
            return _err_response(
                msg_id, -32602, f"unknown server: {server!r}"
            )
        if not backend.alive:
            return _err_response(
                msg_id, -32000,
                f"backend {server!r} offline",
            )
        self._call_seq += 1
        seq = self._call_seq
        # Effect class: MCP annotations else conservative default (unknown ⇒ write).
        effect = self._effect_class.get(name, "write")
        # ── RECOURSE GATE (enforce mode) ─────────────────────────────────────────────
        # Refuse a cross-owner call whose counterparty deed is not authentic + included
        # under a root we pin independently + certifying fee=0 — BEFORE the backend is
        # ever touched (so the breach is prevented, not merely logged). `enforce=False`
        # skips this entirely: the identity transform, today's advisory behaviour.
        # Scope: SIDE-EFFECTING calls only (the gateway law is "no unreceipted side
        # effects", not "no unreceipted reads") unless `gate_reads=True`.
        if (
            self._enforce
            and self._registry is not None
            and (effect != "read" or self._gate_reads)
        ):
            refusal = self._gate_call(server, tool, arguments)
            if refusal is not None:
                self.telemetry.emit({
                    "event": "gate_refused", "seq": seq,
                    "server": server, "tool": tool,
                    "deficiency": refusal.get("deficiency"),
                    "root_trust": (refusal.get("observed") or {}).get("root_trust"),
                })
                cure = (refusal.get("cure") or {}).get("human", "")
                return _err_response(
                    msg_id, -32001,
                    f"recourse gate refused {server}__{tool}: "
                    f"{refusal.get('deficiency')} — {cure}",
                    data={"refusal_certificate": refusal},
                )
            # Proceeded: strip the gate sidecars so the backend sees only its own args.
            arguments = {k: v for k, v in arguments.items()
                         if not k.startswith("_bulla_")}
        outcome = "success"
        error_detail: str | None = None
        resp: dict[str, Any] | None = None
        record = None
        try:
            resp = await backend.call_tool(tool, arguments)
        except BackendCallTimeout as exc:
            outcome = "timeout"
            error_detail = str(exc)
        except Exception as exc:
            outcome = "backend_error"
            error_detail = repr(exc)
            backend.alive = False
        # Update LiveSession with the call (incremental fee tracking).
        # Failures here are bookkeeping, not transport — the agent still
        # gets the backend's reply, but we emit telemetry so divergence
        # is visible rather than silent.
        assert self.session is not None
        if outcome == "success":
            assert resp is not None
            try:
                record = self.session.record_call(
                    server, tool, arguments=arguments,
                    result=resp.get("result"),
                )
            except Exception as exc:
                # Intentionally a separate event type: record_call_failure
                # is a bookkeeping error (Bulla's internal tracking broke),
                # not a transport outcome. It carries seq for joinability
                # but is not folded into the tools/call event because the
                # *call itself succeeded* — the agent got a valid response.
                self.telemetry.emit({
                    "event": "record_call_failure",
                    "seq": seq,
                    "server": server, "tool": tool, "error": repr(exc),
                })
                self._log_stderr(
                    f"[bulla] record_call({server}__{tool}) failed: {exc}"
                )
        # Check for MCP tool-level errors returned inside a 200 response.
        # Real MCP servers (e.g., server-filesystem) return errors as
        # {"result": {"content": [{"type":"text","text":"..."}], "isError": true}}
        # rather than as JSON-RPC error objects. Without this check, those
        # are mislabeled as outcome="success" in telemetry.
        if outcome == "success" and resp is not None:
            result_body = resp.get("result", {})
            if isinstance(result_body, dict) and result_body.get("isError"):
                outcome = "tool_error"
        # Unified telemetry — all call outcomes (success, timeout,
        # backend_error, tool_error) flow through one event so downstream
        # analyzers can join on seq without reconciling multiple event types.
        # ── SHADOW MODE ──────────────────────────────────────────────────────────────
        # Emit a signed per-call deed — with the v0.2 recourse envelope — for every
        # completed SIDE-EFFECTING call. Never blocks and never alters the reply:
        # emission failure is telemetry, not an error (shadow is the observe-grade
        # gateway; enforce is the refusing one).
        shadow_attestation: str | None = None
        if self._shadow and effect != "read" and outcome in ("success", "tool_error"):
            try:
                shadow_attestation = self._emit_shadow_receipt(
                    name, arguments, seq, effect
                )
            except Exception as exc:
                self.telemetry.emit({
                    "event": "shadow_receipt_failure",
                    "seq": seq, "server": server, "tool": tool,
                    "error": repr(exc),
                })
        tel: dict[str, Any] = {
            "event": "tools/call",
            "seq": seq,
            "server": server,
            "tool": tool,
            "effect": effect,
            "outcome": outcome,
            "fee_after": self.session.fee,
            "n_blind_spots": len(self.session.hidden_basis),
        }
        if shadow_attestation is not None:
            tel["shadow_attestation"] = shadow_attestation
        if error_detail is not None:
            tel["error"] = error_detail
        if record is not None:
            tel["call_id"] = record.call_id
        if record is not None and record.flows:
            tel["had_flow_conflicts"] = True
            tel["flows"] = [
                {
                    "source_server": f.source_server,
                    "source_tool": f.source_tool,
                    "source_field": f.source_field,
                    "target_server": f.target_server,
                    "target_tool": f.target_tool,
                    "target_field": f.target_field,
                    "category": f.category,
                    "mismatch_type": f.mismatch_type,
                    "severity": f.severity,
                }
                for f in record.flows
            ]
        else:
            tel["had_flow_conflicts"] = False
        try:
            decomp = decompose_fee_by_dimension(self.session.composition)
            tel["fee_by_dimension"] = dict(decomp.by_dimension)
            tel["interaction_score"] = decomp.residual
        except Exception:
            pass
        self.telemetry.emit(tel)
        if outcome == "timeout":
            return _err_response(msg_id, -32000, error_detail or "timeout")
        if outcome == "backend_error":
            return _err_response(
                msg_id, -32000,
                f"backend {server!r} call failed: {error_detail}",
            )
        # Rewrite the response id to the upstream client's msg_id so the
        # client correlates this response to its own request. The
        # backend's internal id is an implementation detail.
        assert resp is not None
        return {**resp, "id": msg_id}

    async def _handle_meta_tool(
        self, msg_id: Any, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a bulla__* meta-tool call with an exception envelope.

        A buggy classifier or a malformed BlindSpot must not bring down
        the whole proxy — every agent connection would drop. We wrap
        the dispatch identically to ``_handle_tool_call``: errors become
        JSON-RPC -32000 responses and a telemetry event.
        """
        assert self.session is not None
        start = time.perf_counter()
        try:
            if name == "bulla__fee":
                payload = {
                    "fee": self.session.fee,
                    "n_blind_spots": len(self.session.hidden_basis),
                }
            elif name == "bulla__blind_spots":
                payload = self._blind_spots_payload(arguments)
            elif name == "bulla__bridge":
                payload = self._bridge_payload(arguments)
            elif name == "bulla__should_proceed":
                payload = self._should_proceed_payload(arguments)
            elif name == "bulla__why":
                payload = self._why_payload(arguments)
            elif name == "bulla__deed_emit":
                payload = self._deed_emit_payload(arguments)
            elif name == "bulla__deed_verify":
                payload = self._deed_verify_payload(arguments)
            elif name == "bulla__deed_lookup":
                payload = self._deed_lookup_payload(arguments)
            else:
                return _err_response(
                    msg_id, -32601, f"unknown meta-tool: {name}"
                )
        except Exception as exc:
            import traceback
            self.telemetry.emit({
                "event": "meta_tool_failure",
                "tool": name,
                "error": repr(exc),
                "traceback": traceback.format_exc(),
            })
            self._log_stderr(
                f"[bulla] meta-tool {name} raised {exc!r}; "
                f"returning -32000 to client"
            )
            return _err_response(
                msg_id, -32000, f"meta-tool {name} failed: {exc}"
            )
        latency_ms = (time.perf_counter() - start) * 1000.0
        self.telemetry.emit({
            "event": "meta_tool",
            "tool": name,
            "args": _safe_args(arguments),
            "latency_ms": round(latency_ms, 3),
        })
        return _ok_response(msg_id, {
            "content": [{"type": "text", "text": json.dumps(payload)}],
        })

    # ── Meta-tool payloads ────────────────────────────────────────

    def _blind_spots_payload(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        assert self.session is not None
        comp = self.session.composition
        diag = diagnose(comp)
        filt = arguments.get("filter_by_tool")
        entries = []
        for bs in diag.blind_spots:
            if filt and filt not in (bs.from_tool, bs.to_tool):
                continue
            entries.append({
                "dimension": bs.dimension,
                "edge": bs.edge,
                "from_tool": bs.from_tool,
                "to_tool": bs.to_tool,
                "from_field": bs.from_field,
                "to_field": bs.to_field,
                "from_hidden": bs.from_hidden,
                "to_hidden": bs.to_hidden,
            })
        # Per-dimension breakdown: which dimensions carry the fee, and whether
        # they are entangled. ``residual`` (= Σ fee_d − fee = the defect
        # identity Δ_full − Δ_obs) is the cross-dimensional interaction score:
        # 0 means dimensions are modular and each fee_d can be repaired
        # independently; nonzero means hidden state couples them through the
        # ``shared_columns`` below, so resolving one may not clear the others.
        decomp = decompose_fee_by_dimension(comp)
        shared = {
            f"{tool}.{field}": sorted(dims)
            for (tool, field), dims in decomp.shared_columns.items()
        }
        return {
            "fee": diag.coherence_fee,
            "n_blind_spots": len(entries),
            "blind_spots": entries,
            "fee_by_dimension": dict(decomp.by_dimension),
            "interaction_score": decomp.residual,
            "dimensions_modular": decomp.dfd_holds,
            "entangled_columns": shared,
        }

    def _bridge_payload(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        assert self.session is not None
        server = arguments.get("server", "")
        tool = arguments.get("tool", "")
        call_args = arguments.get("arguments", None)
        diag = diagnose(self.session.composition)
        advices = classify_for_call(diag, server, tool, call_args)
        self._last_advices = advices
        return {
            "advices": [a.to_dict() for a in advices],
            "n_value_level": sum(1 for a in advices if a.kind == "value"),
            "n_schema_level": sum(1 for a in advices if a.kind == "schema"),
            "composition_fee": diag.coherence_fee,
        }

    def _should_proceed_payload(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        assert self.session is not None
        server = arguments.get("server", "")
        tool = arguments.get("tool", "")
        call_args = arguments.get("arguments", None)
        diag = diagnose(self.session.composition)
        advices = classify_for_call(diag, server, tool, call_args)
        self._last_advices = advices
        verdict = summarize_verdict(diag.coherence_fee, advices)
        return {
            "verdict": verdict,
            "composition_fee": diag.coherence_fee,
            "composition_blind_spots": len(diag.blind_spots),
            "call_touches_n_obstructions": len(advices),
            "advices_summary": [
                {"kind": a.kind, "applicable": a.applicable, "edge": a.edge}
                for a in advices
            ],
        }

    def _why_payload(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        about = (arguments or {}).get("about", "should_proceed")
        # Map meta-tool to the theorem family backing its claim.
        # The whole pipeline ultimately rests on
        # disclosure_characterization (uniqueness on the abstract
        # carrier) + sheaf_realization (instantiation on the concrete
        # cellular-sheaf carrier matching Bulla). Independence stamps
        # the minimality side. ``about="why"`` is the self-referential
        # case — what backs the provenance tool itself.
        relevant = {
            "fee": ("sheaf_realization",),
            "blind_spots": ("sheaf_realization",),
            "bridge": (
                "disclosure_characterization", "sheaf_realization",
            ),
            "should_proceed": (
                "disclosure_characterization", "sheaf_realization",
                "axiom_independence",
            ),
            "why": (
                "disclosure_characterization", "axiom_independence",
            ),
        }.get(about, ("disclosure_characterization", "sheaf_realization"))
        return {
            "about": about,
            "theorems": [
                {
                    "theorem": ARISTOTLE_STAMPS[key]["theorem"],
                    "lean_module": ARISTOTLE_STAMPS[key]["lean_module"],
                    "aristotle_run": ARISTOTLE_STAMPS[key]["aristotle_run"],
                    "status": ARISTOTLE_STAMPS[key]["status"],
                    "carrier": ARISTOTLE_STAMPS[key]["carrier"],
                }
                for key in relevant
            ],
            "axioms_used": list(AXIOMS_USED),
            "mathlib_pin": MATHLIB_PIN,
            "kernel_version": f"bulla-{__version__}",
        }

    # ── Deed meta-tools (the in-loop coherence-deed surface) ──────

    def _deed_emit_payload(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Sign the current composition's coherence certificate under the proxy's
        identity and log it to the registry — the in-loop, execution-attributable
        record (the moat's data engine). Machine-speed: it NEVER anchors inline;
        the registry root is anchored out-of-band and backdating-resistance comes
        from the inclusion proof against that anchored root."""
        assert self.session is not None
        if self._signer is None:
            return {"error": "no identity configured — start the proxy with --key "
                             "(run `bulla key gen` first) to emit signed deeds"}
        if self._registry is None or not hasattr(self._registry, "append"):
            return {"error": "no appendable registry — start the proxy with a local "
                             "--registry PATH (a remote URL is read-only)"}
        from bulla.certificate import certify, sign_certificate, to_dict
        from bulla.registry import Deed

        comp = self.session.composition
        cert = to_dict(sign_certificate(certify(comp), self._signer))
        # Verify against the key we just signed with — NOT issuer-resolution — so
        # emit works for ANY issuer scheme, including an external --issuer (did:web,
        # eip155, …) whose key can't be derived from the issuer id.
        deed = Deed.from_certificate(cert, public_key=self._signer.public_key)
        idx = self._registry.append(deed)
        proof = self._registry.inclusion(idx)
        fee = self.session.fee
        disposition = "coherent" if fee == 0 else "obstructed"
        self.telemetry.emit({  # bank the {composition, conventions, outcome} record
            "event": "deed_emitted",
            "content_hash": deed.content_hash,
            "composition_hash": deed.composition_hash,
            "issuer": deed.issuer,
            "fee": fee,
            "n_blind_spots": len(self.session.hidden_basis),
            "disposition": disposition,
        })
        return {
            "deed": {
                "issuer": deed.issuer,
                "content_hash": deed.content_hash,
                "composition_hash": deed.composition_hash,
                "attestation_hash": deed.attestation_hash,
            },
            # The full signed certificate — the producer hands this to a relying party so
            # ITS gate can recompute fee=0 trustlessly (a bare deed record carries no fee).
            # This is what closes the both-proxies loop: emit → attach the cert to the
            # result → the consumer presents it to `bulla__deed_verify` / the enforce gate.
            "certificate": cert,
            "registry_index": idx,
            "inclusion_proof": proof,
            "root": proof["root"],
            "anchor": ("unanchored — anchor the registry root out-of-band "
                       "(`bulla registry anchor`); the inclusion proof against the "
                       "anchored root is what resists backdating"),
            "fee": fee,
            "disposition": disposition,
        }

    def _deed_verify_payload(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Verify a (counterparty's) deed and — the rung-4 move — demand its
        INCLUSION in the registry THIS proxy trusts (never one the deed points at).
        An agent policy that refuses to act unless ``recommend == 'proceed'`` is
        'the relying party refuses the unlogged': the omission-closer, no bond
        required. Integrity/authenticity are re-checked when the full signed cert
        is supplied; inclusion is always checked.

        This is the ADVISORY face of the one decision core
        (``recourse_gate.evaluate_gate`` under ``ADVISORY_GATE_POLICY``): it gates on
        inclusion + root-trust + authenticity and *reports* — but does not block on —
        the fee. The ENFORCING gate (the proxy interceptor and ``bulla gate``) calls
        the same core under ``DEFAULT_GATE_POLICY``, so there is one decision impl."""
        if self._registry is None:
            return {"error": "no registry configured — start the proxy with "
                             "--registry to demand inclusion"}
        from bulla.recourse_gate import (evaluate_gate, ADVISORY_GATE_POLICY,
                                         GatePolicy)
        cert = arguments.get("certificate")
        deed = dict(arguments.get("deed") or {})
        att = (deed.get("attestation_hash")
               or (cert or {}).get("attestation_hash")
               or arguments.get("attestation_hash"))
        if not att:
            return {"error": "no attestation_hash to verify (pass `deed`, "
                             "`certificate`, or `attestation_hash`)"}
        deed.setdefault("attestation_hash", att)
        want_comp = arguments.get("composition_hash")
        try:  # a remote registry may be unreachable — fail CLOSED, never crash
            proof = self._registry.inclusion_by_attestation(att)
        except Exception as e:
            return {
                "integrity": None, "authenticity": None,
                "included": False, "root_trust": "unreachable",
                "composition_bound": None, "registry_root": None,
                "recommend": "refuse",
                "reason": f"could not reach the registry to confirm inclusion ({e}) — refusing",
            }
        # Advisory policy: gate on inclusion / root-trust / authenticity, REPORT fee but
        # never block on it (the shipped contract). `composition_hash`, when supplied,
        # binds the deed to that composition (fail closed).
        policy = (ADVISORY_GATE_POLICY if want_comp is None
                  else GatePolicy(max_fee=None, require_certificate_for_fee=False,
                                  expected_composition_hash=want_comp))
        decision = evaluate_gate(
            deed_rec=deed, inclusion_rec=proof, certificate=cert,
            trusted_root=arguments.get("trusted_root"),
            root_ots=arguments.get("root_ots"),
            is_remote=getattr(self._registry, "is_remote", False), policy=policy)
        return decision.as_verify_payload()

    def _emit_shadow_receipt(
        self, ns_name: str, arguments: dict[str, Any], seq: int, effect: str
    ) -> str | None:
        """Shadow-mode per-call receipt: sign the current composition's deed with
        a v0.2 recourse envelope whose `bounds.scope` names THIS call (namespaced
        tool + argument digest), and log it. Returns the attestation hash, or
        None when the deed surface isn't configured (no signer / no appendable
        registry) — shadow without a deed surface degrades to telemetry-only.

        The envelope's remedies climb the ladder: recompute (the deed itself),
        challenge (this registry under the root the consumer pins), cure (the
        composition's disclosure repair), and — when a `mandate` is configured —
        escalate to the surviving principal. Every remedy names its verifier and
        stateful anchor: the modality law, applied per call."""
        if self._signer is None or self._registry is None or not hasattr(self._registry, "append"):
            return None
        import hashlib as _hashlib

        from bulla.certificate import certify, sign_certificate, to_dict
        from bulla.envelope import (
            Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy,
        )
        from bulla.registry import Deed

        assert self.session is not None
        comp = self.session.composition
        args_digest = _hashlib.sha256(
            json.dumps(_safe_args(arguments), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]

        authority = None
        if self._mandate:
            authority = Authority(
                principal=self._mandate.get("principal", ""),
                policy=self._mandate.get("policy", ""),
                delegation=tuple(self._mandate.get("delegation", ())),
            )
        remedies = [
            Remedy("recompute", "bulla verify --registry", "attestation:self"),
            Remedy("challenge", "rfc6962-inclusion",
                   f"root:{self._gate_trusted_root or 'pin-out-of-band'}"),
            Remedy("cure", "bulla repair", f"composition:{comp.canonical_hash()}"),
        ]
        if authority is not None:
            remedies.append(
                Remedy("escalate", "human-review",
                       f"delegation:{authority.principal}")
            )
        env = RecourseEnvelope(
            authority=authority,
            bounds=Bounds(scope=f"call:{ns_name}@sha256:{args_digest}"),
            recourse=Recourse(
                challenge_window="P30D",
                forum=Forum(
                    log_endpoint=str(getattr(self._registry, "path", "local-log")),
                    trusted_root_ref=self._gate_trusted_root or "pin-out-of-band",
                ),
                remedies=tuple(remedies),
            ),
            disclosure_class="party",
        )
        cert = to_dict(sign_certificate(certify(comp), self._signer, envelope=env))
        deed = Deed.from_certificate(cert, public_key=self._signer.public_key)
        self._registry.append(deed)
        self.telemetry.emit({
            "event": "shadow_receipt",
            "seq": seq,
            "call": ns_name,
            "effect": effect,
            "attestation_hash": deed.attestation_hash,
            "composition_hash": deed.composition_hash,
            "fee": self.session.fee,
        })
        return deed.attestation_hash

    def _classify_root_trust(self, served_root, trusted_root, root_ots):
        """Delegate to the shared `registry.classify_root_trust`, passing whether
        this proxy's registry serves a remote (host-asserted) root."""
        from bulla.registry import classify_root_trust
        return classify_root_trust(
            getattr(self._registry, "is_remote", False),
            served_root, trusted_root, root_ots,
        )

    def _gate_call(
        self, server: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The enforce-mode recourse gate for one cross-owner ``tools/call``. Returns a
        contestable refusal certificate to REFUSE (the backend must NOT be touched), or
        ``None`` to proceed. The counterparty presents its deed via sidecar arguments —
        ``_bulla_certificate`` (the full signed cert; required to prove fee=0),
        ``_bulla_deed`` (the deed triple), ``_bulla_trusted_root`` / ``_bulla_root_ots``
        (the root the relying party pins). Uses the one decision core under the enforcing
        policy (require fee=0 + an independently-trusted root)."""
        from bulla.model import Disposition
        from bulla.recourse_gate import (
            evaluate_gate, build_refusal_certificate, DEFAULT_GATE_POLICY,
            GateDecision, MISSING, UNREACHABLE,
        )
        policy = self._gate_policy or DEFAULT_GATE_POLICY
        cert = arguments.get("_bulla_certificate")
        deed = dict(arguments.get("_bulla_deed") or {})
        if cert and not deed:
            deed = {
                "issuer": (cert.get("issuer") or {}).get("id"),
                "content_hash": cert.get("certificate_content_hash"),
                "attestation_hash": cert.get("attestation_hash"),
                "composition_hash": (cert.get("subject") or {}).get("composition_sha256"),
            }
        att = deed.get("attestation_hash") or (cert or {}).get("attestation_hash")
        max_fee = getattr(policy, "max_fee", 0) or 0

        def _refuse(decision: GateDecision) -> dict[str, Any]:
            return build_refusal_certificate(decision, subject_deed=deed, signer=self._signer)

        if not att:  # nothing presented at all — distinct from "presented but not logged"
            return _refuse(GateDecision(
                disposition=Disposition.REFUSE_PENDING_DISCLOSURE.value, deficiency=MISSING,
                root_trust="none", fee=None,
                reason="no deed presented for this cross-owner action — refuse",
                cure={"action": "present_deed_for_composition_under_trusted_root",
                      "deficiency": MISSING, "require_fee": max_fee, "disclose": [],
                      "human": "Present a signed certificate and its inclusion proof for "
                               "this composition, logged under a root I trust, certifying "
                               f"coherence_fee <= {max_fee}."}))
        try:  # a remote registry may be unreachable — fail CLOSED
            proof = self._registry.inclusion_by_attestation(att)
        except Exception as e:
            return _refuse(GateDecision(
                disposition=Disposition.REFUSE_PENDING_DISCLOSURE.value, deficiency=UNREACHABLE,
                root_trust="unreachable", fee=None,
                reason=f"could not reach the registry to confirm inclusion ({e}) — refuse",
                cure={"action": "present_deed_for_composition_under_trusted_root",
                      "deficiency": UNREACHABLE, "require_fee": max_fee, "disclose": [],
                      "human": "Re-present once the registry is reachable."}))
        decision = evaluate_gate(
            deed_rec=deed, inclusion_rec=proof, certificate=cert,
            trusted_root=arguments.get("_bulla_trusted_root") or self._gate_trusted_root,
            root_ots=arguments.get("_bulla_root_ots"),
            is_remote=getattr(self._registry, "is_remote", False), policy=policy)
        return None if decision.proceed else _refuse(decision)

    def _deed_lookup_payload(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Which deeds certify the EXACT same composition, and under whose issuer.
        A factual enumeration — NOT a score and NOT a verdict; the agent decides
        which issuers it trusts and what, if anything, to infer. Defaults to the
        current session composition. Only meaningful because the content-hash is
        machine-independent (the same composition resolves to the same address)."""
        if self._registry is None:
            return {"error": "no registry configured — start the proxy with --registry"}
        comp_hash = arguments.get("composition_hash")
        if not comp_hash:
            assert self.session is not None
            from bulla.certificate import _composition_sha256
            comp_hash = _composition_sha256(self.session.composition)
        deeds = self._registry.by_composition(comp_hash)
        return {
            "composition_hash": comp_hash,
            "n_deeds": len(deeds),
            "deeds": deeds,
            "issuers": sorted({d["issuer"] for d in deeds}),
        }

    def _log_stderr(self, msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)


# ── Static meta-tool definitions ──────────────────────────────────


def _meta_tool_definitions() -> list[dict[str, Any]]:
    """The eight `bulla__*` tools, injected into every tools/list response (five
    diagnostic + three deed). The deed tools are advertised unconditionally; if
    the proxy was started without --key/--registry they return a structured
    ``{"error": …}`` explaining what to configure, rather than failing silently.

    Schemas match what the system prompt at
    ``bulla/agents/system_prompt_v1.md`` instructs agents to send.
    """
    call_shape = {
        "type": "object",
        "properties": {
            "server": {"type": "string"},
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["server", "tool"],
    }
    return [
        {
            "name": "bulla__fee",
            "description": (
                "Current witness rank (coherence fee) of the running "
                "composition. Zero means coherent so far; positive "
                "means N independent obstruction dimensions exist."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bulla__blind_spots",
            "description": (
                "Enumerated obstructions in the current composition. "
                "Each entry names a dimension and the edge it crosses. "
                "Also returns fee_by_dimension (per-dimension fee), an "
                "interaction_score (0 = dimensions are modular and "
                "independently repairable; nonzero = entangled), and the "
                "entangled_columns that couple them."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "filter_by_tool": {"type": "string"},
                },
            },
        },
        {
            "name": "bulla__bridge",
            "description": (
                "Recommended repair for an obstruction touching a "
                "pending tool call. Returns advices with kind 'value' "
                "(apply at runtime) or 'schema' (manifest edit required)."
            ),
            "inputSchema": call_shape,
        },
        {
            "name": "bulla__should_proceed",
            "description": (
                "Ternary verdict for a pending tool call: 'safe' "
                "(proceed normally), 'advise' (apply a value-level "
                "bridge first), or 'refuse' (schema-level obstruction "
                "blocks runtime; surface to operator)."
            ),
            "inputSchema": call_shape,
        },
        {
            "name": "bulla__why",
            "description": (
                "Formal-verification provenance for the most recent "
                "recommendation: theorem name, Aristotle run hash, "
                "axioms used, carrier. Aristotle is Harmonic's "
                "machine-checked proof assistant."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "about": {
                        "type": "string",
                        "enum": [
                            "fee", "blind_spots", "bridge",
                            "should_proceed", "why",
                        ],
                    },
                },
            },
        },
        {
            "name": "bulla__deed_emit",
            "description": (
                "Sign the CURRENT composition's coherence certificate under "
                "this agent's identity and log it to the registry — a "
                "non-repudiable, execution-attributable deed. Emit at a "
                "coherence checkpoint (e.g. before delegating to a "
                "counterparty, or after a clean multi-tool composition). "
                "Returns the deed id, its registry inclusion proof, the root, "
                "and the full signed `certificate`. When you delegate, ATTACH "
                "that certificate to your result (the counterparty presents it "
                "to its gate so it can recompute coherence_fee=0 — a bare deed "
                "record carries no fee). Machine-speed: it does not wait on the "
                "timechain."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "bulla__deed_verify",
            "description": (
                "Verify a counterparty's deed and DEMAND its inclusion. Returns "
                "recommend='proceed' only if the deed is logged AGAINST A ROOT YOU "
                "TRUST — your own log, or (for a remote registry) a root you pin via "
                "trusted_root or root_ots. A remote host's bare claim yields "
                "recommend='refuse' with root_trust='host-asserted' (you'd be "
                "trusting the operator). recommend='refuse' also for an unlogged "
                "deed, a failed signature/integrity (when the full certificate is "
                "given), or a pinned-root mismatch (equivocation). Refusing the "
                "unverifiable is the omission-closer — call before acting on "
                "someone else's coherence claim."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "deed": {"type": "object"},
                    "certificate": {"type": "object"},
                    "attestation_hash": {"type": "string"},
                    "content_hash": {"type": "string"},
                    "composition_hash": {"type": "string"},
                    "trusted_root": {"type": "string"},
                    "root_ots": {"type": "string"},
                },
            },
        },
        {
            "name": "bulla__deed_lookup",
            "description": (
                "Has this EXACT composition been certified coherent before, "
                "and under whose deed? Returns the deeds logged for a "
                "composition hash (defaults to the current composition) and "
                "their issuers. Factual enumeration, not a score — you decide "
                "which issuers you trust."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "composition_hash": {"type": "string"},
                },
            },
        },
    ]


# ── Helpers ───────────────────────────────────────────────────────


def _ok_response(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err_response(
    msg_id: Any, code: int, message: str, *, data: Any = None
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data  # JSON-RPC 2.0 error.data — carries the refusal certificate
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": err,
    }


_TELEMETRY_DENY_KEYS = frozenset({
    "api_key", "apikey", "x-api-key", "x_api_key",
    "token", "access_token", "refresh_token", "id_token", "bearer",
    "password", "passwd", "pwd",
    "secret", "client_secret",
    "authorization", "auth",
    "cookie", "set-cookie",
    "session", "session_id", "session_token",
    "credentials", "private_key", "private-key",
    "github_token", "openai_api_key", "anthropic_api_key",
})

_CREDENTIAL_PREFIXES = (
    "bearer ", "basic ", "token ",
    "ghp_", "gho_", "ghu_", "ghs_", "ghr_",  # GitHub
    "sk-", "sk_",                              # OpenAI / Stripe
    "xoxa-", "xoxb-", "xoxp-", "xoxs-",        # Slack
    "AKIA", "ASIA",                            # AWS access keys
    "eyJ",                                     # JWT
)


def _looks_like_credential(value: str) -> bool:
    lowered = value.lower()
    for prefix in _CREDENTIAL_PREFIXES:
        if lowered.startswith(prefix.lower()):
            return True
    return False


def _safe_value(v: Any) -> Any:
    """Redact / truncate a single value (string, list, dict, scalar)."""
    if isinstance(v, str):
        if _looks_like_credential(v):
            return "<redacted>"
        if len(v) > 200:
            return v[:200] + "…"
        return v
    if isinstance(v, dict):
        return _safe_args(v)
    if isinstance(v, (list, tuple)):
        # Recurse into lists — OpenAI-style chat-message arrays are the
        # obvious carrier of leaked credentials inside ``messages: [{...}]``.
        return [_safe_value(item) for item in v]
    return v


def _safe_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redact secrets and truncate noise for telemetry.

    Three rules, in order:
      1. Keys whose lowercase form matches a known credential keyword
         (api_key, token, secret, …) are redacted regardless of value.
      2. String values matching a known credential pattern prefix
         (``Bearer ``, ``ghp_``, ``sk-`` …) are redacted regardless of
         key — this catches the case where the agent put a token in a
         differently-named field.
      3. Other strings longer than 200 chars are truncated.

    Recurses into nested dicts AND lists / tuples so credentials
    embedded in chat-message arrays are caught.

    Defaults are redact-first because telemetry files are durable;
    agents that override secret carriage should use a separate channel.
    """
    out: dict[str, Any] = {}
    for k, v in (arguments or {}).items():
        if k.lower().replace("-", "_") in _TELEMETRY_DENY_KEYS:
            out[k] = "<redacted>"
            continue
        out[k] = _safe_value(v)
    return out


# ── Entry point ───────────────────────────────────────────────────


async def serve(
    backend_specs: list[tuple[str, str, dict[str, str] | None]],
    *,
    telemetry_path: Path | None = None,
    signer: Any = None,
    registry: Any = None,
    enforce: bool = False,
    trusted_root: str | None = None,
    shadow: bool = False,
    mandate: dict | None = None,
    gate_reads: bool = False,
) -> None:
    """Spawn backends, start the proxy loop, run until stdin closes."""
    backends = [
        BackendServer(name=n, command=c, env=e)
        for (n, c, e) in backend_specs
    ]
    tel = TelemetrySink(path=telemetry_path)
    tel.open()
    proxy = BullaLiveProxy(backends, telemetry=tel, signer=signer, registry=registry,
                           enforce=enforce, trusted_root=trusted_root,
                           shadow=shadow, mandate=mandate, gate_reads=gate_reads)
    try:
        await proxy.start_backends()
        await proxy.run()
    finally:
        for b in backends:
            await b.stop()
        tel.close()


__all__ = [
    "ARISTOTLE_STAMPS",
    "BackendServer",
    "BullaLiveProxy",
    "TelemetrySink",
    "serve",
]
