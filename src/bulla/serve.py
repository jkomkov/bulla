"""MCP stdio server: exposes the witness kernel to agents.

Two tools:
  - bulla.witness: composition → WitnessReceipt
  - bulla.bridge:  composition → patched composition + receipt

One resource:
  - bulla.taxonomy: returns the convention taxonomy

Anti-reflexivity contract:
  - Measurement (diagnostic.py) has zero imports from this module
  - The server proposes patches; it never silently mutates compositions
  - Recursive self-audit is bounded by caller-supplied max_depth
  - Policy is explicit and named in every receipt

Transport: JSON-RPC 2.0 over stdin/stdout (MCP stdio).
No SDK dependency — pure stdlib.
"""

from __future__ import annotations

import importlib.resources
import json
import sys
from typing import Any

import yaml

from bulla import __version__
from bulla.diagnostic import diagnose
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    PolicyProfile,
    WitnessError,
    WitnessErrorCode,
)
from bulla.parser import CompositionError, load_composition
from bulla.witness import witness

SERVER_NAME = "bulla"
SERVER_VERSION = __version__
PROTOCOL_VERSION = "2024-11-05"
MAX_DEPTH = 10

# JSON-RPC error codes for MCP transport
_RPC_CODES = {
    WitnessErrorCode.INVALID_COMPOSITION: -32001,
    WitnessErrorCode.INVALID_PARAMS: -32002,
    WitnessErrorCode.RECURSION_LIMIT: -32003,
    WitnessErrorCode.INTERNAL: -32004,
}


def _error_response(
    msg_id: int | str | None,
    code: WitnessErrorCode,
    message: str,
) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": _RPC_CODES.get(code, -32000),
            "message": message,
            "data": {"error_type": code.value},
        },
    }


# ── Tool definitions ─────────────────────────────────────────────────


TOOLS = [
    {
        "name": "bulla.witness",
        "description": (
            "Measure semantic composition risk and emit a WitnessReceipt. "
            "Returns disposition (proceed / proceed_with_bridge / "
            "proceed_with_receipt / refuse_pending_disclosure / "
            "refuse_pending_human_review), coherence fee, blind spot count, "
            "and machine-actionable Bulla Patches."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "composition": {
                    "type": "string",
                    "description": "Composition YAML as a string",
                },
                "policy": {
                    "type": "string",
                    "description": (
                        "Named policy profile (default: witness.default.v1)"
                    ),
                    "default": DEFAULT_POLICY_PROFILE.name,
                },
                "depth": {
                    "type": "integer",
                    "description": (
                        "Current recursion depth for bounded self-audit "
                        f"(max: {MAX_DEPTH})"
                    ),
                    "default": 0,
                },
            },
            "required": ["composition"],
        },
    },
    {
        "name": "bulla.bridge",
        "description": (
            "Auto-bridge a composition: diagnose, apply patches, "
            "re-diagnose, and return the patched composition YAML "
            "with before/after metrics and a WitnessReceipt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "composition": {
                    "type": "string",
                    "description": "Composition YAML as a string",
                },
                "policy": {
                    "type": "string",
                    "description": (
                        "Named policy profile (default: witness.default.v1)"
                    ),
                    "default": DEFAULT_POLICY_PROFILE.name,
                },
            },
            "required": ["composition"],
        },
    },
]


# ── Resource definitions ─────────────────────────────────────────────


def _load_taxonomy() -> str:
    pkg = importlib.resources.files("bulla")
    taxonomy_path = pkg / "taxonomy.yaml"
    return taxonomy_path.read_text(encoding="utf-8")


RESOURCES = [
    {
        "uri": "bulla://taxonomy",
        "name": "bulla.taxonomy",
        "description": "Convention taxonomy (10 dimensions with field patterns, description keywords, known values)",
        "mimeType": "text/yaml",
    },
]


# ── Tool handlers ────────────────────────────────────────────────────


def _handle_witness(args: dict) -> dict:
    composition_yaml = args.get("composition", "")
    policy_name = args.get("policy", DEFAULT_POLICY_PROFILE.name)
    depth = args.get("depth", 0)

    if depth > MAX_DEPTH:
        raise WitnessError(
            WitnessErrorCode.RECURSION_LIMIT,
            f"Recursion depth {depth} exceeds max {MAX_DEPTH}",
        )

    policy = PolicyProfile(name=policy_name)
    comp = load_composition(text=composition_yaml)
    diag = diagnose(comp)
    receipt = witness(diag, comp, policy_profile=policy)
    return receipt.to_dict()


def _handle_bridge(args: dict) -> dict:
    composition_yaml = args.get("composition", "")
    policy_name = args.get("policy", DEFAULT_POLICY_PROFILE.name)
    policy = PolicyProfile(name=policy_name)

    # Diagnose and witness original
    comp = load_composition(text=composition_yaml)
    diag = diagnose(comp)
    original_receipt = witness(diag, comp, policy_profile=policy)
    before_bs = len(diag.blind_spots)

    if before_bs == 0:
        return {
            "patched_composition": composition_yaml,
            "original_receipt": original_receipt.to_dict(),
            "receipt": original_receipt.to_dict(),
            "patches": [],
            "before": {"blind_spots": 0},
            "after": {"blind_spots": 0},
        }

    # Apply bridges to raw YAML
    raw = yaml.safe_load(composition_yaml)
    tools_section = raw.get("tools", {})
    for br in diag.bridges:
        for tool_name in br.add_to:
            if tool_name in tools_section:
                tool = tools_section[tool_name]
                internal = tool.get("internal_state", [])
                obs = tool.get("observable_schema", [])
                if br.field not in internal:
                    internal.append(br.field)
                if br.field not in obs:
                    obs.append(br.field)

    patched_yaml = yaml.dump(raw, default_flow_style=False, sort_keys=False)

    # Re-diagnose and witness patched
    patched_comp = load_composition(text=patched_yaml)
    patched_diag = diagnose(patched_comp)
    patched_receipt = witness(patched_diag, patched_comp, policy_profile=policy)

    return {
        "patched_composition": patched_yaml,
        "original_receipt": original_receipt.to_dict(),
        "receipt": patched_receipt.to_dict(),
        "patches": [p.to_bulla_patch() for p in original_receipt.patches],
        "before": {"blind_spots": before_bs},
        "after": {"blind_spots": len(patched_diag.blind_spots)},
    }


TOOL_HANDLERS = {
    "bulla.witness": _handle_witness,
    "bulla.bridge": _handle_bridge,
}


# ── JSON-RPC dispatch ────────────────────────────────────────────────


def _handle_request(request: dict) -> dict | None:
    """Dispatch a single JSON-RPC 2.0 request. Returns response or None for notifications."""
    msg_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    # Notifications (no id) get no response
    is_notification = "id" not in request

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        }

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return _error_response(
                msg_id,
                WitnessErrorCode.INVALID_PARAMS,
                f"Unknown tool: {tool_name}",
            )

        try:
            result = handler(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, indent=2),
                        }
                    ],
                },
            }
        except CompositionError as e:
            return _error_response(
                msg_id,
                WitnessErrorCode.INVALID_COMPOSITION,
                str(e),
            )
        except WitnessError as e:
            return _error_response(msg_id, e.code, e.message)
        except Exception as e:
            return _error_response(
                msg_id,
                WitnessErrorCode.INTERNAL,
                str(e),
            )

    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"resources": RESOURCES},
        }

    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "bulla://taxonomy":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/yaml",
                            "text": _load_taxonomy(),
                        }
                    ],
                },
            }
        return _error_response(
            msg_id,
            WitnessErrorCode.INVALID_PARAMS,
            f"Unknown resource: {uri}",
        )

    if is_notification:
        return None

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    }


def run_server() -> None:
    """Run the MCP stdio server. Reads JSON-RPC from stdin, writes to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        response = _handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
