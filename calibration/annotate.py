"""Phase 3b: LLM-assisted blind spot annotation.

Extends ground truth coverage beyond live-validated cases.
Uses the existing bulla discover adapter infrastructure.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BATCH_SIZE = 15  # blind spots per LLM call
VERDICT_LABELS = {"REAL_MISMATCH", "PLAUSIBLE", "FALSE_POSITIVE"}


@dataclass
class AnnotationResult:
    """Result of annotating one blind spot."""

    blind_spot_id: int
    dimension: str
    from_tool: str
    to_tool: str
    annotation: str  # REAL_MISMATCH | PLAUSIBLE | FALSE_POSITIVE
    evidence: str


def _build_prompt(blind_spots: list[dict[str, Any]]) -> str:
    """Build a batched annotation prompt."""
    parts = [
        "You are evaluating whether semantic convention mismatches between "
        "MCP server tools represent real risks of silent data corruption.\n\n"
        "For each blind spot below, assess whether the two tools could disagree "
        "on the flagged convention dimension, causing silently wrong results "
        "when data flows between them.\n\n"
        "Respond with one verdict per blind spot using the numbered delimiters.\n"
    ]

    for i, bs in enumerate(blind_spots):
        parts.append(f"\n---BEGIN_CASE_{i}---")
        desc_a = bs.get("from_tool_desc", "")
        desc_b = bs.get("to_tool_desc", "")
        parts.append(f"Tool A: {bs['from_tool']}")
        if desc_a:
            parts.append(f"  Description: {desc_a[:200]}")
        parts.append(f"  Field: {bs['from_field']}")
        parts.append(f"Tool B: {bs['to_tool']}")
        if desc_b:
            parts.append(f"  Description: {desc_b[:200]}")
        parts.append(f"  Field: {bs['to_field']}")
        parts.append(f"Dimension: {bs['dimension']}")
        parts.append(
            f"\nCould these tools disagree on {bs['dimension']}? "
            f"Would passing data from Tool A's {bs['from_field']} to "
            f"Tool B's {bs['to_field']} risk a silent semantic mismatch?"
        )
        parts.append(f"---END_CASE_{i}---")

    parts.append(
        "\nFor each case, respond with:\n"
        "---BEGIN_VERDICT_N---\n"
        "VERDICT: REAL_MISMATCH | PLAUSIBLE | FALSE_POSITIVE\n"
        "EVIDENCE: Brief explanation (1-2 sentences)\n"
        "---END_VERDICT_N---\n"
    )
    return "\n".join(parts)


def _parse_response(raw: str, n_cases: int) -> list[tuple[str, str]]:
    """Parse batched LLM response into (verdict, evidence) pairs."""
    results: list[tuple[str, str]] = []

    for i in range(n_cases):
        pattern = rf"---BEGIN_VERDICT_{i}---\s*(.*?)\s*---END_VERDICT_{i}---"
        match = re.search(pattern, raw, re.DOTALL)
        if not match:
            results.append(("PLAUSIBLE", "Could not parse verdict"))
            continue

        block = match.group(1)
        verdict = "PLAUSIBLE"
        evidence = ""

        for label in VERDICT_LABELS:
            if label in block.upper():
                verdict = label
                break

        ev_match = re.search(r"EVIDENCE:\s*(.+?)(?:\n|$)", block, re.DOTALL)
        if ev_match:
            evidence = ev_match.group(1).strip()

        results.append((verdict, evidence))

    return results


def _get_unannotated_blind_spots(
    conn: sqlite3.Connection,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Fetch blind spots not yet annotated."""
    rows = conn.execute(
        "SELECT id, comp_id, dimension, from_tool, to_tool, from_field, to_field "
        "FROM blind_spots WHERE annotation IS NULL ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "comp_id": r[1],
            "dimension": r[2],
            "from_tool": r[3],
            "to_tool": r[4],
            "from_field": r[5],
            "to_field": r[6],
        }
        for r in rows
    ]


def _load_tool_context(manifests_dir: Path) -> dict[str, dict[str, Any]]:
    """Load tool descriptions from manifest files for richer prompts."""
    tools: dict[str, dict[str, Any]] = {}
    if not manifests_dir.exists():
        return tools
    for json_file in manifests_dir.glob("*.json"):
        try:
            data = json.loads(json_file.read_text())
            server_name = json_file.stem
            tool_list = data.get("tools", data) if isinstance(data, dict) else data
            if not isinstance(tool_list, list):
                continue
            for t in tool_list:
                full_name = f"{server_name}__{t.get('name', '')}"
                tools[full_name] = {
                    "description": t.get("description", ""),
                    "inputSchema": t.get("inputSchema", {}),
                }
        except Exception:
            continue
    return tools


def annotate_batch(
    db_path: str | Path,
    *,
    provider: str = "auto",
    adapter: Any | None = None,
    manifests_dir: Path | None = None,
    sample_size: int = 500,
    batch_size: int = BATCH_SIZE,
) -> list[AnnotationResult]:
    """Annotate unannotated blind spots using LLM.

    Args:
        adapter: Pre-constructed adapter (takes precedence over provider).
        manifests_dir: Directory of manifest JSONs for richer tool context.
    """
    if adapter is None:
        from bulla.discover.adapter import get_adapter
        adapter = get_adapter(provider=provider)

    tool_context = _load_tool_context(manifests_dir) if manifests_dir else {}
    conn = sqlite3.connect(str(db_path))
    blind_spots = _get_unannotated_blind_spots(conn, limit=sample_size)

    if not blind_spots:
        logger.info("No unannotated blind spots to process")
        conn.close()
        return []

    logger.info("Annotating %d blind spots in batches of %d", len(blind_spots), batch_size)
    results: list[AnnotationResult] = []

    for batch_start in range(0, len(blind_spots), batch_size):
        batch = blind_spots[batch_start : batch_start + batch_size]
        # Enrich with tool descriptions if available
        for bs in batch:
            tc_a = tool_context.get(bs["from_tool"], {})
            tc_b = tool_context.get(bs["to_tool"], {})
            bs["from_tool_desc"] = tc_a.get("description", "")
            bs["to_tool_desc"] = tc_b.get("description", "")
        prompt = _build_prompt(batch)

        try:
            raw_response = adapter.complete(prompt)
            verdicts = _parse_response(raw_response, len(batch))
        except Exception as e:
            logger.warning("LLM call failed for batch at %d: %s", batch_start, e)
            verdicts = [("PLAUSIBLE", f"LLM error: {e}")] * len(batch)

        for bs, (verdict, evidence) in zip(batch, verdicts):
            conn.execute(
                "UPDATE blind_spots SET annotation = ?, annotation_source = 'llm' "
                "WHERE id = ?",
                (verdict, bs["id"]),
            )
            results.append(AnnotationResult(
                blind_spot_id=bs["id"],
                dimension=bs["dimension"],
                from_tool=bs["from_tool"],
                to_tool=bs["to_tool"],
                annotation=verdict,
                evidence=evidence,
            ))

        conn.commit()
        logger.info(
            "  Batch %d-%d: %d annotations",
            batch_start, batch_start + len(batch), len(batch),
        )

    conn.close()
    logger.info("Annotation complete: %d blind spots", len(results))
    return results


def annotation_stats(db_path: str | Path) -> dict[str, Any]:
    """Return annotation statistics."""
    conn = sqlite3.connect(str(db_path))
    total = conn.execute("SELECT COUNT(*) FROM blind_spots").fetchone()[0]
    annotated = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE annotation IS NOT NULL"
    ).fetchone()[0]
    by_label = conn.execute(
        "SELECT annotation, COUNT(*) FROM blind_spots "
        "WHERE annotation IS NOT NULL GROUP BY annotation"
    ).fetchall()
    by_source = conn.execute(
        "SELECT annotation_source, COUNT(*) FROM blind_spots "
        "WHERE annotation IS NOT NULL GROUP BY annotation_source"
    ).fetchall()
    validated = conn.execute(
        "SELECT COUNT(*) FROM blind_spots WHERE validated = 1"
    ).fetchone()[0]
    conn.close()

    return {
        "total_blind_spots": total,
        "annotated": annotated,
        "unannotated": total - annotated,
        "by_label": dict(by_label),
        "by_source": dict(by_source),
        "live_validated": validated,
    }


def run(
    *,
    db_path: str | Path = "calibration/data/coherence.db",
    provider: str = "auto",
    adapter: Any | None = None,
    manifests_dir: Path | None = None,
    sample_size: int = 500,
) -> list[AnnotationResult]:
    """Run the annotation pipeline."""
    return annotate_batch(
        db_path, provider=provider, adapter=adapter,
        manifests_dir=manifests_dir, sample_size=sample_size,
    )
