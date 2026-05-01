"""Generate micro-packs from structural agreements across the calibration corpus.

Aggregates field schemas from all servers, clusters by field name + schema
shape, and emits candidate convention dimensions for fields that appear
consistently across multiple servers.

This is step 3 of the structural inference pipeline:
  1. Detect structural overlaps (scan_composition)
  2. Cluster consistent field patterns
  3. Emit micro-packs that feed back into the classifier

Usage:
    python calibration/scripts/generate_micropacks.py [--corpus PATH] [--min-servers N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from calibration.corpus import ManifestStore
from bulla.infer.mcp import extract_field_infos
from bulla.infer.classifier import FieldInfo


@dataclass
class FieldCluster:
    """A cluster of fields with the same name and compatible schema."""
    leaf_name: str
    schema_type: str | None
    format: str | None
    servers: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    enum_values: set[str] = field(default_factory=set)
    full_names: set[str] = field(default_factory=set)

    @property
    def signature(self) -> str:
        parts = [self.leaf_name]
        if self.schema_type:
            parts.append(self.schema_type)
        if self.format:
            parts.append(self.format)
        return ":".join(parts)


def _leaf_name(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _field_count(tools: list[dict[str, Any]]) -> int:
    total = 0
    for t in tools:
        schema = t.get("inputSchema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except (json.JSONDecodeError, TypeError):
                continue
        props = schema.get("properties") or {}
        total += len(props)
    return total


MIN_SCHEMA_FIELDS = 3


def generate_micropacks(
    corpus_dir: Path,
    min_servers: int = 3,
) -> dict[str, Any]:
    store = ManifestStore(data_dir=corpus_dir)
    servers = store.list_servers()

    clusters: dict[str, FieldCluster] = {}

    real_servers = []
    for s in servers:
        tools = store.get_tools(s)
        if tools and _field_count(tools) >= MIN_SCHEMA_FIELDS:
            real_servers.append(s)

    print(f"Servers with >= {MIN_SCHEMA_FIELDS} schema fields: {len(real_servers)}")

    for server_name in real_servers:
        tools = store.get_tools(server_name)
        for tool in tools:
            raw_name = tool.get("name", "unknown")
            field_infos = extract_field_infos(tool)
            for fi in field_infos:
                leaf = _leaf_name(fi.name)
                key = f"{leaf}:{fi.schema_type or 'none'}:{fi.format or 'none'}"

                if key not in clusters:
                    clusters[key] = FieldCluster(
                        leaf_name=leaf,
                        schema_type=fi.schema_type,
                        format=fi.format,
                    )
                cluster = clusters[key]
                cluster.servers.add(server_name)
                cluster.tools.add(f"{server_name}__{raw_name}")
                cluster.full_names.add(fi.name)
                if fi.enum:
                    cluster.enum_values.update(fi.enum)

    candidates = [
        c for c in clusters.values()
        if len(c.servers) >= min_servers
    ]
    candidates.sort(key=lambda c: len(c.servers), reverse=True)

    print(f"Field clusters found: {len(clusters)}")
    print(f"Candidate conventions (>= {min_servers} servers): {len(candidates)}")
    print()

    dimensions: dict[str, dict[str, Any]] = {}
    for c in candidates:
        dim_name = c.leaf_name
        if c.format:
            dim_name = f"{dim_name}_{c.format.replace('-', '_')}"
        if dim_name in dimensions:
            dim_name = f"{dim_name}_{c.schema_type or 'any'}"

        dim: dict[str, Any] = {
            "description": (
                f"Auto-inferred: {c.leaf_name} convention "
                f"({c.schema_type or 'any'}"
                f"{', ' + c.format if c.format else ''})"
            ),
            "field_patterns": sorted(c.full_names),
            "source": "structural",
            "n_servers": len(c.servers),
            "n_tools": len(c.tools),
        }
        if c.enum_values and len(c.enum_values) <= 20:
            dim["known_values"] = sorted(c.enum_values)
        dimensions[dim_name] = dim

    pack = {
        "pack_name": "structural_inferred",
        "pack_version": "0.1.0",
        "dimensions": dimensions,
    }

    print(f"--- Top 20 candidate conventions ---")
    for name, dim in list(dimensions.items())[:20]:
        print(
            f"  {name:30s}  "
            f"servers={dim['n_servers']:2d}  "
            f"tools={dim['n_tools']:3d}  "
            f"patterns={dim['field_patterns'][:3]}"
        )
    print()

    pack_path = corpus_dir / "report" / "structural_micropack.yaml"
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_path.write_text(yaml.dump(pack, default_flow_style=False, sort_keys=False))
    print(f"Micro-pack written to: {pack_path}")

    report = {
        "n_servers": len(real_servers),
        "n_clusters": len(clusters),
        "n_candidates": len(candidates),
        "dimensions": {
            name: {
                **dim,
                "field_patterns": dim["field_patterns"][:10],
            }
            for name, dim in dimensions.items()
        },
    }
    report_path = corpus_dir / "report" / "micropack_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"Report written to: {report_path}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate micro-packs")
    parser.add_argument(
        "--corpus",
        default="calibration/data/registry",
        help="Path to corpus directory",
    )
    parser.add_argument(
        "--min-servers",
        type=int,
        default=3,
        help="Minimum servers for a field to be a convention candidate",
    )
    args = parser.parse_args()
    generate_micropacks(Path(args.corpus), min_servers=args.min_servers)


if __name__ == "__main__":
    main()
