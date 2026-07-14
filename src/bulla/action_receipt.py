"""ActionReceipt v0.1 — the receipt for a consequential agent action.

Bulla's diagnostic layer answers "is this composition coherent?" (`WitnessReceipt`).
This module answers the next question: **an agent just changed the world — write
a file, publish a package, move a record — under whose authority, within what
bounds, with what verdict, and how is it contested?** That record is the substrate
a bond will one day slash against: you cannot collect a remedy from an ephemeral
actor, only from an artifact or a stake, and only against an *adjudicable record*
of what was promised and what happened. The receipt is that record.

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
                      recourse envelope}. Mirrors ``certificate._attestation_hash``
                      byte-for-byte in discipline; the signed, anchorable identity.
  - ``log_leaf``    — "where logged": the RFC 6962 leaf (``H(0x00‖…)``) of the
                      attestation hash, ready to append to a ``DeedLog``.

RESERVED. ``stake`` (the bond slot) is declared and must be ``None`` in v0.1 —
the field format ships now; the slashing mechanism is gated on a real
cross-boundary counterparty, not built here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from bulla._canonical import canonical_json
from bulla.envelope import EnvelopeError, RecourseEnvelope

SCHEMA_VERSION = "0.2"
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

#: The one executable-definition form of v0.2 — a JSON-schema constraint
#: subset plus an integer unit/quantum declaration. Deliberately NOT a general
#: language: every keyword below is decidable with a stdlib-only verifier.
EXECUTABLE_FORM = "jsonschema+quantum/1"

#: The closed keyword vocabulary of ``jsonschema+quantum/1``. A definition
#: using any other keyword is malformed — fail closed, never guess.
_SCHEMA_TOP_KEYS = frozenset({"type", "properties", "required", "additionalProperties"})
_SCHEMA_PROP_KEYS = frozenset({"type", "enum", "const", "minimum", "maximum", "pattern"})
_SCHEMA_PROP_TYPES = frozenset({"string", "integer", "number", "boolean"})

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


def convention_definition_hash(definition: Any) -> str:
    """The pin: ``sha256:`` over ``canonical_json`` for a structured
    (executable) definition, over raw UTF-8 bytes for an opaque (semantic)
    definition string."""
    if isinstance(definition, str):
        return _sha(definition.encode("utf-8"))
    return _canon_hash(definition)


def _validate_executable_definition(defn: Any) -> None:
    """Well-formedness of a ``jsonschema+quantum/1`` definition — the closed
    vocabulary is validated at CONSTRUCTION so a malformed convention can
    never ride inside a hashed receipt."""
    if not isinstance(defn, dict):
        raise ActionReceiptError("executable convention definition must be an object")
    if defn.get("form") != EXECUTABLE_FORM:
        raise ActionReceiptError(
            f"executable convention definition.form must be {EXECUTABLE_FORM!r} "
            "(the one declared form of v0.2 — not a general language)"
        )
    extra = set(defn) - {"form", "schema", "quantum"}
    if extra:
        raise ActionReceiptError(f"unknown executable-definition keys {sorted(extra)} — fail closed")
    schema = defn.get("schema")
    if not isinstance(schema, dict):
        raise ActionReceiptError("executable convention needs a 'schema' object")
    unknown = set(schema) - _SCHEMA_TOP_KEYS
    if unknown:
        raise ActionReceiptError(f"unknown schema keywords {sorted(unknown)} — fail closed")
    if schema.get("type", "object") != "object":
        raise ActionReceiptError("schema.type must be 'object' (the act's subject)")
    for pname, pschema in (schema.get("properties") or {}).items():
        if not isinstance(pschema, dict):
            raise ActionReceiptError(f"schema.properties[{pname!r}] must be an object")
        bad = set(pschema) - _SCHEMA_PROP_KEYS
        if bad:
            raise ActionReceiptError(
                f"unknown keywords {sorted(bad)} in schema.properties[{pname!r}] — fail closed"
            )
        if "type" in pschema and pschema["type"] not in _SCHEMA_PROP_TYPES:
            raise ActionReceiptError(
                f"schema.properties[{pname!r}].type must be one of {sorted(_SCHEMA_PROP_TYPES)}"
            )
    quantum = defn.get("quantum")
    if quantum is not None:
        if not isinstance(quantum, dict):
            raise ActionReceiptError("quantum must map field name -> {unit, multipleOf}")
        for fname, q in quantum.items():
            if not isinstance(q, dict) or set(q) - {"unit", "multipleOf"}:
                raise ActionReceiptError(
                    f"quantum[{fname!r}] must be {{'unit': str, 'multipleOf': int}}"
                )
            if not (q.get("unit") or "").strip():
                raise ActionReceiptError(f"quantum[{fname!r}].unit is required")
            mo = q.get("multipleOf", 1)
            if not isinstance(mo, int) or isinstance(mo, bool) or mo < 1:
                raise ActionReceiptError(
                    f"quantum[{fname!r}].multipleOf must be a positive integer "
                    "(quantized fields are integers in minor units — decidable, no float ties)"
                )


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
    defn = convention["definition"]
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
    ed: dict = {"deed_schema": RecourseEnvelope.__dataclass_fields__["deed_schema"].default}
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
    stake: dict | None = None                        # RESERVED (the bond slot) — must be None
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
        if self.schema_version not in ("0.1", "0.2"):
            raise ActionReceiptError(f"unknown schema_version {self.schema_version!r}")
        is_v02 = self.schema_version == "0.2"
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
            raise ActionReceiptError("stake is RESERVED (the bond slot) — must be None")
        if not isinstance(self.envelope, RecourseEnvelope):
            raise ActionReceiptError("envelope must be a RecourseEnvelope (mandate+remedy)")

    # ---- the four hashes ----
    def _content_preimage(self) -> dict:
        """The recomputable claim: act + verdict + evidence + anchor +
        conventions. Envelope-free (the recomputable core must not depend on
        the appeal path), time-free, signature-free — so it is identical on
        any machine, any version, forever.

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
    def attestation_hash(self) -> str:
        # commitment to {content, signer, recourse envelope} — mirrors
        # certificate._attestation_hash's discipline; anchorable identity.
        preimage: dict = {"content_hash": self.content_hash, "signature": self.signature}
        preimage["recourse_envelope"] = self.envelope.to_dict()
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
            "timestamp": self.timestamp,
            "producer": self.producer,
            "hashes": self.hashes(),
        }
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
            stake=d.get("stake"),
            timestamp=d.get("timestamp") or "",
            producer=d.get("producer") or {},
            schema_version=d.get("schema_version") or SCHEMA_VERSION,
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
    attestation ({scheme, rekor_log_index, attestation_bundle_sha256}) and
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

    def summary(self) -> str:
        head = "OK" if self.ok else "FAIL"
        return f"{head}  verified_to={self.verified_to}"


def verify_receipt(d: dict, *, public_key: bytes | None = None) -> ReceiptVerification:
    """Verify an ActionReceipt dict. Fails closed and reports how far it got:

      digest         — hashes recompute, envelope re-validates (modality law),
                       evidence well-formed, verdict slot non-null. Zero deps.
      attestation    — + the detached signature over ``content_hash`` verifies
                       (needs ``bulla[identity]``; skipped, not failed, if the
                       receipt is unsigned or the extra is absent).
      log_inclusion  — + an external inclusion proof verifies. v0.2 has no such
                       proof inline; reaching this rung is the ``bulla[sigstore]``
                       follow-up. Reported, never faked.

    Alongside the rung it reports (never folds in): ``effective_grounding``
    (the minimum class over carried evidence — the display rule) and, per
    convention, a recomputed ``conforms`` / ``violates`` / ``pinned`` status.
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
        ok = (want is None) or (got == want)
        checks[f"hash_{name}"] = ok
        if not ok:
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

    def done(ok: bool, rung: str) -> ReceiptVerification:
        return ReceiptVerification(
            ok, rung, checks, tuple(reasons),
            conventions=conv_status, effective_grounding=grounding,
        )

    # ---- attestation rung ----
    sig = receipt.signature
    if not sig:
        reasons.append("unsigned receipt — verified to digest only (no signature to check)")
        return done(True, "digest")
    try:
        from bulla.identity import verify_proof
    except Exception:  # pragma: no cover - identity extra absent
        reasons.append("signature present but bulla[identity] not installed — verified to digest only")
        return done(True, "digest")
    auth = verify_proof(receipt.content_hash, sig, public_key=public_key)
    checks["signature"] = bool(getattr(auth, "authentic", False))
    if not checks["signature"]:
        reasons.append(
            f"signature not authentic ({getattr(auth, 'method', '?')}: "
            f"{getattr(auth, 'detail', None) or 'not authentic'})"
        )
        return done(False, "digest")

    # ---- log_inclusion rung (no inline proof; honestly reported) ----
    reasons.append(
        "verified to attestation — log_inclusion (external Rekor/registry proof) "
        "is the bulla[sigstore] follow-up; no inline proof present"
    )
    return done(True, "attestation")
