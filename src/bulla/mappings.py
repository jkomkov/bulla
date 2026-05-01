"""Cross-pack value translation (Extension E).

A *passive* helper that walks the active pack stack's ``mappings:``
blocks and translates a value from one (pack, dimension) into another.
Pure data, no measurement-layer interaction: Bulla's coboundary uses
dimension *names*, not values, so a mapping table never changes a
coherence fee. Mappings are consumer-side translation tables that
ride along inside regular packs.

Why this lives outside ``bulla.diagnostic`` / ``bulla.coboundary``:

1. The math is value-blind (see WITNESS-CONTRACT.md and the Plan
   agent's pressure-test report). A value-level translation cannot
   change δ₀, cannot change H¹, cannot change the fee. Mappings are
   not Bulla measurement primitives.
2. Embedding mappings inside regular packs as data (rather than
   building a ``MappingPack`` first-class artifact) avoids a one-way
   door (the ``pack_kind`` discriminator) and keeps the loader simple.
3. When/if 5+ packs need to share a mapping (e.g. a currency
   crosswalk used by FIX, GS1, and ISO-20022 packs), promote it to
   its own pack with ``dimensions: {}`` and a ``mappings:`` block —
   no new artifact type.

The Seam Network roadmap claims bond-backed mapping bridges as its
own scope. Bulla's role is to *carry the data*, not to police it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TranslationResult:
    """Outcome of one translation attempt.

    ``found`` is True when at least one mapping row produced a
    translation; the resulting list may contain multiple ``to`` values
    if the source-side dimension has more than one mapping row to the
    target dimension (e.g. ``contextual`` mappings).

    ``equivalence`` is the strongest equivalence class encountered
    among the matching rows: ``exact`` > ``lossy_bidirectional`` >
    ``lossy_forward`` > ``contextual``. None when ``found`` is False.
    """

    found: bool
    values: tuple[str, ...]
    equivalence: str | None
    note: str = ""


_EQUIVALENCE_PRIORITY = {
    "exact": 4,
    "lossy_bidirectional": 3,
    "lossy_forward": 2,
    "contextual": 1,
}


def _strongest(a: str | None, b: str) -> str:
    if a is None:
        return b
    return a if _EQUIVALENCE_PRIORITY.get(a, 0) >= _EQUIVALENCE_PRIORITY.get(b, 0) else b


def translate(
    value: str,
    *,
    from_pack: dict[str, Any],
    to_pack_name: str,
    to_dimension: str,
    direction: str = "forward",
) -> TranslationResult:
    """Translate ``value`` from ``from_pack`` into ``(to_pack_name, to_dimension)``.

    ``from_pack`` is a parsed pack dict (the result of yaml.safe_load
    on a pack file). The pack's ``mappings:`` block is consulted; if a
    row matches ``value`` (against ``from`` for forward direction, or
    against ``to`` for ``direction="reverse"``), the corresponding
    ``to`` (or ``from``) is returned.

    Returns ``TranslationResult(found=False, ...)`` when no mapping is
    available. This is not an error — many compositions will operate
    without mappings and the consumer can decide how to handle it.

    The function is intentionally tiny: it holds no state, performs no
    I/O, and never calls into the measurement layer. A caller wanting
    multi-pack search can call it once per active pack.
    """
    if direction not in {"forward", "reverse"}:
        raise ValueError(
            f"direction must be 'forward' or 'reverse', got {direction!r}"
        )

    mappings = from_pack.get("mappings") if isinstance(from_pack, dict) else None
    if not isinstance(mappings, dict):
        return TranslationResult(found=False, values=(), equivalence=None)
    dim_table = mappings.get(to_pack_name)
    if not isinstance(dim_table, dict):
        return TranslationResult(found=False, values=(), equivalence=None)
    rows = dim_table.get(to_dimension)
    if not isinstance(rows, list):
        return TranslationResult(found=False, values=(), equivalence=None)

    matches: list[str] = []
    strongest_eq: str | None = None
    matched_note = ""
    src_key = "from" if direction == "forward" else "to"
    dst_key = "to" if direction == "forward" else "from"

    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get(src_key) != value:
            continue
        dst = row.get(dst_key)
        if not isinstance(dst, str):
            continue
        matches.append(dst)
        eq = row.get("equivalence", "exact")
        if isinstance(eq, str):
            strongest_eq = _strongest(strongest_eq, eq)
        if not matched_note and isinstance(row.get("note"), str):
            matched_note = row["note"]

    if not matches:
        return TranslationResult(found=False, values=(), equivalence=None)

    return TranslationResult(
        found=True,
        values=tuple(matches),
        equivalence=strongest_eq,
        note=matched_note,
    )


def list_mappings(parsed_pack: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Summarize a pack's ``mappings:`` block as ``(target_pack,
    target_dimension, row_count)`` triples.

    Useful for ``bulla pack status`` to report mapping coverage at
    a glance without dumping every row.
    """
    out: list[tuple[str, str, int]] = []
    if not isinstance(parsed_pack, dict):
        return out
    mappings = parsed_pack.get("mappings")
    if not isinstance(mappings, dict):
        return out
    for target_pack, dim_table in mappings.items():
        if not isinstance(target_pack, str) or not isinstance(dim_table, dict):
            continue
        for target_dim, rows in dim_table.items():
            if not isinstance(target_dim, str) or not isinstance(rows, list):
                continue
            out.append((target_pack, target_dim, len(rows)))
    return out
