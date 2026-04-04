"""Micro-pack validation for Bulla convention packs.

Validates that a parsed pack dict conforms to the required schema:
- pack_name and dimensions are required
- Each dimension needs description + at least one of field_patterns/description_keywords
- Optional fields (refines, provenance, known_values) are type-checked
"""

from __future__ import annotations

from typing import Any


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

        if "known_values" in dim_def and not isinstance(
            dim_def["known_values"], list
        ):
            errors.append(f"{prefix}.known_values: must be a list")

        if "refines" in dim_def and dim_def["refines"] is not None and not isinstance(dim_def["refines"], str):
            errors.append(f"{prefix}.refines: must be a string or null")

        if "provenance" in dim_def and not isinstance(
            dim_def["provenance"], dict
        ):
            errors.append(f"{prefix}.provenance: must be a mapping")

    return errors
