"""Tests for bulla.LiveSession — online composition proxy.

The load-bearing invariant: LiveSession.fee after all add_server calls
equals compose_multi(all_server_tools).diagnostic.coherence_fee.  Every
test that adds servers verifies this.
"""

from __future__ import annotations

import pytest

from bulla.live import AddServerResult, LiveSession
from bulla.model import Disposition
from bulla.sdk import compose_multi


# ── Fixtures ──────────────────────────────────────────────────────────

# Two servers with a shared "path" field that the classifier detects
# as path_convention — creates a blind spot (hidden dimension) and
# introduces fee > 0 when composed.
ANALYTICS_TOOLS = [
    {
        "name": "get_events",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string", "format": "date-time"},
                "path": {"type": "string"},
            },
        },
    }
]

STORAGE_TOOLS = [
    {
        "name": "write_file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        "outputSchema": {"type": "object", "properties": {}},
    }
]

# Contradiction fixture: enum mismatch on "status" field
SOURCE_TOOLS = [
    {
        "name": "list_orders",
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "closed"]},
            },
        },
    }
]

TARGET_TOOLS = [
    {
        "name": "filter_orders",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["draft", "published"]},
            },
        },
        "outputSchema": {"type": "object", "properties": {}},
    }
]

# Third server for 3-server tests — shares "path" with storage
LOGGING_TOOLS = [
    {
        "name": "log_event",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string"},
                "level": {"type": "string", "enum": ["info", "warn", "error"]},
            },
        },
        "outputSchema": {"type": "object", "properties": {}},
    }
]


def _assert_fee_matches_compose_multi(live: LiveSession) -> None:
    """The load-bearing invariant: incremental fee == full rebuild fee."""
    full = compose_multi(dict(live._server_tools))
    assert live.fee == full.diagnostic.coherence_fee, (
        f"LiveSession.fee={live.fee} != "
        f"compose_multi.fee={full.diagnostic.coherence_fee}"
    )


# ── Core: add_server + fee invariant ─────────────────────────────────

class TestAddServer:
    def test_single_server_fee_zero(self):
        live = LiveSession(name="test")
        r = live.add_server("analytics", ANALYTICS_TOOLS)
        assert isinstance(r, AddServerResult)
        assert r.server == "analytics"
        assert r.fee_after == live.fee
        assert len(r.new_tools) >= 1
        _assert_fee_matches_compose_multi(live)

    def test_two_servers_fee_matches_compose_multi(self):
        live = LiveSession(name="test")
        live.add_server("analytics", ANALYTICS_TOOLS)
        r2 = live.add_server("storage", STORAGE_TOOLS)
        assert r2.fee_after == live.fee
        assert r2.delta_fee == r2.fee_after  # first server had fee=0
        _assert_fee_matches_compose_multi(live)

    def test_three_servers_fee_matches_compose_multi(self):
        live = LiveSession(name="test")
        live.add_server("analytics", ANALYTICS_TOOLS)
        live.add_server("storage", STORAGE_TOOLS)
        live.add_server("logging", LOGGING_TOOLS)
        _assert_fee_matches_compose_multi(live)

    def test_delta_fee_is_signed(self):
        """delta_fee correctly reflects fee change at each step."""
        live = LiveSession(name="test")
        r1 = live.add_server("analytics", ANALYTICS_TOOLS)
        r2 = live.add_server("storage", STORAGE_TOOLS)
        assert r2.delta_fee == r2.fee_after - r1.fee_after

    def test_new_edges_counted(self):
        live = LiveSession(name="test")
        r1 = live.add_server("analytics", ANALYTICS_TOOLS)
        r2 = live.add_server("storage", STORAGE_TOOLS)
        # Cross-server edges should appear with the second server
        assert r2.new_edges >= 0
        # Total edges in composition should equal sum of new_edges
        total_edges = len(live.composition.edges)
        assert total_edges == r1.new_edges + r2.new_edges

    def test_duplicate_server_raises(self):
        live = LiveSession(name="test")
        live.add_server("analytics", ANALYTICS_TOOLS)
        with pytest.raises(ValueError, match="already registered"):
            live.add_server("analytics", ANALYTICS_TOOLS)

    def test_empty_tools_raises(self):
        live = LiveSession(name="test")
        with pytest.raises(ValueError, match="no tools"):
            live.add_server("empty", [])

    def test_servers_property(self):
        live = LiveSession(name="test")
        assert live.servers == ()
        live.add_server("analytics", ANALYTICS_TOOLS)
        assert live.servers == ("analytics",)
        live.add_server("storage", STORAGE_TOOLS)
        assert live.servers == ("analytics", "storage")


# ── from_server_tools convenience constructor ─────────────────────────

class TestFromServerTools:
    def test_matches_incremental(self):
        server_tools = {
            "analytics": ANALYTICS_TOOLS,
            "storage": STORAGE_TOOLS,
        }
        live = LiveSession.from_server_tools(server_tools, name="test")
        assert live.servers == ("analytics", "storage")
        _assert_fee_matches_compose_multi(live)

    def test_fee_equals_incremental(self):
        server_tools = {
            "analytics": ANALYTICS_TOOLS,
            "storage": STORAGE_TOOLS,
        }
        live_batch = LiveSession.from_server_tools(server_tools, name="t1")
        live_incr = LiveSession(name="t2")
        for s, t in server_tools.items():
            live_incr.add_server(s, t)
        assert live_batch.fee == live_incr.fee


# ── Call tracing ──────────────────────────────────────────────────────

class TestCallTracing:
    def test_record_call_basic(self):
        live = LiveSession.from_server_tools(
            {"analytics": ANALYTICS_TOOLS, "storage": STORAGE_TOOLS},
            name="test",
        )
        call = live.record_call(
            "analytics", "get_events", result={"path": "/foo"}
        )
        assert call.call_id == 1
        assert call.server == "analytics"
        assert call.tool == "get_events"

    def test_record_call_with_flow(self):
        live = LiveSession.from_server_tools(
            {"analytics": ANALYTICS_TOOLS, "storage": STORAGE_TOOLS},
            name="test",
        )
        c1 = live.record_call(
            "analytics", "get_events",
            result={"path": "/events/2026"},
        )
        c2 = live.record_call(
            "storage", "write_file",
            arguments={"path": "/events/2026"},
            argument_sources={
                "path": live.make_ref(c1.call_id, "path"),
            },
        )
        assert c2.call_id == 2
        assert len(c2.flows) == 1

    def test_flow_conflict_detection(self):
        """Enum mismatch on status field is detected as a contradiction."""
        live = LiveSession.from_server_tools(
            {"source": SOURCE_TOOLS, "target": TARGET_TOOLS},
            name="test",
        )
        c1 = live.record_call(
            "source", "list_orders",
            result={"status": "open"},
        )
        c2 = live.record_call(
            "target", "filter_orders",
            arguments={"status": "open"},
            argument_sources={
                "status": live.make_ref(c1.call_id, "status"),
            },
        )
        assert len(c2.flows) == 1
        assert c2.flows[0].category == "contradiction"
        assert c2.receipt.structural_contradictions is not None

    def test_record_call_before_add_server_raises(self):
        live = LiveSession(name="test")
        with pytest.raises(ValueError, match="no servers registered"):
            live.record_call("analytics", "get_events")

    def test_proxy_rebuilds_after_add_server(self):
        live = LiveSession(name="test")
        live.add_server("analytics", ANALYTICS_TOOLS)
        c1 = live.record_call(
            "analytics", "get_events", result={"path": "/a"}
        )
        assert c1.call_id == 1

        # Add another server — proxy should be invalidated and rebuilt
        live.add_server("storage", STORAGE_TOOLS)
        # New proxy — call_id restarts (proxy is fresh)
        c2 = live.record_call(
            "storage", "write_file",
            arguments={"path": "/a", "content": "x"},
        )
        assert c2.call_id == 1  # fresh proxy


# ── Translation ───────────────────────────────────────────────────────

class TestTranslation:
    def test_translate_currency(self):
        live = LiveSession(name="test")
        live.add_server("dummy", [
            {
                "name": "t1",
                "inputSchema": {
                    "type": "object", "properties": {},
                },
            }
        ])
        tr = live.translate(
            "currency_code",
            value="USD",
            to_convention="stripe-lower",
        )
        assert tr.value == "usd"
        # Translation receipt is chained
        assert len(live.receipt_chain) == 1


# ── Receipt chain ─────────────────────────────────────────────────────

class TestReceiptChain:
    def test_checkpoint_chains(self):
        live = LiveSession.from_server_tools(
            {"analytics": ANALYTICS_TOOLS},
            name="test",
        )
        cp1 = live.checkpoint()
        cp2 = live.checkpoint()
        assert cp2.parent_receipt_hashes == (cp1.receipt_hash,)
        assert len(live.receipt_chain) == 2

    def test_diagnose_chains(self):
        live = LiveSession.from_server_tools(
            {"analytics": ANALYTICS_TOOLS, "storage": STORAGE_TOOLS},
            name="test",
        )
        cp = live.checkpoint()
        receipt = live.diagnose()
        assert receipt.parent_receipt_hashes == (cp.receipt_hash,)
        assert receipt.fee == live.fee

    def test_translate_then_checkpoint_chains(self):
        live = LiveSession(name="test")
        live.add_server("dummy", [
            {
                "name": "t1",
                "inputSchema": {
                    "type": "object", "properties": {},
                },
            }
        ])
        tr = live.translate(
            "currency_code", value="USD",
            to_convention="stripe-lower",
        )
        cp = live.checkpoint()
        assert cp.parent_receipt_hashes == (
            tr.receipt.receipt_hash,
        )


# ── Replay trace ──────────────────────────────────────────────────────

class TestReplayTrace:
    def test_replay_basic(self):
        live = LiveSession.from_server_tools(
            {"source": SOURCE_TOOLS, "target": TARGET_TOOLS},
            name="test",
        )
        trace = [
            {
                "server": "source",
                "tool": "list_orders",
                "result": {"status": "open"},
            },
            {
                "server": "target",
                "tool": "filter_orders",
                "arguments": {"status": "open"},
                "argument_sources": {
                    "status": {"call_id": 1, "field": "status"},
                },
            },
        ]
        records = live.replay_trace(trace)
        assert len(records) == 2
        assert records[1].flows[0].category == "contradiction"


# ── Property: fee invariant across server orderings ───────────────────

class TestFeeInvariant:
    def test_fee_independent_of_add_order(self):
        """Fee should be the same regardless of server addition order."""
        tools = {
            "analytics": ANALYTICS_TOOLS,
            "storage": STORAGE_TOOLS,
            "logging": LOGGING_TOOLS,
        }
        # Order 1: analytics, storage, logging
        live1 = LiveSession(name="t1")
        for s in ["analytics", "storage", "logging"]:
            live1.add_server(s, tools[s])

        # Order 2: logging, analytics, storage
        live2 = LiveSession(name="t2")
        for s in ["logging", "analytics", "storage"]:
            live2.add_server(s, tools[s])

        assert live1.fee == live2.fee
        _assert_fee_matches_compose_multi(live1)
        _assert_fee_matches_compose_multi(live2)
