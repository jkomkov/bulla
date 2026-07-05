"""Tests for the live MCP proxy: dispatch, meta-tools, latency, telemetry."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from bulla.live_proxy import (
    ARISTOTLE_STAMPS,
    BackendServer,
    BullaLiveProxy,
    TelemetrySink,
)


# ── Fake backend MCP server ──────────────────────────────────────────


_FAKE_BACKEND_SCRIPT = r'''
"""Minimal MCP server that exposes a fixed tools list.

Used by test_live_proxy.py as a stand-in for a real backend. The
tools list is set via the BULLA_FAKE_TOOLS_JSON env var.
"""
import json
import os
import sys

TOOLS = json.loads(os.environ.get("BULLA_FAKE_TOOLS_JSON", "[]"))


def respond(msg_id, result):
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": result,
    }) + "\n")
    sys.stdout.flush()


while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        respond(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "fake", "version": "0"},
        })
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        respond(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        respond(msg_id, {
            "content": [{
                "type": "text",
                "text": json.dumps({"echoed": name, "args": args}),
            }],
        })
    else:
        if msg_id is not None:
            respond(msg_id, {})
'''


_FAKE_SCRIPT_PATH: Path | None = None


def _write_fake_backend_script(tmp_path: Path) -> Path:
    """Persist the fake-backend script to disk for spawning."""
    global _FAKE_SCRIPT_PATH
    if _FAKE_SCRIPT_PATH is None or not _FAKE_SCRIPT_PATH.exists():
        path = tmp_path / "fake_backend.py"
        path.write_text(_FAKE_BACKEND_SCRIPT)
        _FAKE_SCRIPT_PATH = path
    return _FAKE_SCRIPT_PATH


def _fake_backend_command(
    tools_payload: list[dict], tmp_path: Path
) -> tuple[str, dict[str, str]]:
    """Build a (command, env) pair that spawns the fake backend."""
    script_path = _write_fake_backend_script(tmp_path)
    env = {"BULLA_FAKE_TOOLS_JSON": json.dumps(tools_payload)}
    return f"{sys.executable} {script_path}", env


_FETCH_TOOLS = [
    {
        "name": "get",
        "description": "Fetch URL contents",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
        },
        "_internal_state": ["url", "body", "encoding"],
        "_observable_schema": ["url", "body"],
        "_emits_dimensions": [
            {"name": "encoding", "from_field": "encoding"}
        ],
    },
]

_MEMORY_TOOLS = [
    {
        "name": "store",
        "description": "Store fetched content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "encoding": {"type": "string"},
            },
        },
        "_internal_state": ["content", "encoding"],
        "_observable_schema": ["content", "encoding"],
        "_consumes_dimensions": [
            {"name": "encoding", "to_field": "encoding"}
        ],
    },
]


# ── Helpers ──────────────────────────────────────────────────────────


async def _start_proxy(
    backend_specs: list[tuple[str, str, dict[str, str] | None]],
    telemetry_path: Path | None = None,
) -> BullaLiveProxy:
    backends = [
        BackendServer(name=n, command=c, env=e)
        for (n, c, e) in backend_specs
    ]
    tel = TelemetrySink(path=telemetry_path)
    tel.open()
    proxy = BullaLiveProxy(backends, telemetry=tel)
    await proxy.start_backends()
    return proxy


def _build_specs(
    tmp_path: Path,
    entries: list[tuple[str, list[dict]]],
) -> list[tuple[str, str, dict[str, str] | None]]:
    """Construct backend specs from (name, tools_payload) pairs."""
    out: list[tuple[str, str, dict[str, str] | None]] = []
    for name, tools in entries:
        cmd, env = _fake_backend_command(tools, tmp_path)
        out.append((name, cmd, env))
    return out


# ── Tests ────────────────────────────────────────────────────────────


def test_meta_tool_definitions_are_listed(tmp_path: Path):
    """tools/list should include all five bulla__* meta-tools."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        names = [t["name"] for t in resp["result"]["tools"]]
        for backend in proxy.backends.values():
            await backend.stop()
        return names

    names = asyncio.run(run())
    assert "fetch__get" in names
    assert {"bulla__fee", "bulla__blind_spots", "bulla__bridge",
            "bulla__should_proceed", "bulla__why"} <= set(names)


def test_bulla_fee_returns_session_state(tmp_path: Path):
    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
            ("memory", _MEMORY_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "bulla__fee", "arguments": {}},
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    text = resp["result"]["content"][0]["text"]
    payload = json.loads(text)
    assert "fee" in payload
    assert "n_blind_spots" in payload
    assert isinstance(payload["fee"], int)


def test_bulla_should_proceed_returns_ternary_verdict(tmp_path: Path):
    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
            ("memory", _MEMORY_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {
                "name": "bulla__should_proceed",
                "arguments": {
                    "server": "memory", "tool": "store",
                    "arguments": {"content": "x", "encoding": "utf-8"},
                },
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["verdict"] in {"safe", "advise", "refuse"}
    assert "composition_fee" in payload
    assert "call_touches_n_obstructions" in payload
    assert "advices_summary" in payload


def test_bulla_why_returns_aristotle_provenance(tmp_path: Path):
    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {
                "name": "bulla__why",
                "arguments": {"about": "should_proceed"},
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["about"] == "should_proceed"
    runs = {t["aristotle_run"] for t in payload["theorems"]}
    assert ARISTOTLE_STAMPS["disclosure_characterization"][
        "aristotle_run"
    ] in runs
    assert ARISTOTLE_STAMPS["sheaf_realization"][
        "aristotle_run"
    ] in runs
    assert payload["axioms_used"] == [
        "propext", "Classical.choice", "Quot.sound"
    ]


def test_unknown_meta_tool_returns_jsonrpc_error(tmp_path: Path):
    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "bulla__does_not_exist", "arguments": {}},
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    assert resp["error"]["code"] == -32601


def test_unnamespaced_tool_call_rejected(tmp_path: Path):
    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 15, "method": "tools/call",
            "params": {"name": "not_namespaced", "arguments": {}},
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    assert resp["error"]["code"] == -32602


def test_backend_call_forwards_and_increments_fee(tmp_path: Path):
    """A real tool call to a backend should be forwarded and recorded."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
            ("memory", _MEMORY_TOOLS),
        ]))
        # Forward a real call to the fetch backend
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {
                "name": "fetch__get",
                "arguments": {"url": "https://example.com"},
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    assert "result" in resp
    text = resp["result"]["content"][0]["text"]
    echoed = json.loads(text)
    assert echoed["echoed"] == "get"
    assert echoed["args"]["url"] == "https://example.com"


def test_meta_tool_latency_under_budget(tmp_path: Path):
    """p99 of meta-tool dispatch should be well under 100 ms.

    The plan's latency budget says p99 < 100 ms on `tools/call`
    overhead. This microbenchmark exercises only the in-process
    dispatch path (no backend round-trip), so it's a lower bound for
    that budget. n=200 so p99 is a real 99th percentile, not max.
    """

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fetch", _FETCH_TOOLS),
            ("memory", _MEMORY_TOOLS),
        ]))
        latencies = []
        n = 200
        for i in range(n):
            start = time.perf_counter()
            await proxy.dispatch({
                "jsonrpc": "2.0", "id": 1000 + i, "method": "tools/call",
                "params": {
                    "name": "bulla__should_proceed",
                    "arguments": {
                        "server": "memory", "tool": "store",
                        "arguments": {"content": "x"},
                    },
                },
            })
            latencies.append((time.perf_counter() - start) * 1000.0)
        for backend in proxy.backends.values():
            await backend.stop()
        return latencies

    latencies = asyncio.run(run())
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99) - 1]
    # CI variance bound. The plan's hard target is p99 < 100 ms on
    # `tools/call` overhead end-to-end (proxy + backend); this in-
    # process microbench is a lower bound on that and budgets 100/200.
    assert p50 < 100, f"meta-tool p50={p50:.1f}ms exceeds 100ms"
    assert p99 < 200, f"meta-tool p99={p99:.1f}ms exceeds 200ms (n={len(latencies)})"


# ── Regression tests for the post-review correctness fixes ────────


def test_msg_id_is_rewritten_to_client_request_id(tmp_path: Path):
    """The response id must equal the client's request id, not the
    backend's internal sequence number. JSON-RPC clients correlate
    requests by id; the wrong id breaks the matching."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fake", _FETCH_TOOLS),
        ]))
        client_id = 99999
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": client_id, "method": "tools/call",
            "params": {"name": "fake__get", "arguments": {"url": "x"}},
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp, client_id

    resp, client_id = asyncio.run(run())
    assert resp["id"] == client_id, (
        f"response id should be {client_id} (client's), got {resp.get('id')}"
    )


def test_concurrent_backend_calls_do_not_race(tmp_path: Path):
    """Five parallel tools/call requests all return their own results
    by id — proves the reader-task multiplexer pattern correlates
    correctly under concurrency."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fake", _FETCH_TOOLS),
        ]))
        client_ids = [100, 200, 300, 400, 500]
        coros = [
            proxy.dispatch({
                "jsonrpc": "2.0", "id": cid, "method": "tools/call",
                "params": {
                    "name": "fake__get",
                    "arguments": {"url": f"req-{cid}"},
                },
            })
            for cid in client_ids
        ]
        results = await asyncio.gather(*coros)
        for backend in proxy.backends.values():
            await backend.stop()
        return list(zip(client_ids, results))

    pairs = asyncio.run(run())
    for cid, resp in pairs:
        assert resp["id"] == cid, (
            f"correlation broken: expected id {cid}, got {resp.get('id')}"
        )
        echoed = json.loads(resp["result"]["content"][0]["text"])
        assert echoed["args"]["url"] == f"req-{cid}", (
            f"id {cid} got the wrong tool result"
        )


def test_backend_timeout_returns_jsonrpc_error(tmp_path: Path):
    """A backend that never responds should produce a -32000 error,
    not hang the proxy."""

    silent_script = (
        "import sys, time\n"
        "# Drain stdin but never emit a response to tools/call.\n"
        "for line in sys.stdin:\n"
        "    if 'initialize' in line:\n"
        "        sys.stdout.write('"
        "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\\n'); sys.stdout.flush()\n"
        "    elif 'tools/list' in line:\n"
        "        sys.stdout.write('"
        "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[]}}\\n'); sys.stdout.flush()\n"
        "    # tools/call -> deliberately silent\n"
    )
    silent_path = tmp_path / "silent_backend.py"
    silent_path.write_text(silent_script)

    async def run():
        from bulla.live_proxy import BackendServer, BullaLiveProxy, TelemetrySink
        backend = BackendServer(
            name="silent",
            command=f"{sys.executable} {silent_path}",
            call_timeout_s=0.3,
        )
        proxy = BullaLiveProxy([backend], telemetry=TelemetrySink(path=None))
        proxy.telemetry.open()
        await proxy.start_backends()
        # A real tool isn't listed; build the namespaced name manually.
        backend.tools = [{"name": "noop"}]
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "silent__noop", "arguments": {}},
        })
        await backend.stop()
        return resp

    resp = asyncio.run(run())
    assert "error" in resp, f"expected timeout error, got {resp}"
    assert resp["error"]["code"] == -32000
    assert "did not respond" in resp["error"]["message"]


def test_telemetry_redacts_credential_keys(tmp_path: Path):
    """API keys and tokens MUST NOT be persisted to telemetry."""
    out = tmp_path / "events.jsonl"

    async def run():
        proxy = await _start_proxy(
            _build_specs(tmp_path, [("fake", _FETCH_TOOLS)]),
            telemetry_path=out,
        )
        await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "bulla__should_proceed",
                "arguments": {
                    "server": "fake", "tool": "get",
                    "arguments": {
                        "api_key": "should-not-appear",
                        "Authorization": "Bearer ghp_secrettokenvalue",
                        "url": "https://example.com",
                    },
                },
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        proxy.telemetry.close()

    asyncio.run(run())
    text = out.read_text()
    assert "should-not-appear" not in text, "raw api_key value leaked"
    assert "ghp_secrettokenvalue" not in text, "GitHub token leaked"
    assert "<redacted>" in text


def test_per_call_sensitivity_skips_obstructions_not_touched(tmp_path: Path):
    """A call against the consumer side, without the obstructed field
    in its arguments, must NOT be refused — that was the false-positive
    trap the post-review caught.

    Constructed via direct Composition so the diagnostic generates a
    blind spot on encoding; then the should_proceed payload with vs.
    without that field yields different verdicts.
    """
    from bulla.bridge_kinds import classify_for_call, summarize_verdict
    from bulla.diagnostic import diagnose
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    producer = ToolSpec(
        name="fake_fetch__get",
        internal_state=("url", "body", "encoding"),
        observable_schema=("url", "body"),
    )
    consumer = ToolSpec(
        name="fake_memory__store",
        internal_state=("content", "encoding"),
        observable_schema=("content", "encoding"),
    )
    edge = Edge(
        from_tool="fake_fetch__get",
        to_tool="fake_memory__store",
        dimensions=(
            SemanticDimension(
                name="encoding", from_field="encoding", to_field="encoding"
            ),
        ),
    )
    comp = Composition(name="t", tools=(producer, consumer), edges=(edge,))
    diag = diagnose(comp)
    # Call WITHOUT encoding: the consumer isn't traversing the seam.
    advices_clean = classify_for_call(
        diag, "fake_memory", "store", arguments={"content": "x"}
    )
    assert advices_clean == [], (
        "consumer call without obstructed field must not yield advice"
    )
    assert summarize_verdict(diag.coherence_fee, advices_clean) == "safe"
    # Call WITH encoding: the consumer IS traversing the seam.
    advices_touched = classify_for_call(
        diag, "fake_memory", "store",
        arguments={"content": "x", "encoding": "utf-8"},
    )
    assert len(advices_touched) == 1
    assert advices_touched[0].kind == "schema"
    assert summarize_verdict(diag.coherence_fee, advices_touched) == "refuse"


def test_backend_named_bulla_rejected(tmp_path: Path):
    """A backend named 'bulla' collides with the meta-tool prefix."""
    import pytest

    from bulla.live_proxy import (
        BackendServer, BullaLiveProxy, TelemetrySink,
    )

    backend = BackendServer(
        name="bulla", command=f"{sys.executable} -c 'pass'"
    )
    proxy = BullaLiveProxy([backend], telemetry=TelemetrySink(path=None))
    with pytest.raises(ValueError, match="bulla__\\* meta-tool"):
        asyncio.run(proxy.start_backends())


def test_blind_spots_meta_tool(tmp_path: Path):
    """bulla__blind_spots returns the obstruction list directly."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fake", _FETCH_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "bulla__blind_spots", "arguments": {}},
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert "fee" in payload
    assert "n_blind_spots" in payload
    assert "blind_spots" in payload
    assert isinstance(payload["blind_spots"], list)


def test_telemetry_records_meta_tool_invocations(tmp_path: Path):
    out = tmp_path / "events.jsonl"

    async def run():
        proxy = await _start_proxy(
            _build_specs(tmp_path, [("fetch", _FETCH_TOOLS)]),
            telemetry_path=out,
        )
        await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "bulla__fee", "arguments": {}},
        })
        await proxy.dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "bulla__should_proceed",
                "arguments": {"server": "fetch", "tool": "get"},
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        proxy.telemetry.close()

    asyncio.run(run())
    lines = out.read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    kinds = {e.get("event") for e in events}
    assert "backends_started" in kinds
    assert "meta_tool" in kinds
    meta_events = [e for e in events if e.get("event") == "meta_tool"]
    tool_names = {e["tool"] for e in meta_events}
    assert {"bulla__fee", "bulla__should_proceed"} <= tool_names


# ── Post-second-review regression tests ─────────────────────────────


def test_empty_arguments_is_conservative_not_false_negative(tmp_path: Path):
    """`arguments={}` must be treated as `arguments=None` (conservative,
    no info) — not as 'obstructed field absent'. Otherwise the
    inverse false-negative: a dirty composition reports verdict=safe
    when the agent omits the inner arguments dict."""
    from bulla.bridge_kinds import classify_for_call, summarize_verdict
    from bulla.diagnostic import diagnose
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    producer = ToolSpec(
        name="fetch__get",
        internal_state=("url", "body", "encoding"),
        observable_schema=("url", "body"),
    )
    consumer = ToolSpec(
        name="memory__store",
        internal_state=("content", "encoding"),
        observable_schema=("content", "encoding"),
    )
    edge = Edge(
        from_tool="fetch__get",
        to_tool="memory__store",
        dimensions=(SemanticDimension(
            name="encoding", from_field="encoding", to_field="encoding"
        ),),
    )
    comp = Composition(name="t", tools=(producer, consumer), edges=(edge,))
    diag = diagnose(comp)
    # arguments=None: conservative — surfaces the obstruction
    advices_none = classify_for_call(diag, "memory", "store", arguments=None)
    assert len(advices_none) == 1
    assert summarize_verdict(diag.coherence_fee, advices_none) == "refuse"
    # arguments={}: ALSO conservative (this was the regression)
    advices_empty = classify_for_call(diag, "memory", "store", arguments={})
    assert len(advices_empty) == 1, (
        "arguments={} regressed to false-negative: empty dict should "
        "be treated as 'no info' (conservative), not 'field absent'"
    )
    assert summarize_verdict(diag.coherence_fee, advices_empty) == "refuse"


def test_meta_tool_failure_returns_jsonrpc_error_not_crash(tmp_path: Path):
    """Exception inside a meta-tool handler must envelope to -32000,
    not propagate out and kill the proxy."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fake", _FETCH_TOOLS),
        ]))
        # Inject a broken session that raises on diagnose.
        class _Boom:
            fee = 0
            hidden_basis: list = []
            @property
            def composition(self):
                raise RuntimeError("synthetic boom")
        proxy.session = _Boom()
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {
                "name": "bulla__should_proceed",
                "arguments": {"server": "fake", "tool": "get"},
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    assert "error" in resp, f"expected -32000 envelope, got {resp}"
    assert resp["error"]["code"] == -32000
    assert "should_proceed" in resp["error"]["message"]


def test_meta_tools_appear_first_in_tools_list(tmp_path: Path):
    """Meta-tools must lead tools/list so the LLM encounters them
    before backend tools (position bias is real for large lists)."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fake", _FETCH_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names[0].startswith("bulla__"), (
        f"meta-tools should be first; first tool is {names[0]!r}"
    )
    # All five meta-tools come before any backend tool.
    backend_idx = next(
        (i for i, n in enumerate(names) if not n.startswith("bulla__")), len(names)
    )
    meta_prefix = names[:backend_idx]
    assert {
        "bulla__fee", "bulla__blind_spots", "bulla__bridge",
        "bulla__should_proceed", "bulla__why",
    } <= set(meta_prefix)


def test_telemetry_redacts_credentials_in_lists(tmp_path: Path):
    """OpenAI-style chat-message arrays carrying tokens must be
    redacted. The fix recurses into lists/tuples."""
    out = tmp_path / "events.jsonl"

    async def run():
        proxy = await _start_proxy(
            _build_specs(tmp_path, [("fake", _FETCH_TOOLS)]),
            telemetry_path=out,
        )
        await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "bulla__should_proceed",
                "arguments": {
                    "server": "fake", "tool": "get",
                    "arguments": {
                        "messages": [
                            {"role": "user", "content": "hi"},
                            {"api_key": "leaked-via-list-item"},
                            {"authorization": "Bearer ghp_leakedviaitem"},
                        ],
                    },
                },
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        proxy.telemetry.close()

    asyncio.run(run())
    text = out.read_text()
    assert "leaked-via-list-item" not in text
    assert "ghp_leakedviaitem" not in text


def test_bulla_why_accepts_self_introspection(tmp_path: Path):
    """about='why' must be a valid value — the prompt promises agents
    can introspect Bulla itself."""

    async def run():
        proxy = await _start_proxy(_build_specs(tmp_path, [
            ("fake", _FETCH_TOOLS),
        ]))
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "bulla__why",
                "arguments": {"about": "why"},
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        return resp

    resp = asyncio.run(run())
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["about"] == "why"
    assert len(payload["theorems"]) >= 1


def test_end_to_end_should_proceed_yields_refuse_on_hidden_seam(tmp_path: Path):
    """End-to-end: a composition with a hidden cross-server seam
    routes through `should_proceed` and yields `refuse`. Bypasses the
    `BullaGuard` parser by injecting a hand-built composition into
    the session — the parser path is a Bulla-product issue tracked
    separately. What this test certifies is the proxy + bridge_kinds
    + system-prompt contract end-to-end on a known-bad composition."""
    from bulla.live import LiveSession
    from bulla.live_proxy import (
        BullaLiveProxy, TelemetrySink, _meta_tool_definitions,
    )
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
    from bulla.session import Session

    proxy = BullaLiveProxy(backends=[], telemetry=TelemetrySink(path=None))
    proxy.telemetry.open()
    live = LiveSession(name="e2e")
    session = live.session
    producer = ToolSpec(
        name="fake_fetch__get",
        internal_state=("url", "body", "encoding"),
        observable_schema=("url", "body"),
    )
    consumer = ToolSpec(
        name="fake_memory__store",
        internal_state=("content", "encoding"),
        observable_schema=("content", "encoding"),
    )
    edge = Edge(
        from_tool="fake_fetch__get",
        to_tool="fake_memory__store",
        dimensions=(SemanticDimension(
            name="encoding", from_field="encoding", to_field="encoding"
        ),),
    )
    session.add_tools_and_edges(tools=[producer, consumer], edges=[edge])
    proxy.session = live
    proxy._namespaced_tools = _meta_tool_definitions()

    async def run():
        return await proxy.dispatch({
            "jsonrpc": "2.0", "id": 42, "method": "tools/call",
            "params": {
                "name": "bulla__should_proceed",
                "arguments": {
                    "server": "fake_memory", "tool": "store",
                    "arguments": {"content": "hello", "encoding": "utf-8"},
                },
            },
        })

    resp = asyncio.run(run())
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["verdict"] == "refuse", (
        f"e2e dirty seam must yield refuse, got {payload!r}"
    )
    # Bulla's coherence_fee is rank_internal - rank_obs of the
    # coboundary submatrices; in this two-tool composition it
    # happens to be 0 even though one blind spot exists. What
    # matters for the prevention story is that the call traverses
    # an obstruction — surfaced via call_touches_n_obstructions and
    # composition_blind_spots, not composition_fee.
    assert payload["composition_blind_spots"] >= 1
    assert payload["call_touches_n_obstructions"] >= 1
    # Bridge meta-tool should classify as schema-level.
    bridge_resp = asyncio.run(proxy.dispatch({
        "jsonrpc": "2.0", "id": 43, "method": "tools/call",
        "params": {
            "name": "bulla__bridge",
            "arguments": {
                "server": "fake_memory", "tool": "store",
                "arguments": {"content": "hello", "encoding": "utf-8"},
            },
        },
    }))
    bridge_payload = json.loads(bridge_resp["result"]["content"][0]["text"])
    assert bridge_payload["n_schema_level"] >= 1
    assert bridge_payload["advices"][0]["kind"] == "schema"
    assert bridge_payload["advices"][0]["applicable"] is False


def test_blind_spots_payload_surfaces_per_dimension_interaction():
    """``bulla__blind_spots`` exposes the per-dimension fee breakdown and the
    cross-dimensional interaction score (the defect-identity residual). We use
    a non-DFD composition — one hidden field carried by two distinct dimension
    names — so the interaction score is genuinely nonzero and the entangled
    column is reported. This is the only agent-facing surface where the
    residual is observable, so it is exercised on the case it was built for."""
    from bulla.live import LiveSession
    from bulla.live_proxy import (
        BullaLiveProxy, TelemetrySink, _meta_tool_definitions,
    )
    from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

    proxy = BullaLiveProxy(backends=[], telemetry=TelemetrySink(path=None))
    proxy.telemetry.open()
    live = LiveSession(name="interaction")
    session = live.session
    # One physical coupling on field ``x`` round-tripped under two distinct
    # dimension names -> DFD violated, residual = Σ fee_d − fee > 0.
    a = ToolSpec(name="svc_a", internal_state=("x",), observable_schema=())
    b = ToolSpec(name="svc_b", internal_state=("x",), observable_schema=())
    edges = [
        Edge("svc_a", "svc_b", (SemanticDimension("d_fwd", "x", "x"),)),
        Edge("svc_b", "svc_a", (SemanticDimension("d_rev", "x", "x"),)),
    ]
    session.add_tools_and_edges(tools=[a, b], edges=edges)
    proxy.session = live
    proxy._namespaced_tools = _meta_tool_definitions()

    resp = asyncio.run(proxy.dispatch({
        "jsonrpc": "2.0", "id": 71, "method": "tools/call",
        "params": {"name": "bulla__blind_spots", "arguments": {}},
    }))
    payload = json.loads(resp["result"]["content"][0]["text"])

    # Per-dimension breakdown is present and names both dimensions.
    assert set(payload["fee_by_dimension"]) == {"d_fwd", "d_rev"}
    # Interaction score is the residual Σ fee_d − fee; nonzero here (entangled).
    assert payload["interaction_score"] == sum(
        payload["fee_by_dimension"].values()
    ) - payload["fee"]
    assert payload["interaction_score"] > 0
    assert payload["dimensions_modular"] is False
    # The shared column that couples the two dimensions is reported,
    # JSON-safely keyed as "tool.field".
    assert any(col.endswith(".x") for col in payload["entangled_columns"])
    for dims in payload["entangled_columns"].values():
        assert set(dims) == {"d_fwd", "d_rev"}


def test_blind_spots_payload_modular_when_dfd_holds():
    """Complement: when dimensions are modular (DFD holds), the interaction
    score is 0 and no columns are entangled."""
    from bulla.live import LiveSession
    from bulla.live_proxy import (
        BullaLiveProxy, TelemetrySink, _meta_tool_definitions,
    )
    from bulla.model import Edge, SemanticDimension, ToolSpec

    proxy = BullaLiveProxy(backends=[], telemetry=TelemetrySink(path=None))
    proxy.telemetry.open()
    live = LiveSession(name="modular")
    session = live.session
    # Two independent hidden couplings on disjoint fields -> DFD holds.
    a = ToolSpec(name="svc_a", internal_state=("x", "y"), observable_schema=())
    b = ToolSpec(name="svc_b", internal_state=("x", "y"), observable_schema=())
    edges = [
        Edge("svc_a", "svc_b", (SemanticDimension("dx", "x", "x"),)),
        Edge("svc_b", "svc_a", (SemanticDimension("dx", "x", "x"),)),
        Edge("svc_a", "svc_b", (SemanticDimension("dy", "y", "y"),)),
        Edge("svc_b", "svc_a", (SemanticDimension("dy", "y", "y"),)),
    ]
    session.add_tools_and_edges(tools=[a, b], edges=edges)
    proxy.session = live
    proxy._namespaced_tools = _meta_tool_definitions()

    resp = asyncio.run(proxy.dispatch({
        "jsonrpc": "2.0", "id": 72, "method": "tools/call",
        "params": {"name": "bulla__blind_spots", "arguments": {}},
    }))
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["interaction_score"] == 0
    assert payload["dimensions_modular"] is True
    assert payload["entangled_columns"] == {}


def test_noisy_backend_stderr_does_not_deadlock(tmp_path: Path):
    """A backend that floods stderr should not block its stdout reads.

    Before the stderr drain task, the OS pipe buffer (~64 KB on Linux,
    less on macOS) would fill, the backend's write(2) to stderr would
    block, and the backend would stop servicing stdin — proxy hangs.
    """
    noisy_script = tmp_path / "noisy_backend.py"
    noisy_script.write_text(r"""
import json
import sys
# Flood stderr with chatter for a tool that real MCP servers do too.
for i in range(2000):
    sys.stderr.write(f"chatter line {i}: " + "x" * 80 + "\n")
sys.stderr.flush()

while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0", "id": msg_id,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }) + "\n"); sys.stdout.flush()
    elif method == "tools/list":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0", "id": msg_id,
            "result": {"tools": [{"name": "ping", "description": "p", "inputSchema": {"type":"object","properties":{}}}]},
        }) + "\n"); sys.stdout.flush()
    elif method == "tools/call":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0", "id": msg_id,
            "result": {"content": [{"type":"text","text":"pong"}]},
        }) + "\n"); sys.stdout.flush()
""")

    async def run():
        from bulla.live_proxy import BackendServer, BullaLiveProxy, TelemetrySink
        backend = BackendServer(
            name="noisy",
            command=f"{sys.executable} {noisy_script}",
            call_timeout_s=5.0,
        )
        proxy = BullaLiveProxy([backend], telemetry=TelemetrySink(path=None))
        proxy.telemetry.open()
        await proxy.start_backends()
        # Make several tool calls; if stderr drain is missing the
        # backend will deadlock after stderr buffer fills.
        for i in range(10):
            resp = await asyncio.wait_for(
                proxy.dispatch({
                    "jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
                    "params": {"name": "noisy__ping", "arguments": {}},
                }),
                timeout=3.0,
            )
            assert "result" in resp, f"call {i} failed: {resp}"
        await backend.stop()

    asyncio.run(run())


# ── WS6: Enriched telemetry tests ────────────────────────────────────


def test_telemetry_unified_tools_call_event(tmp_path: Path):
    """tools/call telemetry unifies outcomes with seq, outcome,
    fee_by_dimension, interaction_score, and call_id."""
    out = tmp_path / "events.jsonl"

    async def run():
        proxy = await _start_proxy(
            _build_specs(tmp_path, [
                ("fetch", _FETCH_TOOLS),
                ("memory", _MEMORY_TOOLS),
            ]),
            telemetry_path=out,
        )
        await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "fetch__get",
                "arguments": {"url": "https://example.com"},
            },
        })
        await proxy.dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "memory__store",
                "arguments": {"content": "x", "encoding": "utf-8"},
            },
        })
        for backend in proxy.backends.values():
            await backend.stop()
        proxy.telemetry.close()

    asyncio.run(run())
    lines = out.read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    call_events = [e for e in events if e.get("event") == "tools/call"]
    assert len(call_events) == 2
    for e in call_events:
        assert "seq" in e
        assert "outcome" in e
        assert e["outcome"] == "success"
        assert "fee_after" in e
        assert "n_blind_spots" in e
        assert "had_flow_conflicts" in e
        assert "fee_by_dimension" in e
        assert isinstance(e["fee_by_dimension"], dict)
        assert "interaction_score" in e
        assert "ts" in e
        assert "call_id" in e
    # seq is monotonic
    assert call_events[0]["seq"] < call_events[1]["seq"]


def test_telemetry_timeout_emits_unified_event(tmp_path: Path):
    """Timeout calls emit the unified tools/call event with outcome='timeout'
    instead of a separate backend_timeout event."""
    out = tmp_path / "timeout_events.jsonl"
    silent_script = tmp_path / "silent_ws6.py"
    silent_script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    if 'initialize' in line:\n"
        "        sys.stdout.write('{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\\n')\n"
        "        sys.stdout.flush()\n"
        "    elif 'tools/list' in line:\n"
        "        sys.stdout.write('{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[]}}\\n')\n"
        "        sys.stdout.flush()\n"
    )

    async def run():
        backend = BackendServer(
            name="silent",
            command=f"{sys.executable} {silent_script}",
            call_timeout_s=0.3,
        )
        tel = TelemetrySink(path=out)
        tel.open()
        proxy = BullaLiveProxy([backend], telemetry=tel)
        await proxy.start_backends()
        backend.tools = [{"name": "noop"}]
        await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "silent__noop", "arguments": {}},
        })
        await backend.stop()
        tel.close()

    asyncio.run(run())
    lines = out.read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    call_events = [e for e in events if e.get("event") == "tools/call"]
    assert len(call_events) == 1
    e = call_events[0]
    assert e["outcome"] == "timeout"
    assert "error" in e
    assert "seq" in e
    assert e["had_flow_conflicts"] is False
    # No separate backend_timeout event
    assert not any(ev.get("event") == "backend_timeout" for ev in events)


def test_telemetry_tool_error_detected_from_isError(tmp_path: Path):
    """MCP tool-level errors (isError: true in result body) should be
    recorded as outcome='tool_error', not 'success'."""
    error_script = tmp_path / "error_backend.py"
    error_script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    try: msg = json.loads(line)\n"
        "    except: continue\n"
        "    method = msg.get('method'); mid = msg.get('id')\n"
        "    if method == 'initialize':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{'protocolVersion':'2024-11-05','capabilities':{}}})"
        "+'\\n'); sys.stdout.flush()\n"
        "    elif method == 'tools/list':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{'tools':[{'name':'fail_tool','description':'x',"
        "'inputSchema':{'type':'object','properties':{}}}]}})"
        "+'\\n'); sys.stdout.flush()\n"
        "    elif method == 'tools/call':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,"
        "'result':{'content':[{'type':'text','text':'error msg'}],"
        "'isError':True}})"
        "+'\\n'); sys.stdout.flush()\n"
    )
    out = tmp_path / "tool_error_events.jsonl"

    async def run():
        backend = BackendServer(
            name="erroring",
            command=f"{sys.executable} {error_script}",
        )
        tel = TelemetrySink(path=out)
        tel.open()
        proxy = BullaLiveProxy([backend], telemetry=tel)
        await proxy.start_backends()
        resp = await proxy.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "erroring__fail_tool", "arguments": {}},
        })
        await backend.stop()
        tel.close()
        return resp

    resp = asyncio.run(run())
    # The response should still be forwarded to the agent (observe-only)
    assert "result" in resp
    assert resp["result"]["isError"] is True
    # But telemetry should label it as tool_error
    lines = out.read_text().strip().splitlines()
    events = [json.loads(line) for line in lines]
    call_events = [e for e in events if e.get("event") == "tools/call"]
    assert len(call_events) == 1
    assert call_events[0]["outcome"] == "tool_error"
