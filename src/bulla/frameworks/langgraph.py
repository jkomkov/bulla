"""LangGraph / LangChain tool definitions.

Static AST extraction of common LangChain tool-declaration patterns:

1. ``@tool`` and ``@tool("name")`` decorators on functions
2. ``StructuredTool.from_function(func, name=..., description=...)``
3. Subclasses of ``BaseTool`` with ``name``/``description`` class attributes

Pydantic ``args_schema`` references are recorded by class name; their
field structure is left to runtime mode (future). For now, the static
extractor records ``{type: "object", properties: {}, x_pydantic: <name>}``
when an args_schema is present.

Optional dep: ``pip install bulla[langgraph]`` adds langchain-core for
deeper schema validation. Adapters use lazy imports — bulla itself has
no langgraph dependency.
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


class LangGraphAdapter:
    name = "langgraph"
    display_name = "LangGraph / LangChain"

    def supports(self, mode: ParseMode) -> bool:
        return mode is ParseMode.STATIC

    def parse(self, source: Path | str, mode: ParseMode = ParseMode.STATIC) -> list[ToolDef]:
        if mode is ParseMode.RUNTIME:
            raise NotImplementedError(
                "Runtime mode is reserved for a future sprint. "
                "Dynamic tool registrations (for-loops, metaprogramming) will require "
                "live import — use --mode static for now."
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

    # Pattern 1: @tool decorator on functions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tdef = _from_decorated_function(node, path)
            if tdef:
                tools.append(tdef)

    # Pattern 2: BaseTool subclasses
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            tdef = _from_basetool_subclass(node, path)
            if tdef:
                tools.append(tdef)

    # Pattern 3: StructuredTool.from_function(...) calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            tdef = _from_structured_tool_call(node, path)
            if tdef:
                tools.append(tdef)

    return tools


def _from_decorated_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef, path: Path
) -> ToolDef | None:
    tool_decorator = None
    for dec in node.decorator_list:
        # @tool
        if isinstance(dec, ast.Name) and dec.id == "tool":
            tool_decorator = dec
            break
        # @tool("name", ...)
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "tool":
            tool_decorator = dec
            break
    if tool_decorator is None:
        return None

    name = node.name
    if isinstance(tool_decorator, ast.Call) and tool_decorator.args:
        first = tool_decorator.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            name = first.value

    description = ast.get_docstring(node) or ""
    schema = _function_signature_to_schema(node)

    return ToolDef(
        name=name,
        description=description,
        input_schema=schema,
        source_location=f"{path}:{node.lineno}",
    )


def _from_basetool_subclass(node: ast.ClassDef, path: Path) -> ToolDef | None:
    bases = {_base_name(b) for b in node.bases}
    if not bases & {"BaseTool", "Tool"}:
        return None

    name = node.name
    description = ast.get_docstring(node) or ""
    args_schema_class: str | None = None

    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id == "name" and isinstance(stmt.value, ast.Constant):
                if isinstance(stmt.value.value, str):
                    name = stmt.value.value
            elif stmt.target.id == "description" and isinstance(stmt.value, ast.Constant):
                if isinstance(stmt.value.value, str):
                    description = stmt.value.value
            elif stmt.target.id == "args_schema":
                args_schema_class = _ann_or_value_name(stmt)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    if target.id == "name" and isinstance(stmt.value, ast.Constant):
                        if isinstance(stmt.value.value, str):
                            name = stmt.value.value
                    elif target.id == "description" and isinstance(stmt.value, ast.Constant):
                        if isinstance(stmt.value.value, str):
                            description = stmt.value.value
                    elif target.id == "args_schema":
                        args_schema_class = _ann_or_value_name(stmt)

    schema: dict = {"type": "object", "properties": {}}
    if args_schema_class:
        schema["x_pydantic"] = args_schema_class

    return ToolDef(
        name=name,
        description=description,
        input_schema=schema,
        source_location=f"{path}:{node.lineno}",
    )


def _from_structured_tool_call(node: ast.Call, path: Path) -> ToolDef | None:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "from_function":
        return None
    if not isinstance(node.func.value, ast.Name):
        return None
    if node.func.value.id not in ("StructuredTool", "Tool"):
        return None

    name = ""
    description = ""
    for kw in node.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            name = kw.value.value
        elif kw.arg == "description" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            description = kw.value.value

    if not name:
        return None
    return ToolDef(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
        source_location=f"{path}:{node.lineno}",
    )


def _function_signature_to_schema(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    properties: dict = {}
    required: list[str] = []
    args = node.args.args
    defaults = node.args.defaults
    n_defaults = len(defaults)
    n_args = len(args)
    default_offset = n_args - n_defaults

    for i, arg in enumerate(args):
        if arg.arg in ("self", "cls"):
            continue
        ptype = _annotation_to_type(arg.annotation)
        properties[arg.arg] = {"type": ptype} if ptype else {}
        if i < default_offset:
            required.append(arg.arg)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _annotation_to_type(ann: ast.AST | None) -> str | None:
    if ann is None:
        return None
    if isinstance(ann, ast.Name):
        return _PYTHON_TO_JSON.get(ann.id)
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
        return _PYTHON_TO_JSON.get(ann.value.id, None)
    return None


_PYTHON_TO_JSON = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "List": "array",
    "tuple": "array",
    "dict": "object",
    "Dict": "object",
}


def _base_name(b: ast.AST) -> str:
    if isinstance(b, ast.Name):
        return b.id
    if isinstance(b, ast.Attribute):
        return b.attr
    return ""


def _ann_or_value_name(stmt: ast.Assign | ast.AnnAssign) -> str | None:
    val = stmt.value
    if isinstance(val, ast.Name):
        return val.id
    if isinstance(val, ast.Attribute):
        return val.attr
    return None


register(LangGraphAdapter())
