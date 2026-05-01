from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulla.cli import _cmd_proxy


def test_cmd_proxy_epistemic_receipt_shape_in_json_output(
    tmp_path: Path,
    capsys,
):
    """CLI acceptance: epistemic_receipt appears with correct shape when fee > 0."""
    manifests = tmp_path / "manifests"
    manifests.mkdir()

    # Richer schemas that create hidden dimensions and nonzero fee
    alpha_manifest = {
        "tools": [
            {
                "name": "get_data",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            }
        ]
    }
    beta_manifest = {
        "tools": [
            {
                "name": "process_data",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "mode": {"type": "string"},
                    },
                },
                "outputSchema": {
                    "type": "object",
                    "properties": {"result": {"type": "string"}},
                },
            }
        ]
    }
    (manifests / "alpha.json").write_text(json.dumps(alpha_manifest))
    (manifests / "beta.json").write_text(json.dumps(beta_manifest))

    trace = {
        "name": "epistemic_acceptance",
        "calls": [
            {
                "server": "alpha",
                "tool": "get_data",
                "result": {"path": "/tmp", "content": "hello"},
            },
            {
                "server": "beta",
                "tool": "process_data",
                "arguments": {"path": "/tmp"},
                "argument_sources": {
                    "path": {"call_id": 1, "field": "path"},
                },
            },
        ],
    }
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(trace))

    _cmd_proxy(
        argparse.Namespace(
            manifests=manifests,
            trace=trace_path,
            format="json",
            output=None,
        )
    )

    captured = json.loads(capsys.readouterr().out)
    call_2 = captured["calls"][1]

    # epistemic_receipt must be present (fee > 0 in the local cluster)
    assert "epistemic_receipt" in call_2, (
        "epistemic_receipt missing from CLI JSON when local fee > 0"
    )
    er = call_2["epistemic_receipt"]

    # Required fields always present
    assert "fee" in er
    assert "geometry_dividend" in er
    assert "sigma_star" in er
    assert "regime" in er
    assert er["regime"] in ("exact", "surrogate")
    assert isinstance(er["fee"], int)
    assert isinstance(er["geometry_dividend"], (int, float))
    assert isinstance(er["sigma_star"], (int, float))

    # Conditional fields: exact regime omits forced_cost and downgrade
    if er["regime"] == "exact":
        assert "forced_cost" not in er
        assert "downgrade" not in er

    # First call (no flows, single tool) should NOT have epistemic_receipt
    call_1 = captured["calls"][0]
    assert "epistemic_receipt" not in call_1


def test_cmd_proxy_replays_trace_and_emits_local_fee(
    tmp_path: Path,
    capsys,
):
    manifests = tmp_path / "manifests"
    manifests.mkdir()

    left_manifest = {
        "tools": [
            {
                "name": "emit_path",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
                "outputSchema": {"type": "object", "properties": {}},
            }
        ]
    }
    right_manifest = {
        "tools": [
            {
                "name": "consume_path",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
                "outputSchema": {"type": "object", "properties": {}},
            }
        ]
    }
    (manifests / "left-server.json").write_text(json.dumps(left_manifest))
    (manifests / "right-server.json").write_text(json.dumps(right_manifest))

    trace = {
        "name": "proxy_cli_test",
        "calls": [
            {
                "server": "left-server",
                "tool": "emit_path",
                "arguments": {"path": "/tmp/a"},
                "result": {},
            },
            {
                "server": "right-server",
                "tool": "consume_path",
                "arguments": {"path": "/tmp/a"},
                "argument_sources": {
                    "path": {"call_id": 1, "field": "path"},
                },
                "result": {},
            },
        ],
    }
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps(trace))

    _cmd_proxy(
        argparse.Namespace(
            manifests=manifests,
            trace=trace_path,
            format="json",
            output=None,
        )
    )

    captured = json.loads(capsys.readouterr().out)
    assert captured["trace_name"] == "proxy_cli_test"
    assert captured["calls"][-1]["local_diagnostic"]["n_tools"] == 2
    assert captured["calls"][-1]["local_diagnostic"]["n_edges"] == 1
