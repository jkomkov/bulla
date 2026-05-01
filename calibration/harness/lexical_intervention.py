"""Lexical intervention experiment: causal test for vocabulary-mediated identification.

Design:
    Hold composition graph FIXED. Intervene ONLY on field names.
    Three conditions per composition:

    BASELINE  — original field names (path is hidden, state is hidden)
    SWAP      — rename path→resource_locator, rename state→path
    MASK      — all fields → neutral placeholders (field_1, field_2, ...)

    If identification follows the NAME not the ROLE:
      - BASELINE: model identifies "path" (high), misses "state" (low)
      - SWAP: model identifies "state"-now-called-"path" (high), misses "path"-now-called-"resource_locator" (low)
      - MASK: model identifies nothing (collapse to 0%)

    That is the causal proof: lexical form governs access to hiddenness.

Minimal pairs:
    Each test case is a composition where BOTH a path-family field AND a
    non-path field are hidden. The swap condition exchanges their names.
    Everything else (graph structure, schema shape, types) is preserved.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any

# Renaming maps for the swap condition
CANONICAL_TO_OBSCURE = {
    "path": "resource_locator",
    "paths": "locator_set",
    "filePath": "artifact_reference",
}

OBSCURE_TO_CANONICAL = {
    "direction": "path",       # give a non-path hidden field the canonical name
    "state": "path",
    "after": "filepath",
    "sort.timestamp": "path_order",
    "page": "file_path",
}


@dataclass(frozen=True)
class InterventionCase:
    """A minimal pair for the intervention experiment."""

    pair_name: str
    fee: int
    # The canonical hidden field (expected: high baseline ID, low after swap)
    canonical_field: str
    canonical_field_server: str
    # The obscure hidden field (expected: low baseline ID, high after swap)
    obscure_field: str
    obscure_field_server: str
    # Tool schemas per condition
    baseline_left_tools: list[dict[str, Any]]
    baseline_right_tools: list[dict[str, Any]]
    swap_left_tools: list[dict[str, Any]]
    swap_right_tools: list[dict[str, Any]]
    mask_left_tools: list[dict[str, Any]]
    mask_right_tools: list[dict[str, Any]]
    # Mapping for mask condition
    mask_map: dict[str, str]  # original_name → masked_name
    mask_reverse: dict[str, str]  # masked_name → original_name


def _rename_field_in_schema(
    tools: list[dict[str, Any]],
    old_name: str,
    new_name: str,
) -> list[dict[str, Any]]:
    """Deep-rename a field across all tool schemas."""
    tools = copy.deepcopy(tools)
    for tool in tools:
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        props = schema.get("properties", {})
        if old_name in props:
            props[new_name] = props.pop(old_name)
            # Update description to not leak the old name
            if "description" in props[new_name]:
                props[new_name]["description"] = props[new_name]["description"].replace(
                    old_name, new_name
                )
        required = schema.get("required", [])
        if old_name in required:
            idx = required.index(old_name)
            required[idx] = new_name
    return tools


def _mask_all_fields(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """Replace ALL field names with neutral placeholders. Return map."""
    tools = copy.deepcopy(tools)
    # Collect all unique field names
    all_fields: set[str] = set()
    for tool in tools:
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        props = schema.get("properties", {})
        all_fields.update(props.keys())

    # Create deterministic mapping (sorted for reproducibility)
    sorted_fields = sorted(all_fields)
    forward_map = {f: f"field_{i+1}" for i, f in enumerate(sorted_fields)}
    reverse_map = {v: k for k, v in forward_map.items()}

    # Apply renaming
    for tool in tools:
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        props = schema.get("properties", {})
        new_props = {}
        for fname, fval in list(props.items()):
            new_name = forward_map.get(fname, fname)
            new_val = copy.deepcopy(fval)
            # Strip description to prevent leaking semantics
            new_val.pop("description", None)
            new_props[new_name] = new_val
        # Replace properties
        if "properties" in schema:
            schema["properties"] = new_props
        elif "properties" in tool.get("input_schema", {}):
            tool["input_schema"]["properties"] = new_props

        # Update required
        required = schema.get("required", [])
        new_required = [forward_map.get(r, r) for r in required]
        if "required" in schema:
            schema["required"] = new_required

        # Strip tool description to prevent semantic leaking
        tool["description"] = f"Tool: {tool['name']}"

    return tools, forward_map, reverse_map


def build_intervention_cases(
    cases_with_ground_truth: list[dict[str, Any]],
) -> list[InterventionCase]:
    """Build minimal pairs from compositions that have BOTH path-family AND non-path hidden fields."""
    PATH_FAMILY = {"path", "paths", "filePath", "file_path", "filepath"}
    intervention_cases = []

    for case in cases_with_ground_truth:
        left_hidden = set(case["left_hidden"])
        right_hidden = set(case["right_hidden"])
        all_hidden = left_hidden | right_hidden

        # Need at least one path-family AND one non-path hidden field
        path_hidden = all_hidden & PATH_FAMILY
        non_path_hidden = all_hidden - PATH_FAMILY

        if not path_hidden or not non_path_hidden:
            continue

        # Pick one of each
        canonical = sorted(path_hidden)[0]
        obscure = sorted(non_path_hidden)[0]

        # Determine which server each belongs to
        canonical_server = "left" if canonical in left_hidden else "right"
        obscure_server = "left" if obscure in left_hidden else "right"

        # Build swap condition: rename canonical→obscure_alias, obscure→canonical_alias
        swap_left = copy.deepcopy(case["left_tools"])
        swap_right = copy.deepcopy(case["right_tools"])

        # Rename canonical field to something obscure
        obscure_alias = CANONICAL_TO_OBSCURE.get(canonical, f"x_{canonical}_ref")
        if canonical_server == "left":
            swap_left = _rename_field_in_schema(swap_left, canonical, obscure_alias)
        else:
            swap_right = _rename_field_in_schema(swap_right, canonical, obscure_alias)

        # Rename obscure field to something canonical
        canonical_alias = "path" if obscure != "path" else "file_path"
        if obscure_server == "left":
            swap_left = _rename_field_in_schema(swap_left, obscure, canonical_alias)
        else:
            swap_right = _rename_field_in_schema(swap_right, obscure, canonical_alias)

        # Build mask condition
        mask_left, mask_map_l, mask_rev_l = _mask_all_fields(copy.deepcopy(case["left_tools"]))
        mask_right, mask_map_r, mask_rev_r = _mask_all_fields(copy.deepcopy(case["right_tools"]))
        mask_map = {**mask_map_l, **mask_map_r}
        mask_reverse = {**mask_rev_l, **mask_rev_r}

        intervention_cases.append(InterventionCase(
            pair_name=case["pair_name"],
            fee=case["fee"],
            canonical_field=canonical,
            canonical_field_server=case["left_server"] if canonical_server == "left" else case["right_server"],
            obscure_field=obscure,
            obscure_field_server=case["left_server"] if obscure_server == "left" else case["right_server"],
            baseline_left_tools=case["left_tools"],
            baseline_right_tools=case["right_tools"],
            swap_left_tools=swap_left,
            swap_right_tools=swap_right,
            mask_left_tools=mask_left,
            mask_right_tools=mask_right,
            mask_map=mask_map,
            mask_reverse=mask_reverse,
        ))

    return intervention_cases


INTERVENTION_PROMPT = """\
You are analyzing a composition of two MCP tool servers for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between the two servers without any schema-level \
indication

Your task: identify which fields in each server have hidden conventions that \
could create silent composition failures.

## Server: {left_server}
Tools:
{left_tools_json}

## Server: {right_server}
Tools:
{right_tools_json}

## Instructions
For each server, list the field names that have hidden conventions. \
Return JSON with this exact structure:
{{
  "{left_server}": ["field1", "field2", ...],
  "{right_server}": ["field3", "field4", ...]
}}

Only include fields whose conventions are genuinely ambiguous or \
under-specified by the schema. Do not include fields with obvious semantics.\
"""


def build_prompt_for_condition(
    case: InterventionCase,
    condition: str,  # "baseline", "swap", "mask"
    left_server: str,
    right_server: str,
) -> str:
    """Build prompt for a given condition."""
    if condition == "baseline":
        left_tools = case.baseline_left_tools
        right_tools = case.baseline_right_tools
    elif condition == "swap":
        left_tools = case.swap_left_tools
        right_tools = case.swap_right_tools
    elif condition == "mask":
        left_tools = case.mask_left_tools
        right_tools = case.mask_right_tools
        left_server = "server_A"
        right_server = "server_B"
    else:
        raise ValueError(f"Unknown condition: {condition}")

    return INTERVENTION_PROMPT.format(
        left_server=left_server,
        right_server=right_server,
        left_tools_json=json.dumps(left_tools, indent=2),
        right_tools_json=json.dumps(right_tools, indent=2),
    )


@dataclass(frozen=True)
class InterventionResult:
    """Result of one intervention trial."""

    pair_name: str
    condition: str  # baseline, swap, mask
    canonical_field: str
    obscure_field: str
    # Did the model identify the canonical-role field? (tracks ROLE)
    canonical_role_identified: bool
    # Did the model identify the obscure-role field? (tracks ROLE)
    obscure_role_identified: bool
    # What name did it identify? (tracks NAME)
    identified_names: frozenset[str]
    # Raw response for debugging
    raw_fields_left: frozenset[str]
    raw_fields_right: frozenset[str]


def score_intervention(
    case: InterventionCase,
    condition: str,
    left_fields: set[str],
    right_fields: set[str],
) -> InterventionResult:
    """Score an intervention result, tracking both ROLE and NAME."""
    all_identified = left_fields | right_fields

    if condition == "baseline":
        canonical_identified = case.canonical_field in all_identified
        obscure_identified = case.obscure_field in all_identified
    elif condition == "swap":
        # In swap: canonical field was renamed to obscure_alias
        obscure_alias = CANONICAL_TO_OBSCURE.get(
            case.canonical_field, f"x_{case.canonical_field}_ref"
        )
        canonical_alias = "path" if case.obscure_field != "path" else "file_path"
        # Did model identify the ROLE (now under different name)?
        canonical_identified = obscure_alias in all_identified
        # Did model identify the obscure ROLE (now under canonical name)?
        obscure_identified = canonical_alias in all_identified
    elif condition == "mask":
        # In mask: need to check if masked versions of canonical/obscure are identified
        canonical_masked = case.mask_map.get(case.canonical_field, "")
        obscure_masked = case.mask_map.get(case.obscure_field, "")
        canonical_identified = canonical_masked in all_identified
        obscure_identified = obscure_masked in all_identified
    else:
        canonical_identified = False
        obscure_identified = False

    return InterventionResult(
        pair_name=case.pair_name,
        condition=condition,
        canonical_field=case.canonical_field,
        obscure_field=case.obscure_field,
        canonical_role_identified=canonical_identified,
        obscure_role_identified=obscure_identified,
        identified_names=frozenset(all_identified),
        raw_fields_left=frozenset(left_fields),
        raw_fields_right=frozenset(right_fields),
    )
