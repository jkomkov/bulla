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

import json
import sys
from typing import Any

import yaml

from bulla import __version__
from bulla.diagnostic import decompose_fee, diagnose, minimum_disclosure_set
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    PolicyProfile,
    WitnessBasis,
    WitnessError,
    WitnessErrorCode,
)
from bulla.infer.classifier import get_active_pack_refs
from bulla.parser import CompositionError, load_composition
from bulla.witness import witness

SERVER_NAME = "bulla"
SERVER_VERSION = __version__
PROTOCOL_VERSION = "2024-11-05"
MAX_DEPTH = 10

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


# ── Shared schema fragments ──────────────────────────────────────────

_POLICY_INPUT_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "string",
            "description": "Named policy profile (backward compat)",
        },
        {
            "type": "object",
            "description": "Full policy profile with thresholds",
            "properties": {
                "name": {
                    "type": "string",
                    "default": DEFAULT_POLICY_PROFILE.name,
                },
                "max_blind_spots": {"type": "integer", "default": 0},
                "max_fee": {"type": "integer", "default": 0},
                "max_unknown": {"type": "integer", "default": -1},
                "require_bridge": {"type": "boolean", "default": True},
            },
        },
    ],
    "description": (
        "Policy profile: either a name string (backward compat) or "
        "an object with explicit thresholds"
    ),
    "default": DEFAULT_POLICY_PROFILE.name,
}

_RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "receipt_version": {"type": "string"},
        "kernel_version": {"type": "string"},
        "receipt_hash": {"type": "string"},
        "composition_hash": {"type": "string"},
        "diagnostic_hash": {"type": "string"},
        "policy_profile": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "max_blind_spots": {"type": "integer"},
                "max_fee": {"type": "integer"},
                "max_unknown": {"type": "integer"},
                "require_bridge": {"type": "boolean"},
            },
        },
        "fee": {"type": "integer"},
        "blind_spots_count": {"type": "integer"},
        "bridges_required": {"type": "integer"},
        "unknown_dimensions": {"type": "integer"},
        "disposition": {
            "type": "string",
            "enum": [
                "proceed",
                "proceed_with_receipt",
                "proceed_with_bridge",
                "refuse_pending_disclosure",
                "refuse_pending_human_review",
            ],
        },
        "timestamp": {"type": "string"},
        "patches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "bulla_patch_version": {"type": "string"},
                    "action": {"type": "string"},
                    "target_tool": {"type": "string"},
                    "field": {"type": "string"},
                    "path": {"type": "string"},
                    "dimension": {"type": "string"},
                    "eliminates": {"type": "string"},
                    "expected_fee_delta": {"type": "integer"},
                },
            },
        },
        "anchor_ref": {"type": ["string", "null"]},
        "parent_receipt_hash": {"type": ["string", "null"]},
        "active_packs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "hash": {"type": "string"},
                },
            },
        },
        "witness_basis": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "declared": {"type": "integer"},
                        "inferred": {"type": "integer"},
                        "unknown": {"type": "integer"},
                    },
                },
            ],
        },
    },
    "required": [
        "receipt_version",
        "kernel_version",
        "receipt_hash",
        "composition_hash",
        "diagnostic_hash",
        "policy_profile",
        "fee",
        "blind_spots_count",
        "bridges_required",
        "unknown_dimensions",
        "disposition",
        "timestamp",
        "patches",
    ],
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
                "policy": _POLICY_INPUT_SCHEMA,
                "unknown_dimensions": {
                    "type": "integer",
                    "description": (
                        "Number of convention dimensions that could not be "
                        "classified under the active packs. Used by policy "
                        "to enforce max_unknown thresholds."
                    ),
                    "default": 0,
                },
                "witness_basis": {
                    "type": "object",
                    "description": (
                        "Epistemic provenance: how many conventions were "
                        "declared, inferred, or unknown. Omit if not attested."
                    ),
                    "properties": {
                        "declared": {"type": "integer"},
                        "inferred": {"type": "integer"},
                        "unknown": {"type": "integer"},
                    },
                    "required": ["declared", "inferred", "unknown"],
                },
                "depth": {
                    "type": "integer",
                    "description": (
                        "Current recursion depth for bounded self-audit "
                        f"(max: {MAX_DEPTH})"
                    ),
                    "default": 0,
                },
                "partition": {
                    "type": "array",
                    "description": (
                        "Optional tool-name partition for fee decomposition. "
                        "Each element is an array of tool name strings. "
                        "When provided, the output includes a 'decomposition' "
                        "field with total_fee, local_fees, boundary_fee, "
                        "rho_obs, rho_full."
                    ),
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "required": ["composition"],
        },
        "outputSchema": _RECEIPT_SCHEMA,
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
                "policy": _POLICY_INPUT_SCHEMA,
                "unknown_dimensions": {
                    "type": "integer",
                    "description": (
                        "Number of convention dimensions that could not be "
                        "classified under the active packs. Used by policy "
                        "to enforce max_unknown thresholds."
                    ),
                    "default": 0,
                },
                "witness_basis": {
                    "type": "object",
                    "description": (
                        "Epistemic provenance: how many conventions were "
                        "declared, inferred, or unknown. Omit if not attested."
                    ),
                    "properties": {
                        "declared": {"type": "integer"},
                        "inferred": {"type": "integer"},
                        "unknown": {"type": "integer"},
                    },
                    "required": ["declared", "inferred", "unknown"],
                },
            },
            "required": ["composition"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "patched_composition": {"type": "string"},
                "original_receipt": _RECEIPT_SCHEMA,
                "receipt": _RECEIPT_SCHEMA,
                "patches": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "before": {
                    "type": "object",
                    "properties": {"blind_spots": {"type": "integer"}},
                },
                "after": {
                    "type": "object",
                    "properties": {"blind_spots": {"type": "integer"}},
                },
            },
            "required": [
                "patched_composition",
                "original_receipt",
                "receipt",
                "patches",
                "before",
                "after",
            ],
        },
    },
]


# ── Resource definitions ─────────────────────────────────────────────


def _load_taxonomy() -> str:
    from bulla.infer.classifier import load_pack_stack

    merged, _ = load_pack_stack()
    return yaml.dump(merged, default_flow_style=False, sort_keys=False)


RESOURCES = [
    {
        "uri": "bulla://taxonomy",
        "name": "bulla.taxonomy",
        "description": "Convention taxonomy (10 dimensions with field patterns, description keywords, known values)",
        "mimeType": "text/yaml",
    },
]


# ── Policy parsing ───────────────────────────────────────────────────


def _parse_policy(raw: Any) -> PolicyProfile:
    """Parse policy input: accepts a string name or a full object."""
    if raw is None:
        return DEFAULT_POLICY_PROFILE
    if isinstance(raw, str):
        return PolicyProfile(name=raw)
    if isinstance(raw, dict):
        return PolicyProfile(
            name=raw.get("name", DEFAULT_POLICY_PROFILE.name),
            max_blind_spots=raw.get(
                "max_blind_spots", DEFAULT_POLICY_PROFILE.max_blind_spots
            ),
            max_fee=raw.get("max_fee", DEFAULT_POLICY_PROFILE.max_fee),
            max_unknown=raw.get(
                "max_unknown", DEFAULT_POLICY_PROFILE.max_unknown
            ),
            require_bridge=raw.get(
                "require_bridge", DEFAULT_POLICY_PROFILE.require_bridge
            ),
        )
    return DEFAULT_POLICY_PROFILE


# ── Tool handlers ────────────────────────────────────────────────────


def _parse_witness_basis(raw: Any) -> WitnessBasis | None:
    """Parse optional witness_basis from MCP input."""
    if raw is None or not isinstance(raw, dict):
        return None
    try:
        return WitnessBasis(
            declared=int(raw["declared"]),
            inferred=int(raw["inferred"]),
            unknown=int(raw["unknown"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _handle_witness(args: dict) -> dict:
    composition_yaml = args.get("composition", "")
    depth = args.get("depth", 0)
    unknown_dims = args.get("unknown_dimensions", 0)

    if depth > MAX_DEPTH:
        raise WitnessError(
            WitnessErrorCode.RECURSION_LIMIT,
            f"Recursion depth {depth} exceeds max {MAX_DEPTH}",
        )

    policy = _parse_policy(args.get("policy"))
    basis = _parse_witness_basis(args.get("witness_basis"))
    comp = load_composition(text=composition_yaml)
    diag = diagnose(comp)
    receipt = witness(
        diag,
        comp,
        unknown_dimensions=unknown_dims,
        policy_profile=policy,
        witness_basis=basis,
        active_packs=get_active_pack_refs(),
    )
    result = receipt.to_dict()

    if receipt.fee > 0:
        result["disclosure_set"] = [
            [tool, field] for tool, field in minimum_disclosure_set(comp)
        ]
    else:
        result["disclosure_set"] = []

    raw_partition = args.get("partition")
    if raw_partition:
        partition = [frozenset(group) for group in raw_partition]
        dec = decompose_fee(comp, partition)
        result["decomposition"] = {
            "total_fee": dec.total_fee,
            "local_fees": list(dec.local_fees),
            "boundary_fee": dec.boundary_fee,
            "rho_obs": dec.rho_obs,
            "rho_full": dec.rho_full,
            "boundary_edges": dec.boundary_edges,
        }

    return result


def _handle_bridge(args: dict) -> dict:
    composition_yaml = args.get("composition", "")
    policy = _parse_policy(args.get("policy"))
    unknown_dims = args.get("unknown_dimensions", 0)
    basis = _parse_witness_basis(args.get("witness_basis"))
    packs = get_active_pack_refs()

    comp = load_composition(text=composition_yaml)
    diag = diagnose(comp)
    original_receipt = witness(
        diag, comp,
        unknown_dimensions=unknown_dims,
        policy_profile=policy,
        witness_basis=basis,
        active_packs=packs,
    )
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

    patched_comp = load_composition(text=patched_yaml)
    patched_diag = diagnose(patched_comp)
    patched_receipt = witness(
        patched_diag,
        patched_comp,
        policy_profile=policy,
        parent_receipt_hash=original_receipt.receipt_hash,
        witness_basis=basis,
        active_packs=packs,
    )

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
        return None

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
                    "structuredContent": result,
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
