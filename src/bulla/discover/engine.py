"""Discovery engine: tool schemas -> LLM -> validated micro-pack dict.

Pure function from tool schemas to micro-pack YAML. The LLM call is the
only external dependency, isolated behind the adapter interface.

Guided discovery (v0.26.0) adds ``guided_discover``: obligation-directed
probing via a batched prompt with per-obligation verdicts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from bulla.discover.adapter import DiscoverAdapter, get_adapter
from bulla.discover.prompt import (
    build_guided_prompt,
    build_prompt,
    parse_guided_response,
    parse_response,
)
from bulla.infer.classifier import load_pack_stack
from bulla.model import BoundaryObligation, ObligationVerdict, ProbeResult
from bulla.packs.validate import validate_pack

logger = logging.getLogger(__name__)


class DiscoveryResult:
    """Container for discovery output including raw LLM response for diagnostics."""

    def __init__(
        self,
        pack: dict[str, Any],
        raw_response: str,
        prompt: str,
        errors: list[str] | None = None,
    ) -> None:
        self.pack = pack
        self.raw_response = raw_response
        self.prompt = prompt
        self.errors = errors or []

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def n_dimensions(self) -> int:
        return len(self.pack.get("dimensions", {}))


def discover_dimensions(
    tool_schemas: list[dict[str, Any]],
    *,
    adapter: DiscoverAdapter | None = None,
    existing_packs: list[Path] | None = None,
    session_id: str | None = None,
) -> DiscoveryResult:
    """Discover convention dimensions from tool schemas via LLM.

    Args:
        tool_schemas: List of MCP tool dicts (name, description, inputSchema).
        adapter: LLM adapter. If None, auto-detects from environment.
        existing_packs: Extra pack paths to load (merged with base).
        session_id: Optional session ID for the discovered pack name.

    Returns:
        DiscoveryResult with the parsed pack dict and raw LLM response.
    """
    if adapter is None:
        adapter = get_adapter()

    merged, _ = load_pack_stack(extra_paths=existing_packs)
    existing_dims = merged.get("dimensions", {})

    prompt = build_prompt(
        tools=tool_schemas,
        existing_dimensions=existing_dims,
        session_id=session_id,
    )

    logger.info("Sending discovery prompt (%d tools, %d existing dims)",
                len(tool_schemas), len(existing_dims))
    raw_response = adapter.complete(prompt)
    logger.info("Received LLM response (%d chars)", len(raw_response))

    yaml_text = parse_response(raw_response)
    if yaml_text is None:
        return DiscoveryResult(
            pack={},
            raw_response=raw_response,
            prompt=prompt,
            errors=["No valid YAML block found in LLM response"],
        )

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        return DiscoveryResult(
            pack={},
            raw_response=raw_response,
            prompt=prompt,
            errors=[f"YAML parse error: {e}"],
        )

    if not isinstance(parsed, dict):
        return DiscoveryResult(
            pack={},
            raw_response=raw_response,
            prompt=prompt,
            errors=["Parsed YAML is not a mapping"],
        )

    validation_errors = validate_pack(parsed)
    if validation_errors:
        return DiscoveryResult(
            pack=parsed,
            raw_response=raw_response,
            prompt=prompt,
            errors=validation_errors,
        )

    return DiscoveryResult(
        pack=parsed,
        raw_response=raw_response,
        prompt=prompt,
    )


# ── Guided discovery (v0.26.0) ──────────────────────────────────────


class GuidedDiscoveryResult:
    """Result of probing obligations via batched guided discovery."""

    def __init__(
        self,
        probes: tuple[ProbeResult, ...],
        raw_response: str,
        prompt: str,
    ) -> None:
        self.probes = probes
        self.raw_response = raw_response
        self.prompt = prompt

    @property
    def n_confirmed(self) -> int:
        return sum(1 for p in self.probes if p.verdict == ObligationVerdict.CONFIRMED)

    @property
    def n_denied(self) -> int:
        return sum(1 for p in self.probes if p.verdict == ObligationVerdict.DENIED)

    @property
    def n_uncertain(self) -> int:
        return sum(1 for p in self.probes if p.verdict == ObligationVerdict.UNCERTAIN)

    @property
    def confirmed(self) -> tuple[ProbeResult, ...]:
        return tuple(p for p in self.probes if p.verdict == ObligationVerdict.CONFIRMED)


def guided_discover(
    obligations: tuple[BoundaryObligation, ...],
    tool_schemas: list[dict[str, Any]],
    adapter: DiscoverAdapter,
    pack_context: dict[str, Any] | None = None,
) -> GuidedDiscoveryResult:
    """Direct LLM discovery at specific obligations via a single batched call.

    For each obligation, matches the relevant tool schema by server group
    prefix (from ``placeholder_tool``) or specific tool name (from
    ``source_edge``), then constructs one batched prompt asking the LLM
    to evaluate all obligations together.

    Args:
        obligations: Boundary obligations to probe.
        tool_schemas: All available MCP tool dicts for tool matching.
        adapter: LLM adapter (real or mock).
        pack_context: Merged pack dict for known_values lookup.

    Returns:
        GuidedDiscoveryResult with per-obligation ProbeResults.
    """
    if not obligations:
        return GuidedDiscoveryResult(probes=(), raw_response="", prompt="")

    obl_dicts = [o.to_dict() for o in obligations]

    prompt = build_guided_prompt(obl_dicts, tool_schemas, pack_context)

    logger.info("Sending guided discovery prompt (%d obligations)", len(obligations))
    raw_response = adapter.complete(prompt)
    logger.info("Received guided discovery response (%d chars)", len(raw_response))

    verdict_dicts = parse_guided_response(raw_response, len(obligations))

    probes: list[ProbeResult] = []
    for obl, vd in zip(obligations, verdict_dicts):
        try:
            verdict = ObligationVerdict(vd["verdict"].lower())
        except ValueError:
            verdict = ObligationVerdict.UNCERTAIN

        probes.append(ProbeResult(
            obligation=obl,
            verdict=verdict,
            evidence=vd.get("evidence", ""),
            convention_value=vd.get("convention_value", ""),
        ))

    return GuidedDiscoveryResult(
        probes=tuple(probes),
        raw_response=raw_response,
        prompt=prompt,
    )
