"""Narrative formatter for ``bulla scan``.

Turns a ``Diagnostic`` plus the list of server names plus optional
pairwise-fee breakdown into a 10-second-readable prose block.

The formatter is a pure function. It runs no I/O, does no
classification, makes no network calls. The pipeline (the scan
command in ``cli.py``) does the work; this module just renders.

Output sections:
  1. Header — what was scanned, server count.
  2. Headline — fee=0 success, or fee + blind-spot list.
  3. Pairwise comparison block (only when every pair has fee=0
     and the global fee > 0). This is the moat case: pairwise
     checking literally cannot find what bulla finds.
  4. JSON pointer footer.

The dimension explanations come from ``bulla.explanations``. Every
dimension that ``Diagnostic.blind_spots[i].dimension`` can carry has
an entry there, locked by a CI test.
"""

from __future__ import annotations

from typing import Any, Sequence

from bulla.explanations import explain
from bulla.model import BlindSpot, Diagnostic


_DEFAULT_BLIND_SPOT_DISPLAY_LIMIT = 8
"""Default cap on the number of blind spots rendered in narrative
output. Real compositions can produce 50–100+ blind spots; dumping
all of them swamps the prose. The footer points at ``--json`` for
the full list. Override via ``max_blind_spots`` argument."""


def format_scan_narrative(
    diagnostic: Diagnostic,
    server_names: Sequence[str],
    *,
    config_source: str | None = None,
    pairwise_fees: dict[tuple[str, str], int] | None = None,
    max_blind_spots: int = _DEFAULT_BLIND_SPOT_DISPLAY_LIMIT,
    cross_server_only: bool = True,
) -> str:
    """Render a scan diagnostic as a prose block.

    Args:
        diagnostic: The composition diagnostic from
            ``BullaGuard.diagnose()`` or ``compose_multi(...).diagnostic``.
        server_names: Ordered list of server names that were scanned.
        config_source: Path or label describing where the servers
            came from (e.g. ``"~/.cursor/mcp.json"``). When None, the
            header omits the location.
        pairwise_fees: Optional mapping of
            ``(server_a, server_b)`` (sorted) to the pairwise fee.
            When supplied AND every pair has fee=0 AND the global
            fee > 0, the moat-case pairwise section is rendered.

    Returns:
        A multi-line string ending with a newline.
    """
    out: list[str] = []

    # ── Header ──────────────────────────────────────────────────────
    if config_source:
        out.append(f"Scanning {config_source}...")
    n = len(server_names)
    if n == 0:
        out.append("No servers in this composition.")
        out.append("")
        return "\n".join(out) + "\n"
    server_word = "server" if n == 1 else "servers"
    out.append(f"Found {n} {server_word}: {', '.join(server_names)}")
    out.append("")

    # ── Headline ────────────────────────────────────────────────────
    fee = diagnostic.coherence_fee
    if fee == 0 and not diagnostic.blind_spots:
        out.append("Composition is clean. Fee = 0, no blind spots.")
        out.append("")
        out.append("Run `bulla scan --json` for the machine-readable receipt.")
        out.append("")
        return "\n".join(out) + "\n"

    # ── Blind spots ─────────────────────────────────────────────────
    blind_spots = list(diagnostic.blind_spots)
    if cross_server_only and len(server_names) >= 2:
        # Filter to cross-server seams: blind spots whose endpoints
        # span two distinct server prefixes (the canonical
        # ``<server>__<tool>`` naming used by ``compose_multi``).
        # Within-server blind spots are real but not the awareness-
        # gap story; surface them only when there are no cross-
        # server ones to report.
        cross = [
            bs for bs in blind_spots
            if _is_cross_server_blind_spot(bs)
        ]
        if cross:
            blind_spots = cross

    # Distinct-dimensions count surfaces the actual story: a
    # composition may have 22 blind-spot rows but only 1 underlying
    # convention seam (every filesystem-tool × github-tool pair
    # tripping on the same path_convention). The fee number is the
    # rank of H¹ — mathematically correct but operationally noisy
    # for an awareness-gap headline. Surface both.
    distinct_dims = sorted({
        _strip_match_suffix(bs.dimension) for bs in blind_spots
    })

    if len(distinct_dims) <= 1:
        n = len(distinct_dims) or 1
        suffix = f"across {n} convention dimension"
    else:
        suffix = f"across {len(distinct_dims)} convention dimensions"
    out.append(f"Coherence fee: {fee} ({suffix})")
    out.append("")
    n_total = len(blind_spots)
    n_displayed = min(n_total, max_blind_spots)
    for i, bs in enumerate(blind_spots[:n_displayed], 1):
        out.extend(_render_blind_spot(i, bs))
        out.append("")
    if n_total > n_displayed:
        remainder = n_total - n_displayed
        out.append(
            f"  ({remainder} more blind spot{'' if remainder == 1 else 's'} "
            f"omitted; run `bulla scan --json` for the full list.)"
        )
        out.append("")

    # ── Pairwise comparison (the moat case) ─────────────────────────
    if pairwise_fees is not None and _is_moat_case(fee, pairwise_fees):
        out.extend(_render_pairwise_block(pairwise_fees, fee))
        out.append("")

    # ── Footer ──────────────────────────────────────────────────────
    out.append("Run `bulla scan --json` for the machine-readable receipt.")
    out.append("")
    return "\n".join(out) + "\n"


def _render_blind_spot(index: int, bs: BlindSpot) -> list[str]:
    """One blind-spot block, indented two spaces."""
    explanation = explain(bs.dimension)
    lines = [
        f"  {index}. {explanation.human_label}",
    ]
    # Identify the two endpoints. Some BlindSpot rows carry empty
    # tool names (ancient receipts) — fall back to the edge label
    # in that case.
    a_label = (bs.from_tool or "").strip() or "tool A"
    b_label = (bs.to_tool or "").strip() or "tool B"
    a_field = (bs.from_field or "").strip() or "<field>"
    b_field = (bs.to_field or "").strip() or "<field>"
    if a_label != b_label:
        lines.append(
            f"     {a_label}.{a_field} ↔ {b_label}.{b_field}"
        )
    else:
        lines.append(f"     {a_label}: {a_field}")
    lines.append(f"     {explanation.explanation}")
    lines.append(f"     {explanation.failure_mode}")
    return lines


def _strip_match_suffix(dimension: str) -> str:
    """Strip the ``_match`` suffix that ``compose_multi`` appends to
    edge-inferred dimension names. Lets the distinct-dimensions count
    treat ``path_convention`` and ``path_convention_match`` as one
    underlying dimension."""
    if dimension.endswith("_match"):
        return dimension[: -len("_match")]
    return dimension


def _is_cross_server_blind_spot(bs: BlindSpot) -> bool:
    """True iff this blind spot's endpoints span two distinct server
    prefixes (using the ``<server>__<tool>`` naming convention).

    Within-server blind spots are real (a server can have internal
    convention drift between its own tools) but they aren't the
    awareness-gap story for cross-server compositions. The narrative
    formatter filters to cross-server seams by default.
    """
    a = (bs.from_tool or "").split("__", 1)[0]
    b = (bs.to_tool or "").split("__", 1)[0]
    if not a or not b:
        return False
    return a != b


def _is_moat_case(global_fee: int, pairwise_fees: dict[tuple[str, str], int]) -> bool:
    """True iff every pair has fee=0 AND the global fee > 0.

    This is the case where pairwise checking literally cannot find
    what bulla finds. Suppressing the section in any other case
    keeps the output focused.
    """
    if global_fee <= 0:
        return False
    if not pairwise_fees:
        return False
    return max(pairwise_fees.values()) == 0


def _render_pairwise_block(
    pairwise_fees: dict[tuple[str, str], int], global_fee: int
) -> list[str]:
    """Render the moat-case pairwise-vs-global comparison."""
    out = ["Pairwise checks:"]
    width = max(
        len(f"{a} × {b}") for (a, b) in pairwise_fees.keys()
    )
    for (a, b) in sorted(pairwise_fees.keys()):
        pair = f"{a} × {b}".ljust(width)
        out.append(f"  {pair}    0 blind spots")
    out.append("")
    out.append(f"Global composition: fee = {global_fee}")
    out.append("")
    out.append("  Every pair looks clean. The full composition carries an")
    out.append("  undisclosed convention no pairwise check can see (a structural")
    out.append("  obstruction, not a predicted failure — see FALSIFICATIONS.md).")
    return out


def compute_pairwise_fees(
    server_tools: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str], int]:
    """Compute the coherence fee for every pair of servers.

    Returns a dict keyed by ``(name_a, name_b)`` with ``name_a < name_b``
    so callers iterate deterministically. Each pair triggers one
    ``compose_multi({pair})`` call, which is fast for typical 3–5
    server scans.

    For ``n_servers >= 8`` the caller should skip pairwise computation
    entirely; this function would still produce correct output, but
    n*(n-1)/2 compose_multi calls dominates wall-clock time. The CLI
    enforces the cutoff.
    """
    from bulla.sdk import compose_multi

    names = sorted(server_tools.keys())
    fees: dict[tuple[str, str], int] = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            pair_tools = {a: server_tools[a], b: server_tools[b]}
            try:
                result = compose_multi(pair_tools)
                fees[(a, b)] = result.diagnostic.coherence_fee
            except Exception:
                # If the pair fails to compose for any reason, record
                # a sentinel value (-1) and let the formatter skip it.
                fees[(a, b)] = -1
    return fees


__all__ = [
    "compute_pairwise_fees",
    "format_scan_narrative",
]
