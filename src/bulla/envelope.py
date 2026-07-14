"""Deed v0.2 — the minimal recourse triple (`authority`, `bounds`, `recourse`).

Optional signed-envelope fields on top of the v0.1 deed. The envelope is part of
the ATTESTATION preimage (signed, tamper-evident, anchored transitively through
`attestation_hash`) and deliberately NOT part of `certificate_content_hash` —
the recomputable core stays pure: anyone re-derives the coherence verdict from
pinned inputs without needing the envelope, and a v0.1 deed (no envelope)
hashes exactly as before.

THE MODALITY LAW (design invariant, enforced at construction). Recourse under
the absent master cannot assume a stateful respondent: the acting process is
gone at contest time; nothing can be served, confined, or made to answer.
Every remedy therefore attaches to an artifact or a stake — something that IS
persistent and stateful — never to the vanished actor. A remedy must name its
`verifier` (how the remedy's execution is checked) and its `anchor` (the
artifact or stake it executes against). A remedy without a stateful anchor is
process theater, and this module refuses to construct one.

The remedy ladder, in escalation order:

    recompute  — re-derive the verdict from pinned inputs (no adjudicator;
                 the deed IS the evidence). Anchor: the deed itself.
    challenge  — inclusion / omission / consistency proofs against the log.
                 Anchor: the registry root the consumer pins (Pin-the-Root).
    cure       — the disclosure that repairs the composition (the repair
                 loop). Anchor: the composition hash.
    revert     — bounded rollback / compensating action within
                 `bounds.rollback_window`. Anchor: the acted-on resource.
    slash      — the bond staked on the spec-hash. FRONTIER RUNG: the field
                 format ships now; the mechanism is gated on adoption. Anchor:
                 the bond reference.
    escalate   — the surviving principal, via the mandate/delegation chain in
                 `authority`. The ladder deliberately ENDS in human
                 jurisdiction; it never simulates a court mid-ladder. Anchor:
                 the delegation chain.

Constitutional mapping (abridged Appendix C): Act = the deed's certified
content scope · Authority = `authority` · Bounds = `bounds` · Justification =
the recomputable diagnostic itself (rung one is why a bulla receipt has
content) · Appeal Path = `recourse`.

Schema-versioned (`deed_schema: "0.2"`); grown by profiles, never by a maximal
envelope. `retention_class` / `disclosure_class` are stubs: the civic
asymmetry (records of power persist; records against persons must be able to
end) is a schema invariant from day one, while the forgetting mechanism is a
later, separately pre-registered instrument.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bulla._canonical import canonical_json

DEED_SCHEMA_VERSION = "0.2"

#: The remedy ladder, in escalation order. Order is normative for conformance
#: (a well-formed recourse block lists remedies in non-decreasing rung order);
#: construction enforces membership + the modality law, not ordering.
REMEDY_RUNGS = ("recompute", "challenge", "cure", "revert", "slash", "escalate")

#: Retention classes (stubs — the mechanism is a later instrument). The
#: asymmetry is the invariant: authority-class records default to permanence,
#: person-class records must carry a finite class.
RETENTION_CLASSES = ("authority-permanent", "operational", "personal-expiring")
DISCLOSURE_CLASSES = ("public", "party", "auditor")


class EnvelopeError(ValueError):
    """Raised when an envelope violates its schema or the modality law."""


@dataclass(frozen=True)
class Remedy:
    """One rung of the ladder: what can be done, checked how, against what.

    `verifier` names how execution of the remedy is checked (a command, a
    proof kind, an endpoint). `anchor` names the stateful artifact or stake
    the remedy executes against. Both are required — that IS the modality law.
    """

    rung: str
    verifier: str
    anchor: str

    def __post_init__(self) -> None:
        if self.rung not in REMEDY_RUNGS:
            raise EnvelopeError(
                f"unknown remedy rung {self.rung!r}; the ladder is {REMEDY_RUNGS}"
            )
        if not (self.verifier or "").strip():
            raise EnvelopeError(
                f"remedy {self.rung!r} names no verifier — an uncheckable remedy "
                "is process theater (modality law)"
            )
        if not (self.anchor or "").strip():
            raise EnvelopeError(
                f"remedy {self.rung!r} names no stateful anchor — a remedy must "
                "execute against an artifact or a stake, never against the "
                "vanished actor (modality law)"
            )

    def to_dict(self) -> dict:
        return {"rung": self.rung, "verifier": self.verifier, "anchor": self.anchor}


@dataclass(frozen=True)
class Authority:
    """Mandate reference: the delegation chain terminating at a surviving
    principal, plus the governing policy at a pinned hash.

    `principal` is the surviving-principal reference — the party that persists
    when the acting process does not, and the terminus of the `escalate` rung.
    """

    principal: str
    policy: str
    delegation: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (self.principal or "").strip():
            raise EnvelopeError(
                "authority.principal is required — the delegation chain must "
                "terminate at a surviving principal (there is no one else left "
                "to answer)"
            )
        if not (self.policy or "").strip():
            raise EnvelopeError("authority.policy is required (policy@hash reference)")
        object.__setattr__(self, "delegation", tuple(self.delegation))

    def to_dict(self) -> dict:
        return {
            "principal": self.principal,
            "policy": self.policy,
            "delegation": list(self.delegation),
        }


@dataclass(frozen=True)
class Bounds:
    """Scope, expiry, and rollback window for the certified act."""

    scope: str
    expires: str | None = None
    rollback_window: str | None = None

    def __post_init__(self) -> None:
        if not (self.scope or "").strip():
            raise EnvelopeError("bounds.scope is required")

    def to_dict(self) -> dict:
        d: dict = {"scope": self.scope}
        if self.expires is not None:
            d["expires"] = self.expires
        if self.rollback_window is not None:
            d["rollback_window"] = self.rollback_window
        return d


@dataclass(frozen=True)
class Forum:
    """Where a challenge is heard: a log endpoint plus the root reference the
    consumer pins. Honors Pin-the-Root — the forum never asks the challenger
    to trust the host's own served root."""

    log_endpoint: str
    trusted_root_ref: str

    def __post_init__(self) -> None:
        if not (self.log_endpoint or "").strip():
            raise EnvelopeError("forum.log_endpoint is required")
        if not (self.trusted_root_ref or "").strip():
            raise EnvelopeError(
                "forum.trusted_root_ref is required — a forum that verifies "
                "against the host's own served root is self-consistency, not "
                "recourse (Pin-the-Root)"
            )

    def to_dict(self) -> dict:
        return {
            "log_endpoint": self.log_endpoint,
            "trusted_root_ref": self.trusted_root_ref,
        }


@dataclass(frozen=True)
class Recourse:
    """The appeal path: challenge window, forum, and the remedy ladder."""

    challenge_window: str
    forum: Forum
    remedies: tuple[Remedy, ...] = ()

    def __post_init__(self) -> None:
        if not (self.challenge_window or "").strip():
            raise EnvelopeError("recourse.challenge_window is required")
        object.__setattr__(self, "remedies", tuple(self.remedies))
        if not self.remedies:
            raise EnvelopeError(
                "recourse.remedies must name at least one remedy — an appeal "
                "path with no executable remedy is process theater"
            )

    def to_dict(self) -> dict:
        return {
            "challenge_window": self.challenge_window,
            "forum": self.forum.to_dict(),
            "remedies": [r.to_dict() for r in self.remedies],
        }


@dataclass(frozen=True)
class RecourseEnvelope:
    """The v0.2 signed envelope: the minimal recourse triple plus the civic
    stubs. All fields optional EXCEPT the cross-field law: an `escalate`
    remedy requires `authority` (its anchor is the delegation chain)."""

    authority: Authority | None = None
    bounds: Bounds | None = None
    recourse: Recourse | None = None
    retention_class: str | None = None
    disclosure_class: str | None = None
    deed_schema: str = DEED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.deed_schema != DEED_SCHEMA_VERSION:
            raise EnvelopeError(
                f"unknown deed_schema {self.deed_schema!r} (this build speaks "
                f"{DEED_SCHEMA_VERSION!r})"
            )
        if self.retention_class is not None and self.retention_class not in RETENTION_CLASSES:
            raise EnvelopeError(
                f"unknown retention_class {self.retention_class!r}; classes: {RETENTION_CLASSES}"
            )
        if self.disclosure_class is not None and self.disclosure_class not in DISCLOSURE_CLASSES:
            raise EnvelopeError(
                f"unknown disclosure_class {self.disclosure_class!r}; classes: {DISCLOSURE_CLASSES}"
            )
        if self.recourse is not None:
            for r in self.recourse.remedies:
                if r.rung == "escalate" and self.authority is None:
                    raise EnvelopeError(
                        "an `escalate` remedy requires `authority` — the rung's "
                        "anchor is the delegation chain to the surviving principal"
                    )
        if all(
            v is None
            for v in (
                self.authority,
                self.bounds,
                self.recourse,
                self.retention_class,
                self.disclosure_class,
            )
        ):
            raise EnvelopeError(
                "empty envelope — omit the envelope entirely (v0.1 deed) rather "
                "than attaching a vacuous one"
            )

    def to_dict(self) -> dict:
        d: dict = {"deed_schema": self.deed_schema}
        if self.authority is not None:
            d["authority"] = self.authority.to_dict()
        if self.bounds is not None:
            d["bounds"] = self.bounds.to_dict()
        if self.recourse is not None:
            d["recourse"] = self.recourse.to_dict()
        if self.retention_class is not None:
            d["retention_class"] = self.retention_class
        if self.disclosure_class is not None:
            d["disclosure_class"] = self.disclosure_class
        return d

    def canonical(self) -> str:
        """Deterministic JSON — the exact string committed inside the
        attestation preimage (``bulla._canonical.canonical_json``)."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "RecourseEnvelope":
        """Reconstruct (and re-validate) an envelope from a serialized dict.
        Verification paths go through here so a served envelope that violates
        the modality law is refused even if its bytes hash correctly."""
        if not isinstance(d, dict):
            raise EnvelopeError("envelope must be a dict")
        auth = d.get("authority")
        bounds = d.get("bounds")
        rec = d.get("recourse")
        return cls(
            authority=Authority(
                principal=auth.get("principal", ""),
                policy=auth.get("policy", ""),
                delegation=tuple(auth.get("delegation", ())),
            )
            if auth is not None
            else None,
            bounds=Bounds(
                scope=bounds.get("scope", ""),
                expires=bounds.get("expires"),
                rollback_window=bounds.get("rollback_window"),
            )
            if bounds is not None
            else None,
            recourse=Recourse(
                challenge_window=rec.get("challenge_window", ""),
                forum=Forum(
                    log_endpoint=(rec.get("forum") or {}).get("log_endpoint", ""),
                    trusted_root_ref=(rec.get("forum") or {}).get("trusted_root_ref", ""),
                ),
                remedies=tuple(
                    Remedy(
                        rung=r.get("rung", ""),
                        verifier=r.get("verifier", ""),
                        anchor=r.get("anchor", ""),
                    )
                    for r in rec.get("remedies", ())
                ),
            )
            if rec is not None
            else None,
            retention_class=d.get("retention_class"),
            disclosure_class=d.get("disclosure_class"),
            deed_schema=d.get("deed_schema", DEED_SCHEMA_VERSION),
        )


def ladder_ordered(remedies: tuple[Remedy, ...]) -> bool:
    """Conformance predicate (not a construction requirement): remedies listed
    in non-decreasing ladder order, starting from `recompute` when present —
    public copy climbs the ladder in this order."""
    idx = [REMEDY_RUNGS.index(r.rung) for r in remedies]
    return idx == sorted(idx)
