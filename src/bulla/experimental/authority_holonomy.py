"""Experimental signed authority-cycle evidence.

This sidecar binds pairwise authority translations to one occurrence and checks
whether their ordered cycle product is nonidentity. It does not alter
ActionReceipt v0.2, discover authority relationships, or prove occurrence,
coverage, non-equivocation, or remedy.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from bulla._canonical import canonical_json
from bulla.identity import verify_proof

KIND = "authority_translation"
VERSION = "0.1-experimental"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

Permutation = tuple[int, ...]


def _identity(size: int) -> Permutation:
    return tuple(range(size))


def _validate_permutation(permutation: Permutation, size: int) -> None:
    if len(permutation) != size or tuple(sorted(permutation)) != _identity(size):
        raise ValueError("translation must be a permutation of the declared authority vocabulary")


def compose(left: Permutation, right: Permutation) -> Permutation:
    """Return ``left ∘ right``; order is part of the evidence semantics."""
    if len(left) != len(right):
        raise ValueError("translations use different authority vocabularies")
    _validate_permutation(left, len(left))
    _validate_permutation(right, len(right))
    return tuple(left[right[index]] for index in range(len(left)))


def _preimage(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": KIND,
        "version": VERSION,
        "source_owner": document["source_owner"],
        "target_owner": document["target_owner"],
        "occurrence_hash": document["occurrence_hash"],
        "authority_vocabulary": list(document["authority_vocabulary"]),
        "translation": list(document["translation"]),
    }


def authority_translation_hash(document: dict[str, Any]) -> str:
    raw = canonical_json(_preimage(document)).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class AuthorityTranslation:
    source_owner: str
    target_owner: str
    occurrence_hash: str
    authority_vocabulary: tuple[str, ...]
    translation: Permutation
    signature: dict[str, Any]
    kind: str = KIND
    version: str = VERSION

    def __post_init__(self) -> None:
        if self.kind != KIND or self.version != VERSION:
            raise ValueError(f"expected {KIND} {VERSION}")
        if not self.source_owner or not self.target_owner or self.source_owner == self.target_owner:
            raise ValueError("translation must name two distinct owners")
        if not _HASH_RE.fullmatch(self.occurrence_hash):
            raise ValueError("occurrence_hash must be sha256:<64 lowercase hex>")
        if not self.authority_vocabulary or len(set(self.authority_vocabulary)) != len(self.authority_vocabulary):
            raise ValueError("authority_vocabulary must contain distinct labels")
        _validate_permutation(self.translation, len(self.authority_vocabulary))
        if not isinstance(self.signature, dict):
            raise ValueError("signature must be a detached proof object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "source_owner": self.source_owner,
            "target_owner": self.target_owner,
            "occurrence_hash": self.occurrence_hash,
            "authority_vocabulary": list(self.authority_vocabulary),
            "translation": list(self.translation),
            "signature": dict(self.signature),
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "AuthorityTranslation":
        if not isinstance(document, dict):
            raise ValueError("authority translation must be an object")
        try:
            return cls(
                source_owner=document["source_owner"],
                target_owner=document["target_owner"],
                occurrence_hash=document["occurrence_hash"],
                authority_vocabulary=tuple(document["authority_vocabulary"]),
                translation=tuple(document["translation"]),
                signature=document["signature"],
                kind=document.get("kind", ""),
                version=document.get("version", ""),
            )
        except KeyError as exc:
            raise ValueError(f"authority translation missing {exc.args[0]}") from exc


def cycle_holonomy(translations: tuple[AuthorityTranslation, ...]) -> Permutation:
    if not translations:
        raise ValueError("authority cycle is empty")
    if any(
        edge.target_owner != translations[(index + 1) % len(translations)].source_owner
        for index, edge in enumerate(translations)
    ):
        raise ValueError("translations do not form one ordered closed owner cycle")
    if len({edge.occurrence_hash for edge in translations}) != 1:
        raise ValueError("translations are not bound to the same occurrence")
    if len({edge.authority_vocabulary for edge in translations}) != 1:
        raise ValueError("translations do not use the same authority vocabulary")
    result = _identity(len(translations[0].authority_vocabulary))
    for edge in translations:
        result = compose(edge.translation, result)
    return result


def verify_authority_cycle(
    documents: Iterable[AuthorityTranslation | dict[str, Any]],
    *,
    public_keys: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """Verify signed translations and return a narrow descent-obstruction verdict."""
    try:
        translations = tuple(
            item if isinstance(item, AuthorityTranslation) else AuthorityTranslation.from_dict(item)
            for item in documents
        )
        holonomy = cycle_holonomy(translations)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "authenticated": False,
            "descent_obstruction": False,
            "checks": {"well_formed_cycle": False},
            "reasons": [str(exc)],
        }

    checks: dict[str, bool] = {"well_formed_cycle": True}
    reasons: list[str] = []
    for index, translation in enumerate(translations):
        issuer_bound = translation.signature.get("issuer") == translation.source_owner
        checks[f"translation_{index}_issuer_bound"] = issuer_bound
        if not issuer_bound:
            reasons.append(f"translation {index} signature issuer does not equal source_owner")
        key = None if public_keys is None else public_keys.get(translation.source_owner)
        authenticity = verify_proof(
            authority_translation_hash(translation.to_dict()),
            translation.signature,
            public_key=key,
        )
        checks[f"translation_{index}_signature"] = authenticity.authentic
        if not authenticity.authentic:
            reasons.append(f"translation {index} signature is not authentic: {authenticity.detail}")

    authenticated = all(checks.values())
    unit = _identity(len(translations[0].authority_vocabulary))
    return {
        "ok": True,
        "authenticated": authenticated,
        "descent_obstruction": authenticated and holonomy != unit,
        "checks": checks,
        "reasons": reasons,
        "occurrence_hash": translations[0].occurrence_hash,
        "ordered_owners": [edge.source_owner for edge in translations],
        "holonomy": list(holonomy),
        "identity": list(unit),
    }
