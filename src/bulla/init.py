"""Interactive composition generator (bulla init).

Stdin-driven wizard.  No curses/rich dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

import yaml

from bulla.guard import BullaGuard


def _prompt(msg: str, *, default: str = "", input_stream: TextIO = sys.stdin) -> str:
    if default:
        full = f"{msg} [{default}]: "
    else:
        full = f"{msg}: "
    sys.stdout.write(full)
    sys.stdout.flush()
    line = input_stream.readline()
    if not line:
        return default
    value = line.strip()
    return value if value else default


def run_init(
    *,
    output: Path | None = None,
    input_stream: TextIO = sys.stdin,
) -> None:
    """Interactive wizard that builds a composition YAML and diagnoses it."""
    print("bulla init — composition generator\n")

    comp_name = _prompt("Composition name", default="my-pipeline", input_stream=input_stream)

    tools: dict[str, dict[str, list[str]]] = {}
    while True:
        tool_name = _prompt(
            "\nEnter tool name (empty to finish)",
            input_stream=input_stream,
        )
        if not tool_name:
            break

        fields_raw = _prompt(
            f"  Fields for {tool_name} (comma-separated)",
            input_stream=input_stream,
        )
        fields = [f.strip() for f in fields_raw.split(",") if f.strip()]
        if not fields:
            print("  (skipping — no fields)")
            continue

        internal_raw = _prompt(
            f"  Internal-only fields for {tool_name} (comma-separated, or empty)",
            input_stream=input_stream,
        )
        internal_only = {f.strip() for f in internal_raw.split(",") if f.strip()}

        observable = [f for f in fields if f not in internal_only]

        tools[tool_name] = {
            "internal_state": fields,
            "observable_schema": observable if observable else fields,
        }
        print(f"  Added {tool_name}: {len(fields)} fields, {len(internal_only)} internal-only")

    if len(tools) < 2:
        print("\nNeed at least 2 tools to form a composition. Aborting.")
        return

    composition: dict[str, object] = {
        "name": comp_name,
        "tools": tools,
        "edges": [],
    }

    guard = BullaGuard.from_tools(
        {name: {"internal_state": spec["internal_state"],
                "observable_schema": spec["observable_schema"]}
         for name, spec in tools.items()},
        name=comp_name,
    )

    edge_dicts = []
    for edge in guard.composition.edges:
        dims = []
        for d in edge.dimensions:
            dim_entry: dict[str, str] = {"name": d.name}
            if d.from_field:
                dim_entry["from_field"] = d.from_field
            if d.to_field:
                dim_entry["to_field"] = d.to_field
            dims.append(dim_entry)
        edge_dicts.append({"from": edge.from_tool, "to": edge.to_tool, "dimensions": dims})
    composition["edges"] = edge_dicts

    yaml_text = yaml.dump(dict(composition), default_flow_style=False, sort_keys=False)
    out_path = output or Path(f"{comp_name}.yaml")
    out_path.write_text(yaml_text)
    print(f"\nWrote {out_path}")

    print("\nRunning diagnosis...")
    diag = guard.diagnose()
    print(guard.to_text())
