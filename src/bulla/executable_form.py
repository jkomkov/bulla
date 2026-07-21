"""``jsonschema+quantum/1`` — the one executable predicate form, single-sourced.

A closed, decidable, stdlib-only constraint language over a flat object (the act's
declared ``subject``): a JSON-schema keyword subset plus an integer unit/quantum
declaration. Deliberately **not** a general language — every keyword below is
decidable by a verifier with no dependencies, so any party recomputes conformance
from the receipt alone.

This module is a **leaf** (it imports only :mod:`bulla._canonical`), so it can be
shared by every layer that needs the same predicate without a circular import:

- **conventions** (`action_receipt.py`) — rules coined at a seam;
- **bounds scope** (`envelope.py` + `action_receipt.py`) — the capability an act is
  permitted to have, so ``bounds_conformance`` can recompute *did the act obey its
  scope* rather than merely *does the chain convey a matching digest*.

Callers wrap :class:`ExecutableFormError` in their own error type
(``ActionReceiptError`` / ``EnvelopeError``) so existing exception contracts hold.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from bulla._canonical import canonical_json

#: The one executable-definition form. A definition naming any other form is malformed.
EXECUTABLE_FORM = "jsonschema+quantum/1"

#: The closed keyword vocabulary. Anything outside these sets is malformed — fail
#: closed, never guess (an unrecognized keyword could silently weaken a predicate).
_SCHEMA_TOP_KEYS = frozenset({"type", "properties", "required", "additionalProperties"})
_SCHEMA_PROP_KEYS = frozenset({"type", "enum", "const", "minimum", "maximum", "pattern"})
_SCHEMA_PROP_TYPES = frozenset({"string", "integer", "number", "boolean"})
_PORTABLE_PROP_TYPES = frozenset({"string", "integer", "boolean"})
_MAX_SAFE_INTEGER = 2**53 - 1


class ExecutableFormError(ValueError):
    """A definition is not a well-formed ``jsonschema+quantum/1`` predicate."""


def _sha(b: bytes) -> str:
    return f"sha256:{hashlib.sha256(b).hexdigest()}"


def definition_hash(definition: Any) -> str:
    """The pin: ``sha256:`` over ``canonical_json`` for a structured (executable)
    definition, over raw UTF-8 bytes for an opaque string. This polymorphism is what
    lets a scope be *either* prose (hashed as its UTF-8 bytes, byte-identical to the
    historical behaviour) *or* a structured predicate (hashed canonically) without a
    version fork in the hash."""
    if isinstance(definition, str):
        return _sha(definition.encode("utf-8"))
    return _sha(canonical_json(definition).encode("utf-8"))


def validate_executable_definition(defn: Any) -> None:
    """Well-formedness of a ``jsonschema+quantum/1`` definition — the closed
    vocabulary is validated at CONSTRUCTION so a malformed predicate can never ride
    inside a hashed receipt. Raises :class:`ExecutableFormError` on the first
    violation."""
    if not isinstance(defn, dict):
        raise ExecutableFormError("executable definition must be an object")
    if defn.get("form") != EXECUTABLE_FORM:
        raise ExecutableFormError(
            f"executable definition.form must be {EXECUTABLE_FORM!r} "
            "(the one declared form — not a general language)"
        )
    extra = set(defn) - {"form", "schema", "quantum"}
    if extra:
        raise ExecutableFormError(f"unknown executable-definition keys {sorted(extra)} — fail closed")
    schema = defn.get("schema")
    if not isinstance(schema, dict):
        raise ExecutableFormError("executable definition needs a 'schema' object")
    unknown = set(schema) - _SCHEMA_TOP_KEYS
    if unknown:
        raise ExecutableFormError(f"unknown schema keywords {sorted(unknown)} — fail closed")
    if schema.get("type", "object") != "object":
        raise ExecutableFormError("schema.type must be 'object' (the act's subject)")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ExecutableFormError("schema.properties must be an object")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(
        not isinstance(name, str) or not name.strip() for name in required
    ):
        raise ExecutableFormError("schema.required must be a list of non-empty field names")
    if len(set(required)) != len(required):
        raise ExecutableFormError("schema.required must not contain duplicate field names")
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, bool):
        raise ExecutableFormError("schema.additionalProperties must be boolean")
    for pname, pschema in properties.items():
        if not isinstance(pname, str) or not pname.strip():
            raise ExecutableFormError("schema.properties field names must be non-empty strings")
        if not isinstance(pschema, dict):
            raise ExecutableFormError(f"schema.properties[{pname!r}] must be an object")
        bad = set(pschema) - _SCHEMA_PROP_KEYS
        if bad:
            raise ExecutableFormError(
                f"unknown keywords {sorted(bad)} in schema.properties[{pname!r}] — fail closed"
            )
        if "type" in pschema and pschema["type"] not in _SCHEMA_PROP_TYPES:
            raise ExecutableFormError(
                f"schema.properties[{pname!r}].type must be one of {sorted(_SCHEMA_PROP_TYPES)}"
            )
        ptype = pschema.get("type")
        if "enum" in pschema and (
            not isinstance(pschema["enum"], list) or not pschema["enum"]
        ):
            raise ExecutableFormError(
                f"schema.properties[{pname!r}].enum must be a non-empty list"
            )
        for keyword in ("minimum", "maximum"):
            if keyword not in pschema:
                continue
            value = pschema[keyword]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise ExecutableFormError(
                    f"schema.properties[{pname!r}].{keyword} must be a finite number"
                )
            if ptype not in ("integer", "number"):
                raise ExecutableFormError(
                    f"schema.properties[{pname!r}].{keyword} requires numeric type"
                )
        if "minimum" in pschema and "maximum" in pschema \
                and pschema["minimum"] > pschema["maximum"]:
            raise ExecutableFormError(
                f"schema.properties[{pname!r}].minimum must not exceed maximum"
            )
        if "pattern" in pschema:
            if ptype != "string" or not isinstance(pschema["pattern"], str):
                raise ExecutableFormError(
                    f"schema.properties[{pname!r}].pattern requires string type and value"
                )
            try:
                re.compile(pschema["pattern"])
            except re.error as exc:
                raise ExecutableFormError(
                    f"schema.properties[{pname!r}].pattern is invalid: {exc}"
                ) from exc
    quantum = defn.get("quantum")
    if quantum is not None:
        if not isinstance(quantum, dict):
            raise ExecutableFormError("quantum must map field name -> {unit, multipleOf}")
        for fname, q in quantum.items():
            if not isinstance(fname, str) or not fname.strip():
                raise ExecutableFormError("quantum field names must be non-empty strings")
            if not isinstance(q, dict) or set(q) - {"unit", "multipleOf"}:
                raise ExecutableFormError(
                    f"quantum[{fname!r}] must be {{'unit': str, 'multipleOf': int}}"
                )
            if not (q.get("unit") or "").strip():
                raise ExecutableFormError(f"quantum[{fname!r}].unit is required")
            mo = q.get("multipleOf", 1)
            if not isinstance(mo, int) or isinstance(mo, bool) or mo < 1:
                raise ExecutableFormError(
                    f"quantum[{fname!r}].multipleOf must be a positive integer "
                    "(quantized fields are integers in minor units — decidable, no float ties)"
                )
            if fname not in properties or properties[fname].get("type") != "integer":
                raise ExecutableFormError(
                    f"quantum[{fname!r}] requires a declared integer property"
                )


def validate_portable_executable_definition(defn: Any) -> None:
    """Validate the cross-language routed-inference executable subset.

    Legacy ``jsonschema+quantum/1`` permits floats and host-language regex. This
    profile deliberately does not: all numbers are safe integers and patterns are
    absent, so Python, JavaScript, and a zero-import verifier cannot disagree.
    """
    validate_executable_definition(defn)
    schema = defn["schema"]
    for pname, pschema in schema.get("properties", {}).items():
        if pschema.get("type") not in _PORTABLE_PROP_TYPES:
            raise ExecutableFormError(
                f"portable property {pname!r} must declare one of {sorted(_PORTABLE_PROP_TYPES)}"
            )
        if "pattern" in pschema:
            raise ExecutableFormError(
                f"portable property {pname!r} may not use host-language regex"
            )
        for keyword in ("minimum", "maximum", "const"):
            value = pschema.get(keyword)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not isinstance(value, int) or abs(value) > _MAX_SAFE_INTEGER:
                    raise ExecutableFormError(
                        f"portable property {pname!r}.{keyword} must be a safe integer"
                    )
        for value in pschema.get("enum", []):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not isinstance(value, int) or abs(value) > _MAX_SAFE_INTEGER:
                    raise ExecutableFormError(
                        f"portable property {pname!r}.enum values must be safe integers"
                    )


def check_definition(defn: dict, subject: dict) -> tuple[str, list[str]]:
    """Recompute conformance of ``subject`` against a validated executable ``defn``.

    Returns ``(status, reasons)`` with status ``conforms`` / ``violates``. Pure and
    deterministic — no I/O, no globals, no mutation of either argument. Accumulates
    *all* violations (never short-circuits) so the reason list is complete. Assumes
    ``defn`` already passed :func:`validate_executable_definition`."""
    schema, reasons = defn["schema"], []
    props = schema.get("properties") or {}
    for req in schema.get("required") or []:
        if req not in subject:
            reasons.append(f"required field {req!r} absent from action.subject")
    if schema.get("additionalProperties") is False:
        for k in subject:
            if k not in props:
                reasons.append(f"field {k!r} not permitted (additionalProperties: false)")
    for pname, pschema in props.items():
        if pname not in subject:
            continue
        v = subject[pname]
        t = pschema.get("type")
        type_ok = {
            "string": isinstance(v, str),
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": isinstance(v, bool),
            None: True,
        }[t]
        if not type_ok:
            reasons.append(f"{pname!r} is not of type {t!r}")
            continue
        if "const" in pschema and v != pschema["const"]:
            reasons.append(f"{pname!r} != const {pschema['const']!r}")
        if "enum" in pschema and v not in pschema["enum"]:
            reasons.append(f"{pname!r} not in enum {pschema['enum']!r}")
        if "minimum" in pschema and isinstance(v, (int, float)) and v < pschema["minimum"]:
            reasons.append(f"{pname!r} < minimum {pschema['minimum']}")
        if "maximum" in pschema and isinstance(v, (int, float)) and v > pschema["maximum"]:
            reasons.append(f"{pname!r} > maximum {pschema['maximum']}")
        if "pattern" in pschema and isinstance(v, str):
            import re
            if re.search(pschema["pattern"], v) is None:
                reasons.append(f"{pname!r} does not match pattern {pschema['pattern']!r}")
    for fname, q in (defn.get("quantum") or {}).items():
        if fname not in subject:
            reasons.append(f"quantized field {fname!r} absent from action.subject")
            continue
        v = subject[fname]
        if not isinstance(v, int) or isinstance(v, bool):
            reasons.append(f"quantized field {fname!r} must be an integer in {q['unit']!r}")
        elif v % q.get("multipleOf", 1) != 0:
            reasons.append(f"{fname!r}={v} is not a multiple of {q['multipleOf']} {q['unit']!r}")
    return ("conforms" if not reasons else "violates"), reasons
