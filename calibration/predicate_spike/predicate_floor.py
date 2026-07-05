#!/usr/bin/env python3
"""Predicate-spike FLOOR probe — run against the pre-registered bar in
PRE-REGISTRATION.md (fixed before this ran).

Question: of the 703 real-schema MCP pairwise compositions, how many contain at
least one **observable, divergently-typed, shared predicate-like field** — the
same classifier word declared by both tools with incompatible types? That is the
*detectable floor* of the misalignment disease (the type-invisible case is the
ceiling, only partner-measurable). The falsifier binds on the floor.

    PYTHONPATH=bulla/src:bulla python3.11 bulla/calibration/predicate_spike/predicate_floor.py
"""

from __future__ import annotations

import itertools
import json
import unicodedata
from pathlib import Path
from typing import Any

from calibration.corpus import ManifestStore

MIN_SCHEMA_FIELDS = 3  # matches b2b_empirical_check / index.py — reproduces the 703
BAR_FRACTION = 0.05    # pre-registered: >= 5% of compositions (unit: compositions)

_NAME_LITERALS = {
    "urgent", "priority", "severity", "risk", "eligible", "approved", "active",
    "enabled", "verified", "valid", "category", "tier", "role", "mode", "status",
    "level", "state", "type", "kind", "visibility", "permission", "access",
}
_NAME_PREFIXES = ("is_", "has_")
_NAME_SUFFIXES = (
    "_status", "_state", "_level", "_flag", "_type", "_priority", "_mode",
    "_role", "_tier", "_category", "_kind", "_visibility",
)


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s)).casefold()


def _is_predicate_like(name: str, schema: dict) -> bool:
    n = _norm(name)
    t = schema.get("type")
    if t == "boolean":
        return True
    if t == "string" and schema.get("enum"):
        return True
    if t in ("integer", "number"):
        return True  # threshold-typed
    if n in _NAME_LITERALS:
        return True
    if any(n.startswith(p) for p in _NAME_PREFIXES):
        return True
    if any(n.endswith(s) for s in _NAME_SUFFIXES):
        return True
    return False


def _type_sig(schema: dict) -> tuple:
    """The comparable type signature. Divergence = different signature."""
    t = schema.get("type")
    if t == "string" and schema.get("enum"):
        return ("enum", tuple(sorted(map(str, schema["enum"]))))
    if t in ("integer", "number"):
        return ("num", t, schema.get("minimum"), schema.get("maximum"))
    return ("type", t)


def _server_predicate_fields(tools: list[dict[str, Any]]) -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    for tool in tools:
        props = (tool.get("inputSchema") or {}).get("properties") or {}
        for fname, fschema in props.items():
            if isinstance(fschema, dict) and _is_predicate_like(fname, fschema):
                out.setdefault(_norm(fname), (fname, fschema))
    return out


def main() -> int:
    root = Path(__file__).resolve().parents[2]  # bulla/
    store = ManifestStore(data_dir=root / "calibration" / "data" / "registry")

    def field_count(tools):
        return sum(len((t.get("inputSchema") or {}).get("properties") or {}) for t in tools)

    real = {s: store.get_tools(s) for s in store.list_servers()
            if field_count(store.get_tools(s)) >= MIN_SCHEMA_FIELDS}
    pfields = {s: _server_predicate_fields(t) for s, t in real.items()}

    pairs = list(itertools.combinations(sorted(real), 2))
    n_comp = len(pairs)

    floor_hits: list[dict] = []     # divergently-typed shared predicate-like fields
    ceiling_samples: list[dict] = []  # same-type shared predicate-like fields (hand-audit fodder)
    for a, b in pairs:
        fa, fb = pfields[a], pfields[b]
        shared = set(fa) & set(fb)
        diverge, same = [], []
        for name in sorted(shared):
            sa, sb = _type_sig(fa[name][1]), _type_sig(fb[name][1])
            rec = {"field": name, "a": a, "b": b, "sig_a": sa, "sig_b": sb}
            (diverge if sa != sb else same).append(rec)
        if diverge:
            floor_hits.append({"a": a, "b": b, "fields": diverge})
        ceiling_samples.extend(same)

    n_floor = len(floor_hits)
    bar = BAR_FRACTION * n_comp
    passed = n_floor >= bar

    print(f"corpus: {len(real)} real-schema servers → {n_comp} compositions (target 703)")
    print(f"FLOOR: {n_floor} compositions ({100*n_floor/n_comp:.1f}%) have >=1 observable, "
          f"divergently-typed, shared predicate-like field")
    print(f"pre-registered bar: >= {BAR_FRACTION:.0%} ({bar:.0f}) → "
          f"{'PASS — the disease is real at the detectable floor' if passed else 'FAIL → ledger NEGATIVE'}")
    print(f"CEILING fodder: {len(ceiling_samples)} same-type shared predicate-like field instances "
          f"(hand-audit ~20 for meaning divergence)")

    # the sharpest divergences → candidate DisagreementWitnesses (enum-vs-enum / type-vs-type)
    def sharpness(hit):
        return max(1 if f["sig_a"][0] == "enum" or f["sig_b"][0] == "enum" else 0 for f in hit["fields"])
    top = sorted(floor_hits, key=lambda h: (sharpness(h), len(h["fields"])), reverse=True)[:8]
    print("\nsharpest divergences (candidate DisagreementWitnesses):")
    for h in top:
        for f in h["fields"][:2]:
            print(f"  · '{f['field']}'  {h['a']} {f['sig_a']}  ≠  {h['b']} {f['sig_b']}")

    out = Path(__file__).resolve().parent / "floor_result.json"
    out.write_text(json.dumps({
        "n_compositions": n_comp, "floor_hits": n_floor,
        "floor_fraction": round(n_floor / n_comp, 4), "bar_fraction": BAR_FRACTION,
        "passed": passed, "ceiling_same_type_instances": len(ceiling_samples),
        "top_divergences": top,
    }, indent=2, default=list) + "\n")
    print(f"\nwrote {out.name}")
    return 0 if passed else 2  # nonzero on ledger-negative, honestly


if __name__ == "__main__":
    raise SystemExit(main())
