"""The recourse gate — turn a coherence deed into an ENFORCED, contestable decision.

Bulla's deed surface (sign / anchor / register / verify) lets a relying party *check*
a counterparty's coherence deed. This module lets it *act* on the check: refuse to
proceed on a transient counterparty's unverifiable deed, and emit a contestable
``RefusalCertificate`` the counterparty can CURE. It is the OBSERVE -> ENFORCE move.

``evaluate_gate`` is the single decision core. The proxy's advisory ``bulla__deed_verify``
calls it with ``ADVISORY_GATE_POLICY`` (the shipped behaviour: gate on inclusion +
root-trust + authenticity, *report* fee but do not block on it); the enforcing proxy
interceptor and ``bulla gate`` call it with ``DEFAULT_GATE_POLICY`` (additionally require
a full signed certificate proving ``fee == 0``). Only the fee arms differ, so there is
one decision implementation, not two that can drift.

What the gate gates on — TYPE signals only:
  * **coherence** — the deed's certificate must certify ``fee == 0`` (no undisclosed
    cross-owner convention). Trustworthy only from an integrity-verified certificate,
    because a deed *record* (a registry leaf) does not carry the fee.
  * **authenticity** — the certificate/deed is signed by the issuer it claims.
  * **inclusion under an independently-trusted root** — the deed is in a log whose root
    you pinned yourself (own-log / ``trusted_root`` / OTS-anchored), *never* the host's
    bare word. The inclusion check is **leaf-bound** (see ``registry.deed_leaf`` and the
    borrowed-inclusion fix, commit ccfbb23): a host cannot pair an authentic record with
    a valid proof for an *unrelated* leaf.

It does NOT touch the value half ("did the provider deliver V?") — that is the deferred
oracle and the bond rung. Coherence is necessary, not sufficient.

Refuse on the ADVERSARIAL property, not mere absence: an equivocating or host-asserting
operator is refused, not trusted. The test where the host is adversarial IS the property.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from bulla.model import Disposition

# ── deficiency taxonomy — the named reason a deed fails the gate, and the seed of a cure
OMITTED_FROM_LOG = "OMITTED_FROM_LOG"      # not in the registry at all (omission)
BORROWED_INCLUSION = "BORROWED_INCLUSION"  # proof covers a different leaf than this deed's
EQUIVOCATED_ROOT = "EQUIVOCATED_ROOT"      # host served a root != the one you pinned
UNPINNED_ROOT = "UNPINNED_ROOT"            # logged only against a host-asserted root
INAUTHENTIC = "INAUTHENTIC"                # signature not the issuer's / content tampered
FEE_UNVERIFIABLE = "FEE_UNVERIFIABLE"      # cannot confirm fee=0 (no full certificate)
FEE_POSITIVE = "FEE_POSITIVE"              # certifies an undisclosed convention (fee>0)
WRONG_COMPOSITION = "WRONG_COMPOSITION"    # a different composition than was demanded
MISSING = "MISSING"                        # no deed presented at all (caller-level)
UNREACHABLE = "UNREACHABLE"                # registry could not be reached (caller, fail-closed)


@dataclass(frozen=True)
class GatePolicy:
    """What a relying party demands before it will proceed. Two knobs are the whole
    policy: *require fee=0* and *require an independently-trusted root*. (The advisory
    surface relaxes the fee knobs; nothing else.)"""

    max_fee: int | None = 0                 # refuse fee>max_fee; None = do not gate on fee value
    require_independently_trusted_root: bool = True   # refuse host-asserted / none roots
    require_certificate_for_fee: bool = True          # a bare deed cannot prove fee=0 -> refuse
    expected_composition_hash: str | None = None      # if set, the deed must be for this composition


DEFAULT_GATE_POLICY = GatePolicy()
# The shipped advisory behaviour of bulla__deed_verify: gate on inclusion/root/authenticity,
# but only *report* fee (never block on it, and never demand a certificate).
ADVISORY_GATE_POLICY = GatePolicy(max_fee=None, require_certificate_for_fee=False)


@dataclass(frozen=True)
class GateDecision:
    """The full signal record behind a proceed/refuse. ``disposition`` is a
    ``model.Disposition`` value (``PROCEED`` or ``REFUSE_PENDING_DISCLOSURE`` — refuse,
    pending a disclosure that cures it). The remaining fields are the evidence the
    decision was taken on, so the same object serves the advisory payload, the enforced
    interceptor, and the refusal certificate."""

    disposition: str
    deficiency: str | None
    root_trust: str
    fee: int | None
    reason: str
    included: bool = False
    integrity: bool | None = None
    authenticity: bool | None = None
    composition_bound: bool | None = None
    registry_root: str | None = None
    cure: dict | None = None
    refusal_certificate: dict | None = None

    @property
    def proceed(self) -> bool:
        return self.disposition == Disposition.PROCEED.value

    def as_verify_payload(self) -> dict:
        """The legacy ``bulla__deed_verify`` response shape (so the proxy stays one
        decision impl and its API is unchanged)."""
        return {
            "integrity": self.integrity,
            "authenticity": self.authenticity,
            "included": self.included,
            "root_trust": self.root_trust,
            "composition_bound": self.composition_bound,
            "registry_root": self.registry_root,
            "recommend": "proceed" if self.proceed else "refuse",
            "reason": self.reason,
        }


def _cure_block(
    deficiency: str,
    *,
    composition_hash: str | None,
    max_fee: int | None,
    trusted_root: str | None,
    root_trust: str,
    disclose: tuple[str, ...] = (),
) -> dict:
    """The contestable instruction: what the counterparty must present to clear the
    refusal. ``disclose`` names the undeclared convention(s) — for the git demo,
    ``minimum_disclosure_set(seam_composition())`` — so the cure is executable, not
    decorative."""
    require_fee = 0 if max_fee is None else max_fee
    kind = "pinned" if trusted_root else ("anchored" if root_trust == "anchored" else "own-log")
    disclosed = (" (disclose " + ", ".join(str(x) for x in disclose) + ")") if disclose else ""
    return {
        "action": "present_deed_for_composition_under_trusted_root",
        "deficiency": deficiency,
        "composition_hash": composition_hash,
        "require_fee": require_fee,
        "require_root": {"kind": kind, "value": trusted_root},
        "disclose": list(disclose),
        "human": (
            f"Present a deed for composition {composition_hash or '<H>'}, logged under a "
            f"root you trust ({kind}), certifying coherence_fee <= {require_fee}{disclosed}."
        ),
    }


def evaluate_gate(
    *,
    deed_rec: dict,
    inclusion_rec: dict | None,
    trusted_root: str | None = None,
    root_ots: str | None = None,
    is_remote: bool = False,
    certificate: dict | None = None,
    policy: GatePolicy = DEFAULT_GATE_POLICY,
    public_key: bytes | None = None,
) -> GateDecision:
    """The decision core. I/O-free: the caller fetches ``inclusion_rec`` (via
    ``registry.inclusion_by_attestation``) inside its own fail-closed try/except and
    passes the record (or ``None``) in.

    The chain refuses on the most fundamental deficiency first; the **fee arms fire only
    after inclusion, root-trust and authenticity have passed**, so fee is a genuinely
    independent gate (a fee>0 deed that is perfectly logged and signed is still refused)."""
    from bulla.registry import (classify_root_trust, deed_leaf,
                                 verify_deed_record, verify_inclusion_record)

    cert = certificate or {}
    issuer = deed_rec.get("issuer") or cert.get("issuer", {}).get("id")
    content = deed_rec.get("content_hash") or cert.get("certificate_content_hash")
    att = deed_rec.get("attestation_hash") or cert.get("attestation_hash")
    decl_comp = deed_rec.get("composition_hash") or cert.get("subject", {}).get("composition_sha256")

    served_root = inclusion_rec.get("root") if inclusion_rec else None
    root_trust, root_ok = classify_root_trust(is_remote, served_root, trusted_root, root_ots)

    # Inclusion — leaf-bound (borrowed-inclusion-safe; ccfbb23). A self-consistent proof
    # is not enough: it must cover THIS deed's own leaf, or a host can answer the query
    # with a valid proof for an unrelated leaf that genuinely sits under the root.
    expected_leaf = (
        deed_leaf({"issuer": issuer, "content_hash": content, "attestation_hash": att})
        if issuer and content and att else None
    )
    included = bool(inclusion_rec) and verify_inclusion_record(
        inclusion_rec, expected_leaf=expected_leaf)

    # Authenticity / integrity — prefer the full certificate (it carries the fee we gate
    # on); else re-authenticate the served deed record from its own signature.
    integrity: bool | None = None
    authenticity: bool | None = None
    if certificate is not None:
        from bulla.certificate import verify_certificate_integrity
        from bulla.identity import verify_proof
        integrity = bool(verify_certificate_integrity(certificate))
        sig = certificate.get("signature")
        if sig:
            authenticity = bool(verify_proof(content or "", sig, public_key=public_key).authentic)
    elif deed_rec.get("signature"):
        authenticity = bool(verify_deed_record(deed_rec, public_key=public_key))

    # Fee — only trustworthy from an integrity-verified certificate.
    fee: int | None = None
    if certificate is not None and integrity:
        try:
            fee = int(cert["diagnostic"]["coherence_fee"])
        except (KeyError, TypeError, ValueError):
            fee = None

    comp_bound: bool | None = None
    if policy.expected_composition_hash is not None:
        # Fail closed: a binding we cannot evaluate is False, forcing refuse.
        comp_bound = (decl_comp == policy.expected_composition_hash)

    want_comp = policy.expected_composition_hash or decl_comp

    def refuse(deficiency: str, reason: str, *, disclose: tuple[str, ...] = ()) -> GateDecision:
        return GateDecision(
            disposition=Disposition.REFUSE_PENDING_DISCLOSURE.value,
            deficiency=deficiency, root_trust=root_trust, fee=fee, reason=reason,
            included=included, integrity=integrity, authenticity=authenticity,
            composition_bound=comp_bound, registry_root=served_root,
            cure=_cure_block(deficiency, composition_hash=want_comp, max_fee=policy.max_fee,
                             trusted_root=trusted_root, root_trust=root_trust, disclose=disclose),
        )

    # ── the decision chain ────────────────────────────────────────────────────────
    if not included:
        if not inclusion_rec:
            return refuse(OMITTED_FROM_LOG, "not in the registry — refuse the unlogged")
        return refuse(BORROWED_INCLUSION,
                      "inclusion proof covers a different leaf than this deed — refuse")
    if root_trust == "mismatch":
        return refuse(EQUIVOCATED_ROOT,
                      "root mismatch — the host served a different root than you pinned "
                      "(possible equivocation) — refuse")
    if authenticity is False:
        return refuse(INAUTHENTIC, "signature not attributable to the claimed issuer — refuse")
    if integrity is False:
        return refuse(INAUTHENTIC, "certificate content integrity failed — refuse")
    if comp_bound is False:
        return refuse(WRONG_COMPOSITION,
                      "deed is for a different composition, or its composition could not "
                      "be confirmed — refuse")
    if policy.require_independently_trusted_root and not root_ok:
        return refuse(UNPINNED_ROOT,
                      "logged only against a host-asserted, unpinned root — you would be "
                      "trusting the operator. Pin the root (trusted_root / root_ots) or "
                      "verify against your own log to proceed.")
    if policy.require_certificate_for_fee and certificate is None:
        return refuse(FEE_UNVERIFIABLE,
                      "no certificate supplied — coherence_fee cannot be confirmed from a "
                      "bare deed record (the leaf carries no fee). Present the full signed "
                      "certificate to prove fee = 0.")
    if policy.max_fee is not None and fee is not None and fee > policy.max_fee:
        return refuse(FEE_POSITIVE,
                      f"the certificate certifies coherence_fee = {fee} (an undisclosed "
                      f"cross-owner convention) > {policy.max_fee} — refuse")

    tail = "" if fee is None else f", fee = {fee}"
    note = ("" if authenticity is not None
            else " (pass the full certificate to also verify the signature)")
    return GateDecision(
        disposition=Disposition.PROCEED.value, deficiency=None, root_trust=root_trust,
        fee=fee, reason=f"logged against a root you trust ({root_trust}){tail} — proceed{note}",
        included=True, integrity=integrity, authenticity=authenticity,
        composition_bound=comp_bound, registry_root=served_root,
    )


# ── the contestable artifact ──────────────────────────────────────────────────────

def _content_hash(body: dict) -> str:
    """SHA-256 over canonical JSON — the same discipline as the certificate's content
    hash (`certificate._compute_certificate_content_hash`), applied to a refusal."""
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def build_refusal_certificate(
    decision: GateDecision,
    *,
    subject_deed: dict,
    disclose: tuple[str, ...] = (),
    signer: Any = None,
) -> dict:
    """A relying party's contestable record of a refusal: it names the deficiency and the
    cure, is content-addressed, and — symmetric to the deed — is signed by the relying
    party when a signer is present (so the refusal is non-repudiable and recomputable).

    ``disclose`` enriches the cure with the named convention(s) to disclose (the git demo
    passes ``minimum_disclosure_set(seam_composition())``); without it the cure still says
    "present a fee=0 deed"."""
    cure = dict(decision.cure or {})
    if disclose:
        cure["disclose"] = [str(x) for x in disclose]
        rf = cure.get("require_fee", 0)
        cure["human"] = (
            f"Present a deed for composition {cure.get('composition_hash') or '<H>'}, "
            f"logged under a root you trust ({(cure.get('require_root') or {}).get('kind', 'pinned')}), "
            f"certifying coherence_fee <= {rf} (disclose {', '.join(str(x) for x in disclose)})."
        )
    body = {
        "schema": "bulla.refusal/v1",
        "disposition": decision.disposition,
        "deficiency": decision.deficiency,
        "subject_deed": {
            "issuer": subject_deed.get("issuer"),
            "content_hash": subject_deed.get("content_hash"),
            "attestation_hash": subject_deed.get("attestation_hash"),
            "composition_hash": subject_deed.get("composition_hash"),
        },
        "observed": {
            "root_trust": decision.root_trust,
            "served_root": decision.registry_root,
            "fee": decision.fee,
            "integrity": decision.integrity,
            "authenticity": decision.authenticity,
        },
        "cure": cure,
        "relying_party": {"id": getattr(signer, "issuer", None)},
    }
    content_hash = _content_hash(body)
    out = {**body, "refusal_content_hash": content_hash}
    out["signature"] = signer.sign(content_hash) if signer is not None else None
    return out


def verify_refusal_certificate(refusal: dict, *, public_key: bytes | None = None) -> bool:
    """Recompute a refusal's content hash and, if signed, authenticate it — the read-side
    of the contestable artifact (a counterparty confirms the relying party really issued
    this refusal, and over exactly this content)."""
    claimed = refusal.get("refusal_content_hash")
    if not claimed:
        return False
    body = {k: v for k, v in refusal.items()
            if k not in ("refusal_content_hash", "signature")}
    if _content_hash(body) != claimed:
        return False
    sig = refusal.get("signature")
    if sig is None:
        return True  # unsigned but intact
    from bulla.identity import verify_proof
    return bool(verify_proof(claimed, sig, public_key=public_key).authentic)
