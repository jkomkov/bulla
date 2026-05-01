"""Anthropic Messages API tool definitions.

Anthropic tools are JSON dicts: ``{name, description, input_schema}``.
Source can be a JSON file (an array of tools, or a top-level object
with a ``tools`` key) or a Python file containing a ``tools = [...]``
literal that the AST extractor lifts.

No third-party dependencies required.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from bulla.frameworks import (
    FrameworkError,
    ParseMode,
    ToolDef,
    register,
)


class AnthropicMessagesAdapter:
    name = "anthropic-messages"
    display_name = "Anthropic Messages API"

    def supports(self, mode: ParseMode) -> bool:
        return mode is ParseMode.STATIC

    def parse(self, source: Path | str, mode: ParseMode = ParseMode.STATIC) -> list[ToolDef]:
        if mode is ParseMode.RUNTIME:
            raise NotImplementedError(
                "Runtime mode is reserved for a future sprint. "
                "Use --mode static (default) for now."
            )

        path = Path(source) if not isinstance(source, Path) else source
        if not path.exists():
            raise FrameworkError(f"Source file not found: {path}")

        suffix = path.suffix.lower()
        if suffix in (".json", ".ndjson"):
            return _from_json(path)
        if suffix in (".py",):
            return _from_python(path)
        raise FrameworkError(
            f"Unsupported source extension {suffix!r} for anthropic-messages "
            f"(want .json or .py)."
        )


def _from_json(path: Path) -> list[ToolDef]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise FrameworkError(f"Invalid JSON in {path}: {e}") from e

    if isinstance(data, dict) and "tools" in data:
        items = data["tools"]
    elif isinstance(data, list):
        items = data
    else:
        raise FrameworkError(
            f"Expected an array of tools or {{tools: [...]}} in {path}."
        )

    tools: list[ToolDef] = []
    for i, t in enumerate(items):
        if not isinstance(t, dict) or "name" not in t:
            continue
        tools.append(ToolDef(
            name=str(t["name"]),
            description=str(t.get("description", "")),
            input_schema=_normalize_schema(t.get("input_schema") or t.get("inputSchema")),
            source_location=f"{path}:[{i}]",
        ))
    if not tools:
        raise FrameworkError(f"No tool definitions found in {path}.")
    return tools


def _from_python(path: Path) -> list[ToolDef]:
    """Extract a top-level ``tools = [...]`` literal from a Python source file."""
    src = path.read_text()
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as e:
        raise FrameworkError(f"Cannot parse {path}: {e}") from e

    tools_literal: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "tools":
                    tools_literal = node.value
                    break
        if tools_literal:
            break

    if tools_literal is None:
        raise FrameworkError(
            f"No top-level 'tools = [...]' assignment found in {path}."
        )

    try:
        evaluated = ast.literal_eval(tools_literal)
    except (ValueError, SyntaxError) as e:
        raise FrameworkError(
            f"'tools' in {path} must be a literal list of dicts: {e}"
        ) from e

    if not isinstance(evaluated, list):
        raise FrameworkError(f"'tools' in {path} is not a list.")

    out: list[ToolDef] = []
    for i, t in enumerate(evaluated):
        if not isinstance(t, dict) or "name" not in t:
            continue
        out.append(ToolDef(
            name=str(t["name"]),
            description=str(t.get("description", "")),
            input_schema=_normalize_schema(t.get("input_schema") or t.get("inputSchema")),
            source_location=f"{path}:tools[{i}]",
        ))
    if not out:
        raise FrameworkError(f"No usable tool definitions in {path}.")
    return out


def _normalize_schema(schema: Any) -> dict:
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


register(AnthropicMessagesAdapter())
