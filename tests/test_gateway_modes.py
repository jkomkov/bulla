"""Gateway modes: side-effect classification, shadow receipts, gate scoping.

The gateway law is *no unreceipted side effects*: reads pass freely; every
completed side-effecting call in shadow mode leaves a signed per-call deed
carrying the v0.2 recourse envelope; enforce mode refuses side-effecting calls
without a qualifying counterparty deed while leaving reads ungated. Shadow
NEVER blocks — a broken deed surface degrades to telemetry, not errors.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from bulla.live_proxy import BackendServer, BullaLiveProxy, TelemetrySink
from bulla.registry import DeedLog, verify_deed_record
from bulla.side_effects import COMMIT, NOTIFY, READ, WRITE, classify_tool

from .test_live_proxy import _fake_backend_command  # reuse the fake backend


# ── classification unit tests ────────────────────────────────────────


class TestClassifyTool:
    def test_read_only_annotation_wins(self):
        assert classify_tool({"name": "nuke", "annotations": {"readOnlyHint": True}}) == READ

    def test_destructive_annotation_wins(self):
        assert classify_tool({"name": "get_x", "annotations": {"destructiveHint": True}}) == COMMIT

    def test_declared_side_effecting_refined_by_name(self):
        assert classify_tool({"name": "send_email", "annotations": {"readOnlyHint": False}}) == NOTIFY

    def test_read_stems(self):
        assert classify_tool({"name": "list_files"}) == READ
        assert classify_tool({"name": "get"}) == READ

    def test_commit_stems(self):
        assert classify_tool({"name": "delete_branch"}) == COMMIT

    def test_unknown_means_write(self):
        assert classify_tool({"name": "frobnicate"}) == WRITE
        assert classify_tool({}) == WRITE


# ── proxy integration ────────────────────────────────────────────────

_READ_TOOL = [{
    "name": "get",
    "description": "Fetch",
    "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
    "annotations": {"readOnlyHint": True},
    "_internal_state": ["url"],
    "_observable_schema": ["url"],
}]

_WRITE_TOOL = [{
    "name": "store",
    "description": "Store content",
    "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}}},
    "_internal_state": ["content"],
    "_observable_schema": ["content"],
}]


def _signer():
    from bulla.identity import LocalEd25519Signer

    return LocalEd25519Signer.generate()


async def _gateway(tmp_path: Path, **kw) -> tuple[BullaLiveProxy, DeedLog]:
    log = DeedLog(tmp_path / "shadow-log.jsonl")
    cmd_r, env_r = _fake_backend_command(_READ_TOOL, tmp_path)
    cmd_w, env_w = _fake_backend_command(_WRITE_TOOL, tmp_path)
    proxy = BullaLiveProxy(
        [
            BackendServer(name="fetch", command=cmd_r, env=env_r),
            BackendServer(name="memory", command=cmd_w, env=env_w),
        ],
        telemetry=TelemetrySink(path=None),
        registry=log,
        **kw,
    )
    await proxy.start_backends()
    return proxy, log


async def _call(proxy: BullaLiveProxy, name: str, args: dict) -> dict:
    return await proxy.dispatch({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": name, "arguments": args},
    })


def test_shadow_emits_envelope_receipt_for_writes_only(tmp_path: Path):
    async def run():
        signer = _signer()
        proxy, log = await _gateway(
            tmp_path, signer=signer, shadow=True,
            mandate={"principal": "did:web:ops.example", "policy": "sha256:" + "aa" * 32},
        )
        try:
            # a READ: passes, no receipt
            resp = await _call(proxy, "fetch__get", {"url": "https://x"})
            assert "error" not in resp
            assert len(log) == 0
            # a WRITE: passes AND leaves a receipt
            resp = await _call(proxy, "memory__store", {"content": "hi"})
            assert "error" not in resp
            assert len(log) == 1
            rec = log.by_composition(log.deeds()[0][1].composition_hash)[0]
            assert verify_deed_record(rec)
            env = rec["envelope"]
            assert env["bounds"]["scope"].startswith("call:memory__store@sha256:")
            rungs = [r["rung"] for r in env["recourse"]["remedies"]]
            assert rungs[0] == "recompute"
            assert "escalate" in rungs  # mandate configured -> surviving principal
            assert env["recourse"]["forum"]["trusted_root_ref"]
        finally:
            for b in proxy.backends.values():
                await b.stop()

    asyncio.run(run())


def test_shadow_never_blocks_without_signer(tmp_path: Path):
    async def run():
        proxy, log = await _gateway(tmp_path, signer=None, shadow=True)
        try:
            resp = await _call(proxy, "memory__store", {"content": "hi"})
            assert "error" not in resp  # the call succeeded
            assert len(log) == 0       # degraded to telemetry-only, no receipt
        finally:
            for b in proxy.backends.values():
                await b.stop()

    asyncio.run(run())


def test_enforce_gates_writes_but_not_reads(tmp_path: Path):
    async def run():
        proxy, _log = await _gateway(tmp_path, signer=_signer(), enforce=True)
        try:
            # reads are exempt from the gate by default
            resp = await _call(proxy, "fetch__get", {"url": "https://x"})
            assert "error" not in resp
            # a side-effecting call with no counterparty deed is refused
            resp = await _call(proxy, "memory__store", {"content": "hi"})
            assert resp.get("error", {}).get("code") == -32001
            cert = resp["error"]["data"]["refusal_certificate"]
            assert cert.get("deficiency")
        finally:
            for b in proxy.backends.values():
                await b.stop()

    asyncio.run(run())


def test_gate_reads_flag_gates_everything(tmp_path: Path):
    async def run():
        proxy, _log = await _gateway(
            tmp_path, signer=_signer(), enforce=True, gate_reads=True
        )
        try:
            resp = await _call(proxy, "fetch__get", {"url": "https://x"})
            assert resp.get("error", {}).get("code") == -32001
        finally:
            for b in proxy.backends.values():
                await b.stop()

    asyncio.run(run())
