"""CrewAI tool definitions.

Static AST extraction of CrewAI's two main patterns:

1. ``@tool`` and ``@tool("name")`` decorated functions (via crewai.tools)
2. ``BaseTool`` subclasses with ``name``, ``description``, and
   ``args_schema`` (Pydantic) class attributes

CrewAI shares enough structural shape with LangChain's tool patterns
that this adapter delegates to the LangChain extractor for the @tool
case, and adds a CrewAI-specific class extractor for ``BaseTool``.

Optional dep: ``pip install bulla[crewai]`` adds crewai for runtime
schema introspection (future ``RUNTIME`` mode). Static parse uses
stdlib only.
"""

from __future__ import annotations

import ast
from pathlib import Path

from bulla.frameworks import (
    FrameworkError,
    ParseMode,
    ToolDef,
    register,
)
from bulla.frameworks.langgraph import (
    _from_decorated_function,
    _from_basetool_subclass,
)


class CrewAIAdapter:
    name = "crewai"
    display_name = "CrewAI"

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

        if path.is_dir():
            tools: list[ToolDef] = []
            for py in sorted(path.rglob("*.py")):
                tools.extend(_extract_from_file(py))
            if not tools:
                raise FrameworkError(f"No tool definitions found under {path}.")
            return tools

        if path.suffix.lower() != ".py":
            raise FrameworkError(
                f"Unsupported source {path}. Pass a .py file or directory."
            )
        return _extract_from_file(path)


def _extract_from_file(path: Path) -> list[ToolDef]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except SyntaxError as e:
        raise FrameworkError(f"Cannot parse {path}: {e}") from e

    tools: list[ToolDef] = []

    # @tool decorated functions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tdef = _from_decorated_function(node, path)
            if tdef:
                tools.append(tdef)

    # BaseTool subclasses (CrewAI uses the same base class name)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            tdef = _from_basetool_subclass(node, path)
            if tdef:
                tools.append(tdef)

    return tools


register(CrewAIAdapter())
