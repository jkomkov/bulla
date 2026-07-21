"""ActionReceipt v0.1 — the receipt for a consequential agent action.

Bulla's diagnostic layer answers "is this composition coherent?" (`WitnessReceipt`).
This module answers the next question: **an agent just changed the world — write
a file, publish a package, move a record — under whose authority, within what
bounds, with what verdict, and how is it contested?** You cannot contest an
ephemeral actor directly, only a durable, *adjudicable record* of what was
promised and what happened. Any collateral or settlement rail remains an
external policy sidecar. The receipt is the record it may reference, not the
collateral mechanism itself.

THE ONE ABSTRACTION. There is a single new object here — the ActionReceipt
envelope. A *release* is not a new type; it is an ``action.type`` (open
vocabulary: ``package.release``, ``github.create_file``, …). ``WitnessReceipt``
stays a separate type — the thing ``diagnostic_ref`` points at, never folded in.

THE DIFFERENTIATION TRIAD (the reason this is not a better-funded audit log):
  - **verdict** — ``diagnostic_ref`` carries a *recomputable* verdict any party
    re-derives from pinned inputs. A receipt without a verdict is a signed log
    line; everyone ships those. This is the field that must never be blurred:
    it is never bare ``null`` (see ``DIAGNOSTIC_STATUSES``), and evidence lives
    in ``evidence_refs``, never in the verdict slot.
  - **coverage** — the receipt anchors into a log (``log_leaf``) so *missing*
    receipts are detectable against a declared anchor (see ``bulla coverage``).
  - **retention** — the civic asymmetry (records of power persist; records
    against persons must be able to end) rides in the recourse envelope's
    ``retention_class`` from day one, before the format ossifies.

MANDATE vs REMEDY (two triples, never one overloaded "recourse triple"):
  - **mandate** (ex ante legitimacy): ``authority`` + ``bounds`` — was the act
    authorized and bounded *before* it ran.
  - **remedy** (ex post contestation): ``recourse`` (challenge window, forum,
    remedy ladder) — what can be done *now that* it happened.
Both are surfaced as named views over a single ``bulla.envelope.RecourseEnvelope``,
which remains the source of truth for validation (the modality law) and for the
attestation preimage — so a served receipt whose remedy names no stateful anchor
is refused even if its bytes hash correctly.

THE FOUR HASHES, each answering exactly one question (the CT leaf-vs-STH lesson):
  - ``content``     — "recompute the verdict": the act + verdict core, envelope-
                      free, time-free, signature-free. Stable across machines and
                      bulla versions — the recomputable identity.
  - ``event``       — "which occurrence": ``content`` bound to a timestamp. Two
                      re-derivations of the same claim share a ``content`` hash
                      but are distinct events.
  - ``attestation`` — "who vouched": commitment to {content, signature, the
                      recourse envelope, the authorization proof}. Mirrors
                      ``certificate._attestation_hash`` in discipline; the signed,
                      anchorable identity.
  - ``log_leaf``    — "where logged": the RFC 6962 leaf (``H(0x00‖…)``) of the
                      attestation hash, ready to append to a ``DeedLog``.

THE AUTHORITY BINDING (why ``content`` alone is not enough). ``content`` is
envelope-free by design — the verdict must recompute without the appeal path.
That same exclusion means a signature over ``content`` says nothing about the
mandate or remedy: an adversary can keep the issuer's valid content signature,
swap the ``authority``/``bounds``/``recourse`` envelope, recompute the two
downstream hashes a verifier recomputes anyway, and present forged authority
that still verifies. ``authorization_hash = H(content_hash, envelope_hash)`` is
the fix: the issuer signs it (the ``authorization`` proof) to vouch for THIS
envelope, so swapping the envelope breaks that proof. A verifier reports two
authenticity facts, never one — content (the claim) and authority (the mandate).

RESERVED. ``stake`` is declared and must be ``None``. Collateral belongs in an
external settlement sidecar, and a future wire revision must not silently
change the signed preimage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from bulla._canonical import canonical_json
from bulla.envelope import EnvelopeError, RecourseEnvelope
from bulla.executable_form import (
    EXECUTABLE_FORM,
    ExecutableFormError,
    check_definition,
    definition_hash,
    validate_executable_definition,
)

SCHEMA_VERSION = "0.2"
# Authority binding changes the wire shape and the attestation preimage. It is
# therefore a receipt-schema revision, not a silent amendment to shipped v0.2.
# Builders continue to mint v0.2 unless callers use ``sign_action_receipt``,
# which upgrades the receipt to this draft revision.
AUTHORIZATION_SCHEMA_VERSION = "0.3"
RECEIPT_KIND = "action_receipt"

#: Evidence grounding classes (spec v0.2 §1), ordered LOWEST first for the
#: display rule: a receipt's effective grounding is the minimum class over its
#: necessary evidence. The order below is the default (stranger-relative)
#: ranking — against the signer of a counterparty signature, that class ranks
#: higher; the spec's relativity note governs.
GROUNDING_CLASSES = (
    "self_asserted",
    "counterparty_signed",
    "third_party_anchored",
    "execution_verified",
)

#: Convention kinds (spec v0.2 §5). The discriminator IS the decidability
#: boundary: ``executable`` conventions are recomputable by any verifier;
#: ``semantic`` conventions are pinned by hash and enforced by recourse.
CONVENTION_KINDS = ("executable", "semantic")

#: The one executable-definition form (``jsonschema+quantum/1``) and its closed
#: keyword vocabulary now live in :mod:`bulla.executable_form`, a leaf module shared
#: by conventions here and ``bounds.scope`` in the envelope. ``EXECUTABLE_FORM`` is
#: re-exported above for callers that referenced it from this module.

#: ``diagnostic_ref`` is never bare ``null`` — the ambiguity between "no
#: composition existed to diagnose" and "we skipped it" is exactly where the
#: verdict leg of the triad erodes. A missing verdict must say *why*.
DIAGNOSTIC_STATUSES = ("reference", "not_applicable", "deferred")

_LEAF = b"\x00"  # RFC 6962 leaf prefix — wire-compatible with bulla.registry.leaf_hash


def _sha(b: bytes) -> str:
    return f"sha256:{hashlib.sha256(b).hexdigest()}"


def _canon_hash(obj: Any) -> str:
    """SHA-256 over ``bulla._canonical.canonical_json`` — the one
    canonicalization rule (CANON_VERSION 2), single-sourced so this layer,
    the certificate layer, and the witness layer can never drift. Documented
    in the spec so a second implementer reproduces every hash without our
    source. Byte-identical to what this layer hashed in v0.1."""
    return _sha(canonical_json(obj).encode("utf-8"))


def _leaf_hash(data: bytes) -> str:
    """RFC 6962 leaf hash ``H(0x00 ‖ data)`` — same bytes as
    ``bulla.registry.leaf_hash``, inlined so this stays a light leaf module."""
    return _sha(_LEAF + data)


class ActionReceiptError(ValueError):
    """Raised when a receipt violates its schema or an invariant."""


# ── conventions: predicate invention, made auditable ─────────────────────────
#
# A convention is a rule two parties coin AT THE SEAM, in-line, and commit
# inside the receipt's content hash — on-the-fly DDL with an audit trail. The
# ``kind`` discriminator is the decidability boundary:
#
#   executable — the definition is a small declared form (JSON-schema subset +
#     integer quantum; NOT a general language) whose conformance any verifier
#     recomputes against the act's declared subject. Enforcement = recompute.
#   semantic — the definition is opaque, pinned by ``definition_hash``;
#     enforcement is recourse, so a ``forum`` (the RecourseEnvelope modality
#     law: a persistent verifier + a pinned root) is REQUIRED.
#
# Per ADR-001: the global convention graph is EMERGENT — the transitive
# closure of referenced definitions — never an operated product.


#: The pin over a definition — str → UTF-8, structured → canonical JSON. Single-sourced
#: in :mod:`bulla.executable_form` and re-exported under its historical name here.
convention_definition_hash = definition_hash


def _validate_executable_definition(defn: Any) -> None:
    """Validate a ``jsonschema+quantum/1`` definition, raising ``ActionReceiptError``
    (the convention layer's error type) on a malformed one. Delegates the closed-form
    check to :func:`bulla.executable_form.validate_executable_definition`."""
    try:
        validate_executable_definition(defn)
    except ExecutableFormError as exc:
        raise ActionReceiptError(str(exc)) from exc


def _validate_convention(c: Any) -> None:
    """Shape + pin validation for one convention entry. Raises on the first
    violation; a receipt carrying a malformed convention never constructs."""
    if not isinstance(c, dict):
        raise ActionReceiptError("each convention must be an object")
    if not (c.get("name") or "").strip():
        raise ActionReceiptError("convention.name is required")
    if not (c.get("scope") or "").strip():
        raise ActionReceiptError(f"convention {c.get('name')!r}: scope is required (the seam it binds)")
    kind = c.get("kind")
    if kind not in CONVENTION_KINDS:
        raise ActionReceiptError(
            f"convention {c['name']!r}: kind must be one of {CONVENTION_KINDS} "
            "(the decidability boundary is the discriminator)"
        )
    dh = c.get("definition_hash") or ""
    if not dh.startswith("sha256:"):
        raise ActionReceiptError(f"convention {c['name']!r}: definition_hash ('sha256:…') is required")
    extra = set(c) - {"name", "scope", "kind", "definition", "definition_hash", "forum"}
    if extra:
        raise ActionReceiptError(f"convention {c['name']!r}: unknown keys {sorted(extra)}")
    if kind == "executable":
        if "definition" not in c:
            raise ActionReceiptError(
                f"convention {c['name']!r}: executable conventions carry their definition in-line "
                "(a verifier recomputes conformance from the receipt alone)"
            )
        _validate_executable_definition(c["definition"])
        if convention_definition_hash(c["definition"]) != dh:
            raise ActionReceiptError(
                f"convention {c['name']!r}: definition_hash does not match the in-line definition"
            )
    else:  # semantic
        forum = c.get("forum")
        if not isinstance(forum, dict):
            raise ActionReceiptError(
                f"convention {c['name']!r}: semantic conventions require a forum — enforcement is "
                "recourse, and recourse needs a persistent verifier and a pinned root (modality law)"
            )
        # Reuse the RecourseEnvelope forum law (Pin-the-Root) verbatim.
        from bulla.envelope import EnvelopeError, Forum
        try:
            Forum(
                log_endpoint=forum.get("log_endpoint", ""),
                trusted_root_ref=forum.get("trusted_root_ref", ""),
            )
        except EnvelopeError as exc:
            raise ActionReceiptError(f"convention {c['name']!r}: {exc}") from exc
        defn = c.get("definition")
        if defn is not None:
            if not isinstance(defn, str):
                raise ActionReceiptError(
                    f"convention {c['name']!r}: a semantic definition, when inlined, is an opaque string"
                )
            if convention_definition_hash(defn) != dh:
                raise ActionReceiptError(
                    f"convention {c['name']!r}: definition_hash does not match the in-line definition"
                )


def check_convention_conformance(convention: dict, subject: dict) -> tuple[str, list[str]]:
    """Recompute one convention's verdict against the act's declared subject.

    Returns ``(status, reasons)`` with status ``conforms`` / ``violates`` for
    executable conventions and ``pinned`` for semantic ones (whose enforcement
    is the named forum, not this function). Assumes the convention already
    passed :func:`_validate_convention`.
    """
    if convention.get("kind") == "semantic":
        return "pinned", []
    # Executable conformance is single-sourced in bulla.executable_form.check_definition
    # — the same evaluator ``bounds_conformance`` uses over a scope predicate.
    return check_definition(convention["definition"], subject)


def effective_grounding(evidence_refs: tuple[dict, ...] | list[dict]) -> str | None:
    """The display rule (spec v0.2 §1): the minimum grounding class over the
    receipt's necessary evidence (v0.2 treats all carried evidence as
    necessary). ``None`` when no evidence carries a grounding (a v0.1
    receipt) — unspecified, not assumed."""
    ranks = [
        GROUNDING_CLASSES.index(e["grounding"])
        for e in evidence_refs
        if e.get("grounding") in GROUNDING_CLASSES
    ]
    if not ranks or len(ranks) != len(list(evidence_refs)):
        return None
    return GROUNDING_CLASSES[min(ranks)]


# ── mandate / remedy / retention  <->  RecourseEnvelope ──────────────────────
#
# The envelope is the single source of truth (validation + attestation preimage).
# These two projections give the receipt its grokkable top-level keys — a
# stranger reading the JSON sees `mandate` (before) and `remedy` (after) without
# reading a spec — while all validation still flows through RecourseEnvelope.

def _envelope_views(env: RecourseEnvelope) -> dict:
    mandate: dict = {}
    # The envelope's schema version must survive the view round-trip (a v0.3
    # envelope carries structured delegation grants). Conditional-include keeps
    # v0.2 receipts byte-identical; the views are never hashed (the attestation
    # preimage uses env.to_dict() directly), so this changes no hash.
    if env.deed_schema != "0.2":
        mandate["deed_schema"] = env.deed_schema
    if env.authority is not None:
        mandate["authority"] = env.authority.to_dict()
    if env.bounds is not None:
        mandate["bounds"] = env.bounds.to_dict()
    remedy: dict = env.recourse.to_dict() if env.recourse is not None else {}
    retention: dict = {}
    if env.retention_class is not None:
        retention["record"] = env.retention_class
    if env.disclosure_class is not None:
        retention["disclosure"] = env.disclosure_class
    return {"mandate": mandate, "remedy": remedy, "retention": retention}


def _views_to_envelope(mandate: dict, remedy: dict, retention: dict) -> RecourseEnvelope:
    """Reconstruct (and thereby re-validate) the envelope from the receipt's
    named views. Any modality-law violation raises here."""
    ed: dict = {
        "deed_schema": (mandate or {}).get("deed_schema")
        or RecourseEnvelope.__dataclass_fields__["deed_schema"].default
    }
    if mandate.get("authority"):
        ed["authority"] = mandate["authority"]
    if mandate.get("bounds"):
        ed["bounds"] = mandate["bounds"]
    if remedy:
        ed["recourse"] = remedy
    if retention.get("record"):
        ed["retention_class"] = retention["record"]
    if retention.get("disclosure"):
        ed["disclosure_class"] = retention["disclosure"]
    return RecourseEnvelope.from_dict(ed)


# ── the receipt ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionReceipt:
    """One consequential agent action, made accountable. Construct via
    :func:`build_action_receipt` (which computes the hashes); this dataclass
    holds the semantic fields and derives the four hashes deterministically."""

    action: dict                       # {"type": <open vocab>, "subject": {...}}
    diagnostic_ref: dict               # {"status": <DIAGNOSTIC_STATUSES>, "ref"?: "sha256:…"}
    envelope: RecourseEnvelope         # mandate (authority+bounds) + remedy (recourse) + retention
    anchor_ref: dict = field(default_factory=dict)   # {"kind": "git"|"pypi"|…, "ref": "…"}
    evidence_refs: tuple[dict, ...] = ()             # ({"name", "hash", "grounding"(0.2)}, …)
    conventions: tuple[dict, ...] = ()               # coined-at-the-seam rules; inside content hash
    signature: dict | None = None                    # detached ed25519/COSE proof over content hash
    authorization: dict | None = None                # detached proof over authorization_hash — binds the envelope
    stake: dict | None = None                        # RESERVED — collateral is an external sidecar
    timestamp: str = ""
    producer: dict = field(default_factory=dict)     # {"bulla_version": "…"} — provenance, not identity
    schema_version: str = SCHEMA_VERSION             # the version the PRODUCER spoke — in the preimage

    # ---- validation ----
    def __post_init__(self) -> None:
        if not isinstance(self.action, dict) or not (self.action.get("type") or "").strip():
            raise ActionReceiptError("action.type is required (the open-vocabulary act, e.g. 'package.release')")
        st = (self.diagnostic_ref or {}).get("status")
        if st not in DIAGNOSTIC_STATUSES:
            raise ActionReceiptError(
                f"diagnostic_ref.status must be one of {DIAGNOSTIC_STATUSES} (never bare null — "
                "the verdict slot is the first leg of the triad)"
            )
        if st == "reference" and not (self.diagnostic_ref.get("ref") or "").strip():
            raise ActionReceiptError("diagnostic_ref.status=='reference' requires a 'ref' (the recomputable verdict)")
        if self.schema_version not in ("0.1", "0.2", AUTHORIZATION_SCHEMA_VERSION):
            raise ActionReceiptError(f"unknown schema_version {self.schema_version!r}")
        is_v02 = self.schema_version in ("0.2", AUTHORIZATION_SCHEMA_VERSION)
        for e in self.evidence_refs:
            if not (e.get("name") or "").strip() or not (e.get("hash") or "").strip():
                raise ActionReceiptError("every evidence_ref needs a name and a hash")
            g = e.get("grounding")
            if is_v02 and g not in GROUNDING_CLASSES:
                raise ActionReceiptError(
                    f"evidence_ref {e.get('name')!r}: v0.2 requires grounding "
                    f"∈ {GROUNDING_CLASSES} — the record inherits the grounding of "
                    "its weakest necessary anchor, so the class must be declared"
                )
            if not is_v02 and g is not None and g not in GROUNDING_CLASSES:
                raise ActionReceiptError(f"evidence_ref {e.get('name')!r}: unknown grounding {g!r}")
        if self.conventions and not is_v02:
            raise ActionReceiptError("conventions are a v0.2 field — bump schema_version")
        for c in self.conventions:
            _validate_convention(c)
        if self.stake is not None:
            raise ActionReceiptError("stake is RESERVED — collateral belongs in an external sidecar; must be None")
        if self.authorization is not None and not isinstance(self.authorization, dict):
            raise ActionReceiptError("authorization must be a detached proof dict (over authorization_hash) or None")
        if self.authorization is not None and self.schema_version != AUTHORIZATION_SCHEMA_VERSION:
            raise ActionReceiptError(
                "authorization is a v0.3 field; v0.1/v0.2 receipts cannot silently change wire semantics"
            )
        if not isinstance(self.envelope, RecourseEnvelope):
            raise ActionReceiptError("envelope must be a RecourseEnvelope (mandate+remedy)")

    # ---- the four hashes ----
    def _content_preimage(self) -> dict:
        """The recomputable claim: act + verdict + evidence + anchor +
        conventions. Envelope-free (the recomputable core must not depend on
        the appeal path), time-free, signature-free — so it is identical on
        any conforming implementation for the receipt's own schema version.

        ``schema_version`` is the RECEIPT'S OWN (a v0.1 receipt recomputes
        with "0.1" forever). ``conventions`` enters the preimage whenever
        non-empty — a coined rule outside the content hash would be forgeable,
        and presence-vs-absence itself perturbs the hash, so a convention
        cannot be silently stripped either."""
        out: dict = {
            "schema_version": self.schema_version,
            "kind": RECEIPT_KIND,
            "action": self.action,
            "diagnostic_ref": self.diagnostic_ref,
            "evidence_refs": [dict(e) for e in self.evidence_refs],
            "anchor_ref": self.anchor_ref,
        }
        if self.conventions:
            out["conventions"] = [dict(c) for c in self.conventions]
        return out

    @property
    def content_hash(self) -> str:
        return _canon_hash(self._content_preimage())

    @property
    def event_hash(self) -> str:
        # the occurrence = the claim, at a time.
        return _canon_hash({"content_hash": self.content_hash, "timestamp": self.timestamp})

    @property
    def envelope_hash(self) -> str:
        """Canonical hash of the recourse envelope alone — the object an
        authorization proof binds. Derived, not stored: a verifier recomputes it
        from the served mandate/remedy/retention views, so it cannot be lied
        about independently of the envelope it summarizes."""
        return _canon_hash(self.envelope.to_dict())

    @property
    def authorization_hash(self) -> str:
        """*"Which mandate was authorized."* ``H({content_hash, envelope_hash})``
        — the recomputable claim bound to its appeal path. The issuer signs THIS
        (not ``content`` alone) to vouch for the envelope; swapping the envelope
        moves ``envelope_hash``, so a proof over ``authorization_hash`` stops
        verifying. Invariant to ``signature``/``authorization`` (both excluded
        from the content and envelope preimages), so it is stable to sign against
        before either proof exists."""
        return _canon_hash({"content_hash": self.content_hash, "envelope_hash": self.envelope_hash})

    @property
    def attestation_hash(self) -> str:
        # commitment to {content, signer, recourse envelope, authority proof} —
        # mirrors certificate._attestation_hash's discipline; anchorable identity.
        # v0.3 always includes the authorization slot (including null) so the
        # field cannot be stripped without changing the attestation hash.
        # v0.1/v0.2 retain their historical preimage byte-for-byte.
        preimage: dict = {"content_hash": self.content_hash, "signature": self.signature}
        preimage["recourse_envelope"] = self.envelope.to_dict()
        if self.schema_version == AUTHORIZATION_SCHEMA_VERSION:
            preimage["authorization"] = self.authorization
        return _canon_hash(preimage)

    @property
    def log_leaf(self) -> str:
        return _leaf_hash(self.attestation_hash.encode("utf-8"))

    def hashes(self) -> dict:
        return {
            "content": self.content_hash,
            "event": self.event_hash,
            "attestation": self.attestation_hash,
            "log_leaf": self.log_leaf,
        }

    # ---- serialization ----
    def to_dict(self) -> dict:
        views = _envelope_views(self.envelope)
        out: dict = {
            "schema_version": self.schema_version,
            "kind": RECEIPT_KIND,
            "action": self.action,
            "diagnostic_ref": self.diagnostic_ref,
            "evidence_refs": [dict(e) for e in self.evidence_refs],
            "anchor_ref": self.anchor_ref,
            "mandate": views["mandate"],
            "remedy": views["remedy"],
            "retention": views["retention"],
            "stake": self.stake,                 # reserved (None)
            "conventions": [dict(c) for c in self.conventions],
            "signature": self.signature,
        }
        if self.schema_version == AUTHORIZATION_SCHEMA_VERSION:
            out["authorization"] = self.authorization
        out.update({
            "timestamp": self.timestamp,
            "producer": self.producer,
            "hashes": self.hashes(),
        })
        return out

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ActionReceipt":
        """Reconstruct (and re-validate) a receipt from a serialized dict. The
        envelope is rebuilt through RecourseEnvelope.from_dict, so the modality
        law is re-enforced on served data. Does NOT check the stored hashes —
        that is :func:`verify_receipt`."""
        if not isinstance(d, dict):
            raise ActionReceiptError("receipt must be a dict")
        if d.get("kind") != RECEIPT_KIND:
            raise ActionReceiptError(f"not an {RECEIPT_KIND} (kind={d.get('kind')!r})")
        schema_version = d.get("schema_version") or SCHEMA_VERSION
        if "authorization" in d and schema_version != AUTHORIZATION_SCHEMA_VERSION:
            raise ActionReceiptError("authorization member is only valid in schema_version '0.3'")
        if schema_version == AUTHORIZATION_SCHEMA_VERSION and "authorization" not in d:
            raise ActionReceiptError("v0.3 receipt is missing its authorization member")
        env = _views_to_envelope(
            d.get("mandate") or {}, d.get("remedy") or {}, d.get("retention") or {}
        )
        return cls(
            action=d.get("action") or {},
            diagnostic_ref=d.get("diagnostic_ref") or {},
            envelope=env,
            anchor_ref=d.get("anchor_ref") or {},
            evidence_refs=tuple(d.get("evidence_refs") or ()),
            conventions=tuple(d.get("conventions") or ()),
            signature=d.get("signature"),
            authorization=d.get("authorization"),
            stake=d.get("stake"),
            timestamp=d.get("timestamp") or "",
            producer=d.get("producer") or {},
            schema_version=schema_version,
        )


def build_action_receipt(
    *,
    action: dict,
    diagnostic_ref: dict,
    envelope: RecourseEnvelope,
    anchor_ref: dict | None = None,
    evidence_refs: tuple[dict, ...] | list[dict] = (),
    conventions: tuple[dict, ...] | list[dict] = (),
    signature: dict | None = None,
    timestamp: str = "",
    producer: dict | None = None,
) -> ActionReceipt:
    """Assemble a validated ActionReceipt. Signing is out of band: pass a
    detached ``signature`` (a proof over ``content_hash`` from
    ``bulla.identity.LocalEd25519Signer``) to raise verification to the
    ``attestation`` depth; without it the receipt still verifies to ``digest``.

    ``conventions`` entries may omit ``definition_hash``; it is computed here
    (the pin a stranger recomputes). Served receipts must carry it."""
    filled: list[dict] = []
    for c in conventions:
        c = dict(c)
        if "definition_hash" not in c and "definition" in c:
            c["definition_hash"] = convention_definition_hash(c["definition"])
        filled.append(c)
    return ActionReceipt(
        action=dict(action),
        diagnostic_ref=dict(diagnostic_ref),
        envelope=envelope,
        anchor_ref=dict(anchor_ref or {}),
        evidence_refs=tuple(dict(e) for e in evidence_refs),
        conventions=tuple(filled),
        signature=signature,
        timestamp=timestamp,
        producer=dict(producer or {}),
    )


def sign_action_receipt(receipt: ActionReceipt, signer: Any) -> ActionReceipt:
    """Sign a receipt at full depth: a **content** proof (verdict authenticity)
    AND an **authorization** proof (authority authenticity). Returns a new
    receipt carrying both.

    The two proofs answer different questions — *is the claim as signed?* and
    *is the mandate/remedy as signed?* — and :func:`verify_receipt` reports them
    separately. Signing only the content (``signature=signer.sign(content_hash)``
    passed to ``build_*``) leaves the envelope unauthenticated: a verifier will
    surface ``authority_authentic='unauthenticated'``. Use this helper whenever
    the mandate matters, so a swapped envelope under a valid content signature is
    caught as forgery rather than sailing through.

    ``signer`` is any object with ``sign_domain(purpose, hash) -> proof``
    (:class:`bulla.identity.LocalEd25519Signer`). v0.3 proofs are
    **domain-separated**: content and authorization sign a canonical
    ``{context, schema, purpose, digest}`` preimage, so neither can be replayed as
    the other regardless of digest. Both hashes are invariant to the proofs
    themselves, so the returned receipt's stored proofs verify."""
    import dataclasses

    if receipt.schema_version not in ("0.2", AUTHORIZATION_SCHEMA_VERSION):
        raise ActionReceiptError(
            "full authority signing upgrades v0.2 to v0.3; older receipt schemas must be migrated explicitly"
        )
    unsigned = dataclasses.replace(
        receipt,
        schema_version=AUTHORIZATION_SCHEMA_VERSION,
        signature=None,
        authorization=None,
    )
    signature = signer.sign_domain("content", unsigned.content_hash)
    authorization = signer.sign_domain("authorization", unsigned.authorization_hash)
    return dataclasses.replace(unsigned, signature=signature, authorization=authorization)


# ── the two golden instances (same envelope, different action.type) ──────────
#
# These are NOT new types — a release IS a tool call (a side-effecting act), so
# `package.release` is a value of `action.type`, not a branch of a union. They
# are thin assemblers so the two canonical receipts read the same way.

def build_release_receipt(
    *,
    package: str,
    version: str,
    git_commit: str,
    git_tag: str,
    wheel_sha256: str,
    sdist_sha256: str,
    diagnostic_ref: dict,
    envelope: RecourseEnvelope,
    tree_hash: str | None = None,
    test_result: str | None = None,
    root_of_trust: dict | None = None,
    signature: dict | None = None,
    timestamp: str = "",
    producer: dict | None = None,
) -> ActionReceipt:
    """A ``package.release`` receipt — the genesis instance. Its root of trust is
    EXTERNAL: Bulla cannot vouch for the publication that introduces its own
    receipts (trusting-trust), so ``root_of_trust`` names the PEP 740 / Sigstore
    attestation ({scheme, publisher, integrity_api}) and
    ``verify_receipt`` binds — never replaces — that public anchor. Ships as CI
    plumbing, not a launch story."""
    subject: dict = {
        "package": package,
        "version": version,
        "git_commit": git_commit,
        "git_tag": git_tag,
    }
    if test_result is not None:
        subject["test_result"] = test_result
    # wheel/sdist/tree are held by systems the producer does not administer
    # (PyPI, the git remote) — third_party_anchored under the display rule.
    evidence: list[dict] = [
        {"name": "wheel", "hash": wheel_sha256, "grounding": "third_party_anchored"},
        {"name": "sdist", "hash": sdist_sha256, "grounding": "third_party_anchored"},
    ]
    if tree_hash:
        evidence.append({"name": "tree", "hash": tree_hash, "grounding": "third_party_anchored"})
    anchor: dict = {"kind": "pypi", "ref": f"{package} {version}"}
    if root_of_trust:
        anchor["root_of_trust"] = root_of_trust
    return build_action_receipt(
        action={"type": "package.release", "subject": subject},
        diagnostic_ref=diagnostic_ref,
        envelope=envelope,
        anchor_ref=anchor,
        evidence_refs=tuple(evidence),
        signature=signature,
        timestamp=timestamp,
        producer=producer,
    )


def build_tool_call_receipt(
    *,
    tool: str,
    call_subject: dict,
    diagnostic_ref: dict,
    envelope: RecourseEnvelope,
    result_hash: str | None = None,
    anchor_ref: dict | None = None,
    conventions: tuple[dict, ...] | list[dict] = (),
    signature: dict | None = None,
    timestamp: str = "",
    producer: dict | None = None,
) -> ActionReceipt:
    """A tool-call receipt — the audience-facing instance (e.g.
    ``tool="github.create_file"``). ``diagnostic_ref`` should carry the
    ``WitnessReceipt`` for the caller↔tool composition: the recomputable
    semantic-mismatch verdict is the whole point, and the reason a bond staked
    on this receipt can be slashed without an oracle.

    ``conventions`` carries rules the caller and tool coined at this seam —
    predicate invention with an audit trail. Each executable entry's
    conformance is recomputed against ``call_subject`` by any verifier."""
    evidence: tuple[dict, ...] = ()
    if result_hash:
        # the producer's own record of what came back — testimony until an
        # independent anchor holds it.
        evidence = ({"name": "result", "hash": result_hash, "grounding": "self_asserted"},)
    return build_action_receipt(
        action={"type": tool, "subject": dict(call_subject)},
        diagnostic_ref=diagnostic_ref,
        envelope=envelope,
        anchor_ref=dict(anchor_ref or {}),
        evidence_refs=evidence,
        conventions=conventions,
        signature=signature,
        timestamp=timestamp,
        producer=producer,
    )


# ── verification (one verifier, honest about depth) ──────────────────────────

VERIFY_LEVELS = ("none", "digest", "attestation", "log_inclusion")


@dataclass(frozen=True)
class ReceiptVerification:
    """The result of verifying a receipt. ``verified_to`` is the highest rung
    reached — the recourse ladder appearing in its own verifier, honest about
    depth instead of collapsing three assurance levels into one lying boolean."""

    ok: bool
    verified_to: str            # one of VERIFY_LEVELS
    checks: dict                # name -> bool
    reasons: tuple[str, ...]    # human-readable failures / notes
    #: name -> "conforms" | "violates" | "pinned" — recomputed per convention.
    #: A violation is a verdict about the ACT, surfaced next to (never folded
    #: into) hash integrity: the record of a non-conforming act is still a
    #: valid record.
    conventions: dict = field(default_factory=dict)
    #: The display rule: minimum grounding class over carried evidence, or
    #: None when unspecified (v0.1). A digest-valid receipt whose necessary
    #: evidence is self_asserted is attested testimony, nothing more.
    effective_grounding: str | None = None
    #: Authority authenticity — is the mandate/remedy the issuer vouched for?
    #: Distinct from ``checks['signature']`` (content authenticity). One of:
    #:   ``verified``        — an authorization proof binds this envelope and checks out;
    #:   ``forged``          — an authorization proof is present but does NOT bind
    #:                         this envelope (a swapped mandate/remedy) → ``ok`` is False;
    #:   ``unauthenticated`` — the receipt carries an envelope but no authorization
    #:                         proof; content may be signed, authority is not — do
    #:                         not present the envelope as issuer-vouched;
    #:   ``unresolved``      — a proof is present but bulla[identity] is absent;
    #:   ``not_applicable``  — the envelope carries no authority/bounds/recourse.
    authority_authentic: str = "not_applicable"
    #: Delegation verdicts (v0.3 structured grants) — SIX INDEPENDENT dimensions,
    #: never flattened. ``bulla.delegation.DelegationVerdict`` combines them into two
    #: named predicates: ``cryptographically_bound`` (chain + principal + policy +
    #: scope) is the bounded OFFLINE claim — "this principal delegated this exact
    #: declared capability to this key" — and ``fully_delegated``, which additionally
    #: demands positive temporal and revocation evidence and is therefore false today
    #: (revocation transport is unbuilt). ``policy_binding``/``scope_binding`` are hash
    #: agreement, NEVER a decision that the act obeys the policy. Default
    #: "not_applicable" — no structured delegation was claimed. See bulla.delegation.
    chain_integrity: str = "not_applicable"       # verified|broken|cycle|over_depth|not_applicable
    principal_binding: str = "not_applicable"     # verified|wrong_principal|unresolved|not_applicable
    policy_binding: str = "not_applicable"        # verified|mismatch|not_applicable
    scope_binding: str = "not_applicable"         # verified|mismatch|not_applicable
    temporal_status: str = "not_applicable"       # unresolved|within_window|expired|not_yet_valid|not_applicable
    revocation_status: str = "not_applicable"     # unresolved|not_revoked|revoked|not_applicable
    #: Did the ACT obey its declared scope? Recomputed at the digest rung (crypto-free)
    #: when ``bounds.scope`` is a structured ``jsonschema+quantum/1`` predicate over
    #: ``action.subject``. This is the missing half of authorization: ``scope_binding``
    #: proves the chain CONVEYED scope S (hash agreement); ``bounds_conformance`` proves
    #: the act was WITHIN S (predicate recompute). "not_applicable" for a prose scope;
    #: "not_checkable" when a structured scope has no ``action.subject`` to evaluate.
    bounds_conformance: str = "not_applicable"    # conforms|violates|not_checkable|not_applicable

    def __bool__(self) -> bool:
        # A ReceiptVerification has NO single truth value, and the most natural
        # misuse — ``if verify_receipt(d): ...`` — would otherwise be unconditionally
        # true (a plain object is always truthy), silently accepting a receipt that
        # FAILED. Raise instead, numpy-style, so the footgun dies at the first test
        # rather than in production. A record can be authentic yet unauthorized, or
        # hash-valid yet unsigned; these are separate questions and must be asked by name.
        raise TypeError(
            "The truth value of a ReceiptVerification is ambiguous — do not write "
            "`if verify_receipt(...):`. Read `.ok` for record integrity, the named "
            "dimensions (`authority_authentic`, `scope_binding`, `bounds_conformance`, …) "
            "for what each proves, or call `bulla.reliance.decide(v, policy)` for a "
            "rely/refuse/escalate answer."
        )

    def to_dict(self) -> dict:
        """The reliance-decision surface, serializable and pinnable (the raw object
        cannot be hashed — ``checks``/``conventions`` are dicts). This is the view a
        ``bulla.reliance`` policy decides over, and what a ``bulla.rely`` receipt pins;
        ``reasons`` (human text) and ``checks`` (internal booleans) are omitted."""
        return {
            "ok": self.ok,
            "verified_to": self.verified_to,
            "authority_authentic": self.authority_authentic,
            "effective_grounding": self.effective_grounding,
            "conventions": dict(self.conventions),
            "chain_integrity": self.chain_integrity,
            "principal_binding": self.principal_binding,
            "policy_binding": self.policy_binding,
            "scope_binding": self.scope_binding,
            "temporal_status": self.temporal_status,
            "revocation_status": self.revocation_status,
            "bounds_conformance": self.bounds_conformance,
        }

    def summary(self) -> str:
        head = "OK" if self.ok else "FAIL"
        s = f"{head}  verified_to={self.verified_to}  authority={self.authority_authentic}"
        if self.chain_integrity != "not_applicable" or self.principal_binding != "not_applicable":
            s += (f"  delegation[chain={self.chain_integrity} principal={self.principal_binding} "
                  f"policy={self.policy_binding} scope={self.scope_binding} "
                  f"temporal={self.temporal_status} revoc={self.revocation_status}]")
        if self.bounds_conformance != "not_applicable":
            s += f"  bounds_conformance={self.bounds_conformance}"
        return s


def verify_receipt(d: dict, *, public_key: bytes | None = None) -> ReceiptVerification:
    """Verify an ActionReceipt dict. Fails closed and reports how far it got:

      digest         — hashes recompute, envelope re-validates (modality law),
                       evidence well-formed, verdict slot non-null. Zero deps.
      attestation    — + the detached ``signature`` over ``content_hash`` and/or
                       the ``authorization`` proof over ``authorization_hash``
                       verify (needs ``bulla[identity]``; skipped, not failed,
                       if the receipt is unsigned or the extra is absent).
      log_inclusion  — + an external inclusion proof verifies. v0.2 has no such
                       proof inline; reaching this rung is the ``bulla[sigstore]``
                       follow-up. Reported, never faked.

    Alongside the rung it reports (never folds in): ``effective_grounding``
    (the minimum class over carried evidence — the display rule); per
    convention, a recomputed ``conforms`` / ``violates`` / ``pinned`` status;
    and ``authority_authentic`` — whether the mandate/remedy envelope is the one
    the issuer signed. Content authenticity (``checks['signature']``) and
    authority authenticity are separate verdicts: a valid content signature over
    a **swapped** envelope is caught here as ``authority_authentic='forged'``.
    """
    checks: dict = {}
    reasons: list[str] = []

    # ---- digest rung ----
    try:
        receipt = ActionReceipt.from_dict(d)  # re-runs schema + modality law
        checks["envelope_valid"] = True
    except (ActionReceiptError, EnvelopeError) as exc:
        return ReceiptVerification(False, "none", {"envelope_valid": False}, (str(exc),))

    stored = d.get("hashes") or {}
    recomputed = receipt.hashes()
    for name in ("content", "event", "attestation", "log_leaf"):
        got, want = recomputed[name], stored.get(name)
        ok = isinstance(want, str) and (got == want)
        checks[f"hash_{name}"] = ok
        if not ok:
            if want is None:
                reasons.append(f"{name} hash missing from served receipt")
            else:
                reasons.append(f"{name} hash mismatch: recomputed {got} != stored {want}")
    digest_ok = all(checks[f"hash_{n}"] for n in ("content", "event", "attestation", "log_leaf"))
    if not digest_ok:
        return ReceiptVerification(False, "none", checks, tuple(reasons))

    # ---- surfaced verdicts (about the ACT, not the record's integrity) ----
    grounding = effective_grounding(receipt.evidence_refs)
    if grounding == "self_asserted":
        reasons.append(
            "effective grounding: self_asserted — every necessary anchor is the "
            "actor's own testimony; do not present this as more than attested testimony"
        )
    conv_status: dict = {}
    subject = receipt.action.get("subject") or {}
    for c in receipt.conventions:
        status, why = check_convention_conformance(c, subject)
        conv_status[c["name"]] = status
        if status == "violates":
            reasons.append(
                f"convention {c['name']!r}: act does not conform — " + "; ".join(why)
            )

    # ---- bounds_conformance: did the ACT obey its declared scope? (digest rung) ----
    # Crypto-free — a pure recompute of action.subject against a structured bounds.scope
    # predicate, surfaced (never folded into ``ok``) exactly like convention conformance.
    # This is the missing half of authorization; ``scope_binding`` (delegation) proves the
    # chain conveyed the scope, this proves the act stayed within it.
    bounds_conf = "not_applicable"
    _bounds = receipt.envelope.bounds
    if _bounds is not None and isinstance(_bounds.scope, dict):
        raw_subject = receipt.action.get("subject")
        if not isinstance(raw_subject, dict):
            bounds_conf = "not_checkable"
            reasons.append(
                "bounds.scope is a structured predicate but action.subject is absent — "
                "cannot recompute conformance (not_checkable)"
            )
        else:
            bounds_conf, why = check_definition(_bounds.scope, raw_subject)
            if bounds_conf == "violates":
                reasons.append(
                    "act exceeds its declared bounds.scope — " + "; ".join(why)
                )

    def done(ok: bool, rung: str, authority: str, deleg: Any = None) -> ReceiptVerification:
        dims = {
            "chain_integrity": "not_applicable",
            "principal_binding": "not_applicable",
            "policy_binding": "not_applicable",
            "scope_binding": "not_applicable",
            "temporal_status": "not_applicable",
            "revocation_status": "not_applicable",
        }
        if deleg is not None:
            dims = deleg.to_dict()
        return ReceiptVerification(
            ok, rung, checks, tuple(reasons),
            conventions=conv_status, effective_grounding=grounding,
            authority_authentic=authority, bounds_conformance=bounds_conf, **dims,
        )

    # ---- attestation rung: content authenticity AND authority authenticity ----
    sig = receipt.signature
    auth_proof = receipt.authorization
    is_v03 = receipt.schema_version == AUTHORIZATION_SCHEMA_VERSION
    env_dict = receipt.envelope.to_dict()
    # Almost every ActionReceipt carries a non-trivial envelope (the modality law
    # requires remedies), so an unsigned envelope is the common case to flag.
    envelope_nontrivial = bool(
        env_dict.get("authority") or env_dict.get("bounds") or env_dict.get("recourse")
    )
    default_authority = "unauthenticated" if envelope_nontrivial else "not_applicable"

    if not sig and not auth_proof:
        if envelope_nontrivial:
            reasons.append(
                "authority unauthenticated — no issuer authorization proof binds this "
                "mandate/remedy; do not present the envelope as issuer-vouched"
            )
        reasons.append("unsigned receipt — verified to digest only (no signature to check)")
        return done(True, "digest", default_authority)

    if auth_proof and not sig:
        checks["proof_pair"] = False
        reasons.append(
            "authorization proof present without the required content signature — "
            "refusing an incomplete full-depth proof pair"
        )
        return done(False, "digest", "forged")

    if is_v03 and sig and not auth_proof and envelope_nontrivial:
        checks["authorization_required"] = False
        reasons.append(
            "v0.3 authorization proof missing — refusing a downgrade from the "
            "full content+envelope proof pair"
        )
        return done(False, "digest", "unauthenticated")

    try:
        from bulla.identity import verify_proof, verify_proof_domain
        import nacl.signing  # noqa: F401 — prove the optional crypto backend exists
    except ImportError:  # pragma: no cover - identity extra absent
        reasons.append("proof present but bulla[identity] not installed — verified to digest only")
        return done(True, "digest", "unresolved" if auth_proof else default_authority)

    def _vproof(purpose: str, digest: str, proof: dict):
        # v0.3 proofs are domain-separated (purpose in the signed bytes); v0.2
        # proofs sign the raw digest string. Dispatch on the receipt's own version.
        if is_v03:
            return verify_proof_domain(purpose, digest, proof, public_key=public_key)
        return verify_proof(digest, proof, public_key=public_key)

    # content authenticity — the claim/verdict (over content_hash)
    if sig:
        auth = _vproof("content", receipt.content_hash, sig)
        checks["signature"] = bool(getattr(auth, "authentic", False))
        if not checks["signature"]:
            reasons.append(
                f"signature not authentic ({getattr(auth, 'method', '?')}: "
                f"{getattr(auth, 'detail', None) or 'not authentic'})"
            )
            return done(False, "digest", default_authority)

    # authority authenticity — the mandate/remedy (over authorization_hash)
    authority_status = default_authority
    if auth_proof:
        aauth = _vproof("authorization", receipt.authorization_hash, auth_proof)
        checks["authorization"] = bool(getattr(aauth, "authentic", False))
        if not checks["authorization"]:
            reasons.append(
                "authority not authentic — the authorization proof does not bind this "
                f"envelope ({getattr(aauth, 'method', '?')}: "
                f"{getattr(aauth, 'detail', None) or 'not authentic'}); the mandate/remedy "
                "may have been swapped after signing"
            )
            return done(False, "digest", "forged")
        # Both proofs must be made by the same declared signing identity. Domain
        # separation stops cross-purpose replay; this stops signer-substitution —
        # an attacker retaining the honest content signature and attaching their
        # OWN valid authorization signature over a swapped envelope.
        signer_fields = ("type", "issuer", "verificationMethod")
        same_signer = all(sig.get(k) == auth_proof.get(k) for k in signer_fields)
        checks["authorization_same_signer"] = same_signer
        if not same_signer:
            reasons.append(
                "authority not authentic — content and authorization proofs name "
                "different signing identities (possible signer-substitution attack)"
            )
            return done(False, "digest", "forged")
        authority_status = "verified"
    elif envelope_nontrivial:
        reasons.append(
            "authority unauthenticated — content is signed but no authorization proof "
            "binds the mandate/remedy; verify the envelope out of band before relying on it"
        )

    # ---- delegation (v0.3 structured grants) — SURFACED, never folded into ok ----
    # The six dimensions are independent and reported for a relying party to
    # combine; a broken/mismatched chain does not change the record's integrity
    # verdict, exactly like grounding and convention conformance. See bulla.delegation.
    deleg = None
    env_authority = receipt.envelope.authority
    if is_v03 and receipt.envelope.deed_schema == "0.3" and env_authority is not None:
        from bulla.delegation import verify_delegation
        leaf_vm = (sig or {}).get("verificationMethod")
        env_bounds = receipt.envelope.bounds
        # `public_key` is deliberately NOT forwarded: it is the caller's override for
        # THIS receipt's signer, and reusing it upstream would let one supplied key
        # authenticate every grantor in the chain. Each grant's key is derived from
        # its own `grantor` did:key inside verify_delegation.
        deleg = verify_delegation(
            env_authority.delegation,
            principal=env_authority.principal,
            policy_ref=env_authority.policy,
            scope_ref=env_bounds.scope if env_bounds is not None else None,
            leaf_verification_method=leaf_vm,
        )
        for r in deleg.reasons:
            reasons.append("delegation: " + r)

    # ---- log_inclusion rung (no inline proof; honestly reported) ----
    reasons.append(
        "verified to attestation — log_inclusion (external Rekor/registry proof) "
        "is the bulla[sigstore] follow-up; no inline proof present"
    )
    return done(True, "attestation", authority_status, deleg)
