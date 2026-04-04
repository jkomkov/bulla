"""Output formatters: text, JSON, SARIF."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from bulla import __version__
from bulla.model import Bridge, Diagnostic


# ── Text ──────────────────────────────────────────────────────────────


def format_text(d: Diagnostic) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(f"  {d.name}")
    lines.append(f"  {'═' * len(d.name)}")
    lines.append("")
    lines.append(
        f"  Topology: {d.n_tools} tools, {d.n_edges} edges, "
        f"\u03b2\u2081 = {d.betti_1}"
    )
    lines.append("")
    lines.append("  Observable sheaf (F):")
    lines.append(
        f"    C\u2070 = {d.dim_c0_obs}    C\u00b9 = {d.dim_c1}    "
        f"rank(\u03b4\u2070) = {d.rank_obs}"
    )
    lines.append(f"    H\u00b9(F_obs) = {d.h1_obs}")
    lines.append("")
    lines.append("  Full sheaf (S):")
    lines.append(
        f"    C\u2070 = {d.dim_c0_full}    C\u00b9 = {d.dim_c1}    "
        f"rank(\u03b4\u2070) = {d.rank_full}"
    )
    lines.append(f"    H\u00b9(F_full) = {d.h1_full}")
    lines.append("")

    if d.coherence_fee == 0:
        lines.append("  \u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
        lines.append("  \u2551  COHERENCE FEE = 0  \u2713                \u2551")
        lines.append("  \u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d")
    else:
        fee_str = f"COHERENCE FEE = {d.coherence_fee}"
        lines.append(f"  \u2554\u2550{'═' * len(fee_str)}\u2550\u2557")
        lines.append(f"  \u2551 {fee_str} \u2551")
        lines.append(f"  \u255a\u2550{'═' * len(fee_str)}\u2550\u255d")
    lines.append("")

    if d.blind_spots:
        if d.coherence_fee == 0:
            lines.append(
                "  Note: fee = 0 but blind spots exist. The coherence fee"
            )
            lines.append(
                "  measures topological information loss (H\u00b9); blind spots"
            )
            lines.append(
                "  are hidden fields on edges. One-sided hiding reduces"
            )
            lines.append(
                "  auditability without changing H\u00b9."
            )
            lines.append("")
        lines.append(f"  Blind spots ({len(d.blind_spots)}):")
        for i, bs in enumerate(d.blind_spots, 1):
            locs: list[str] = []
            if bs.from_hidden:
                locs.append(f"{bs.from_field} \u2208 S\\F at {bs.from_tool}")
            if bs.to_hidden:
                locs.append(f"{bs.to_field} \u2208 S\\F at {bs.to_tool}")
            lines.append(f"    [{i}] {bs.dimension} ({bs.edge})")
            lines.append(f"        {'; '.join(locs)}")
        lines.append("")

        seen_fields: set[str] = set()
        unique_bridges: list[Bridge] = []
        for br in d.bridges:
            if br.field not in seen_fields:
                merged_tools: list[str] = []
                for b2 in d.bridges:
                    if b2.field == br.field:
                        for t in b2.add_to:
                            if t not in merged_tools:
                                merged_tools.append(t)
                unique_bridges.append(
                    Bridge(
                        field=br.field,
                        add_to=merged_tools,
                        eliminates=br.eliminates,
                    )
                )
                seen_fields.add(br.field)

        lines.append("  Recommended bridges:")
        for i, br in enumerate(unique_bridges, 1):
            tools_str = " and ".join(f"F({t})" for t in br.add_to)
            lines.append(f"    [{i}] Add '{br.field}' to {tools_str}")
        lines.append("")
        fee_after = d.h1_after_bridge - d.h1_full
        after_msg = f"  After bridging: fee = {fee_after}"
        if fee_after == 0:
            after_msg += " \u2713"
        if d.h1_after_bridge > 0 and fee_after == 0:
            after_msg += (
                f"  (residual H\u00b9 = {d.h1_after_bridge} is topological)"
            )
        lines.append(after_msg)
    else:
        if d.h1_obs > 0 and d.h1_full > 0 and d.coherence_fee == 0:
            lines.append("  No observability blind spots.")
            lines.append(
                f"  H\u00b9 = {d.h1_obs} is purely topological "
                f"(cycle redundancy, not information loss)."
            )
        else:
            lines.append("  No blind spots detected.")
            lines.append("  All bilateral dimensions are observable.")

    lines.append("")
    return "\n".join(lines)


# ── JSON ──────────────────────────────────────────────────────────────


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def format_json(d: Diagnostic, source_path: Path | None = None) -> str:
    obj: dict = {
        "name": d.name,
        "bulla_version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "topology": {
            "tools": d.n_tools,
            "edges": d.n_edges,
            "betti_1": d.betti_1,
        },
        "observable_sheaf": {
            "dim_c0": d.dim_c0_obs,
            "dim_c1": d.dim_c1,
            "rank": d.rank_obs,
            "h1": d.h1_obs,
        },
        "full_sheaf": {
            "dim_c0": d.dim_c0_full,
            "dim_c1": d.dim_c1,
            "rank": d.rank_full,
            "h1": d.h1_full,
        },
        "coherence_fee": d.coherence_fee,
        "blind_spots": [
            {
                "dimension": bs.dimension,
                "edge": bs.edge,
                "from_field": bs.from_field,
                "to_field": bs.to_field,
                "from_hidden": bs.from_hidden,
                "to_hidden": bs.to_hidden,
            }
            for bs in d.blind_spots
        ],
        "n_unbridged": d.n_unbridged,
        "bridges": [
            {
                "field": br.field,
                "add_to": br.add_to,
                "eliminates": br.eliminates,
            }
            for br in d.bridges
        ],
        "h1_after_bridge": d.h1_after_bridge,
    }
    if source_path:
        obj["composition_sha256"] = _file_sha256(source_path)
    return json.dumps(obj, indent=2)


# ── SARIF ─────────────────────────────────────────────────────────────


def format_sarif(
    diagnostics: list[tuple[Diagnostic, Path]],
) -> str:
    """Produce a SARIF v2.1.0 document for one or more composition diagnostics."""

    results: list[dict] = []
    for diag, path in diagnostics:
        file_hash = _file_sha256(path) if path.exists() else "N/A"
        for bs in diag.blind_spots:
            locs: list[str] = []
            if bs.from_hidden:
                locs.append(f"'{bs.from_field}' is hidden at {bs.from_tool}")
            if bs.to_hidden:
                locs.append(f"'{bs.to_field}' is hidden at {bs.to_tool}")
            results.append(
                {
                    "ruleId": "bulla/blind-spot",
                    "level": "warning",
                    "message": {
                        "text": (
                            f"Blind spot: dimension '{bs.dimension}' on "
                            f"edge {bs.edge} is not bilaterally observable. "
                            + "; ".join(locs)
                        )
                    },
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {
                                    "uri": str(path),
                                    "uriBaseId": "%SRCROOT%",
                                },
                            }
                        }
                    ],
                    "properties": {
                        "compositionName": diag.name,
                        "compositionSha256": file_hash,
                    },
                }
            )

        seen_fields: set[str] = set()
        for br in diag.bridges:
            if br.field in seen_fields:
                continue
            seen_fields.add(br.field)
            merged_tools: list[str] = []
            for b2 in diag.bridges:
                if b2.field == br.field:
                    for t in b2.add_to:
                        if t not in merged_tools:
                            merged_tools.append(t)
            tools_str = " and ".join(f"F({t})" for t in merged_tools)
            results.append(
                {
                    "ruleId": "bulla/bridge-recommendation",
                    "level": "note",
                    "message": {
                        "text": (
                            f"Recommended bridge: add '{br.field}' to "
                            f"{tools_str} to eliminate "
                            f"'{br.eliminates}' blind spot."
                        )
                    },
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {
                                    "uri": str(path),
                                    "uriBaseId": "%SRCROOT%",
                                },
                            }
                        }
                    ],
                    "properties": {
                        "compositionName": diag.name,
                        "compositionSha256": file_hash,
                    },
                }
            )

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "bulla",
                        "version": __version__,
                        "informationUri": "https://github.com/jkomkov/bulla",
                        "rules": [
                            {
                                "id": "bulla/blind-spot",
                                "shortDescription": {
                                    "text": "Semantic dimension not bilaterally observable"
                                },
                                "helpUri": "https://github.com/jkomkov/bulla#blind-spots",
                            },
                            {
                                "id": "bulla/bridge-recommendation",
                                "shortDescription": {
                                    "text": "Recommended bridge to eliminate blind spot"
                                },
                                "helpUri": "https://github.com/jkomkov/bulla#bridges",
                            },
                        ],
                    }
                },
                "results": results,
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "toolExecutionNotifications": [],
                        "properties": {
                            "bulla_version": __version__,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(sarif, indent=2)
