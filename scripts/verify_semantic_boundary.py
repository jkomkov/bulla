#!/usr/bin/env python3
"""Zero-import verifier for a Semantic Boundary trace-refinement certificate."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROFILE = "bulla.semantic-boundary/0.3-experimental"
SCHEMA_VERSION = "0.3-experimental"
DECISIONS = {"RELY", "REFUSE", "AMBIGUOUS"}
FIELDS = {
    "schema_version", "profile", "prior_semantic_epoch", "refined_semantic_epoch", "prior_trace_hash",
    "refined_trace_hash", "prior_cells", "refined_cells", "same_domain",
    "same_epoch",
    "prior_rely_preserved", "prior_refuse_preserved", "ambiguous_antitone",
    "valid",
}


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def fail(message: str) -> None:
    raise ValueError(message)


def cells(value: Any, where: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    if not isinstance(value, list):
        fail(f"{where} must be a list")
    normalized: list[dict[str, str]] = []
    mapping: dict[str, str] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"cell_id", "decision"}:
            fail(f"{where}[{index}] has unknown or missing fields")
        cell_id, decision = item["cell_id"], item["decision"]
        if not isinstance(cell_id, str) or not cell_id or decision not in DECISIONS:
            fail(f"{where}[{index}] is malformed")
        if cell_id in mapping:
            fail(f"{where} has duplicate cell ids")
        mapping[cell_id] = decision
        normalized.append({"cell_id": cell_id, "decision": decision})
    if normalized != sorted(normalized, key=lambda item: item["cell_id"]):
        fail(f"{where} must be canonically ordered")
    return normalized, mapping


def verify(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FIELDS:
        fail("certificate has unknown or missing fields")
    if value["schema_version"] != SCHEMA_VERSION or value["profile"] != PROFILE:
        fail("certificate profile or version mismatch")
    for name in ("prior_semantic_epoch", "refined_semantic_epoch"):
        epoch = value[name]
        if not isinstance(epoch, str) or len(epoch) != 71 or not epoch.startswith("sha256:"):
            fail(f"{name} must be a full sha256 digest")
    prior_cells, prior = cells(value["prior_cells"], "prior_cells")
    refined_cells, refined = cells(value["refined_cells"], "refined_cells")
    expected = {
        "prior_trace_hash": canonical_hash({"semantic_epoch": value["prior_semantic_epoch"], "cells": prior_cells}),
        "refined_trace_hash": canonical_hash({"semantic_epoch": value["refined_semantic_epoch"], "cells": refined_cells}),
        "same_epoch": value["prior_semantic_epoch"] == value["refined_semantic_epoch"],
        "same_domain": set(prior) == set(refined),
        "prior_rely_preserved": all(refined.get(key) == "RELY" for key, decision in prior.items() if decision == "RELY"),
        "prior_refuse_preserved": all(refined.get(key) == "REFUSE" for key, decision in prior.items() if decision == "REFUSE"),
        "ambiguous_antitone": all(prior.get(key) == "AMBIGUOUS" for key, decision in refined.items() if decision == "AMBIGUOUS"),
    }
    for key, expected_value in expected.items():
        if value[key] != expected_value:
            fail(f"{key} does not recompute")
    expected_valid = all(expected[key] for key in ("same_epoch", "same_domain", "prior_rely_preserved", "prior_refuse_preserved", "ambiguous_antitone"))
    if value["valid"] is not expected_valid or not expected_valid:
        fail("certificate does not establish trace refinement")
    return {
        "ok": True,
        "profile": PROFILE,
        "semantic_epoch": value["prior_semantic_epoch"],
        "cell_count": len(prior),
        "certificate_hash": canonical_hash(value),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_semantic_boundary.py <trace-certificate.json>", file=sys.stderr)
        return 2
    try:
        value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        result = verify(value)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
