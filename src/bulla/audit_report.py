"""Screenshot-grade `bulla audit` text presentation.

Measurement stays in :mod:`bulla.diagnostic`; this module only shapes
human-readable receipts (boundary-first, compact finding cards).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from bulla.diagnostic import FeeDecomposition
    from bulla.model import BlindSpot, Diagnostic

# Higher-impact dimensions surface first after boundary classification.
_DIMENSION_IMPACT_ORDER: tuple[str, ...] = (
    "path_convention",
    "amount_unit",
    "monetary_amount",
    "currency_unit",
    "currency",
    "timestamp_format",
    "date_format",
    "datetime",
    "encoding",
    "charset",
    "id_offset",
    "pagination",
    "page_index",
)

# Default fourth line is enough; verbose adds "what can break" for these.
_OBSCURE_DIMENSIONS: frozenset[str] = frozenset({
    "id_offset",
    "pagination",
    "page_index",
    "gauge_choice",
    "timezone",
    "locale",
})


def server_of(tool_name: str) -> str:
    """Return MCP server prefix from a prefixed tool name."""
    if "__" in tool_name:
        return tool_name.split("__", 1)[0]
    return ""


def tool_suffix(tool_name: str) -> str:
    """Strip ``server__`` prefix for display."""
    if "__" in tool_name:
        return tool_name.split("__", 1)[1]
    return tool_name


def display_dimension(name: str) -> str:
    """Strip ``_match`` suffix from internal dimension names for display."""
    if name.endswith("_match"):
        return name[: -len("_match")]
    return name


def is_boundary_blind_spot(bs: "BlindSpot") -> bool:
    """True when the blind spot crosses distinct MCP servers."""
    a = server_of(bs.from_tool)
    b = server_of(bs.to_tool)
    return bool(a and b and a != b)


def _norm_tool_name(name: str) -> str:
    return name.replace("-", "_").replace(" ", "_")


def _dimension_sort_key(dim: str) -> tuple[int, str]:
    try:
        idx = _DIMENSION_IMPACT_ORDER.index(dim)
    except ValueError:
        idx = len(_DIMENSION_IMPACT_ORDER)
    return (idx, dim)


def _json_prop_type(tool: dict[str, Any] | None, field: str) -> str | None:
    if not tool or not field:
        return None
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    props = schema.get("properties") or {}
    node = props.get(field)
    if not isinstance(node, dict):
        return None
    t = node.get("type")
    if isinstance(t, list):
        return "|".join(str(x) for x in t)
    return str(t) if t is not None else None


def _raw_tools_index(raw_tools: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if not raw_tools:
        return {}
    return {_norm_tool_name(t.get("name", "")): t for t in raw_tools if t.get("name")}


def _schema_validation_line(
    bs: "BlindSpot",
    raw_by_name: dict[str, dict[str, Any]],
) -> str:
    ft = _json_prop_type(raw_by_name.get(_norm_tool_name(bs.from_tool)), bs.from_field)
    tt = _json_prop_type(raw_by_name.get(_norm_tool_name(bs.to_tool)), bs.to_field)
    if ft and tt and ft == tt:
        if bs.from_field == bs.to_field:
            return f"both accept {bs.from_field}: {ft} — validation passes"
        return (
            f"both typed as {ft} ({bs.from_field} ↔ {bs.to_field}) "
            "— validation passes"
        )
    if ft or tt:
        return (
            f"schema types differ ({bs.from_field}: {ft or '?'}"
            f" vs {bs.to_field}: {tt or '?'}) — still passes pairwise checks"
        )
    return (
        "shared semantic dimension — local schemas do not surface "
        "the mismatch as a type error"
    )


def _probe_conventions(
    guided_repair: dict[str, Any] | None,
    dimension: str,
) -> dict[str, str]:
    """Map server prefix -> convention_value from guided repair probes."""
    out: dict[str, str] = {}
    if not guided_repair:
        return out
    for p in guided_repair.get("probes") or []:
        if p.get("verdict") != "CONFIRMED":
            continue
        obl = p.get("obligation") or {}
        if obl.get("dimension") != dimension:
            continue
        tool = obl.get("placeholder_tool") or ""
        val = p.get("convention_value") or ""
        if tool and val:
            out[tool] = val
    return out


def _convention_contrast_line(
    bs: "BlindSpot",
    raw_by_name: dict[str, dict[str, Any]],
    guided_repair: dict[str, Any] | None,
) -> str:
    probes = _probe_conventions(guided_repair, bs.dimension)
    sa = server_of(bs.from_tool)
    sb = server_of(bs.to_tool)
    if probes:
        left = probes.get(sa)
        right = probes.get(sb)
        if left and right:
            return f"{sa}: {left}    {sb}: {right}"
        if left:
            return f"{sa}: {left}    {sb}: (unconfirmed)"
        if right:
            return f"{sa}: (unconfirmed)    {sb}: {right}"

    dim = bs.dimension
    if dim in ("path_convention", "filepath_convention"):
        return f"{sa}: absolute/local paths    {sb}: repo-relative paths"
    if dim in ("encoding", "charset"):
        fa = _json_prop_type(raw_by_name.get(_norm_tool_name(bs.from_tool)), bs.from_field)
        fb = _json_prop_type(raw_by_name.get(_norm_tool_name(bs.to_tool)), bs.to_field)
        ea = "explicit encoding field" if fa else "no encoding field"
        eb = "explicit encoding field" if fb else "no encoding field"
        return f"{sa}: {ea}    {sb}: {eb}"
    if dim in ("currency", "currency_unit"):
        return f"{sa}: currency semantics implicit in tool    {sb}: idem"
    if dim in ("timestamp_format", "date_format", "datetime"):
        return f"{sa}: timestamp/date interpretation local to tool    {sb}: idem"

    fh = "hidden" if bs.from_hidden else "visible"
    th = "hidden" if bs.to_hidden else "visible"
    return (
        f"{sa}: `{bs.from_field}` {fh} in observable schema    "
        f"{sb}: `{bs.to_field}` {th} in observable schema"
    )


def _risk_hint_line(bs: "BlindSpot") -> str:
    dim = bs.dimension
    if dim == "path_convention":
        return (
            "risk: writes land in the wrong tree or silently wrong file targets"
        )
    if dim in ("encoding", "charset"):
        return "risk: text corruption or mistaken binary handling"
    if dim in ("currency", "currency_unit", "amount_unit"):
        return "risk: wrong monetary magnitude or unit drift across tools"
    if dim in ("timestamp_format", "date_format", "datetime"):
        return "risk: ordering / TTL / reconciliation bugs from skewed time semantics"
    if dim in ("id_offset", "pagination", "page_index"):
        return "risk: skipped / duplicated records across paginated calls"
    return (
        f"risk: `{bs.from_field}` ↔ `{bs.to_field}` may diverge silently "
        "across the seam"
    )


def _fix_line(bs: "BlindSpot", bridges: tuple[Any, ...]) -> str:
    dim = display_dimension(bs.dimension)
    for br in bridges:
        if getattr(br, "eliminates", "") != bs.dimension:
            continue
        add_to = getattr(br, "add_to", ()) or ()
        field = getattr(br, "field", "")
        for tool in add_to:
            return f"fix: bridge {dim} on {tool_suffix(tool)}.{field}"
    # Fallback: bridge the hidden side first
    if bs.to_hidden:
        return f"fix: bridge {dim} on {tool_suffix(bs.to_tool)}.{bs.to_field}"
    if bs.from_hidden:
        return f"fix: bridge {dim} on {tool_suffix(bs.from_tool)}.{bs.from_field}"
    return f"fix: bridge {dim} across {server_of(bs.from_tool)} → {server_of(bs.to_tool)}"


def _sorted_boundary_blind_spots(blind_spots: Iterable["BlindSpot"]) -> tuple["BlindSpot", ...]:
    boundary = [bs for bs in blind_spots if is_boundary_blind_spot(bs)]
    boundary.sort(
        key=lambda bs: (
            _dimension_sort_key(bs.dimension),
            server_of(bs.from_tool),
            server_of(bs.to_tool),
            bs.from_tool,
            bs.to_tool,
        ),
    )
    return tuple(boundary)


@dataclass(frozen=True)
class ServerRow:
    name: str
    ok: bool
    tools_count: int | None
    error_summary: str | None


@dataclass(frozen=True)
class BoundaryFinding:
    blind_spot_index: int
    dimension: str
    from_server: str
    to_server: str
    schema_line: str
    convention_line: str
    fix_line: str


@dataclass(frozen=True)
class AuditReport:
    """Frozen snapshot for audit stdout (text serializer lives below)."""

    context_line: str
    server_rows: tuple[ServerRow, ...]
    n_tools_composition: int
    boundary_fee: int
    within_fee: int
    boundary_findings: tuple[BoundaryFinding, ...]
    within_counts: tuple[tuple[str, int], ...]
    n_bridges: int
    boundary_blind_spot_total: int
    within_blind_spot_total: int
    coherence_fee_total: int
    # Boundary-relevant disclosure: (server, field) pairs needed for boundary fee → 0
    disclosure_snippet: tuple[tuple[str, str], ...] = ()


def build_audit_report(
    server_results: list[Any],
    diag: "Diagnostic",
    decomposition: "FeeDecomposition | None",
    *,
    raw_tools: list[dict[str, Any]] | None = None,
    guided_repair: dict[str, Any] | None = None,
    context_line: str | None = None,
    disclosure: list[tuple[str, str]] | None = None,
) -> AuditReport:
    """Assemble a structured audit report for text (and optional JSON) export."""
    rows: list[ServerRow] = []
    for r in server_results:
        name = getattr(r, "name", "?")
        ok = bool(getattr(r, "ok", False))
        tools = getattr(r, "tools", None) or []
        err = getattr(r, "error", None)
        err_sum = None
        if err:
            err_sum = str(err).split("\n")[0][:72]
        rows.append(
            ServerRow(
                name=name,
                ok=ok,
                tools_count=len(tools) if ok else None,
                error_summary=err_sum,
            )
        )

    ok_n = sum(1 for x in rows if x.ok)
    n_tools = diag.n_tools

    if context_line is None:
        host_part = f"{len(rows)} servers"
        context_line = f"{host_part} · {n_tools} tools"

    if decomposition is not None:
        boundary_fee = decomposition.boundary_fee
        within_fee = sum(decomposition.local_fees)
    else:
        boundary_fee = 0
        within_fee = diag.coherence_fee

    raw_by = _raw_tools_index(raw_tools)
    boundary_bs = _sorted_boundary_blind_spots(diag.blind_spots)
    groups: dict[tuple[str, str, str], list[BlindSpot]] = {}
    for bs in boundary_bs:
        gkey = (bs.dimension, server_of(bs.from_tool), server_of(bs.to_tool))
        groups.setdefault(gkey, []).append(bs)

    findings: list[BoundaryFinding] = []
    for i, gkey in enumerate(
        sorted(groups.keys(), key=lambda k: (_dimension_sort_key(k[0]), k[1], k[2])),
        1,
    ):
        grp = groups[gkey]
        bs0 = grp[0]
        dim_display = display_dimension(bs0.dimension)
        dim_disp = dim_display if len(grp) == 1 else f"{dim_display} ({len(grp)} tool-pairs)"
        schema_opts = {_schema_validation_line(bs, raw_by) for bs in grp}
        if len(schema_opts) == 1:
            schema_line = next(iter(schema_opts))
        else:
            schema_line = (
                f"{len(schema_opts)} distinct field pairs — typed compatibly "
                "— validation passes locally"
            )
        sa, sb = server_of(bs0.from_tool), server_of(bs0.to_tool)
        if "path" in bs0.dimension:
            convention_line = f"{sa}: absolute/local paths    {sb}: repo-relative paths"
        else:
            conv_opts = sorted({
                _convention_contrast_line(bs, raw_by, guided_repair) for bs in grp
            })
            if len(conv_opts) == 1:
                convention_line = conv_opts[0]
            else:
                convention_line = (
                    "mixed conventions along this seam — "
                    "`bulla audit -v` enumerates field pairs"
                )
        fix_line = _fix_line(bs0, diag.bridges)
        findings.append(
            BoundaryFinding(
                blind_spot_index=i,
                dimension=dim_disp,
                from_server=server_of(bs0.from_tool),
                to_server=server_of(bs0.to_tool),
                schema_line=schema_line,
                convention_line=convention_line,
                fix_line=fix_line,
            )
        )

    within_only = [bs for bs in diag.blind_spots if not is_boundary_blind_spot(bs)]
    per_server: dict[str, int] = {}
    for bs in within_only:
        key = server_of(bs.from_tool) or server_of(bs.to_tool) or "unknown"
        per_server[key] = per_server.get(key, 0) + 1
    within_counts = tuple(sorted(per_server.items(), key=lambda x: (-x[1], x[0])))

    # Compute boundary-relevant disclosure snippet: filter the global
    # disclosure set to tools involved in boundary blind spots, then
    # deduplicate by (server, field) for a compact repair summary.
    snippet: list[tuple[str, str]] = []
    if disclosure and boundary_bs:
        boundary_tools = set()
        for bs in boundary_bs:
            boundary_tools.add(bs.from_tool)
            boundary_tools.add(bs.to_tool)
        seen: set[tuple[str, str]] = set()
        for tool, field in disclosure:
            if tool in boundary_tools:
                key = (server_of(tool), field)
                if key not in seen:
                    seen.add(key)
                    snippet.append(key)

    return AuditReport(
        context_line=context_line,
        server_rows=tuple(rows),
        n_tools_composition=n_tools,
        boundary_fee=boundary_fee,
        within_fee=within_fee,
        boundary_findings=tuple(findings),
        within_counts=within_counts,
        n_bridges=len(diag.bridges),
        boundary_blind_spot_total=len(boundary_bs),
        within_blind_spot_total=len(within_only),
        coherence_fee_total=diag.coherence_fee,
        disclosure_snippet=tuple(snippet),
    )


def audit_report_to_json_dict(report: AuditReport) -> dict[str, Any]:
    """Compact, additive JSON view of the receipt (optional ``audit_report`` block)."""
    return {
        "context": report.context_line,
        "tools": report.n_tools_composition,
        "boundary_fee": report.boundary_fee,
        "within_fee": report.within_fee,
        "coherence_fee": report.coherence_fee_total,
        "boundary_blind_spots": report.boundary_blind_spot_total,
        "within_blind_spots": report.within_blind_spot_total,
        "boundary_findings": [
            {
                "dimension": f.dimension,
                "from_server": f.from_server,
                "to_server": f.to_server,
                "schema": f.schema_line,
                "convention": f.convention_line,
                "fix": f.fix_line,
            }
            for f in report.boundary_findings
        ],
        "within_by_server": {s: n for s, n in report.within_counts},
        "bridges": report.n_bridges,
    }


def _rule(width: int = 42) -> str:
    return "─" * width


def format_audit_report_text(
    report: AuditReport,
    *,
    verbose: bool = False,
    blind_spots: tuple[Any, ...] | None = None,
    bridges: tuple[Any, ...] | None = None,
) -> str:
    """Render the screenshot-style receipt."""
    lines: list[str] = []
    lines.append("bulla audit")
    lines.append(_rule(11))
    lines.append(report.context_line)
    lines.append("")

    for row in report.server_rows:
        if row.ok:
            lines.append(f"  ✓ {row.name:<18} {row.tools_count:>3} tools")
        else:
            err = row.error_summary or "failed"
            lines.append(f"  ✗ {row.name:<18} {err}")

    lines.append("")
    lines.append("┌──────────────────────┐")
    lines.append(f"│  BOUNDARY FEE  {report.boundary_fee:>5} │")
    lines.append(f"│  within fee    {report.within_fee:>5} │")
    lines.append("└──────────────────────┘")

    if report.boundary_findings:
        lines.append("")
        lines.append(f"── cross-server {_rule(28)}")
        lines.append("")
        for f in report.boundary_findings:
            head = f"{f.dimension}    {f.from_server} → {f.to_server}"
            lines.append(f"⚠ {head}")
            lines.append(f"  {f.schema_line}")
            lines.append(f"  {f.convention_line}")
            lines.append(f"  {f.fix_line}")
            if verbose and blind_spots:
                matching = [
                    bs
                    for bs in blind_spots
                    if is_boundary_blind_spot(bs) and bs.dimension == f.dimension
                    and server_of(bs.from_tool) == f.from_server
                    and server_of(bs.to_tool) == f.to_server
                ]
                if matching:
                    bs0 = matching[0]
                    if bs0.dimension in _OBSCURE_DIMENSIONS:
                        lines.append(f"  {_risk_hint_line(bs0)}")
            lines.append("")

    within_n = report.within_blind_spot_total
    if within_n:
        lines.append(f"── within-server ({within_n} blind spots) {_rule(14)}")
        lines.append("")
        if report.within_counts:
            parts = [f"{srv}  {cnt}" for srv, cnt in report.within_counts[:6]]
            lines.append("  " + "    ".join(parts))
            if len(report.within_counts) > 6:
                lines.append("  …")
        if not verbose:
            lines.append("  bulla audit -v for details")
        lines.append("")

    lines.append(f"── repair {_rule(34)}")
    lines.append("")
    if report.boundary_fee > 0 and report.disclosure_snippet:
        n = len(report.disclosure_snippet)
        cap = 6
        lines.append(f"Expose {n} field(s) to eliminate boundary risk:")
        for srv, field in report.disclosure_snippet[:cap]:
            lines.append(f"  {srv}: {field}")
        if n > cap:
            lines.append(f"  … and {n - cap} more (bulla audit -v)")
    elif report.boundary_fee > 0:
        lines.append(
            f"{report.boundary_blind_spot_total} boundary blind spot(s) "
            f"across {len(report.boundary_findings)} dimension(s)."
        )
    else:
        lines.append("No boundary seam risk.")
    lines.append("bulla audit --max-fee 0     CI gate")
    lines.append("bulla audit --format json   machine output")

    return "\n".join(lines).rstrip() + "\n"


def format_verbose_blind_spots(blind_spots: tuple[Any, ...]) -> str:
    """Detailed blind spot listing (append after the receipt when ``-v``)."""
    if not blind_spots:
        return ""
    lines: list[str] = []
    lines.append("")
    lines.append(f"── verbose: blind spots ({len(blind_spots)}) {_rule(12)}")
    for i, bs in enumerate(blind_spots, 1):
        locs: list[str] = []
        if bs.from_hidden:
            locs.append(f"{bs.from_field} hidden at {bs.from_tool}")
        if bs.to_hidden:
            locs.append(f"{bs.to_field} hidden at {bs.to_tool}")
        edge = f"{bs.from_tool} → {bs.to_tool}"
        lines.append(f"  [{i}] {display_dimension(bs.dimension)} ({edge})")
        lines.append(f"      {'; '.join(locs) if locs else '(schema-visible mismatch)'}")
        if bs.dimension in _OBSCURE_DIMENSIONS:
            lines.append(f"      {_risk_hint_line(bs)}")
    return "\n".join(lines) + "\n"
