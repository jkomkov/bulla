"""YAML composition parser with inline schema validation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

try:
    import yaml
except ImportError:
    print("bulla requires PyYAML: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


class CompositionError(Exception):
    """Raised when a composition file fails validation."""


def _require_key(data: dict[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise CompositionError(f"Missing required field '{key}' in {context}")
    return data[key]


def _require_type(value: Any, expected: type, field: str, context: str) -> None:
    if not isinstance(value, expected):
        raise CompositionError(
            f"Field '{field}' in {context} must be {expected.__name__}, "
            f"got {type(value).__name__}"
        )


def _validate_tool(name: str, spec: Any) -> ToolSpec:
    _require_type(spec, dict, name, "tools")
    internal = _require_key(spec, "internal_state", f"tool '{name}'")
    _require_type(internal, list, "internal_state", f"tool '{name}'")
    if not internal:
        raise CompositionError(
            f"Tool '{name}' must have at least one internal_state dimension"
        )
    for i, dim in enumerate(internal):
        _require_type(dim, str, f"internal_state[{i}]", f"tool '{name}'")

    observable = _require_key(spec, "observable_schema", f"tool '{name}'")
    _require_type(observable, list, "observable_schema", f"tool '{name}'")
    for i, dim in enumerate(observable):
        _require_type(dim, str, f"observable_schema[{i}]", f"tool '{name}'")

    obs_tuple = tuple(observable)
    int_tuple = tuple(internal)
    for field in obs_tuple:
        if field not in int_tuple:
            raise CompositionError(
                f"Tool '{name}': observable_schema field '{field}' "
                f"is not in internal_state"
            )

    return ToolSpec(name=name, internal_state=int_tuple, observable_schema=obs_tuple)


def _validate_edge(edge_data: Any, index: int, tool_names: set[str]) -> Edge:
    context = f"edge[{index}]"
    _require_type(edge_data, dict, f"edges[{index}]", "edges")
    from_tool = _require_key(edge_data, "from", context)
    _require_type(from_tool, str, "from", context)
    to_tool = _require_key(edge_data, "to", context)
    _require_type(to_tool, str, "to", context)

    if from_tool not in tool_names:
        raise CompositionError(
            f"{context}: 'from' references unknown tool '{from_tool}'"
        )
    if to_tool not in tool_names:
        raise CompositionError(
            f"{context}: 'to' references unknown tool '{to_tool}'"
        )

    dims_data = _require_key(edge_data, "dimensions", context)
    _require_type(dims_data, list, "dimensions", context)
    if not dims_data:
        raise CompositionError(f"{context} must have at least one dimension")

    dims: list[SemanticDimension] = []
    for j, d in enumerate(dims_data):
        dim_ctx = f"{context}.dimensions[{j}]"
        _require_type(d, dict, f"dimensions[{j}]", context)
        name = _require_key(d, "name", dim_ctx)
        _require_type(name, str, "name", dim_ctx)
        dims.append(
            SemanticDimension(
                name=name,
                from_field=d.get("from_field"),
                to_field=d.get("to_field"),
            )
        )

    return Edge(from_tool=from_tool, to_tool=to_tool, dimensions=tuple(dims))


def _parse_composition_data(
    data: Any, source: str, default_name: str | None = None,
) -> Composition:
    """Validate parsed YAML data and return a Composition.

    ``source`` is used in error messages (file path or '<text>').
    ``default_name`` is used when the YAML has no ``name`` field.
    """
    if not isinstance(data, dict):
        raise CompositionError(f"{source}: expected a YAML mapping at top level")

    name = data.get("name", default_name or source)
    _require_type(name, str, "name", source)

    tools_data = _require_key(data, "tools", source)
    _require_type(tools_data, dict, "tools", source)
    if not tools_data:
        raise CompositionError(f"{source}: 'tools' must contain at least one tool")

    tools = [_validate_tool(tname, tspec) for tname, tspec in tools_data.items()]
    tool_names = {t.name for t in tools}

    edges_data = _require_key(data, "edges", source)
    _require_type(edges_data, list, "edges", source)
    edges = [_validate_edge(e, i, tool_names) for i, e in enumerate(edges_data)]

    return Composition(name=name, tools=tuple(tools), edges=tuple(edges))


def load_composition(path: Path | None = None, *, text: str | None = None) -> Composition:
    """Load and validate a composition from a YAML file or string.

    Exactly one of ``path`` or ``text`` must be provided.
    Raises CompositionError with actionable messages on validation failure.
    """
    if path is not None and text is not None:
        raise CompositionError("Provide path or text, not both")
    if path is None and text is None:
        raise CompositionError("Provide path or text")

    if path is not None:
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise CompositionError(f"Invalid YAML in {path}: {e}") from e
        return _parse_composition_data(data, str(path), default_name=path.stem)

    # text path
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise CompositionError(f"Invalid YAML: {e}") from e
    return _parse_composition_data(data, "<text>")
