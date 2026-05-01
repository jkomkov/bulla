"""Micro-pack validation for Bulla convention packs.

Validates that a parsed pack dict conforms to the required schema:
- pack_name and dimensions are required
- Each dimension needs description + at least one of field_patterns/description_keywords
- Optional fields (refines, provenance, known_values) are type-checked

Optional pack-level metadata (Extension A — Standards Ingest sprint):
- license: { spdx_id, source_url, registry_license, attribution }
  Describes the licensing posture of the **registry the pack points to**,
  not the pack itself (the pack is always our own metadata, openly licensed).
  registry_license values: "open" | "research-only" | "restricted".

Optional dimension-level field (Extension B — Standards Ingest sprint):
- values_registry: { uri, hash, version, license_id }
  Pointer to an external content-addressed registry that owns the canonical
  set of values for this dimension. Replaces or complements inline
  ``known_values``. The pointer object participates in the pack hash; the
  registry contents themselves are fetched on demand by
  ``bulla packs verify`` (see Extension B for credential-aware fetch).

The metadata-only invariant: packs whose license.registry_license is
"research-only" or "restricted" MUST NOT ship inline known_values on any
dimension that also has a values_registry pointer. This is the single
line of defense against accidental redistribution of licensed content
via PR oversight. For "open" packs, inline + registry may coexist
(inline serves as documentation; registry is authoritative for
membership tests); the canonicalization step in ``_hash_pack`` strips
inline values from dimensions with a registry so authors can curate
them without producing pack-hash drift.
"""

from __future__ import annotations

from typing import Any

# Valid values for license.registry_license. The field describes the
# licensing posture of the *registry* the pack points to — not the pack
# itself, which is always our own openly-published metadata.
_VALID_REGISTRY_LICENSE = {"open", "research-only", "restricted"}


_VALID_EQUIVALENCE_CLASSES = {
    "exact",
    "lossy_forward",
    "lossy_bidirectional",
    "contextual",
}


def _validate_mappings(
    mappings: Any, errors: list[str]
) -> None:
    """Validate the optional pack-level ``mappings:`` block (Extension E).

    Shape::

        mappings:
          target_pack_name:
            target_dimension_name:
              - { from: "code-a", to: "code-b", equivalence: "exact" }
              - { from: "code-c", to: "code-d", equivalence: "lossy_forward" }

    The block is *passive* — Bulla's measurement layer does NOT consume
    it (the coboundary uses dimension *names*, not values; mappings are
    receipt-side translation tables for downstream consumers). The
    validator only checks structure; semantics live in
    ``bulla.mappings.translate`` (a separate consumer-facing helper).
    """
    if not isinstance(mappings, dict):
        errors.append("mappings: must be a mapping of target_pack -> ...")
        return

    for target_pack, dim_table in mappings.items():
        if not isinstance(target_pack, str):
            errors.append(
                "mappings: keys must be target pack names (strings)"
            )
            continue
        if not isinstance(dim_table, dict):
            errors.append(
                f"mappings.{target_pack}: must be a mapping of "
                "target_dimension -> rows"
            )
            continue
        for target_dim, rows in dim_table.items():
            if not isinstance(target_dim, str):
                errors.append(
                    f"mappings.{target_pack}: dimension keys must be "
                    "strings"
                )
                continue
            row_prefix = f"mappings.{target_pack}.{target_dim}"
            if not isinstance(rows, list):
                errors.append(
                    f"{row_prefix}: must be a list of mapping rows"
                )
                continue
            for i, row in enumerate(rows):
                _validate_mapping_row(row, f"{row_prefix}[{i}]", errors)


def _validate_mapping_row(
    row: Any, prefix: str, errors: list[str]
) -> None:
    """Validate a single mapping row.

    Required keys: ``from``, ``to`` (both strings).
    Optional: ``equivalence`` (one of ``exact``, ``lossy_forward``,
    ``lossy_bidirectional``, ``contextual``; default ``exact``),
    ``note`` (string).
    """
    if not isinstance(row, dict):
        errors.append(f"{prefix}: must be a mapping with 'from' and 'to'")
        return

    for required in ("from", "to"):
        if required not in row:
            errors.append(f"{prefix}: missing required key '{required}'")
        elif not isinstance(row[required], str):
            errors.append(f"{prefix}.{required}: must be a string")

    if "equivalence" in row:
        eq = row["equivalence"]
        if not isinstance(eq, str) or eq not in _VALID_EQUIVALENCE_CLASSES:
            errors.append(
                f"{prefix}.equivalence: must be one of "
                f"{sorted(_VALID_EQUIVALENCE_CLASSES)}"
            )

    if "note" in row and not isinstance(row["note"], str):
        errors.append(f"{prefix}.note: must be a string")

    recognized = {"from", "to", "equivalence", "note"}
    unknown = set(row.keys()) - recognized
    if unknown:
        errors.append(
            f"{prefix}: unrecognized keys {sorted(unknown)} "
            f"(allowed: {sorted(recognized)})"
        )


def _validate_known_values_items(
    items: list[Any], prefix: str, errors: list[str]
) -> None:
    """Validate the items of a ``known_values`` list (Extension D).

    Each item may be either:
      - a plain string (existing format; e.g. ``"USD"``)
      - an alias dict ``{ canonical, aliases?, source_codes? }``:
        - ``canonical`` (str): the canonical value name
        - ``aliases`` (list[str]): synonyms that normalize to canonical
        - ``source_codes`` (dict[str, str]): maps an external standard
          short name to that standard's code (e.g. ``{"ISO-4217": "840"}``)

    The two forms can coexist in the same list. Old packs (string-only)
    keep working unchanged — this is strictly additive.
    """
    for i, item in enumerate(items):
        item_prefix = f"{prefix}.known_values[{i}]"
        if isinstance(item, str):
            continue
        if not isinstance(item, dict):
            errors.append(
                f"{item_prefix}: must be a string or an alias mapping "
                "with at minimum 'canonical' (got "
                f"{type(item).__name__})"
            )
            continue
        # Alias-form dict.
        if "canonical" not in item:
            errors.append(
                f"{item_prefix}: missing required key 'canonical'"
            )
        elif not isinstance(item["canonical"], str):
            errors.append(
                f"{item_prefix}.canonical: must be a string"
            )

        if "aliases" in item:
            aliases = item["aliases"]
            if not isinstance(aliases, list):
                errors.append(
                    f"{item_prefix}.aliases: must be a list of strings"
                )
            else:
                for j, alias in enumerate(aliases):
                    if not isinstance(alias, str):
                        errors.append(
                            f"{item_prefix}.aliases[{j}]: must be a string"
                        )

        if "source_codes" in item:
            sc = item["source_codes"]
            if not isinstance(sc, dict):
                errors.append(
                    f"{item_prefix}.source_codes: must be a mapping "
                    "of standard-name to code"
                )
            else:
                for std_name, code in sc.items():
                    if not isinstance(std_name, str):
                        errors.append(
                            f"{item_prefix}.source_codes: "
                            "keys must be strings (standard names)"
                        )
                    if not isinstance(code, str):
                        errors.append(
                            f"{item_prefix}.source_codes.{std_name}: "
                            "value must be a string (the code)"
                        )

        # Reject unknown extra keys to catch typos early.
        recognized = {"canonical", "aliases", "source_codes"}
        unknown = set(item.keys()) - recognized
        if unknown:
            errors.append(
                f"{item_prefix}: unrecognized keys {sorted(unknown)} "
                f"(allowed: {sorted(recognized)})"
            )


def _validate_derives_from(
    block: Any, errors: list[str]
) -> None:
    """Validate the optional pack-level ``derives_from`` provenance block.

    Required keys (when present): ``standard``, ``version`` (both strings).
    Optional: ``source_uri``, ``source_hash`` (both strings).

    Absent block is fine; a pack does not have to derive from anything
    (LLM-discovered micro-packs and the base pack do not).
    """
    if not isinstance(block, dict):
        errors.append("derives_from: must be a mapping")
        return

    for required in ("standard", "version"):
        if required not in block:
            errors.append(
                f"derives_from: missing required key '{required}'"
            )
        elif not isinstance(block[required], str):
            errors.append(f"derives_from.{required}: must be a string")

    for optional in ("source_uri", "source_hash"):
        if optional in block and not isinstance(block[optional], str):
            errors.append(f"derives_from.{optional}: must be a string")


def _validate_values_registry(
    registry: Any, prefix: str, errors: list[str]
) -> None:
    """Validate a dimension's optional ``values_registry`` pointer.

    Required keys: ``uri``, ``hash``, ``version`` (all strings).
    Optional: ``license_id`` (string, cross-references the pack's
    license block so the registry-fetch layer knows which credential
    to use).

    The ``hash`` field accepts two formats:

    - ``sha256:<64-hex>`` — a real SHA-256 of the registry contents,
      produced by an actual ingest. The verifier compares fetched
      content against this hash.
    - ``placeholder:<reason>`` — a sentinel indicating the pack is
      structurally ready to verify but no real ingest has happened
      yet. Common reasons: ``awaiting-ingest`` (open registries we
      haven't fetched), ``awaiting-license`` (license-gated
      registries we don't have credentials for). The verifier
      surfaces these distinctly from real hash mismatches.

    A literal ``sha256:0...0`` is rejected: it's a valid-shaped hash
    that the verifier would silently treat as "checked, mismatched,"
    which is worse than the explicit "not yet checkable" state. Use
    the ``placeholder:`` sentinel instead.
    """
    if not isinstance(registry, dict):
        errors.append(f"{prefix}.values_registry: must be a mapping")
        return

    for required in ("uri", "hash", "version"):
        if required not in registry:
            errors.append(
                f"{prefix}.values_registry: missing required key '{required}'"
            )
        elif not isinstance(registry[required], str):
            errors.append(
                f"{prefix}.values_registry.{required}: must be a string"
            )

    h = registry.get("hash")
    if isinstance(h, str):
        if h == "sha256:" + "0" * 64:
            errors.append(
                f"{prefix}.values_registry.hash: literal "
                f"'sha256:000...000' is rejected — it's a valid-"
                f"shaped hash that the verifier would silently treat "
                f"as 'checked, mismatched.' Use the "
                f"'placeholder:<reason>' sentinel format instead "
                f"(e.g. 'placeholder:awaiting-ingest' or "
                f"'placeholder:awaiting-license') to make the "
                f"not-yet-checkable state machine-readable."
            )
        elif h.startswith("sha256:"):
            rest = h[len("sha256:"):]
            if len(rest) != 64 or any(
                c not in "0123456789abcdefABCDEF" for c in rest
            ):
                errors.append(
                    f"{prefix}.values_registry.hash: 'sha256:' prefix "
                    f"requires exactly 64 hex characters, got {len(rest)}"
                )
        elif h.startswith("placeholder:"):
            reason = h[len("placeholder:"):]
            if not reason:
                errors.append(
                    f"{prefix}.values_registry.hash: "
                    f"'placeholder:' requires a reason (e.g. "
                    f"'placeholder:awaiting-ingest')"
                )
        else:
            errors.append(
                f"{prefix}.values_registry.hash: must start with "
                f"'sha256:' (real hash) or 'placeholder:' (sentinel "
                f"for not-yet-fetched registries)"
            )

    if "license_id" in registry and not isinstance(
        registry["license_id"], str
    ):
        errors.append(
            f"{prefix}.values_registry.license_id: must be a string "
            "(cross-references the pack-level license block)"
        )


def _enforce_metadata_only_invariant(
    parsed: dict[str, Any], errors: list[str]
) -> None:
    """Enforce the metadata-only invariant for restricted packs.

    A pack whose ``license.registry_license`` is ``research-only`` or
    ``restricted`` may not ship inline ``known_values`` on any dimension
    that also carries a ``values_registry`` pointer. The licensed values
    live behind the registry, which the consumer must fetch under their
    own license; we never redistribute them via the pack file.

    This is the single line of defense against accidental redistribution
    of licensed content via PR review oversight. Validator-enforced at
    every pack load and every ``bulla packs validate`` invocation; CI
    gate will block any PR that violates it.

    Note: a restricted pack MAY have inline ``known_values`` on a
    dimension that has NO ``values_registry`` (e.g. a structural
    dimension whose values are not the licensed payload). The invariant
    fires only when both inline values and a registry pointer are
    present on the same dimension.
    """
    license_block = parsed.get("license")
    if not isinstance(license_block, dict):
        return
    registry_license = license_block.get("registry_license")
    if registry_license not in {"research-only", "restricted"}:
        return

    dims = parsed.get("dimensions", {})
    if not isinstance(dims, dict):
        return

    for dim_name, dim_def in dims.items():
        if not isinstance(dim_def, dict):
            continue
        has_registry = "values_registry" in dim_def
        has_inline = bool(dim_def.get("known_values"))
        if has_registry and has_inline:
            errors.append(
                f"dimensions.{dim_name}: pack license.registry_license is "
                f"{registry_license!r}; inline 'known_values' MUST NOT "
                "coexist with 'values_registry' on a licensed dimension "
                "(metadata-only invariant; licensed values must remain "
                "behind the registry pointer, not be redistributed in the "
                "pack file)"
            )


def _validate_license(license_block: Any, errors: list[str]) -> None:
    """Validate the optional pack-level ``license`` metadata block.

    Appends any errors to the provided list.  No-op if the block is absent
    (license is optional for backward compatibility with pre-Extension-A
    packs).
    """
    if not isinstance(license_block, dict):
        errors.append("license: must be a mapping")
        return

    # spdx_id is recommended (humans-readable provenance) but not required.
    if "spdx_id" in license_block and not isinstance(
        license_block["spdx_id"], str
    ):
        errors.append("license.spdx_id: must be a string")

    if "source_url" in license_block and not isinstance(
        license_block["source_url"], str
    ):
        errors.append("license.source_url: must be a string")

    # registry_license is the load-bearing field for the metadata-only
    # invariant (enforced by Extension B's validator pass).  Required
    # whenever a license block is present.
    if "registry_license" not in license_block:
        errors.append(
            "license.registry_license: required when license block is present "
            f"(must be one of {sorted(_VALID_REGISTRY_LICENSE)})"
        )
    else:
        rl = license_block["registry_license"]
        if not isinstance(rl, str) or rl not in _VALID_REGISTRY_LICENSE:
            errors.append(
                f"license.registry_license: must be one of "
                f"{sorted(_VALID_REGISTRY_LICENSE)}, got {rl!r}"
            )

    if "attribution" in license_block and not isinstance(
        license_block["attribution"], str
    ):
        errors.append(
            "license.attribution: must be a string "
            "(typically a sha256:... hash-ref to NOTICES.md)"
        )


def validate_pack(parsed: dict[str, Any]) -> list[str]:
    """Validate a parsed pack dictionary.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    if not isinstance(parsed, dict):
        return ["Pack must be a YAML mapping (dict)"]

    if "pack_name" not in parsed:
        errors.append("Missing required field: pack_name")

    if "dimensions" not in parsed:
        errors.append("Missing required field: dimensions")
        return errors

    dims = parsed["dimensions"]
    if not isinstance(dims, dict):
        errors.append("'dimensions' must be a mapping")
        return errors

    if len(dims) == 0:
        errors.append("Pack must define at least one dimension")

    if "license" in parsed:
        _validate_license(parsed["license"], errors)

    if "derives_from" in parsed:
        _validate_derives_from(parsed["derives_from"], errors)

    if "mappings" in parsed:
        _validate_mappings(parsed["mappings"], errors)

    for dim_name, dim_def in dims.items():
        prefix = f"dimensions.{dim_name}"

        if not isinstance(dim_def, dict):
            errors.append(f"{prefix}: must be a mapping")
            continue

        if "description" not in dim_def:
            errors.append(f"{prefix}: missing required field 'description'")

        has_patterns = bool(dim_def.get("field_patterns"))
        has_keywords = bool(dim_def.get("description_keywords"))
        if not has_patterns and not has_keywords:
            errors.append(
                f"{prefix}: must have at least one of "
                "'field_patterns' or 'description_keywords'"
            )

        if "field_patterns" in dim_def and not isinstance(
            dim_def["field_patterns"], list
        ):
            errors.append(f"{prefix}.field_patterns: must be a list")

        if "description_keywords" in dim_def and not isinstance(
            dim_def["description_keywords"], list
        ):
            errors.append(f"{prefix}.description_keywords: must be a list")

        if "known_values" in dim_def:
            kv = dim_def["known_values"]
            if not isinstance(kv, list):
                errors.append(f"{prefix}.known_values: must be a list")
            else:
                _validate_known_values_items(kv, prefix, errors)

        if "refines" in dim_def and dim_def["refines"] is not None and not isinstance(dim_def["refines"], str):
            errors.append(f"{prefix}.refines: must be a string or null")

        if "provenance" in dim_def and not isinstance(
            dim_def["provenance"], dict
        ):
            errors.append(f"{prefix}.provenance: must be a mapping")

        if "values_registry" in dim_def:
            _validate_values_registry(
                dim_def["values_registry"], prefix, errors
            )

    # Enforce the metadata-only invariant after per-dimension checks: a
    # pack whose registry license is restricted/research-only cannot
    # ship inline values on a dimension that also has a registry pointer.
    _enforce_metadata_only_invariant(parsed, errors)

    return errors
