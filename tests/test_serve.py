"""Tests for MCP server, anti-reflexivity enforcement, and Sprint K surfaces."""

from __future__ import annotations

import ast
import json
import textwrap

import pytest

from bulla.model import WitnessError, WitnessErrorCode
from bulla.parser import CompositionError, load_composition
from bulla.serve import (
    MAX_DEPTH,
    TOOLS,
    RESOURCES,
    _handle_bridge,
    _handle_request,
    _handle_witness,
)
from bulla.witness import DEFAULT_POLICY


# ── Fixtures ─────────────────────────────────────────────────────────


MINIMAL_COMPOSITION = textwrap.dedent("""\
    name: test-pipeline
    tools:
      tool_a:
        internal_state: [x, y]
        observable_schema: [x]
      tool_b:
        internal_state: [x, z]
        observable_schema: [x]
    edges:
      - from: tool_a
        to: tool_b
        dimensions:
          - name: dim_x
            from_field: y
            to_field: z
""")

CLEAN_COMPOSITION = textwrap.dedent("""\
    name: clean-pipeline
    tools:
      tool_a:
        internal_state: [x, y]
        observable_schema: [x, y]
      tool_b:
        internal_state: [x, y]
        observable_schema: [x, y]
    edges:
      - from: tool_a
        to: tool_b
        dimensions:
          - name: dim_x
            from_field: y
            to_field: y
""")


# ── K1: Parser text input ────────────────────────────────────────────


class TestParserTextInput:
    def test_load_from_text(self):
        comp = load_composition(text=MINIMAL_COMPOSITION)
        assert comp.name == "test-pipeline"
        assert len(comp.tools) == 2
        assert len(comp.edges) == 1

    def test_load_from_text_no_name(self):
        yaml_no_name = textwrap.dedent("""\
            tools:
              a:
                internal_state: [x]
                observable_schema: [x]
              b:
                internal_state: [x]
                observable_schema: [x]
            edges:
              - from: a
                to: b
                dimensions:
                  - name: d
        """)
        comp = load_composition(text=yaml_no_name)
        assert comp.name == "<text>"  # default when no name and no path

    def test_both_path_and_text_raises(self):
        from pathlib import Path
        with pytest.raises(CompositionError, match="not both"):
            load_composition(path=Path("x.yaml"), text="x")

    def test_neither_path_nor_text_raises(self):
        with pytest.raises(CompositionError, match="Provide path or text"):
            load_composition()

    def test_invalid_yaml_text(self):
        with pytest.raises(CompositionError, match="Invalid YAML"):
            load_composition(text=": : : invalid")


# ── K2: Bulla Patch format ────────────────────────────────────────────


class TestBullaPatch:
    def test_seam_patch_has_version(self):
        result = _handle_witness({"composition": MINIMAL_COMPOSITION})
        patches = result["patches"]
        assert len(patches) > 0
        for p in patches:
            assert p["bulla_patch_version"] == "0.1.0"
            assert "action" in p
            assert "field" in p
            # NOT RFC 6902: has target_tool, dimension
            assert "target_tool" in p
            assert "dimension" in p


# ── K3: Policy profile ──────────────────────────────────────────────


class TestPolicyProfile:
    def test_default_policy_in_receipt(self):
        result = _handle_witness({"composition": MINIMAL_COMPOSITION})
        assert result["policy_profile"]["name"] == "witness.default.v1"

    def test_custom_policy_name_in_receipt(self):
        result = _handle_witness({
            "composition": MINIMAL_COMPOSITION,
            "policy": "custom.strict.v2",
        })
        assert result["policy_profile"]["name"] == "custom.strict.v2"

    def test_policy_affects_receipt_hash(self):
        r1 = _handle_witness({
            "composition": CLEAN_COMPOSITION,
            "policy": "policy_a",
        })
        r2 = _handle_witness({
            "composition": CLEAN_COMPOSITION,
            "policy": "policy_b",
        })
        assert r1["receipt_hash"] != r2["receipt_hash"]


# ── K5: MCP server ──────────────────────────────────────────────────


class TestMCPInitialize:
    def test_initialize(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert resp["id"] == 1
        result = resp["result"]
        assert "protocolVersion" in result
        assert result["serverInfo"]["name"] == "bulla"
        assert "tools" in result["capabilities"]
        assert "resources" in result["capabilities"]

    def test_initialized_notification_no_response(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        assert resp is None


class TestMCPToolsList:
    def test_tools_list(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        tools = resp["result"]["tools"]
        names = [t["name"] for t in tools]
        assert "bulla.witness" in names
        assert "bulla.bridge" in names
        assert len(names) == 2  # exactly two tools


class TestMCPResourcesList:
    def test_resources_list(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/list",
        })
        resources = resp["result"]["resources"]
        assert len(resources) == 1
        assert resources[0]["uri"] == "bulla://taxonomy"

    def test_read_taxonomy(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "bulla://taxonomy"},
        })
        contents = resp["result"]["contents"]
        assert len(contents) == 1
        assert "dimensions:" in contents[0]["text"]
        assert contents[0]["mimeType"] == "text/yaml"

    def test_unknown_resource(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "resources/read",
            "params": {"uri": "bulla://nonexistent"},
        })
        assert "error" in resp


class TestMCPWitnessTool:
    def test_witness_via_tools_call(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "bulla.witness",
                "arguments": {"composition": MINIMAL_COMPOSITION},
            },
        })
        content = resp["result"]["content"]
        assert content[0]["type"] == "text"
        receipt = json.loads(content[0]["text"])
        assert "receipt_hash" in receipt
        assert "disposition" in receipt
        assert receipt["policy_profile"]["name"] == "witness.default.v1"

    def test_witness_with_blind_spots(self):
        result = _handle_witness({"composition": MINIMAL_COMPOSITION})
        assert result["blind_spots_count"] > 0
        assert result["disposition"] in (
            "proceed_with_bridge",
            "refuse_pending_disclosure",
        )
        assert len(result["patches"]) > 0

    def test_witness_clean_composition(self):
        result = _handle_witness({"composition": CLEAN_COMPOSITION})
        assert result["blind_spots_count"] == 0
        assert result["disposition"] == "proceed"
        assert result["patches"] == []

    def test_invalid_composition(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "bulla.witness",
                "arguments": {"composition": "not: valid: composition"},
            },
        })
        assert "error" in resp
        assert resp["error"]["data"]["error_type"] == "invalid_composition"


class TestMCPBridgeTool:
    def test_bridge_via_tools_call(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "bulla.bridge",
                "arguments": {"composition": MINIMAL_COMPOSITION},
            },
        })
        content = resp["result"]["content"]
        result = json.loads(content[0]["text"])
        assert result["before"]["blind_spots"] > 0
        assert result["after"]["blind_spots"] == 0
        assert "patched_composition" in result
        assert "receipt" in result
        assert "original_receipt" in result
        assert "patches" in result

    def test_bridge_clean_composition(self):
        result = _handle_bridge({"composition": CLEAN_COMPOSITION})
        assert result["before"]["blind_spots"] == 0
        assert result["after"]["blind_spots"] == 0
        assert result["patched_composition"] == CLEAN_COMPOSITION
        # When clean, original_receipt == receipt (same composition)
        assert result["original_receipt"] == result["receipt"]

    def test_bridge_receipt_reflects_patched_state(self):
        result = _handle_bridge({"composition": MINIMAL_COMPOSITION})
        receipt = result["receipt"]
        # Receipt is for the patched composition, so should be clean
        assert receipt["blind_spots_count"] == 0
        assert receipt["disposition"] == "proceed"

    def test_bridge_dual_receipt(self):
        """Bridge emits both original and patched receipts."""
        result = _handle_bridge({"composition": MINIMAL_COMPOSITION})
        orig = result["original_receipt"]
        patched = result["receipt"]
        # Original has blind spots
        assert orig["blind_spots_count"] > 0
        assert orig["disposition"] != "proceed"
        # Patched is clean
        assert patched["blind_spots_count"] == 0
        assert patched["disposition"] == "proceed"
        # Different composition hashes
        assert orig["composition_hash"] != patched["composition_hash"]
        # Patches are Bulla Patch v0.1
        assert len(result["patches"]) > 0
        for p in result["patches"]:
            assert p["bulla_patch_version"] == "0.1.0"


class TestMCPErrors:
    def test_unknown_tool(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 30,
            "method": "tools/call",
            "params": {
                "name": "nonexistent.tool",
                "arguments": {},
            },
        })
        assert "error" in resp
        assert resp["error"]["data"]["error_type"] == "invalid_params"

    def test_unknown_method(self):
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 31,
            "method": "nonexistent/method",
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32601


# ── K6: Anti-reflexivity enforcement ─────────────────────────────────


class TestAntiReflexivity:
    def test_diagnostic_has_no_witness_imports(self):
        """Law 1: Measurement cannot depend on its own judgment.

        diagnostic.py must have zero imports from witness.py.
        """
        import bulla.diagnostic as diag_module
        import inspect
        source = inspect.getsource(diag_module)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module is None or "witness" not in node.module, (
                    f"diagnostic.py imports from witness: {ast.dump(node)}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "witness" not in alias.name, (
                        f"diagnostic.py imports witness: {alias.name}"
                    )

    def test_recursion_depth_limit(self):
        """Law 7: Recursive self-audit must be bounded."""
        resp = _handle_request({
            "jsonrpc": "2.0",
            "id": 40,
            "method": "tools/call",
            "params": {
                "name": "bulla.witness",
                "arguments": {
                    "composition": CLEAN_COMPOSITION,
                    "depth": MAX_DEPTH + 1,
                },
            },
        })
        assert "error" in resp
        assert resp["error"]["data"]["error_type"] == "recursion_limit"

    def test_depth_zero_succeeds(self):
        """Depth 0 (default) should work fine."""
        result = _handle_witness({
            "composition": CLEAN_COMPOSITION,
            "depth": 0,
        })
        assert "receipt_hash" in result

    def test_depth_at_max_succeeds(self):
        """Depth exactly at MAX_DEPTH should still work."""
        result = _handle_witness({
            "composition": CLEAN_COMPOSITION,
            "depth": MAX_DEPTH,
        })
        assert "receipt_hash" in result


# ── K7: Typed error enum ─────────────────────────────────────────────


class TestWitnessErrors:
    def test_error_codes_are_strings(self):
        for code in WitnessErrorCode:
            assert isinstance(code.value, str)

    def test_witness_error_carries_code(self):
        err = WitnessError(WitnessErrorCode.RECURSION_LIMIT, "too deep")
        assert err.code == WitnessErrorCode.RECURSION_LIMIT
        assert err.message == "too deep"
        assert "RECURSION_LIMIT" in str(err)

    def test_all_error_types(self):
        codes = {c.value for c in WitnessErrorCode}
        expected = {
            "invalid_composition",
            "invalid_params",
            "recursion_limit",
            "internal",
        }
        assert codes == expected


# ── K4: Hash semantics ──────────────────────────────────────────────


class TestHashSemantics:
    def test_same_composition_different_times_different_receipt_hash(self):
        """Receipt hash includes timestamp — each witness event is unique.
        For deduplication, use diagnostic_hash instead."""
        from bulla.diagnostic import diagnose
        from bulla.witness import witness

        comp = load_composition(text=CLEAN_COMPOSITION)
        diag = diagnose(comp)

        r1 = witness(diag, comp)

        # Manually construct with different timestamp
        from bulla.model import Disposition, WitnessReceipt
        r2 = WitnessReceipt(
            receipt_version=r1.receipt_version,
            kernel_version=r1.kernel_version,
            composition_hash=r1.composition_hash,
            diagnostic_hash=r1.diagnostic_hash,
            policy_profile=r1.policy_profile,
            fee=r1.fee,
            blind_spots_count=r1.blind_spots_count,
            bridges_required=r1.bridges_required,
            unknown_dimensions=r1.unknown_dimensions,
            disposition=r1.disposition,
            timestamp="2099-01-01T00:00:00+00:00",  # different
            patches=r1.patches,
        )

        # Receipt hashes differ (unique event identity)
        assert r1.receipt_hash != r2.receipt_hash
        # But diagnostic hashes are identical (same measurement)
        assert r1.diagnostic_hash == r2.diagnostic_hash

    def test_three_hash_boundaries_are_independent(self):
        """composition_hash, diagnostic_hash, receipt_hash are distinct."""
        result = _handle_witness({"composition": MINIMAL_COMPOSITION})
        hashes = {
            result["composition_hash"],
            result["diagnostic_hash"],
            result["receipt_hash"],
        }
        assert len(hashes) == 3  # all different
