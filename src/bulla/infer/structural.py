"""Structural schema comparison: pack-free composition-time type checking.

Detects visible-but-incompatible fields across MCP tools by comparing
schema metadata (type, format, enum, range, pattern).  Produces a
StructuralDiagnostic parallel to the cohomological Diagnostic.

The coboundary measures the cost of opacity (hidden conventions).
The structural scan measures the cost of incompatibility (visible
fields with disagreeing schemas).  Together: total verification bill.

Signal hierarchy: schema similarity is the primary trigger; name match
is a confidence booster.  Two fields with identical type+format+enum
across tools are potentially coupled regardless of name.  Two fields
with the same name but completely different types are homonyms.

No packs, no LLM, deterministic.
"""

from __future__ import annotations

import math

from bulla.infer.classifier import FieldInfo
from bulla.model import (
    SchemaContradiction,
    SchemaOverlap,
    StructuralDiagnostic,
)

# ── Similarity weights ───────────────────────────────────────────────

_W_TYPE = 0.45
_W_FORMAT = 0.20
_W_ENUM = 0.15
_W_RANGE = 0.10
_W_PATTERN = 0.10

SIMILARITY_THRESHOLD = 0.60


# ── Helpers ──────────────────────────────────────────────────────────


def _leaf_name(field_name: str) -> str:
    """Extract leaf name from a dot-path field name."""
    return field_name.rsplit(".", 1)[-1]


def _type_score(a: FieldInfo, b: FieldInfo) -> float:
    if a.schema_type is None and b.schema_type is None:
        return 0.5
    if a.schema_type is None or b.schema_type is None:
        return 0.3
    return 1.0 if a.schema_type == b.schema_type else 0.0


def _format_score(a: FieldInfo, b: FieldInfo) -> float:
    if a.format is None and b.format is None:
        return 0.0  # absent metadata is not evidence of similarity
    if a.format is None or b.format is None:
        return 0.0
    return 1.0 if a.format == b.format else 0.0


def _enum_jaccard(a: FieldInfo, b: FieldInfo) -> float:
    if a.enum is None and b.enum is None:
        return 0.0  # absent metadata is not evidence of similarity
    if a.enum is None or b.enum is None:
        return 0.0
    set_a, set_b = set(a.enum), set(b.enum)
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _range_overlap(a: FieldInfo, b: FieldInfo) -> float:
    a_has = a.minimum is not None or a.maximum is not None
    b_has = b.minimum is not None or b.maximum is not None
    if not a_has and not b_has:
        return 0.0  # absent metadata is not evidence of similarity
    if not a_has or not b_has:
        return 0.0
    a_lo = a.minimum if a.minimum is not None else -math.inf
    a_hi = a.maximum if a.maximum is not None else math.inf
    b_lo = b.minimum if b.minimum is not None else -math.inf
    b_hi = b.maximum if b.maximum is not None else math.inf
    if a_lo > b_hi or b_lo > a_hi:
        return 0.0
    overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
    span = max(a_hi, b_hi) - min(a_lo, b_lo)
    if span == 0 or math.isinf(span):
        return 1.0 if a_lo == b_lo and a_hi == b_hi else 0.5
    return max(0.0, min(1.0, overlap / span))


def _pattern_score(a: FieldInfo, b: FieldInfo) -> float:
    if a.pattern is None and b.pattern is None:
        return 0.0  # absent metadata is not evidence of similarity
    if a.pattern is None or b.pattern is None:
        return 0.0
    return 1.0 if a.pattern == b.pattern else 0.0


# ── Core similarity ──────────────────────────────────────────────────


def schema_similarity(a: FieldInfo, b: FieldInfo) -> float:
    """Compute weighted schema similarity between two fields.

    Returns a score in [0, 1].  Higher means more structurally similar.
    The score measures "are these fields likely about the same concept?"
    independent of field names.
    """
    return (
        _W_TYPE * _type_score(a, b)
        + _W_FORMAT * _format_score(a, b)
        + _W_ENUM * _enum_jaccard(a, b)
        + _W_RANGE * _range_overlap(a, b)
        + _W_PATTERN * _pattern_score(a, b)
    )


def compare_fields(
    field_a: FieldInfo,
    field_b: FieldInfo,
    *,
    tool_a: str = "source",
    tool_b: str = "target",
) -> tuple[SchemaOverlap, SchemaContradiction | None]:
    """Compare two fields as a single structural flow relation.

    This is the public pairwise entry point behind the composition-wide
    scan. It exposes the same overlap/contradiction logic used by
    ``scan_composition()`` so other layers (for example a session proxy)
    can reason about one concrete source -> target field flow without
    reimplementing the structural classifier.
    """
    similarity = schema_similarity(field_a, field_b)
    name_match = (
        field_a.name == field_b.name
        or _leaf_name(field_a.name) == _leaf_name(field_b.name)
    )
    return _classify_pair(
        field_a,
        field_b,
        tool_a,
        tool_b,
        similarity,
        name_match=name_match,
    )


# ── Contradiction detection ──────────────────────────────────────────


def _find_mismatches(
    a: FieldInfo, b: FieldInfo,
) -> list[tuple[str, float, str]]:
    """Find specific schema mismatches between two coupled fields.

    Returns a list of (mismatch_type, severity, details) tuples.
    Only called for field pairs already determined to be coupled
    (above similarity threshold or name-matched).
    """
    mismatches: list[tuple[str, float, str]] = []

    if (
        a.schema_type is not None
        and b.schema_type is not None
        and a.schema_type != b.schema_type
    ):
        mismatches.append((
            "type",
            1.0,
            f"type {a.schema_type!r} vs {b.schema_type!r}",
        ))

    if (
        a.format is not None
        and b.format is not None
        and a.format != b.format
    ):
        mismatches.append((
            "format",
            0.7,
            f"format {a.format!r} vs {b.format!r}",
        ))
    elif (a.format is None) != (b.format is None):
        present = a.format if a.format is not None else b.format
        mismatches.append((
            "format",
            0.4,
            f"format {present!r} vs unspecified",
        ))

    if a.enum is not None and b.enum is not None:
        set_a, set_b = set(a.enum), set(b.enum)
        if set_a != set_b:
            only_a = set_a - set_b
            only_b = set_b - set_a
            parts: list[str] = []
            if only_a:
                parts.append(f"only in A: {sorted(only_a)}")
            if only_b:
                parts.append(f"only in B: {sorted(only_b)}")
            jaccard = _enum_jaccard(a, b)
            severity = max(0.3, 1.0 - jaccard)
            mismatches.append(("enum", severity, "; ".join(parts)))
    elif (a.enum is None) != (b.enum is None):
        constrained = a.enum if a.enum is not None else b.enum
        mismatches.append((
            "enum",
            0.5,
            f"one constrains to {len(constrained)} values, other is free-form",
        ))

    a_has_range = a.minimum is not None or a.maximum is not None
    b_has_range = b.minimum is not None or b.maximum is not None
    if a_has_range and b_has_range:
        a_lo = a.minimum if a.minimum is not None else -math.inf
        a_hi = a.maximum if a.maximum is not None else math.inf
        b_lo = b.minimum if b.minimum is not None else -math.inf
        b_hi = b.maximum if b.maximum is not None else math.inf
        if a_lo > b_hi or b_lo > a_hi:
            mismatches.append((
                "range",
                0.6,
                f"disjoint ranges [{a.minimum}, {a.maximum}] "
                f"vs [{b.minimum}, {b.maximum}]",
            ))
        elif (a_lo, a_hi) != (b_lo, b_hi):
            mismatches.append((
                "range",
                0.3,
                f"ranges [{a.minimum}, {a.maximum}] "
                f"vs [{b.minimum}, {b.maximum}]",
            ))

    if (
        a.pattern is not None
        and b.pattern is not None
        and a.pattern != b.pattern
    ):
        mismatches.append((
            "pattern",
            0.4,
            f"pattern {a.pattern!r} vs {b.pattern!r}",
        ))

    return mismatches


# ── Classification ───────────────────────────────────────────────────


def _classify_pair(
    field_a: FieldInfo,
    field_b: FieldInfo,
    tool_a: str,
    tool_b: str,
    similarity: float,
    name_match: bool,
) -> tuple[SchemaOverlap, SchemaContradiction | None]:
    """Classify a surviving field pair into overlap category + optional contradiction.

    Name match is a strong coupling signal that bypasses the similarity
    threshold.  When names match, the question is just "are the schemas
    compatible?" not "are these fields about the same thing?"

    For non-name-matched pairs (synonym candidates), the similarity
    threshold determines coupling.
    """
    mismatches = _find_mismatches(field_a, field_b)
    worst_mismatch = max(mismatches, key=lambda m: m[1]) if mismatches else None
    high_sim = similarity >= SIMILARITY_THRESHOLD

    if name_match:
        types_agree = (
            field_a.schema_type is None
            or field_b.schema_type is None
            or field_a.schema_type == field_b.schema_type
        )
        if types_agree and mismatches:
            category = "contradiction"
        elif types_agree:
            category = "agreement"
        else:
            category = "homonym"
    elif high_sim:
        category = "contradiction" if mismatches else "synonym"
    else:
        category = "agreement"

    mismatch_details = "; ".join(m[2] for m in mismatches) if mismatches else "compatible"
    detail_parts: list[str] = []
    if not name_match:
        detail_parts.append(f"names: {field_a.name!r} ~ {field_b.name!r}")
    detail_parts.append(f"similarity={similarity:.2f}")
    if mismatch_details != "compatible":
        detail_parts.append(mismatch_details)
    details = "; ".join(detail_parts)

    overlap = SchemaOverlap(
        field_a=field_a.name,
        field_b=field_b.name,
        tool_a=tool_a,
        tool_b=tool_b,
        similarity=round(similarity, 3),
        name_match=name_match,
        category=category,
        details=details,
    )

    contradiction: SchemaContradiction | None = None
    if category == "contradiction" and worst_mismatch is not None:
        contradiction = SchemaContradiction(
            field_a=field_a.name,
            field_b=field_b.name,
            tool_a=tool_a,
            tool_b=tool_b,
            mismatch_type=worst_mismatch[0],
            severity=round(worst_mismatch[1], 2),
            details=details,
        )

    return overlap, contradiction


# ── Composition-level scan ───────────────────────────────────────────


def scan_composition(
    tools_fields: dict[str, list[FieldInfo]],
) -> StructuralDiagnostic:
    """Scan all cross-tool field pairs for structural overlaps.

    ``tools_fields`` maps tool name -> list of FieldInfo objects
    (as returned by ``extract_field_infos()``).

    Returns both agreements and contradictions.  Contradictions are
    the diagnostic output; agreements are the micro-pack input.

    This function is pure: no packs, no LLM, no side effects.
    """
    overlaps: list[SchemaOverlap] = []
    contradictions: list[SchemaContradiction] = []
    name_matched_fields: set[tuple[str, str, str, str]] = set()

    tool_names = sorted(tools_fields.keys())

    for i, t1 in enumerate(tool_names):
        fields_1 = tools_fields[t1]
        for t2 in tool_names[i + 1:]:
            fields_2 = tools_fields[t2]

            leaf_index_2: dict[str, list[FieldInfo]] = {}
            for f2 in fields_2:
                leaf = _leaf_name(f2.name)
                leaf_index_2.setdefault(leaf, []).append(f2)

            checked: set[tuple[str, str]] = set()

            for f1 in fields_1:
                leaf_1 = _leaf_name(f1.name)

                name_candidates = leaf_index_2.get(leaf_1, [])
                for f2 in name_candidates:
                    pair_key = (f1.name, f2.name)
                    if pair_key in checked:
                        continue
                    checked.add(pair_key)

                    sim = schema_similarity(f1, f2)
                    is_name = f1.name == f2.name or leaf_1 == _leaf_name(f2.name)
                    name_matched_fields.add((t1, f1.name, t2, f2.name))

                    overlap, contradiction = _classify_pair(
                        f1, f2, t1, t2, sim, name_match=is_name,
                    )
                    overlaps.append(overlap)
                    if contradiction is not None:
                        contradictions.append(contradiction)

                for f2 in fields_2:
                    pair_key = (f1.name, f2.name)
                    if pair_key in checked:
                        continue
                    checked.add(pair_key)

                    sim = schema_similarity(f1, f2)
                    if sim < SIMILARITY_THRESHOLD:
                        continue

                    is_name = f1.name == f2.name
                    overlap, contradiction = _classify_pair(
                        f1, f2, t1, t2, sim, name_match=is_name,
                    )
                    overlaps.append(overlap)
                    if contradiction is not None:
                        contradictions.append(contradiction)

    score = round(sum(c.severity for c in contradictions))

    return StructuralDiagnostic(
        overlaps=tuple(overlaps),
        contradictions=tuple(contradictions),
        n_overlapping_fields=len(name_matched_fields),
        n_contradicted=len(contradictions),
        contradiction_score=score,
    )
