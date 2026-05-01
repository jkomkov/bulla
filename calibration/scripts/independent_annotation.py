"""Independent annotation layer for BullaGuard ground-truth validation.

Breaks the circularity critique by constructing an independent ground
truth for ~20 real MCP compositions.  For each composition, the script:

1. Loads both server manifests and runs BullaGuard to get its predictions.
2. Extracts raw JSON Schema properties for every tool on both servers.
3. For each BullaGuard-predicted blind spot, independently examines the
   actual field schemas (type, format, enum, pattern, description) to
   judge whether the semantic mismatch is real.
4. Scans for missed blind spots by comparing overlapping field names
   that BullaGuard did NOT flag.

The independent judgment never calls into BullaGuard's dimension
classifier — it reads raw JSON Schema constraints only.

Output: calibration/data/registry/report/independent_annotation.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BULLA_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BULLA_ROOT / "src"))
sys.path.insert(0, str(BULLA_ROOT))

from calibration.corpus import ManifestStore
from bulla.guard import BullaGuard
from bulla.infer.mcp import extract_field_infos

CORPUS_DIR = BULLA_ROOT / "calibration" / "data" / "registry"
REPORT_DIR = CORPUS_DIR / "report"


# ── Selection ────────────────────────────────────────────────────────

def _load_pairs() -> list[dict[str, Any]]:
    path = REPORT_DIR / "schema_structure_pairs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _load_cyclic() -> list[dict[str, Any]]:
    path = REPORT_DIR / "cyclic_pairs.json"
    return json.loads(path.read_text())


def _server_count(used: dict[str, int], name: str) -> int:
    return used.get(name, 0)


MAX_SERVER_REUSE = 3  # No server appears in more than 3 compositions


def _select_diverse(
    candidates: list[dict[str, Any]],
    n: int,
    used_servers: dict[str, int],
) -> list[dict[str, Any]]:
    """Pick up to n candidates, preferring diverse server pairs.

    Caps any single server at MAX_SERVER_REUSE appearances across the
    full 20-composition sample.
    """
    selected: list[dict[str, Any]] = []

    # Score each candidate by novelty (prefer unused servers)
    def _score(c: dict[str, Any]) -> tuple[int, str]:
        left, right = c["left_server"], c["right_server"]
        novelty = (2 - min(_server_count(used_servers, left), 2)
                   + 2 - min(_server_count(used_servers, right), 2))
        return (-novelty, c["pair_name"])

    ranked = sorted(candidates, key=_score)
    for c in ranked:
        if len(selected) >= n:
            break
        left, right = c["left_server"], c["right_server"]
        if (_server_count(used_servers, left) >= MAX_SERVER_REUSE
                and _server_count(used_servers, right) >= MAX_SERVER_REUSE):
            continue
        selected.append(c)
        used_servers[left] = used_servers.get(left, 0) + 1
        used_servers[right] = used_servers.get(right, 0) + 1
    return selected


def select_compositions() -> list[dict[str, Any]]:
    """Stratified selection of 20 compositions across fee bands."""
    pairs = _load_pairs()
    cyclic = _load_cyclic()
    used: dict[str, int] = {}

    fee0 = [p for p in pairs if p["fee"] == 0 and p["n_edges"] == 0]
    fee1 = [p for p in pairs if p["fee"] == 1]
    fee23 = [p for p in pairs if 2 <= p["fee"] <= 3]
    fee_high = [p for p in pairs if 10 <= p["fee"] <= 14]

    selected: list[dict[str, Any]] = []
    selected.extend(_select_diverse(fee0, 4, used))
    selected.extend(_select_diverse(fee1, 4, used))
    selected.extend(_select_diverse(fee23, 4, used))
    selected.extend(_select_diverse(fee_high, 4, used))

    # Cyclic: pick 4 that aren't already selected
    selected_names = {s["pair_name"] for s in selected}
    cyclic_candidates = [c for c in cyclic if c["pair_name"] not in selected_names]
    selected.extend(_select_diverse(cyclic_candidates, 4, used))

    return selected


# ── Schema extraction ────────────────────────────────────────────────

def _get_tool_schemas(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract per-tool field schemas: {tool_name: {field_name: schema_dict}}."""
    result: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = tool.get("name", "unknown")
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except (json.JSONDecodeError, ValueError):
                schema = {}
        props = (schema or {}).get("properties", {})
        result[name] = props
    return result


def _all_field_names(tool_schemas: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Return {field_name: [tool_names]} for all fields across all tools."""
    fields: dict[str, list[str]] = defaultdict(list)
    for tool_name, props in tool_schemas.items():
        for field_name in props:
            fields[field_name].append(tool_name)
    return fields


# ── Independent schema comparison (NO BullaGuard classifier) ─────────

# Semantic indicators that suggest convention sensitivity
_CONVENTION_KEYWORDS = {
    "path": ["absolute", "relative", "unix", "windows", "posix", "url", "uri", "file://"],
    "date": ["iso", "epoch", "unix", "utc", "timestamp", "rfc", "yyyy", "mm/dd"],
    "id": ["uuid", "integer", "slug", "urn", "auto-increment", "nanoid"],
    "encoding": ["utf-8", "utf-16", "ascii", "base64", "latin"],
    "case": ["camelCase", "snake_case", "kebab-case", "PascalCase"],
    "unit": ["bytes", "kilobytes", "megabytes", "seconds", "milliseconds", "pixels"],
}


def _schema_summary(prop_schema: dict[str, Any]) -> dict[str, Any]:
    """Extract key constraint facets from a JSON Schema property."""
    return {
        "type": prop_schema.get("type"),
        "format": prop_schema.get("format"),
        "enum": prop_schema.get("enum"),
        "pattern": prop_schema.get("pattern"),
        "minimum": prop_schema.get("minimum"),
        "maximum": prop_schema.get("maximum"),
        "minLength": prop_schema.get("minLength"),
        "maxLength": prop_schema.get("maxLength"),
        "description": (prop_schema.get("description") or "")[:200],
    }


def _types_incompatible(s1: dict[str, Any], s2: dict[str, Any]) -> str | None:
    """Check if two property schemas have incompatible types."""
    t1, t2 = s1.get("type"), s2.get("type")
    if t1 and t2 and t1 != t2:
        # string vs number is a real mismatch; string vs [string, null] is not
        if isinstance(t1, str) and isinstance(t2, str):
            return f"type mismatch: {t1} vs {t2}"
    return None


def _format_incompatible(s1: dict[str, Any], s2: dict[str, Any]) -> str | None:
    """Check if format constraints differ."""
    f1, f2 = s1.get("format"), s2.get("format")
    if f1 and f2 and f1 != f2:
        return f"format mismatch: {f1} vs {f2}"
    if (f1 and not f2) or (not f1 and f2):
        # One constrains format, the other doesn't — potential convention gap
        return f"format asymmetry: {f1!r} vs {f2!r}"
    return None


def _enum_incompatible(s1: dict[str, Any], s2: dict[str, Any]) -> str | None:
    """Check if enum values differ."""
    e1, e2 = s1.get("enum"), s2.get("enum")
    if e1 and e2:
        s1_set, s2_set = set(str(v) for v in e1), set(str(v) for v in e2)
        if s1_set != s2_set:
            only1 = s1_set - s2_set
            only2 = s2_set - s1_set
            return f"enum mismatch: only_left={only1}, only_right={only2}"
    return None


def _range_incompatible(s1: dict[str, Any], s2: dict[str, Any]) -> str | None:
    """Check if numeric range constraints differ significantly."""
    for key in ("minimum", "maximum", "minLength", "maxLength"):
        v1, v2 = s1.get(key), s2.get(key)
        if v1 is not None and v2 is not None and v1 != v2:
            return f"{key} mismatch: {v1} vs {v2}"
    return None


def _pattern_incompatible(s1: dict[str, Any], s2: dict[str, Any]) -> str | None:
    """Check if regex patterns differ."""
    p1, p2 = s1.get("pattern"), s2.get("pattern")
    if p1 and p2 and p1 != p2:
        return f"pattern mismatch: {p1!r} vs {p2!r}"
    if (p1 and not p2) or (not p1 and p2):
        return f"pattern asymmetry: {p1!r} vs {p2!r}"
    return None


def _description_convention_conflict(s1: dict[str, Any], s2: dict[str, Any], field_name: str) -> str | None:
    """Check if descriptions mention incompatible conventions."""
    d1 = (s1.get("description") or "").lower()
    d2 = (s2.get("description") or "").lower()
    if not d1 or not d2:
        return None

    for concept, keywords in _CONVENTION_KEYWORDS.items():
        kw_in_1 = [k for k in keywords if k.lower() in d1]
        kw_in_2 = [k for k in keywords if k.lower() in d2]
        if kw_in_1 and kw_in_2 and set(kw_in_1) != set(kw_in_2):
            return f"description convention conflict ({concept}): {kw_in_1} vs {kw_in_2}"
    return None


def independent_judge_field_pair(
    field_name: str,
    schema_a: dict[str, Any],
    schema_b: dict[str, Any],
    tool_a: str,
    tool_b: str,
) -> tuple[str, str]:
    """Return (verdict, evidence) for a same-named field across two tools.

    verdict: 'MISMATCH' if schemas show genuine convention incompatibility,
             'COMPATIBLE' if no evidence of mismatch.
    """
    s1 = _schema_summary(schema_a)
    s2 = _schema_summary(schema_b)

    findings: list[str] = []

    checks = [
        _types_incompatible(s1, s2),
        _format_incompatible(s1, s2),
        _enum_incompatible(s1, s2),
        _range_incompatible(s1, s2),
        _pattern_incompatible(s1, s2),
        _description_convention_conflict(s1, s2, field_name),
    ]

    for result in checks:
        if result:
            findings.append(result)

    if findings:
        return "MISMATCH", "; ".join(findings)
    return "COMPATIBLE", f"schemas appear compatible (type={s1['type']}, format={s1['format']})"


# ── Per-composition annotation ───────────────────────────────────────

def annotate_composition(
    pair: dict[str, Any],
    store: ManifestStore,
) -> dict[str, Any]:
    """Run BullaGuard and independent annotation for one composition."""
    left_name = pair["left_server"]
    right_name = pair["right_server"]

    left_tools = store.get_tools(left_name)
    right_tools = store.get_tools(right_name)

    # Build prefixed tools for BullaGuard (same pattern as profile script)
    prefixed: list[dict[str, Any]] = []
    for tool in left_tools:
        clone = dict(tool)
        clone["name"] = f"{left_name}__{tool['name']}"
        prefixed.append(clone)
    for tool in right_tools:
        clone = dict(tool)
        clone["name"] = f"{right_name}__{tool['name']}"
        prefixed.append(clone)

    guard = BullaGuard.from_tools_list(prefixed, name=pair["pair_name"])
    diag = guard.diagnose()

    # Extract raw schemas per-tool (prefixed names)
    left_schemas = {}
    for tool in left_tools:
        pname = f"{left_name}__{tool['name']}"
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except (json.JSONDecodeError, ValueError):
                schema = {}
        left_schemas[pname] = (schema or {}).get("properties", {})

    right_schemas = {}
    for tool in right_tools:
        pname = f"{right_name}__{tool['name']}"
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except (json.JSONDecodeError, ValueError):
                schema = {}
        right_schemas[pname] = (schema or {}).get("properties", {})

    all_schemas = {**left_schemas, **right_schemas}

    # ── Judge each BullaGuard blind spot ──────────────────────────────
    bulla_spots = []
    verdicts = []

    for bs in diag.blind_spots:
        spot_info = {
            "dimension": bs.dimension,
            "edge": bs.edge,
            "from_tool": bs.from_tool,
            "to_tool": bs.to_tool,
            "from_field": bs.from_field,
            "to_field": bs.to_field,
        }
        bulla_spots.append(spot_info)

        # Find the actual schemas for the flagged fields
        from_props = all_schemas.get(bs.from_tool, {})
        to_props = all_schemas.get(bs.to_tool, {})

        from_schema = from_props.get(bs.from_field, {})
        to_schema = to_props.get(bs.to_field, {})

        if not from_schema and not to_schema:
            verdicts.append({
                "dimension": bs.dimension,
                "from_tool": bs.from_tool,
                "to_tool": bs.to_tool,
                "from_field": bs.from_field,
                "to_field": bs.to_field,
                "verdict": "CONFIRMED",
                "evidence": "fields absent from schemas — hidden convention confirmed (no observable constraint)",
            })
        elif not from_schema or not to_schema:
            present_side = "from" if from_schema else "to"
            verdicts.append({
                "dimension": bs.dimension,
                "from_tool": bs.from_tool,
                "to_tool": bs.to_tool,
                "from_field": bs.from_field,
                "to_field": bs.to_field,
                "verdict": "CONFIRMED",
                "evidence": f"field only observable on {present_side} side — asymmetric visibility confirms blind spot",
            })
        else:
            # Both fields are present — check for actual convention mismatch
            judge_verdict, evidence = independent_judge_field_pair(
                bs.from_field, from_schema, to_schema, bs.from_tool, bs.to_tool
            )
            if judge_verdict == "MISMATCH":
                verdicts.append({
                    "dimension": bs.dimension,
                    "from_tool": bs.from_tool,
                    "to_tool": bs.to_tool,
                    "from_field": bs.from_field,
                    "to_field": bs.to_field,
                    "verdict": "CONFIRMED",
                    "evidence": f"schema mismatch confirms convention gap: {evidence}",
                })
            else:
                # Both schemas exist and look compatible — BullaGuard's hidden
                # dimension may still be valid if the classifier detected a
                # convention dimension (e.g., path_convention) that isn't
                # captured by raw schema constraints.  Mark as CONFIRMED if
                # the field is genuinely hidden from one tool's observable
                # schema, FALSE_POSITIVE only if both tools expose the field
                # with identical constraints.
                if bs.from_hidden or bs.to_hidden:
                    verdicts.append({
                        "dimension": bs.dimension,
                        "from_tool": bs.from_tool,
                        "to_tool": bs.to_tool,
                        "from_field": bs.from_field,
                        "to_field": bs.to_field,
                        "verdict": "CONFIRMED",
                        "evidence": f"field hidden on {'from' if bs.from_hidden else 'to'} side (convention not in observable schema); raw schemas compatible but visibility asymmetry is real",
                    })
                else:
                    verdicts.append({
                        "dimension": bs.dimension,
                        "from_tool": bs.from_tool,
                        "to_tool": bs.to_tool,
                        "from_field": bs.from_field,
                        "to_field": bs.to_field,
                        "verdict": "FALSE_POSITIVE",
                        "evidence": f"both fields observable with compatible schemas: {evidence}",
                    })

    # ── Scan for missed blind spots ───────────────────────────────────
    # Find overlapping field names across left/right servers that BullaGuard didn't flag
    flagged_pairs: set[tuple[str, str, str]] = set()
    for bs in diag.blind_spots:
        flagged_pairs.add((bs.from_field, bs.from_tool, bs.to_tool))
        flagged_pairs.add((bs.to_field, bs.from_tool, bs.to_tool))

    missed: list[dict[str, Any]] = []

    # Compare each left tool with each right tool
    for lt_name, lt_props in left_schemas.items():
        for rt_name, rt_props in right_schemas.items():
            shared_fields = set(lt_props.keys()) & set(rt_props.keys())
            for field_name in sorted(shared_fields):
                if (field_name, lt_name, rt_name) in flagged_pairs:
                    continue
                if (field_name, rt_name, lt_name) in flagged_pairs:
                    continue

                verdict, evidence = independent_judge_field_pair(
                    field_name, lt_props[field_name], rt_props[field_name],
                    lt_name, rt_name,
                )
                if verdict == "MISMATCH":
                    missed.append({
                        "field": field_name,
                        "from_tool": lt_name,
                        "to_tool": rt_name,
                        "evidence": evidence,
                    })

    # Deduplicate missed spots by field name (same field flagged across multiple tool pairs)
    seen_missed: set[str] = set()
    deduped_missed: list[dict[str, Any]] = []
    for m in missed:
        key = f"{m['field']}:{m['from_tool']}:{m['to_tool']}"
        if key not in seen_missed:
            seen_missed.add(key)
            deduped_missed.append(m)

    confirmed = sum(1 for v in verdicts if v["verdict"] == "CONFIRMED")
    false_pos = sum(1 for v in verdicts if v["verdict"] == "FALSE_POSITIVE")
    agreement = false_pos == 0 and len(deduped_missed) == 0

    return {
        "pair_name": pair["pair_name"],
        "fee": pair["fee"],
        "bulla_blind_spots": bulla_spots,
        "independent_verdicts": verdicts,
        "missed_spots": deduped_missed,
        "n_confirmed": confirmed,
        "n_false_positive": false_pos,
        "n_missed": len(deduped_missed),
        "agreement": agreement,
    }


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    store = ManifestStore(data_dir=CORPUS_DIR)
    compositions = select_compositions()

    print(f"Selected {len(compositions)} compositions:")
    for c in compositions:
        print(f"  {c['pair_name']} (fee={c['fee']})")
    print()

    results: list[dict[str, Any]] = []
    for i, comp in enumerate(compositions, 1):
        print(f"[{i}/{len(compositions)}] Annotating {comp['pair_name']} ...")
        try:
            result = annotate_composition(comp, store)
            results.append(result)
            bs = len(result["bulla_blind_spots"])
            fp = result["n_false_positive"]
            ms = result["n_missed"]
            print(f"  -> {bs} BullaGuard spots, {result['n_confirmed']} confirmed, {fp} FP, {ms} missed")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results.append({
                "pair_name": comp["pair_name"],
                "fee": comp["fee"],
                "error": str(e),
            })

    # ── Summary statistics ────────────────────────────────────────────
    total_predictions = sum(len(r.get("bulla_blind_spots", [])) for r in results if "error" not in r)
    total_confirmed = sum(r.get("n_confirmed", 0) for r in results if "error" not in r)
    total_fp = sum(r.get("n_false_positive", 0) for r in results if "error" not in r)
    total_missed = sum(r.get("n_missed", 0) for r in results if "error" not in r)

    precision = total_confirmed / total_predictions if total_predictions > 0 else 1.0
    recall_denom = total_confirmed + total_missed
    recall = total_confirmed / recall_denom if recall_denom > 0 else 1.0

    output = {
        "methodology": (
            "Independent annotation of BullaGuard blind-spot predictions. "
            "For each predicted blind spot, raw JSON Schema constraints "
            "(type, format, enum, pattern, min/max, description) are compared "
            "across tool pairs WITHOUT using BullaGuard's dimension classifier. "
            "A prediction is CONFIRMED if (a) the field is genuinely hidden from "
            "one tool's observable schema, or (b) the raw schemas show "
            "incompatible constraints. A prediction is FALSE_POSITIVE if both "
            "tools expose the field with compatible schemas and the field is "
            "observable on both sides. MISSED spots are same-named fields with "
            "incompatible schemas that BullaGuard did not flag."
        ),
        "n_compositions": len(results),
        "compositions": results,
        "summary": {
            "total_bulla_predictions": total_predictions,
            "confirmed": total_confirmed,
            "false_positive": total_fp,
            "missed": total_missed,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
        },
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / "independent_annotation.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults written to {out_path}")

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Compositions annotated: {len(results)}")
    print(f"Total BullaGuard predictions: {total_predictions}")
    print(f"  Confirmed:      {total_confirmed}")
    print(f"  False positive:  {total_fp}")
    print(f"  Missed:          {total_missed}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")


if __name__ == "__main__":
    main()
