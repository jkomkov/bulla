"""Perturbation helpers for G27 corpus generation."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
from typing import Any


@dataclass(frozen=True)
class PerturbationStats:
    tools_seen: int
    tools_perturbed: int
    properties_before: int
    properties_after: int
    properties_hidden: int
    required_before: int
    required_after: int
    required_hidden: int

    def to_dict(self) -> dict[str, int]:
        return {
            "tools_seen": self.tools_seen,
            "tools_perturbed": self.tools_perturbed,
            "properties_before": self.properties_before,
            "properties_after": self.properties_after,
            "properties_hidden": self.properties_hidden,
            "required_before": self.required_before,
            "required_after": self.required_after,
            "required_hidden": self.required_hidden,
        }


def perturb_tools_with_stats(
    tools: list[dict[str, Any]],
    *,
    seed: str,
    hide_ratio: float = 0.25,
) -> tuple[list[dict[str, Any]], PerturbationStats]:
    """Deterministically perturb tool schemas by hiding some required fields.

    This creates controlled observability degradation while preserving the
    tool names and high-level interface shape.
    """
    if not 0.0 <= hide_ratio <= 1.0:
        raise ValueError(f"hide_ratio must be in [0,1], got {hide_ratio}")
    out: list[dict[str, Any]] = deepcopy(tools)
    tools_perturbed = 0
    properties_before = 0
    properties_after = 0
    required_before = 0
    required_after = 0
    for idx, tool in enumerate(out):
        schema = (
            tool.get("inputSchema")
            or tool.get("input_schema")
            or {}
        )
        props = schema.get("properties", {})
        required = schema.get("required", [])
        required_keys = [r for r in required if isinstance(r, str)] if isinstance(required, list) else []

        if not isinstance(props, dict):
            continue
        properties_before += len(props)
        required_before += len(required_keys)
        if not props:
            properties_after += len(props)
            required_after += len(required_keys)
            continue
        keys = sorted(props.keys())
        hashed = hashlib.sha256(f"{seed}:{tool.get('name','tool')}:{idx}".encode()).hexdigest()
        budget = max(0, int(len(keys) * hide_ratio))
        if budget == 0:
            properties_after += len(props)
            required_after += len(required_keys)
            continue
        tools_perturbed += 1
        # deterministic selection by hash order
        picked = sorted(keys, key=lambda k: hashlib.sha256(f"{hashed}:{k}".encode()).hexdigest())[:budget]
        picked_set = set(picked)
        for k in picked:
            props.pop(k, None)
        if isinstance(required, list):
            schema["required"] = [r for r in required_keys if r not in picked_set]

        properties_after += len(props)
        required_after += len(schema.get("required", []))

    stats = PerturbationStats(
        tools_seen=len(out),
        tools_perturbed=tools_perturbed,
        properties_before=properties_before,
        properties_after=properties_after,
        properties_hidden=max(0, properties_before - properties_after),
        required_before=required_before,
        required_after=required_after,
        required_hidden=max(0, required_before - required_after),
    )
    return out, stats


def perturb_tools(
    tools: list[dict[str, Any]],
    *,
    seed: str,
    hide_ratio: float = 0.25,
) -> list[dict[str, Any]]:
    """Backward-compatible perturbation helper returning only tool payloads."""
    out, _ = perturb_tools_with_stats(tools, seed=seed, hide_ratio=hide_ratio)
    return out

