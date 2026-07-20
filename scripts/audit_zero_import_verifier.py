#!/usr/bin/env python3
"""Fail when a standalone verifier reaches toward production implementation code."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


FORBIDDEN_CALLS = {"__import__", "eval", "exec"}


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def audit(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "bulla" or alias.name.startswith("bulla."):
                    violations.append(f"line {node.lineno}: imports production package {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module == "bulla" or (node.module or "").startswith("bulla."):
                violations.append(f"line {node.lineno}: imports production or relative module")
        elif isinstance(node, ast.Call):
            name = dotted(node.func)
            if name in FORBIDDEN_CALLS or name in {
                "importlib.import_module",
                "sys.path.append",
                "sys.path.extend",
                "sys.path.insert",
            }:
                violations.append(f"line {node.lineno}: forbidden dynamic capability {name}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(dotted(target) == "sys.path" for target in targets):
                violations.append(f"line {node.lineno}: mutates sys.path")
    return violations


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: audit_zero_import_verifier.py <verifier.py> [...]", file=sys.stderr)
        return 2
    reports = {str(Path(raw)): audit(Path(raw)) for raw in sys.argv[1:]}
    ok = not any(reports.values())
    output = {"ok": ok, "files": reports}
    print(json.dumps(output, sort_keys=True), file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
