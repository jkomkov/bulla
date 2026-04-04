"""Vocabulary merge: union inline_dimensions from multiple receipts.

Argument order IS precedence order: later receipts win on dimension
name collision, consistent with the pack stack convention (later packs
override earlier ones).

Overlap detection is purely informational -- it does not affect the
merge result. Overlap = non-empty intersection of ``field_patterns``
glob sets between dimensions from different source receipts.
"""

from __future__ import annotations

import copy
import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class OverlapReport:
    """Informational report of field_patterns overlap between receipts."""

    dim_a: str
    receipt_a_idx: int
    dim_b: str
    receipt_b_idx: int
    shared_patterns: tuple[str, ...]


def merge_receipt_vocabularies(
    receipts: list[dict],
) -> tuple[dict | None, list[OverlapReport]]:
    """Union inline_dimensions from multiple receipts.

    Argument order IS precedence order: later receipts win on
    dimension name collision, consistent with the pack stack convention.

    Returns ``(merged_vocab_or_None, overlap_reports)``.
    Deep-copies all input data to prevent mutation.
    """
    vocabs: list[tuple[int, dict]] = []
    for i, r in enumerate(receipts):
        inline = r.get("inline_dimensions")
        if inline and isinstance(inline, dict):
            vocabs.append((i, copy.deepcopy(inline)))

    if not vocabs:
        return None, []

    overlaps = _detect_overlaps(vocabs)

    merged: dict = {}
    for _idx, vocab in vocabs:
        dims = vocab.get("dimensions", {})
        for dim_name, dim_def in dims.items():
            merged[dim_name] = dim_def

    last_idx, last_vocab = vocabs[-1]
    result = copy.deepcopy(last_vocab)
    result["dimensions"] = merged

    return result, overlaps


def _detect_overlaps(
    vocabs: list[tuple[int, dict]],
) -> list[OverlapReport]:
    """Detect field_patterns overlap between dimensions from different receipts."""
    dim_sources: list[tuple[str, int, list[str]]] = []
    for idx, vocab in vocabs:
        for dim_name, dim_def in vocab.get("dimensions", {}).items():
            patterns = dim_def.get("field_patterns", [])
            dim_sources.append((dim_name, idx, patterns))

    overlaps: list[OverlapReport] = []
    for i, (name_a, idx_a, pats_a) in enumerate(dim_sources):
        for j in range(i + 1, len(dim_sources)):
            name_b, idx_b, pats_b = dim_sources[j]
            if idx_a == idx_b:
                continue

            if name_a == name_b:
                overlaps.append(OverlapReport(
                    dim_a=name_a, receipt_a_idx=idx_a,
                    dim_b=name_b, receipt_b_idx=idx_b,
                    shared_patterns=("(same name)",),
                ))
                continue

            shared = _intersect_glob_patterns(pats_a, pats_b)
            if shared:
                overlaps.append(OverlapReport(
                    dim_a=name_a, receipt_a_idx=idx_a,
                    dim_b=name_b, receipt_b_idx=idx_b,
                    shared_patterns=tuple(shared),
                ))

    return overlaps


def _intersect_glob_patterns(
    pats_a: list[str], pats_b: list[str],
) -> list[str]:
    """Return patterns from A that also appear in B (exact match)
    or that would match the same field names (cross-match)."""
    shared: list[str] = []
    for pa in pats_a:
        for pb in pats_b:
            if pa == pb:
                if pa not in shared:
                    shared.append(pa)
            elif fnmatch.fnmatch(pa, pb) or fnmatch.fnmatch(pb, pa):
                canonical = f"{pa} ~ {pb}"
                if canonical not in shared:
                    shared.append(canonical)
    return shared
