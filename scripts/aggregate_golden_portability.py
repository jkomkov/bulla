#!/usr/bin/env python3
"""Aggregate archived Golden portability cells without turning configuration into evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_cells(root: Path) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if value.get("schema_version") != "0.2-portability-cell":
            continue
        if value.get("observed") is not True:
            raise ValueError(f"non-observed portability cell in archive: {path}")
        value = dict(value)
        value["observation_path"] = path.relative_to(root).as_posix()
        value["observation_hash"] = digest(path)
        cells.append(value)
    return cells


def cell_key(cell: dict[str, Any]) -> tuple[str, str, str, str]:
    runner = cell["runner"]
    return (
        cell["backend"],
        runner["os"],
        runner["architecture"],
        runner["python"],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-reference", type=int, required=True)
    parser.add_argument("--expected-smt", type=int, required=True)
    parser.add_argument("--expected-lean", type=int, required=True)
    parser.add_argument("--expect-oci", action="store_true")
    args = parser.parse_args()

    cells = load_cells(args.input)
    keys = [cell_key(cell) for cell in cells]
    if len(keys) != len(set(keys)):
        raise SystemExit("duplicate portability observation cell")

    observed = {
        backend: sum(cell["backend"] == backend for cell in cells)
        for backend in ("reference", "smtinterpol", "lean")
    }
    expected = {
        "reference": args.expected_reference,
        "smtinterpol": args.expected_smt,
        "lean": args.expected_lean,
    }
    oci_files = sorted(args.input.rglob("golden-oci-observation.txt"))
    if args.expect_oci and len(oci_files) != 1:
        raise SystemExit(f"expected one OCI observation, found {len(oci_files)}")
    oci = [
        {
            "backend": "oci",
            "status": "OBSERVED_WORKFLOW",
            "observation_path": path.relative_to(args.input).as_posix(),
            "observation_hash": digest(path),
        }
        for path in oci_files
    ]
    blockers = [
        {
            "backend": backend,
            "status": "BLOCKED_MISSING_ARCHIVED_CELL",
            "missing_count": expected[backend] - observed[backend],
        }
        for backend in expected
        if observed[backend] < expected[backend]
    ]
    excess = {
        backend: observed[backend] - expected[backend]
        for backend in expected
        if observed[backend] > expected[backend]
    }
    if excess:
        raise SystemExit(f"unexpected extra portability cells: {excess}")

    report = {
        "schema_version": "0.2-portability-observation-aggregate",
        "status": "COMPLETE" if not blockers else "BLOCKED_INCOMPLETE_MATRIX",
        "expected_cell_count": sum(expected.values()) + int(args.expect_oci),
        "observed_cell_count": len(cells) + len(oci),
        "cells": cells + oci,
        "blockers": blockers,
        "claim_boundary": "Only archived executions are observations; workflow configuration is not evidence.",
        "signed": False,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "expected_cell_count", "observed_cell_count")}))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
