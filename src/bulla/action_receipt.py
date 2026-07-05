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

from bulla.envelope import EnvelopeError, RecourseEnvelope

SCHEMA_VERSION = "0.1"
RECEIPT_KIND = "action_receipt"

#: ``diagnostic_ref`` is never bare ``null`` — the ambiguity between "no
#: composition existed to diagnose" and "we skipped it" is exactly where the
#: verdict leg of the triad erodes. A missing verdict must say *why*.
DIAGNOSTIC_STATUSES = ("reference", "not_applicable", "deferred")

_LEAF = b"\x00"  # RFC 6962 leaf prefix — wire-compatible with bulla.registry.leaf_hash


def _sha(b: bytes) -> str:
    return f"sha256:{hashlib.sha256(b).hexdigest()}"


def _canon(obj: Any) -> str:
    """The one canonicalization rule (shared with certificate.py / envelope.py):
    ``json.dumps(obj, sort_keys=True, separators=(",", ":"))``. Documented in the
    spec so a second implementer reproduces every hash without our source."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _canon_hash(obj: Any) -> str:
    return _sha(_canon(obj).encode("utf-8"))


def _leaf_hash(data: bytes) -> str:
    """RFC 6962 leaf hash ``H(0x00 ‖ data)`` — same bytes as
    ``bulla.registry.leaf_hash``, inlined so this stays a light leaf module."""
    return _sha(_LEAF + data)


class ActionReceiptError(ValueError):
    """Raised when a receipt violates its schema or an invariant."""


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
    evidence_refs: tuple[dict, ...] = ()             # ({"name": str, "hash": "sha256:…"}, …)
    signature: dict | None = None                    # detached ed25519/COSE proof over content hash
    stake: dict | None = None                        # RESERVED (the bond slot) — must be None in v0.1
    timestamp: str = ""
    producer: dict = field(default_factory=dict)     # {"bulla_version": "…"} — provenance, not identity

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
        for e in self.evidence_refs:
            if not (e.get("name") or "").strip() or not (e.get("hash") or "").strip():
                raise ActionReceiptError("every evidence_ref needs a name and a hash")
        if self.stake is not None:
            raise ActionReceiptError("stake is RESERVED in v0.1 (the bond slot) — must be None")
        if not isinstance(self.envelope, RecourseEnvelope):
            raise ActionReceiptError("envelope must be a RecourseEnvelope (mandate+remedy)")

    # ---- the four hashes ----
    def _content_preimage(self) -> dict:
        """The recomputable claim: act + verdict + evidence + anchor. Envelope-
        free (the recomputable core must not depend on the appeal path),
        time-free, signature-free — so it is identical on any machine, any
        version, forever."""
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": RECEIPT_KIND,
            "action": self.action,
            "diagnostic_ref": self.diagnostic_ref,
            "evidence_refs": [dict(e) for e in self.evidence_refs],
            "anchor_ref": self.anchor_ref,
        }

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
            "schema_version": SCHEMA_VERSION,
            "kind": RECEIPT_KIND,
            "action": self.action,
            "diagnostic_ref": self.diagnostic_ref,
            "evidence_refs": [dict(e) for e in self.evidence_refs],
            "anchor_ref": self.anchor_ref,
            "mandate": views["mandate"],
            "remedy": views["remedy"],
            "retention": views["retention"],
            "stake": self.stake,                 # reserved (None)
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
            signature=d.get("signature"),
            stake=d.get("stake"),
            timestamp=d.get("timestamp") or "",
            producer=d.get("producer") or {},
        )


def build_action_receipt(
    *,
    action: dict,
    diagnostic_ref: dict,
    envelope: RecourseEnvelope,
    anchor_ref: dict | None = None,
    evidence_refs: tuple[dict, ...] | list[dict] = (),
    signature: dict | None = None,
    timestamp: str = "",
    producer: dict | None = None,
) -> ActionReceipt:
    """Assemble a validated ActionReceipt. Signing is out of band: pass a
    detached ``signature`` (a proof over ``content_hash`` from
    ``bulla.identity.LocalEd25519Signer``) to raise verification to the
    ``attestation`` depth; without it the receipt still verifies to ``digest``."""
    return ActionReceipt(
        action=dict(action),
        diagnostic_ref=dict(diagnostic_ref),
        envelope=envelope,
        anchor_ref=dict(anchor_ref or {}),
        evidence_refs=tuple(dict(e) for e in evidence_refs),
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
    evidence: list[dict] = [
        {"name": "wheel", "hash": wheel_sha256},
        {"name": "sdist", "hash": sdist_sha256},
    ]
    if tree_hash:
        evidence.append({"name": "tree", "hash": tree_hash})
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
    signature: dict | None = None,
    timestamp: str = "",
    producer: dict | None = None,
) -> ActionReceipt:
    """A tool-call receipt — the audience-facing instance (e.g.
    ``tool="github.create_file"``). ``diagnostic_ref`` should carry the
    ``WitnessReceipt`` for the caller↔tool composition: the recomputable
    semantic-mismatch verdict is the whole point, and the reason a bond staked
    on this receipt can be slashed without an oracle."""
    evidence: tuple[dict, ...] = ()
    if result_hash:
        evidence = ({"name": "result", "hash": result_hash},)
    return build_action_receipt(
        action={"type": tool, "subject": dict(call_subject)},
        diagnostic_ref=diagnostic_ref,
        envelope=envelope,
        anchor_ref=dict(anchor_ref or {}),
        evidence_refs=evidence,
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
      log_inclusion  — + an external inclusion proof verifies. v0.1 has no such
                       proof inline; reaching this rung is the ``bulla[sigstore]``
                       follow-up. Reported, never faked.
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

    # ---- attestation rung ----
    sig = receipt.signature
    if not sig:
        reasons.append("unsigned receipt — verified to digest only (no signature to check)")
        return ReceiptVerification(True, "digest", checks, tuple(reasons))
    try:
        from bulla.identity import verify_proof
    except Exception:  # pragma: no cover - identity extra absent
        reasons.append("signature present but bulla[identity] not installed — verified to digest only")
        return ReceiptVerification(True, "digest", checks, tuple(reasons))
    auth = verify_proof(receipt.content_hash, sig, public_key=public_key)
    checks["signature"] = bool(getattr(auth, "authentic", False))
    if not checks["signature"]:
        reasons.append(
            f"signature not authentic ({getattr(auth, 'method', '?')}: "
            f"{getattr(auth, 'detail', None) or 'not authentic'})"
        )
        return ReceiptVerification(False, "digest", checks, tuple(reasons))

    # ---- log_inclusion rung (v0.1: no inline proof; honestly reported) ----
    reasons.append(
        "verified to attestation — log_inclusion (external Rekor/registry proof) "
        "is the bulla[sigstore] follow-up; no inline proof present"
    )
    return ReceiptVerification(True, "attestation", checks, tuple(reasons))
