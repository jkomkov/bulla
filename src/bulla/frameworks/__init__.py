"""Framework adapter registry.

A "framework" is a programmatic surface for declaring tool definitions
(LangGraph, CrewAI, Anthropic Messages, OpenAI tools, etc.). Each
framework adapter normalizes its source format into a list of
:class:`ToolDef` and then emits a Bulla manifest list.

This sprint implements ``ParseMode.STATIC`` (AST/JSON parse, no code
execution). The protocol reserves ``ParseMode.RUNTIME`` for a future
sprint that will add subprocess sandboxing without changing this
interface.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ParseMode",
    "ToolDef",
    "FrameworkAdapter",
    "FrameworkError",
    "register",
    "all_frameworks",
    "get",
    "tools_to_manifests",
]


class ParseMode(Enum):
    """How a framework adapter extracts tool definitions from source."""

    STATIC = "static"
    """AST/JSON parse — no code execution. Implemented in this sprint."""

    RUNTIME = "runtime"
    """Live import in a sandboxed subprocess. Reserved for a future sprint.
    Adapters currently raise ``NotImplementedError`` for this mode."""


class FrameworkError(Exception):
    """Raised by framework adapters for parse failures or missing deps."""


@dataclass
class ToolDef:
    """Framework-agnostic tool definition extracted by an adapter.

    Maps directly into the Bulla manifest's ``tool`` block; the
    ``input_schema`` feeds the convention classifier downstream.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    version: str | None = None
    source_location: str | None = None  # e.g. "file.py:123" for diagnostics


@runtime_checkable
class FrameworkAdapter(Protocol):
    """One framework Bulla can ingest tool definitions from."""

    name: str
    """Stable lookup key, lowercase-hyphenated. e.g. ``"langgraph"``."""

    display_name: str
    """Human-readable name. e.g. ``"LangGraph"``."""

    def supports(self, mode: ParseMode) -> bool:
        """Whether this adapter can parse in the given mode."""
        ...

    def parse(self, source: Path | str, mode: ParseMode = ParseMode.STATIC) -> list[ToolDef]:
        """Extract tool definitions from framework-native source.

        Adapters should raise :class:`NotImplementedError` for
        ``RUNTIME`` mode in this sprint. CLI surfaces a helpful message.
        """
        ...


_FRAMEWORKS: dict[str, FrameworkAdapter] = {}


def register(adapter: FrameworkAdapter) -> None:
    """Register a framework adapter. Idempotent — re-registration replaces."""
    if not isinstance(adapter.name, str) or not adapter.name:
        raise FrameworkError(f"Adapter has invalid name: {adapter!r}")
    _FRAMEWORKS[adapter.name] = adapter


def all_frameworks() -> Iterable[FrameworkAdapter]:
    """Return registered adapters in insertion order."""
    _ensure_loaded()
    return list(_FRAMEWORKS.values())


def get(name: str) -> FrameworkAdapter:
    """Look up an adapter by stable name."""
    _ensure_loaded()
    try:
        return _FRAMEWORKS[name]
    except KeyError as e:
        known = ", ".join(sorted(_FRAMEWORKS))
        raise FrameworkError(
            f"Unknown framework {name!r}. Registered: {known}."
        ) from e


def tools_to_manifests(tools: list[ToolDef]) -> list[dict]:
    """Convert a list of ToolDef into a list of Bulla manifest dicts.

    Each manifest follows ``bulla-manifest-schema.json`` v0.1. Conventions
    are left to the downstream classifier (manifest.generate_manifest_from_tools);
    this function emits the bare ``tool`` block ready for that pipeline.
    """
    out: list[dict] = []
    for t in tools:
        manifest: dict[str, Any] = {
            "bulla_manifest": "0.1",
            "tool": {
                "name": t.name,
                "description": t.description or "",
            },
            "conventions": {},
        }
        if t.version:
            manifest["tool"]["version"] = t.version
        out.append(manifest)
    return out


def tools_to_raw_dicts(tools: list[ToolDef]) -> list[dict]:
    """Emit tool dicts in the MCP ``tools/list`` shape used by manifest.generate_manifest_from_tools.

    Bulla's existing manifest pipeline accepts ``[{name, description, inputSchema}]``;
    this lets a framework adapter feed straight into ``bulla audit --manifests``
    by way of the JSON manifest format.
    """
    out: list[dict] = []
    for t in tools:
        out.append({
            "name": t.name,
            "description": t.description or "",
            "inputSchema": t.input_schema or {"type": "object", "properties": {}},
        })
    return out


_LOADED = False


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    _load_builtin_frameworks()


def _load_builtin_frameworks() -> None:
    """Import built-in framework adapters. Each registers itself."""
    from bulla.frameworks import anthropic_messages as _anthropic  # noqa: F401
    from bulla.frameworks import langgraph as _langgraph  # noqa: F401
    from bulla.frameworks import crewai as _crewai  # noqa: F401
