"""Canonicalization and version pins — the two constants a recomputation trusts.

This module owns BOTH version pins:

  * ``ALGORITHM_VERSION`` — which *verdict algorithm* produced a deed (below).
  * ``CANON_VERSION`` — which *serialization rule* turned an object into the
    bytes that were hashed. ``canonical_json`` is that rule, single-sourced
    here and imported by every hash-minting site (``action_receipt``,
    ``certificate``, ``envelope``, ``model.WitnessReceipt``, ``recourse_gate``)
    so the layers can never drift apart again.

CANON_VERSION history:

  * **1** — the measurement layer (``WitnessReceipt.receipt_hash``) hashed
    ``json.dumps(obj, sort_keys=True)`` — *spaced* separators — while the deed
    layer hashed the compact form. A stranger following the spec could not
    reproduce a witness hash. Legacy v1 receipts still verify:
    ``witness.verify_receipt_integrity`` tries v2 and falls back to the spaced
    form (a format change is a version difference, not tampering).
  * **2** — one rule everywhere: ``canonical_json`` (compact, key-sorted,
    UTF-8). Deed-layer hashes are byte-unchanged (that layer was already
    compact); witness-layer hashes change and stamp ``canon_version: 2``.

RFC 8785 (JCS) compatibility — two deliberate deviations, both documented
normatively in ``spec/action-receipt-v0.2.md``:

  * non-ASCII characters are ``\\uXXXX``-escaped (``ensure_ascii=True``),
    where JCS emits raw UTF-8 — preserving byte-compatibility with every
    v1 deed-layer hash;
  * hashed material SHOULD restrict numbers to integers; where floats occur
    Python ``repr`` formatting applies, not the ES6 rules of JCS §3.2.2.3.

For key-sorting, Python's code-point sort and JCS's UTF-16 sort agree on all
BMP keys; hashed material MUST NOT use non-BMP characters in object keys.
"""

import json
from typing import Any

CANON_VERSION = 2


def canonical_json(obj: Any) -> str:
    """CANON_VERSION 2: the one serialization rule behind every bulla hash.
    Compact separators, sorted keys, ``\\uXXXX``-escaped non-ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def legacy_json_v1(obj: Any) -> str:
    """CANON_VERSION 1 (verification fallback ONLY — never mint with this):
    the spaced form the measurement layer used before v2."""
    return json.dumps(obj, sort_keys=True)


JCS_INT_PROFILE = "bulla-jcs-int/1"
JCS_SAFE_INTEGER = (1 << 53) - 1


class CanonicalizationError(ValueError):
    """Raised when a value is outside Bulla's portable v0.4 JSON domain."""


def _utf16_sort_key(value: str) -> bytes:
    try:
        # RFC 8785 orders object member names by UTF-16 code units.
        return value.encode("utf-16-be")
    except UnicodeEncodeError as exc:
        raise CanonicalizationError("lone Unicode surrogates are not canonical JSON") from exc


def canonical_jcs_int(obj: Any) -> str:
    """Canonical JSON for ActionReceipt v0.4.

    This is the RFC 8785 object/string ordering profile restricted to safe
    integers.  The restriction removes the cross-language floating-point
    formatting surface while retaining ordinary JSON interoperability.
    """

    def encode(value: Any) -> str:
        if value is None:
            return "null"
        if value is True:
            return "true"
        if value is False:
            return "false"
        if isinstance(value, int):
            if abs(value) > JCS_SAFE_INTEGER:
                raise CanonicalizationError(
                    f"integer {value} exceeds the portable safe range ±{JCS_SAFE_INTEGER}"
                )
            return str(value)
        if isinstance(value, float):
            raise CanonicalizationError(
                "floating-point values are not permitted by bulla-jcs-int/1; "
                "use integer quantum units or a decimal string"
            )
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise CanonicalizationError("lone Unicode surrogates are not canonical JSON") from exc
            return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if isinstance(value, (list, tuple)):
            return "[" + ",".join(encode(item) for item in value) + "]"
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise CanonicalizationError("canonical JSON object keys must be strings")
            keys = sorted(value, key=_utf16_sort_key)
            return "{" + ",".join(f"{encode(key)}:{encode(value[key])}" for key in keys) + "}"
        raise CanonicalizationError(
            f"unsupported canonical JSON value {type(value).__name__}; "
            "expected null, boolean, safe integer, string, array, or object"
        )

    return encode(obj)


# ── ALGORITHM_VERSION — what a deed's ``f`` is pinned to ─────────────────────
#
# A deed is a *recomputable* certificate: ``deed = f(composition@h, algorithm@v)``.
# This constant IS the ``@v`` — committed inside the certificate content hash so a
# verifier knows **which algorithm to run**. A mismatch between the deed's
# ``algorithm_version`` and the verifier's is then a *version difference*, not
# "tampered". It bumps ONLY on a **verdict-affecting** change to
# ``diagnose`` / ``classify`` / ``coboundary`` / ``witness_geometry`` — NOT on every
# release (``bulla_version`` stays excluded provenance).
#
# **Honest ladder.** This semver is the *weakest* rung: it is the one **trusted
# human input** in a system whose whole pitch is "nothing trusted, recompute it" — a
# person must remember to bump it, and the golden seed test (which pins canonical
# hashes) is a **stopgap for the missing auto-coupling between ``f``'s source and its
# version**, NOT the guarantee. The canonical target, which this program is uniquely
# positioned to reach:
#
#   * **now**     — this semver, golden-guarded (forget-prone).
#   * **next**    — derive it from the *content* of ``f`` (a hash over the verdict
#                   source), so any change to ``f`` bumps it automatically (forget-proof).
#   * **target**  — bind it to the Lean-spec hash / Aristotle stamp that *defines* the
#                   fee, so the deed's ``f`` IS the machine-checked proof and
#                   recomputability becomes provable *correctness*, not just determinism.
#                   No eval vendor can pin its algorithm to a proof; Bulla already has
#                   the stamps.

ALGORITHM_VERSION = "1"
