"""BullaGuard: the high-level programmatic API for bulla.

Construct from tool definitions, MCP manifests, YAML files, or live
MCP servers.  Diagnose, check thresholds, and export results.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from bulla.diagnostic import diagnose as _diagnose
from bulla.formatters import format_json, format_sarif, format_text
from bulla.infer.classifier import classify_fields, classify_tool_rich
from bulla.infer.mcp import (
    _extract_tool_fields,
    _find_shared_dimensions,
    extract_field_infos,
)
from bulla.model import (
    Composition,
    Diagnostic,
    Edge,
    SemanticDimension,
    ToolSpec,
    WitnessBasis,
)
from bulla.parser import load_composition


class BullaCheckError(Exception):
    """Raised by BullaGuard.check() when thresholds are exceeded."""

    def __init__(self, message: str, diagnostic: Diagnostic) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


class BullaGuard:
    """High-level API for coherence fee analysis.

    Immutable after construction.  ``diagnose()`` is cached.
    """

    def __init__(
        self,
        composition: Composition,
        witness_basis: WitnessBasis | None = None,
    ) -> None:
        self._composition = composition
        self._diagnostic: Diagnostic | None = None
        self._witness_basis = witness_basis

    @property
    def composition(self) -> Composition:
        return self._composition

    @property
    def witness_basis(self) -> WitnessBasis | None:
        """Epistemic provenance of the composition's conventions.

        Non-None when the composition was built from an inference path
        (from_mcp_manifest, from_mcp_server) where the classifier
        produced confidence tags. None for hand-authored compositions.
        """
        return self._witness_basis

    # ── Construction paths ────────────────────────────────────────────

    @classmethod
    def from_composition(cls, path: str | Path) -> BullaGuard:
        """Load from a YAML composition file (the v0.1 path)."""
        comp = load_composition(Path(path))
        return cls(comp)

    @classmethod
    def from_tools(
        cls,
        tools: dict[str, dict[str, Any]],
        *,
        edges: list[tuple[str, str]] | None = None,
        name: str = "programmatic",
    ) -> BullaGuard:
        """Build from raw tool definitions.

        Each tool dict may contain:
          - ``fields``: list of all field names (both observable and internal)
          - ``conventions``: dict mapping convention dimension names to values;
            fields whose names match convention dimensions are treated as
            internal-only (hidden from the observable schema)
          - ``internal_state`` / ``observable_schema``: explicit override
            (takes precedence over fields/conventions if provided)

        If *edges* is ``None``, pairwise edges are inferred from shared
        convention dimensions via the heuristic classifier.
        """
        tool_specs: list[ToolSpec] = []
        tools_for_inference: dict[str, Any] = {}

        for tool_name, spec in tools.items():
            if "internal_state" in spec and "observable_schema" in spec:
                tool_specs.append(ToolSpec(
                    name=tool_name,
                    internal_state=tuple(spec["internal_state"]),
                    observable_schema=tuple(spec["observable_schema"]),
                ))
            else:
                fields = list(spec.get("fields", []))
                conventions = spec.get("conventions", {})
                convention_fields = set(conventions.keys())
                all_fields = list(dict.fromkeys(fields + list(convention_fields)))
                observable = [f for f in all_fields if f not in convention_fields]
                tool_specs.append(ToolSpec(
                    name=tool_name,
                    internal_state=tuple(all_fields),
                    observable_schema=tuple(observable),
                ))
                tools_for_inference[tool_name] = spec

        if edges is not None:
            edge_list = _build_explicit_edges(tool_specs, edges)
        else:
            edge_list = _infer_edges(tool_specs)

        return cls(Composition(name=name, tools=tuple(tool_specs), edges=tuple(edge_list)))

    @classmethod
    def from_mcp_manifest(cls, path: str | Path) -> BullaGuard:
        """Build from an MCP manifest JSON (list_tools response)."""
        path = Path(path)
        data = json.loads(path.read_text())

        tools_list: list[dict[str, Any]]
        if isinstance(data, list):
            tools_list = data
        elif isinstance(data, dict) and "tools" in data:
            tools_list = data["tools"]
        else:
            raise ValueError(
                f"Expected 'tools' array or plain array in {path}"
            )

        comp, basis = _composition_from_mcp_tools(
            tools_list, name=f"inferred-from-{path.stem}"
        )
        return cls(comp, witness_basis=basis)

    @classmethod
    def from_mcp_server(cls, command: str, *, name: str | None = None) -> BullaGuard:
        """Connect to a live MCP server via stdio, query tools, and build a composition."""
        from bulla.scan import scan_mcp_server
        tools_list = scan_mcp_server(command)
        comp_name = name or f"scan-{command.split()[0].split('/')[-1]}"
        comp, basis = _composition_from_mcp_tools(tools_list, name=comp_name)
        return cls(comp, witness_basis=basis)

    @classmethod
    def from_tools_list(
        cls,
        tools: list[dict[str, Any]],
        *,
        name: str = "composition",
    ) -> "BullaGuard":
        """Build from a raw MCP tools/list response (list of tool dicts).

        This is the public entry point for programmatic use with
        in-memory tool lists.  Used by ``bulla audit`` to compose tools
        from multiple servers without requiring a file on disk.
        """
        comp, basis = _composition_from_mcp_tools(tools, name=name)
        return cls(comp, witness_basis=basis)

    # ── Analysis ──────────────────────────────────────────────────────

    def diagnose(self) -> Diagnostic:
        """Run the coherence fee analysis. Result is cached."""
        if self._diagnostic is None:
            self._diagnostic = _diagnose(self._composition)
        return self._diagnostic

    def check(
        self,
        *,
        max_blind_spots: int = 0,
        max_unbridged: int = 0,
    ) -> Diagnostic:
        """Check thresholds, raising ``BullaCheckError`` if exceeded."""
        diag = self.diagnose()
        bs = len(diag.blind_spots)
        ub = diag.n_unbridged
        violations: list[str] = []
        if bs > max_blind_spots:
            violations.append(
                f"{bs} blind spot(s) (max {max_blind_spots})"
            )
        if ub > max_unbridged:
            violations.append(
                f"{ub} unbridged edge(s) (max {max_unbridged})"
            )
        if violations:
            raise BullaCheckError(
                f"Composition '{diag.name}' failed: " + "; ".join(violations),
                diag,
            )
        return diag

    # ── Export ─────────────────────────────────────────────────────────

    def to_text(self) -> str:
        return format_text(self.diagnose())

    def to_json(self, source_path: Path | None = None) -> str:
        return format_json(self.diagnose(), source_path)

    def to_sarif(self, source_path: Path | None = None) -> str:
        p = source_path or Path(f"{self._composition.name}.yaml")
        return format_sarif([(self.diagnose(), p)])

    def to_yaml(self, path: str | Path | None = None) -> str:
        """Export the composition as YAML.  Optionally write to a file."""
        data = _composition_to_dict(self._composition)
        text = yaml.dump(data, default_flow_style=False, sort_keys=False)
        if path is not None:
            Path(path).write_text(text)
        return text


# ── Helpers ───────────────────────────────────────────────────────────


def _composition_to_dict(comp: Composition) -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for t in comp.tools:
        tools[t.name] = {
            "internal_state": list(t.internal_state),
            "observable_schema": list(t.observable_schema),
        }
    edges_out: list[dict[str, Any]] = []
    for e in comp.edges:
        dims = []
        for d in e.dimensions:
            dim_dict: dict[str, str] = {"name": d.name}
            if d.from_field:
                dim_dict["from_field"] = d.from_field
            if d.to_field:
                dim_dict["to_field"] = d.to_field
            dims.append(dim_dict)
        edges_out.append({"from": e.from_tool, "to": e.to_tool, "dimensions": dims})
    return {"name": comp.name, "tools": tools, "edges": edges_out}


def _build_explicit_edges(
    tools: list[ToolSpec], edge_pairs: list[tuple[str, str]]
) -> list[Edge]:
    """Build edges from explicit (from, to) pairs, inferring shared dimensions."""
    tool_map = {t.name: t for t in tools}
    edges: list[Edge] = []
    for from_name, to_name in edge_pairs:
        t_from = tool_map[from_name]
        t_to = tool_map[to_name]
        shared = set(t_from.internal_state) & set(t_to.internal_state)
        dims = [
            SemanticDimension(name=f"{f}_match", from_field=f, to_field=f)
            for f in sorted(shared)
        ]
        if dims:
            edges.append(Edge(from_name, to_name, tuple(dims)))
    return edges


def _infer_edges(tools: list[ToolSpec]) -> list[Edge]:
    """Infer edges between all tool pairs using the heuristic classifier."""
    from bulla.infer.classifier import InferredDimension

    tools_dims: dict[str, list[InferredDimension]] = {}
    for t in tools:
        inferred = classify_fields(list(t.internal_state))
        tools_dims[t.name] = inferred

    raw_edges = _find_shared_dimensions(tools_dims)
    edges: list[Edge] = []
    for e in raw_edges:
        dims = tuple(
            SemanticDimension(
                name=d["name"],
                from_field=d.get("from_field"),
                to_field=d.get("to_field"),
            )
            for d in e["dimensions"]
        )
        edges.append(Edge(e["from"], e["to"], dims))
    return edges


def _composition_from_mcp_tools(
    tools_list: list[dict[str, Any]],
    *,
    name: str,
) -> tuple[Composition, WitnessBasis]:
    """Convert a list of MCP tool dicts into a Composition and WitnessBasis."""
    from bulla.infer.classifier import InferredDimension

    tool_specs: list[ToolSpec] = []
    tools_dims: dict[str, list[InferredDimension]] = {}

    for tool in tools_list:
        raw_name = tool.get("name", "unknown_tool")
        safe_name = raw_name.replace("-", "_").replace(" ", "_")
        field_infos = extract_field_infos(tool)
        fields = [fi.name for fi in field_infos]
        inferred = classify_tool_rich(tool, field_infos=field_infos)
        real_inferred = [d for d in inferred if d.field_name != "_description"]
        inferred_field_names = {d.field_name for d in real_inferred}
        observable = [f for f in fields if f not in inferred_field_names]

        tool_specs.append(ToolSpec(
            name=safe_name,
            internal_state=tuple(fields) if fields else ("_placeholder",),
            observable_schema=tuple(observable) if observable else tuple(fields[:1]) if fields else ("_placeholder",),
        ))
        tools_dims[safe_name] = inferred

    raw_edges = _find_shared_dimensions(tools_dims)
    edges: list[Edge] = []
    for e in raw_edges:
        dims = tuple(
            SemanticDimension(
                name=d["name"],
                from_field=d.get("from_field"),
                to_field=d.get("to_field"),
            )
            for d in e["dimensions"]
        )
        edges.append(Edge(e["from"], e["to"], dims))

    n_declared = 0
    n_inferred = 0
    n_unknown = 0
    for dims_list in tools_dims.values():
        for dim in dims_list:
            if dim.confidence == "declared":
                n_declared += 1
            elif dim.confidence == "inferred":
                n_inferred += 1
            else:
                n_unknown += 1

    basis = WitnessBasis(
        declared=n_declared, inferred=n_inferred, unknown=n_unknown
    )
    return Composition(name=name, tools=tuple(tool_specs), edges=tuple(edges)), basis
