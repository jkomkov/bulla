"""Multi-signal convention dimension classifier.

Classifies tool fields into semantic convention dimensions using three
independent signal sources:
  1. Field name pattern matching (regex on property names)
  2. Description keyword matching (phrases in tool/field descriptions)
  3. JSON Schema structural signals (format, type+range, enum, pattern)

Confidence tiers:
  - "declared": two or more independent signals agree
  - "inferred": one strong signal (name match or strong schema signal)
  - "unknown":  weak or ambiguous signal only

No LLM, no API key, deterministic.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import logging
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import yaml

from bulla.model import PackRef

logger = logging.getLogger(__name__)


# ── Data types ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FieldInfo:
    """Rich field descriptor extracted from a JSON Schema property."""
    name: str
    schema_type: str | None = None
    format: str | None = None
    enum: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class InferredDimension:
    """A field classified into a convention dimension."""
    field_name: str
    dimension: str
    confidence: str  # "declared", "inferred", "unknown"
    sources: tuple[str, ...] = ()


# ── Pack loading ──────────────────────────────────────────────────────

_taxonomy_cache: dict[str, Any] | None = None
_active_pack_refs: tuple[PackRef, ...] = ()
_extra_pack_paths: tuple[str, ...] = ()


def _hash_pack(parsed: dict[str, Any]) -> str:
    """Deterministic content hash of a parsed pack (not raw YAML)."""
    return hashlib.sha256(
        json.dumps(parsed, sort_keys=True).encode()
    ).hexdigest()


def _load_single_pack(path_or_resource: Path | str) -> dict[str, Any]:
    """Load a single pack YAML from a file path or importlib resource."""
    if isinstance(path_or_resource, Path):
        return yaml.safe_load(path_or_resource.read_text(encoding="utf-8"))
    return yaml.safe_load(path_or_resource)


def _load_base_pack() -> tuple[dict[str, Any], PackRef]:
    """Load the built-in base pack from package resources."""
    pkg = importlib.resources.files("bulla")

    packs_dir = pkg / "packs"
    base_path = packs_dir / "base.yaml"
    try:
        text = base_path.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError):
        taxonomy_path = pkg / "taxonomy.yaml"
        text = taxonomy_path.read_text(encoding="utf-8")

    parsed = yaml.safe_load(text)
    ref = PackRef(
        name=parsed.get("pack_name", "base"),
        version=parsed.get("pack_version", "0.1.0"),
        hash=_hash_pack(parsed),
    )
    return parsed, ref


def _load_community_pack() -> tuple[dict[str, Any], PackRef] | None:
    """Load the bundled community pack if present. Returns None when absent."""
    pkg = importlib.resources.files("bulla")
    community_path = pkg / "packs" / "community.yaml"
    try:
        text = community_path.read_text(encoding="utf-8")
    except (FileNotFoundError, TypeError):
        return None
    parsed = yaml.safe_load(text)
    if not parsed or "dimensions" not in parsed:
        return None
    ref = PackRef(
        name=parsed.get("pack_name", "community"),
        version=parsed.get("pack_version", "0.0.0"),
        hash=_hash_pack(parsed),
    )
    return parsed, ref


def _merge_packs(
    base: dict[str, Any],
    overlay: dict[str, Any],
    base_name: str,
    overlay_name: str,
) -> dict[str, Any]:
    """Merge overlay dimensions into base. Later pack wins on collision."""
    merged_dims = dict(base.get("dimensions", {}))
    for dim_name, dim_def in overlay.get("dimensions", {}).items():
        if dim_name in merged_dims:
            logger.warning(
                "Pack '%s' overrides dimension '%s' from pack '%s'",
                overlay_name,
                dim_name,
                base_name,
            )
        merged_dims[dim_name] = dim_def
    result = dict(base)
    result["dimensions"] = merged_dims
    return result


def load_pack_stack(
    extra_paths: list[Path] | None = None,
) -> tuple[dict[str, Any], tuple[PackRef, ...]]:
    """Load and merge the full pack stack, returning merged taxonomy and refs.

    Precedence order: base (lowest) -> community -> extra packs in order (highest last).
    """
    merged, base_ref = _load_base_pack()
    refs: list[PackRef] = [base_ref]
    prev_name = base_ref.name

    community = _load_community_pack()
    if community is not None:
        parsed, ref = community
        merged = _merge_packs(merged, parsed, prev_name, ref.name)
        refs.append(ref)
        prev_name = ref.name

    for pack_path in extra_paths or []:
        parsed = _load_single_pack(pack_path)
        pack_name = parsed.get("pack_name", pack_path.stem)
        ref = PackRef(
            name=pack_name,
            version=parsed.get("pack_version", "0.0.0"),
            hash=_hash_pack(parsed),
        )
        merged = _merge_packs(merged, parsed, prev_name, pack_name)
        refs.append(ref)
        prev_name = pack_name

    return merged, tuple(refs)


def configure_packs(extra_paths: list[Path] | None = None) -> tuple[PackRef, ...]:
    """Configure the active pack stack. Resets all caches."""
    global _taxonomy_cache, _active_pack_refs, _extra_pack_paths
    _reset_taxonomy_cache()
    paths_key = tuple(str(p) for p in (extra_paths or []))
    merged, refs = load_pack_stack(extra_paths)
    _taxonomy_cache = merged
    _active_pack_refs = refs
    _extra_pack_paths = paths_key
    return refs


def get_active_pack_refs() -> tuple[PackRef, ...]:
    """Return the currently active pack refs (after configure_packs or lazy load)."""
    if not _active_pack_refs:
        configure_packs()
    return _active_pack_refs


def _load_taxonomy() -> dict[str, Any]:
    """Load the merged taxonomy from the active pack stack."""
    global _taxonomy_cache, _active_pack_refs
    if _taxonomy_cache is not None:
        return _taxonomy_cache
    merged, refs = load_pack_stack()
    _taxonomy_cache = merged
    _active_pack_refs = refs
    return _taxonomy_cache


def _reset_taxonomy_cache() -> None:
    """Reset all caches (for testing with custom taxonomies)."""
    global _taxonomy_cache, _compiled_patterns, _ENUM_KNOWN_VALUES, _DOMAIN_MAP
    global _active_pack_refs, _extra_pack_paths, _DESCRIPTION_KEYWORDS_CACHE
    global _refines_map
    _taxonomy_cache = None
    _compiled_patterns = None
    _ENUM_KNOWN_VALUES = None
    _DOMAIN_MAP = None
    _DESCRIPTION_KEYWORDS_CACHE = None
    _refines_map = None
    _active_pack_refs = ()
    _extra_pack_paths = ()


_DOMAIN_MAP: dict[str, list[str]] | None = None


def _get_domain_map() -> dict[str, list[str]]:
    """Return {dimension_name: [domain, ...]} from taxonomy."""
    global _DOMAIN_MAP
    if _DOMAIN_MAP is not None:
        return _DOMAIN_MAP
    taxonomy = _load_taxonomy()
    _DOMAIN_MAP = {}
    for dim_name, dim_def in taxonomy.get("dimensions", {}).items():
        _DOMAIN_MAP[dim_name] = dim_def.get("domains", [])
    return _DOMAIN_MAP


# ── Signal 1: Field name patterns ──────────────────────────────────────

# Hand-tuned patterns that augment the taxonomy's field_patterns with
# richer regex (multi-word tokens, boundary handling).  The taxonomy
# compilation step merges these with taxonomy-derived patterns.
_CORE_NAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("date_format", re.compile(
        r"(^|_)(time|date|timestamp|datetime|created_at|updated_at|"
        r"expires?_at|deadline|scheduled|start_time|end_time|"
        r"birth_?date|due_date|since|after|before|until)($|_)", re.IGNORECASE
    )),
    ("rate_scale", re.compile(
        r"(^|_)(rate|percent|percentage|ratio|probability|"
        r"confidence|likelihood|fraction|proportion|"
        r"interest_rate|tax_rate|growth_rate)($|_)", re.IGNORECASE
    )),
    ("amount_unit", re.compile(
        r"(^|_)(amount|price|cost|fee|total|subtotal|balance|"
        r"salary|revenue|profit|budget|payment|charge|"
        r"discount|tax|tip|wage)($|_|s$)", re.IGNORECASE
    )),
    ("score_range", re.compile(
        r"(^|_)(score|rating|priority|rank|grade|level|"
        r"quality|severity|weight|importance)($|_)", re.IGNORECASE
    )),
    ("id_offset", re.compile(
        r"(^|_)(offset|position|page|page_number)($|_)", re.IGNORECASE
    )),
    ("precision", re.compile(
        r"(^|_)(precision|decimals|decimal_places|dp|"
        r"significant_digits|rounding|accuracy)($|_)", re.IGNORECASE
    )),
    ("encoding", re.compile(
        r"(^|_)(encoding|charset|character_set|locale|"
        r"text_encoding|content_encoding)($|_)", re.IGNORECASE
    )),
    ("timezone", re.compile(
        r"(^|_)(tz|timezone|time_?zone|utc_offset)($|_)", re.IGNORECASE
    )),
    ("null_handling", re.compile(
        r"(^|_)(null_handling|null_strategy|missing_value|"
        r"na_value|nan_handling|default_missing)($|_)", re.IGNORECASE
    )),
    ("line_ending", re.compile(
        r"(^|_)(line_ending|newline|eol|crlf|lf_mode)($|_)", re.IGNORECASE
    )),
    ("path_convention", re.compile(
        r"(^)(path|filepath|file_path|dir_path|directory|"
        r"dirname|folder)($|_)", re.IGNORECASE
    )),
]

# Backward-compat alias
DIMENSION_PATTERNS = _CORE_NAME_PATTERNS

# Negative patterns: field names that match a positive pattern but should
# be excluded from the dimension.  Checked in classify_field_by_name.
_NEGATIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    "id_offset": re.compile(
        r"(^|_)(per_page|page_size|page_count|limit|count|"
        r"total|max_results|num_results|batch_size)($|_)", re.IGNORECASE
    ),
}


def _compile_taxonomy_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Compile field_patterns from taxonomy.yaml into regex, merged with core patterns."""
    taxonomy = _load_taxonomy()
    dims = taxonomy.get("dimensions", {})

    covered_dims = {name for name, _ in _CORE_NAME_PATTERNS}
    extra: list[tuple[str, re.Pattern[str]]] = []

    for dim_name, dim_def in dims.items():
        if dim_name in covered_dims:
            continue
        patterns = dim_def.get("field_patterns", [])
        if not patterns:
            continue
        tokens: list[str] = []
        for pat in patterns:
            clean = pat.strip("*").strip("_").replace("*", "")
            if clean:
                tokens.append(re.escape(clean))
        if tokens:
            regex = re.compile(
                r"(^|_)(" + "|".join(tokens) + r")($|_)", re.IGNORECASE
            )
            extra.append((dim_name, regex))

    return _CORE_NAME_PATTERNS + extra


_compiled_patterns: list[tuple[str, re.Pattern[str]]] | None = None
_refines_map: dict[str, str] | None = None


def _get_name_patterns() -> list[tuple[str, re.Pattern[str]]]:
    global _compiled_patterns
    if _compiled_patterns is None:
        _compiled_patterns = _compile_taxonomy_patterns()
    return _compiled_patterns


def _get_refines_map() -> dict[str, str]:
    """Build child -> parent mapping from ``refines`` fields in taxonomy."""
    global _refines_map
    if _refines_map is not None:
        return _refines_map
    taxonomy = _load_taxonomy()
    _refines_map = {}
    for dim_name, dim_def in taxonomy.get("dimensions", {}).items():
        parent = dim_def.get("refines")
        if parent and isinstance(parent, str):
            _refines_map[dim_name] = parent
    return _refines_map


def _deduplicate_by_specificity(
    matches: list[InferredDimension],
) -> list[InferredDimension]:
    """When a field matches both a child and its refines parent, keep only the child."""
    if len(matches) <= 1:
        return matches
    refines = _get_refines_map()
    matched_dims = {m.dimension for m in matches}
    to_remove: set[str] = set()
    for m in matches:
        parent = refines.get(m.dimension)
        if parent and parent in matched_dims:
            to_remove.add(parent)
    if not to_remove:
        return matches
    return [m for m in matches if m.dimension not in to_remove]


def classify_field_by_name(
    name: str,
    *,
    schema_type: str | None = None,
) -> InferredDimension | None:
    """Classify a field by its name against dimension patterns.

    Collects all matching dimensions, then applies most-specific-wins
    deduplication via the ``refines`` hierarchy: if a child dimension
    and its parent both match, only the child is returned.

    Args:
        schema_type: When provided, enables type-aware exclusions (e.g.
            string-typed ``*_id`` fields are excluded from ``id_offset``).
    """
    leaf = name.rsplit(".", 1)[-1] if "." in name else name
    all_matches: list[InferredDimension] = []
    for dim_name, pattern in _get_name_patterns():
        if pattern.search(leaf):
            neg = _NEGATIVE_PATTERNS.get(dim_name)
            if neg and neg.search(leaf):
                continue
            if (
                dim_name == "id_offset"
                and schema_type == "string"
                and re.search(r"(^|_)id($|_)", leaf, re.IGNORECASE)
            ):
                continue
            all_matches.append(InferredDimension(
                field_name=name,
                dimension=dim_name,
                confidence="inferred",
                sources=("name",),
            ))
    if not all_matches:
        return None
    deduped = _deduplicate_by_specificity(all_matches)
    return deduped[0]


# ── Signal 2: Description keyword matching ─────────────────────────────

_DESCRIPTION_KEYWORDS_CACHE: dict[str, list[str]] | None = None


def _get_description_keywords() -> dict[str, list[str]]:
    """Load description_keywords from the merged pack taxonomy.

    The pack YAML is the single source of truth for keyword lists.
    Custom packs automatically enrich description matching.
    """
    global _DESCRIPTION_KEYWORDS_CACHE
    if _DESCRIPTION_KEYWORDS_CACHE is not None:
        return _DESCRIPTION_KEYWORDS_CACHE
    taxonomy = _load_taxonomy()
    _DESCRIPTION_KEYWORDS_CACHE = {
        dim_name: dim_def.get("description_keywords", [])
        for dim_name, dim_def in taxonomy.get("dimensions", {}).items()
        if dim_def.get("description_keywords")
    }
    return _DESCRIPTION_KEYWORDS_CACHE


def classify_description(text: str) -> list[InferredDimension]:
    """Extract dimension signals from a tool or field description."""
    if not text:
        return []
    lower = text.lower()
    results: list[InferredDimension] = []
    seen: set[str] = set()
    for dim_name, keywords in _get_description_keywords().items():
        for kw in keywords:
            if kw in lower and dim_name not in seen:
                seen.add(dim_name)
                results.append(InferredDimension(
                    field_name="_description",
                    dimension=dim_name,
                    confidence="inferred",
                    sources=("description",),
                ))
                break
    return results


def _classify_field_descriptions(
    field_infos: list[FieldInfo],
) -> list[InferredDimension]:
    """Scan per-field descriptions against pack keyword lists.

    Returns one InferredDimension per (field, dimension) match.
    Source type is "field_description" (weak signal unless corroborated).
    """
    results: list[InferredDimension] = []
    keywords_map = _get_description_keywords()
    for fi in field_infos:
        if not fi.description:
            continue
        lower = fi.description.lower()
        seen: set[str] = set()
        for dim_name, keywords in keywords_map.items():
            if dim_name in seen:
                continue
            for kw in keywords:
                if kw in lower:
                    seen.add(dim_name)
                    results.append(InferredDimension(
                        field_name=fi.name,
                        dimension=dim_name,
                        confidence="inferred",
                        sources=("field_description",),
                    ))
                    break
    return results


# ── Signal 3: JSON Schema structural signals ───────────────────────────

def _normalize_enum_value(v: str) -> str:
    """Normalize an enum value for comparison: lowercase, strip hyphens/underscores."""
    return v.lower().replace("-", "").replace("_", "")


_FORMAT_TO_DIMENSION: dict[str, str] = {
    "date-time": "date_format",
    "date": "date_format",
    "time": "date_format",
    # Note: "uri", "email", "uri-reference" are string formats, not encoding
    # conventions.  Mapping them to "encoding" produced false positives
    # (e.g. a URL field flagged as an encoding convention).
}

_ENUM_KNOWN_VALUES: dict[str, set[str]] | None = None


def _get_enum_known_values() -> dict[str, set[str]]:
    """Build a mapping from known_values to dimension names."""
    global _ENUM_KNOWN_VALUES
    if _ENUM_KNOWN_VALUES is not None:
        return _ENUM_KNOWN_VALUES
    taxonomy = _load_taxonomy()
    dims = taxonomy.get("dimensions", {})
    result: dict[str, set[str]] = {}
    for dim_name, dim_def in dims.items():
        values = dim_def.get("known_values", [])
        normalized = set()
        for v in values:
            normalized.add(_normalize_enum_value(v))
        result[dim_name] = normalized
    _ENUM_KNOWN_VALUES = result
    return result


def classify_schema_signal(field: FieldInfo) -> list[InferredDimension]:
    """Classify a field using JSON Schema metadata (format, type, enum, range, pattern)."""
    results: list[InferredDimension] = []
    seen: set[str] = set()

    if field.format and field.format in _FORMAT_TO_DIMENSION:
        dim = _FORMAT_TO_DIMENSION[field.format]
        if dim not in seen:
            seen.add(dim)
            results.append(InferredDimension(
                field_name=field.name,
                dimension=dim,
                confidence="inferred",
                sources=("schema_format",),
            ))

    if field.enum:
        enum_lower = {_normalize_enum_value(v) for v in field.enum if isinstance(v, str)}
        known_values = _get_enum_known_values()
        for dim_name, dim_values in known_values.items():
            if dim_name in seen:
                continue
            overlap = enum_lower & dim_values
            if len(overlap) >= 2 or (len(overlap) >= 1 and len(field.enum) <= 5):
                seen.add(dim_name)
                results.append(InferredDimension(
                    field_name=field.name,
                    dimension=dim_name,
                    confidence="inferred",
                    sources=("schema_enum",),
                ))

    if field.minimum is not None and field.maximum is not None:
        lo, hi = field.minimum, field.maximum
        if lo == 0 and hi == 1:
            if "rate_scale" not in seen:
                seen.add("rate_scale")
                results.append(InferredDimension(
                    field_name=field.name, dimension="rate_scale",
                    confidence="inferred", sources=("schema_range",),
                ))
        elif lo == 0 and hi == 100:
            if "rate_scale" not in seen and "score_range" not in seen:
                # Disambiguate: check field name and description for rate/percent hints
                _rate_hints = re.compile(
                    r"(percent|pct|rate|ratio|probability|fraction|proportion)",
                    re.IGNORECASE,
                )
                leaf = field.name.rsplit(".", 1)[-1] if "." in field.name else field.name
                desc_text = field.description or ""
                if _rate_hints.search(leaf) or _rate_hints.search(desc_text):
                    dim_choice = "rate_scale"
                else:
                    dim_choice = "score_range"
                seen.add(dim_choice)
                results.append(InferredDimension(
                    field_name=field.name, dimension=dim_choice,
                    confidence="inferred", sources=("schema_range",),
                ))
        elif lo == 1 and hi in (5, 10):
            if "score_range" not in seen:
                seen.add("score_range")
                results.append(InferredDimension(
                    field_name=field.name, dimension="score_range",
                    confidence="inferred", sources=("schema_range",),
                ))

    if field.schema_type == "integer" and field.name:
        leaf = field.name.rsplit(".", 1)[-1] if "." in field.name else field.name
        name_hit = classify_field_by_name(leaf)
        if name_hit and name_hit.dimension == "amount_unit":
            if "amount_unit" not in seen:
                seen.add("amount_unit")
                results.append(InferredDimension(
                    field_name=field.name, dimension="amount_unit",
                    confidence="inferred",
                    sources=("schema_type_integer",),
                ))

    if field.pattern:
        date_patterns = [
            r"\d{4}", r"\d{2}", r"yyyy", r"mm", r"dd",
            r"T\d{2}", r"Z$",
        ]
        if any(p in field.pattern.lower() for p in date_patterns):
            if "date_format" not in seen:
                seen.add("date_format")
                results.append(InferredDimension(
                    field_name=field.name, dimension="date_format",
                    confidence="inferred", sources=("schema_pattern",),
                ))

    return results


# ── Confidence merging ─────────────────────────────────────────────────


def _merge_signals(
    name_hits: list[InferredDimension],
    description_hits: list[InferredDimension],
    schema_hits: list[InferredDimension],
    *,
    field_description_hits: list[InferredDimension] | None = None,
    domain_hint: str | None = None,
) -> list[InferredDimension]:
    """Merge signals from all sources, dedup by (field, dimension), compute confidence.

    When domain_hint is provided, dimensions belonging to that domain get
    a confidence boost: "unknown" → "inferred" for domain-relevant dimensions.
    """
    dim_signals: dict[str, dict[str, list[str]]] = {}

    def _accumulate(hit: InferredDimension, *, is_tool_desc: bool = False) -> None:
        key = hit.dimension
        dim_signals.setdefault(key, {"fields": [], "sources": []})
        if not is_tool_desc and hit.field_name not in dim_signals[key]["fields"]:
            dim_signals[key]["fields"].append(hit.field_name)
        elif is_tool_desc and hit.field_name != "_description" and hit.field_name not in dim_signals[key]["fields"]:
            dim_signals[key]["fields"].append(hit.field_name)
        for s in hit.sources:
            if s not in dim_signals[key]["sources"]:
                dim_signals[key]["sources"].append(s)

    for hit in name_hits:
        _accumulate(hit)
    for hit in description_hits:
        _accumulate(hit, is_tool_desc=True)
    for hit in schema_hits:
        _accumulate(hit)
    for hit in (field_description_hits or []):
        _accumulate(hit)

    results: list[InferredDimension] = []
    for dim_name, info in dim_signals.items():
        sources = tuple(info["sources"])
        n_source_types = len(sources)

        # Strong single signals: field name match, schema format (explicit JSON Schema
        # metadata like "format": "date-time"), schema range (explicit min/max bounds).
        _STRONG_SIGNALS = {"name", "schema_format", "schema_range", "schema_pattern"}

        if n_source_types >= 2:
            confidence = "declared"
        elif n_source_types == 1 and sources[0] in _STRONG_SIGNALS:
            confidence = "inferred"
        else:
            # Weak signals alone: description keyword only, schema_enum (partial
            # overlap), schema_type_integer (needs name corroboration).
            confidence = "unknown"

        # Domain-aware boost: if caller specifies a domain and this dimension
        # belongs to it, promote "unknown" → "inferred".
        if confidence == "unknown" and domain_hint:
            domain_map = _get_domain_map()
            dim_domains = domain_map.get(dim_name, [])
            if domain_hint in dim_domains:
                confidence = "inferred"

        field_name = info["fields"][0] if info["fields"] else "_description"
        results.append(InferredDimension(
            field_name=field_name,
            dimension=dim_name,
            confidence=confidence,
            sources=sources,
        ))

    return results


# ── High-level API ─────────────────────────────────────────────────────


def classify_tool_rich(
    tool: dict[str, Any],
    field_infos: list[FieldInfo] | None = None,
    domain_hint: str | None = None,
) -> list[InferredDimension]:
    """Full multi-signal classification of an MCP tool definition.

    Uses field names, tool description, and JSON Schema metadata to
    produce a merged, deduplicated list of dimension classifications
    with confidence tiers based on signal agreement.

    Args:
        domain_hint: Optional domain (e.g. "financial", "ml") to boost
            domain-relevant dimensions when breaking ties.
    """
    if field_infos is None:
        # Lazy import: mcp.py imports from classifier.py at module level,
        # so we import here to break the circular dependency.
        from bulla.infer.mcp import extract_field_infos
        field_infos = extract_field_infos(tool)

    name_hits: list[InferredDimension] = []
    schema_hits: list[InferredDimension] = []

    for fi in field_infos:
        nh = classify_field_by_name(fi.name, schema_type=fi.schema_type)
        if nh:
            name_hits.append(nh)
        sh = classify_schema_signal(fi)
        schema_hits.extend(sh)

    desc = tool.get("description", "")
    description_hits = classify_description(desc)

    field_desc_hits = _classify_field_descriptions(field_infos)

    return _merge_signals(
        name_hits, description_hits, schema_hits,
        field_description_hits=field_desc_hits,
        domain_hint=domain_hint,
    )


# ── Backward-compatible API ────────────────────────────────────────────


def classify_field(name: str) -> InferredDimension | None:
    """Classify a single field name into a semantic dimension, or None.

    Backward-compatible: returns name-only classification with
    "inferred" confidence.
    """
    return classify_field_by_name(name)


def classify_fields(fields: list[str]) -> list[InferredDimension]:
    """Classify a list of field names, returning only those that match.

    Backward-compatible: name-only classification. Applies
    most-specific-wins deduplication across all results.
    """
    results = []
    for f in fields:
        inferred = classify_field_by_name(f)
        if inferred is not None:
            results.append(inferred)
    return _deduplicate_by_specificity(results)
