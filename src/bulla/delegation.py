"""did:key delegation grants — chain authenticity, NOT policy authorization.

See ``spec/delegation-design-note.md``. This module proves the bounded claim

    "this self-certifying principal delegated this exact declared capability to
    this signing key"

and refuses to claim more. In particular ``policy_binding`` is **hash agreement**
between the grant's declared policy digest and the receipt's policy reference — it
is never a decision that the act *obeys* the policy (that needs an executable scope
language, which is deferred). The name is `policy_binding`, never `authorized_scope`.

Six **independent** verdict dimensions are reported, never flattened into one enum
(flattening would impose an arbitrary precedence and hide evidence):
``chain_integrity``, ``principal_binding``, ``policy_binding``, ``scope_binding``,
``temporal_status``, and ``revocation_status``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any

from bulla._canonical import canonical_json
from bulla.executable_form import definition_hash
from bulla.identity import pubkey_from_did_key, verify_proof_domain

#: The delegation profile speaks did:key only in v0.3 (see design note §7).
DELEGATION_SCHEMA = "0.3"
#: Chain-depth cap — a runaway or padded chain is refused rather than walked.
MAX_DEPTH = 8
#: The proof purpose a grant is signed under (domain separation, identity.py).
GRANT_PURPOSE = "delegation-grant"

#: Every member a grant may carry. Unknown members are REJECTED, never ignored:
#: `core()` hashes only known fields, so an ignored extra (say `role: "admin"`)
#: would ride inside a "verified" grant while sitting outside the grantor's
#: signature — letting a downstream consumer act on semantics nobody signed.
_GRANT_KEYS = frozenset({
    "grantor", "grantee", "principal", "parent", "policy_digest", "scope_digest",
    "not_before", "not_after", "proof",
})

#: Validity bounds and checkpoints are TYPED — `{"domain": str, "value": int}`.
#: Only positions in the same named ordering domain are comparable.


class DelegationError(ValueError):
    """Raised when a grant is structurally malformed at construction."""


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def hash_ref(reference: str) -> str:
    """The pin for an opaque reference string (e.g. ``authority.policy``)."""
    return _sha(reference.encode("utf-8"))


def _is_did_key(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    try:
        pubkey_from_did_key(s)
    except ValueError:
        return False
    return True


def _is_sha(s: Any) -> bool:
    return isinstance(s, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", s) is not None


def _checkpoint_parts(value: Any, *, label: str) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != {"domain", "value"}:
        raise DelegationError(
            f"{label} must be exactly {{'domain': str, 'value': non-negative int}}"
        )
    domain, position = value.get("domain"), value.get("value")
    if not isinstance(domain, str) or not domain.strip():
        raise DelegationError(f"{label}.domain must be a non-empty string")
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise DelegationError(f"{label}.value must be a non-negative integer")
    return domain, position


@dataclass(frozen=True)
class DelegationGrant:
    """A capability handed from one identity to the next, pinned so it cannot be
    reordered, spliced, lifted into another chain, or broadened.

    ``grant_hash`` excludes ``proof`` (the proof signs the hash, so it cannot be in
    its own preimage). ``parent`` binds each grant to its predecessor's hash;
    ``principal`` is carried in every grant so a grant minted under one principal
    cannot be replayed inside a chain terminating at another."""

    grantor: str
    grantee: str
    principal: str
    parent: str | None
    policy_digest: str
    scope_digest: str
    not_before: dict | None = None
    not_after: dict | None = None
    proof: dict | None = None

    def __post_init__(self) -> None:
        for name in ("grantor", "grantee", "principal"):
            if not _is_did_key(getattr(self, name)):
                raise DelegationError(
                    f"grant.{name} must be a did:key (did:key:z…); this profile is "
                    f"did:key only — got {getattr(self, name)!r}"
                )
        if self.parent is not None and not _is_sha(self.parent):
            raise DelegationError("grant.parent must be a 'sha256:…' hash or null")
        for name in ("policy_digest", "scope_digest"):
            if not _is_sha(getattr(self, name)):
                raise DelegationError(f"grant.{name} must be a full lowercase sha256 digest")
        if self.proof is not None and not isinstance(self.proof, dict):
            raise DelegationError("grant.proof must be an object or null")
        before = (
            _checkpoint_parts(self.not_before, label="grant.not_before")
            if self.not_before is not None else None
        )
        after = (
            _checkpoint_parts(self.not_after, label="grant.not_after")
            if self.not_after is not None else None
        )
        if before is not None and after is not None:
            if before[0] != after[0]:
                raise DelegationError("grant validity bounds must use the same checkpoint domain")
            if before[1] > after[1]:
                raise DelegationError("grant.not_before must not exceed grant.not_after")

    def core(self) -> dict:
        """The signed preimage — every field except ``proof``. Optional window
        fields are conditional-include so a grant with no window hashes like the
        minimal grant (the field's absence is itself committed)."""
        d: dict = {
            "grantor": self.grantor,
            "grantee": self.grantee,
            "principal": self.principal,
            "parent": self.parent,
            "policy_digest": self.policy_digest,
            "scope_digest": self.scope_digest,
        }
        if self.not_before is not None:
            d["not_before"] = self.not_before
        if self.not_after is not None:
            d["not_after"] = self.not_after
        return d

    @property
    def grant_hash(self) -> str:
        return _sha(canonical_json(self.core()).encode("utf-8"))

    def to_dict(self) -> dict:
        d = self.core()
        d["proof"] = self.proof
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DelegationGrant":
        if not isinstance(d, dict):
            raise DelegationError("a delegation grant must be an object")
        extra = set(d) - _GRANT_KEYS
        if extra:
            raise DelegationError(
                f"unknown grant members {sorted(extra)} — fail closed. An unknown field is "
                "outside grant_hash and therefore outside the grantor's signature; accepting "
                "it would let a consumer act on semantics the grantor never signed"
            )
        return cls(
            grantor=d.get("grantor", ""),
            grantee=d.get("grantee", ""),
            principal=d.get("principal", ""),
            parent=d.get("parent"),
            policy_digest=d.get("policy_digest", ""),
            scope_digest=d.get("scope_digest", ""),
            not_before=d.get("not_before"),
            not_after=d.get("not_after"),
            proof=d.get("proof"),
        )


def sign_grant(grant: DelegationGrant, signer: Any) -> DelegationGrant:
    """Attach the grantor's domain-separated proof over ``grant_hash``. The signer
    must BE the grantor (its verificationMethod == grant.grantor), because a grant
    is the grantor vouching for the delegation it makes."""
    if getattr(signer, "verification_method", None) != grant.grantor:
        raise DelegationError(
            "a grant must be signed by its grantor (signer.verification_method "
            "!= grant.grantor)"
        )
    proof = signer.sign_domain(GRANT_PURPOSE, grant.grant_hash)
    return replace(grant, proof=proof)


@dataclass(frozen=True)
class DelegationVerdict:
    """Six independent dimensions.

    ``cryptographically_bound`` is the bounded offline claim: the chain
    reaches the signer and conveys the receipt's exact policy reference and
    declared scope. ``fully_delegated`` is deliberately stronger and remains
    false until both temporal and revocation evidence are positively verified.
    """

    chain_integrity: str    # verified | broken | cycle | over_depth | not_applicable
    principal_binding: str  # verified | wrong_principal | unresolved
    policy_binding: str     # verified | mismatch | not_applicable
    scope_binding: str      # verified | mismatch | not_applicable
    temporal_status: str    # unresolved | within_window | expired | not_yet_valid
    revocation_status: str  # unresolved | not_revoked | revoked | not_applicable
    reasons: tuple[str, ...] = ()

    @property
    def cryptographically_bound(self) -> bool:
        return (
            self.chain_integrity == "verified"
            and self.principal_binding == "verified"
            and self.policy_binding == "verified"
            and self.scope_binding == "verified"
        )

    @property
    def fully_delegated(self) -> bool:
        """Conservative reliance predicate; currently false while revocation is unbuilt."""
        return (
            self.cryptographically_bound
            and self.temporal_status == "within_window"
            and self.revocation_status == "not_revoked"
        )

    def __bool__(self) -> bool:
        # Six independent dimensions must never collapse to one boolean — that
        # collapse is exactly the class of bug this object exists to prevent (a
        # chain can be integral yet bound to the wrong principal). A bare
        # ``if verdict:`` would read every dimension as satisfied. Raise instead.
        raise TypeError(
            "The truth value of a DelegationVerdict is ambiguous — its six dimensions "
            "are independent. Read a specific dimension, or the derived predicates "
            "`.cryptographically_bound` / `.fully_delegated`."
        )

    def to_dict(self) -> dict:
        return {
            "chain_integrity": self.chain_integrity,
            "principal_binding": self.principal_binding,
            "policy_binding": self.policy_binding,
            "scope_binding": self.scope_binding,
            "temporal_status": self.temporal_status,
            "revocation_status": self.revocation_status,
        }


def _grants_from(raw: Any) -> list[DelegationGrant]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise DelegationError("delegation must be an ordered array of grant objects")
    grants: list[DelegationGrant] = []
    for g in raw:
        grants.append(g if isinstance(g, DelegationGrant) else DelegationGrant.from_dict(g))
    return grants


def _chain_integrity(grants: list[DelegationGrant], reasons: list[str]) -> str:
    if len(grants) > MAX_DEPTH:
        reasons.append(f"delegation chain depth {len(grants)} exceeds MAX_DEPTH {MAX_DEPTH}")
        return "over_depth"
    # proof validity + root-parent + continuity (all "broken" class)
    for i, g in enumerate(grants):
        if not isinstance(g.proof, dict):
            reasons.append(f"grant {i} has no proof")
            return "broken"
        # A grantor is SELF-CERTIFYING here: the key is derived from `grant.grantor`
        # itself — never from the proof's own issuer claim (which an attacker sets),
        # and never from a caller-supplied key. A receipt-signer key override must
        # not become an override for upstream principals, or one attacker key would
        # authenticate every grantor in the chain.
        if g.proof.get("issuer") != g.grantor or g.proof.get("verificationMethod") != g.grantor:
            reasons.append(
                f"grant {i} must name its grantor {g.grantor!r} as both issuer and "
                f"verificationMethod (got issuer={g.proof.get('issuer')!r}, "
                f"vm={g.proof.get('verificationMethod')!r})"
            )
            return "broken"
        auth = verify_proof_domain(GRANT_PURPOSE, g.grant_hash, g.proof)
        if not auth.authentic:
            reasons.append(f"grant {i} proof not authentic ({auth.method}: {auth.detail or 'invalid'})")
            return "broken"
    if grants[0].parent is not None:
        reasons.append("root grant must have parent=null")
        return "broken"
    for i in range(1, len(grants)):
        if grants[i].grantor != grants[i - 1].grantee:
            reasons.append(f"grant {i} grantor != grant {i-1} grantee (continuity break)")
            return "broken"
        if grants[i].parent != grants[i - 1].grant_hash:
            reasons.append(f"grant {i} parent != hash(grant {i-1}) (spliced or stripped)")
            return "broken"
    # cycle: the identity path grantor_0, grantee_0, grantee_1, … must be distinct
    path = [grants[0].grantor] + [g.grantee for g in grants]
    if len(set(path)) != len(path):
        reasons.append("delegation chain revisits an identity (cycle)")
        return "cycle"
    return "verified"


def _principal_binding(
    grants: list[DelegationGrant], principal: str, leaf_vm: str | None, reasons: list[str]
) -> str:
    if not _is_did_key(principal):
        reasons.append(f"principal {principal!r} is not a did:key — binding needs an external resolver (unresolved)")
        return "unresolved"
    if leaf_vm is None:
        reasons.append("no content signer to tie the chain's leaf to (unresolved)")
        return "unresolved"
    if grants[0].grantor != principal:
        reasons.append(f"root grantor {grants[0].grantor!r} != authority.principal {principal!r}")
        return "wrong_principal"
    if any(g.principal != principal for g in grants):
        reasons.append("a grant names a different principal (principal-consistency)")
        return "wrong_principal"
    if grants[-1].grantee != leaf_vm:
        reasons.append(f"leaf grantee {grants[-1].grantee!r} != the receipt signer {leaf_vm!r}")
        return "wrong_principal"
    return "verified"


def _policy_binding(
    grants: list[DelegationGrant], policy_ref: str, reasons: list[str]
) -> str:
    """Hash agreement with the receipt's policy reference, never policy obedience."""
    if not isinstance(policy_ref, str) or not policy_ref.strip():
        reasons.append("the receipt declares no authority.policy to bind (fail closed)")
        return "mismatch"
    expected_policy = hash_ref(policy_ref)
    if any(g.policy_digest != expected_policy for g in grants):
        reasons.append(
            "a grant's policy_digest != H(authority.policy) — the chain does not convey this policy "
            "(hash agreement only; this is not an authorization check)"
        )
        return "mismatch"
    return "verified"


def _scope_binding(
    grants: list[DelegationGrant], scope_ref: str | dict | None, reasons: list[str]
) -> str:
    """Exact hash agreement with THIS receipt's declared bounds.scope — prose OR a
    structured ``jsonschema+quantum/1`` predicate. ``definition_hash`` pins both (a
    prose scope hashes byte-identically to the historical behaviour), so a structured
    scope needs no fork here. This is still hash agreement — that the chain *conveys*
    the declared scope; whether the act *obeys* it is ``bounds_conformance``."""
    if scope_ref is None or (isinstance(scope_ref, str) and not scope_ref.strip()):
        reasons.append(
            "the receipt declares no bounds.scope for the grants' scope_digest to bind to — "
            "a delegated capability must name the scope it covers (fail closed)"
        )
        return "mismatch"
    expected_scope = definition_hash(scope_ref)
    # Equality against the RECEIPT's declared scope (which also forces chain-wide
    # equality). Attenuation is not decidable over opaque digests — "narrower" is
    # indistinguishable from "different" — so narrowing is deferred, not faked.
    if any(g.scope_digest != expected_scope for g in grants):
        reasons.append(
            "a grant's scope_digest != H(bounds.scope) — the chain does not convey the capability "
            "this receipt exercises (the act may have been widened after the grant was signed)"
        )
        return "mismatch"
    return "verified"


def _temporal_status(grants: list[DelegationGrant], checkpoint: Any, reasons: list[str]) -> str:
    if any(g.not_before is None or g.not_after is None for g in grants):
        reasons.append(
            "every grant needs a closed validity window for temporal_status=within_window"
        )
        return "unresolved"
    if checkpoint is None:
        reasons.append("grants carry validity windows but no checkpoint was supplied to evaluate them")
        return "unresolved"
    try:
        domain, position = _checkpoint_parts(checkpoint, label="checkpoint")
    except DelegationError as exc:
        reasons.append(str(exc))
        return "unresolved"
    for g in grants:
        before_domain, before = _checkpoint_parts(g.not_before, label="grant.not_before")
        after_domain, after = _checkpoint_parts(g.not_after, label="grant.not_after")
        if before_domain != domain or after_domain != domain:
            reasons.append("validity window and checkpoint use incomparable ordering domains")
            return "unresolved"
        if position < before:
            return "not_yet_valid"
        if position > after:
            return "expired"
    return "within_window"


def verify_delegation(
    grants: Any,
    *,
    principal: str,
    policy_ref: str,
    scope_ref: str | None,
    leaf_verification_method: str | None,
    checkpoint: Any = None,
) -> DelegationVerdict:
    """Report the six independent delegation dimensions for a receipt's grant
    chain. Never raises on a hostile chain — it classifies it. See the module
    docstring for the bounded claim and the honest limits of ``policy_binding``.

    ``scope_ref`` is the RECEIPT's declared ``bounds.scope``: every grant's
    ``scope_digest`` must equal its hash, or the chain does not convey the capability
    actually being exercised. There is deliberately **no** ``public_key`` parameter —
    every grant's key is derived from its own ``grantor`` (§7, did:key only), so a
    caller's key can never authenticate an upstream grantor."""
    reasons: list[str] = []
    try:
        chain = _grants_from(grants)
    except DelegationError as exc:
        return DelegationVerdict(
            "broken", "wrong_principal", "mismatch", "mismatch",
            "unresolved", "unresolved", (str(exc),),
        )

    if not chain:
        # No delegation: the principal must have signed the act directly.
        if not _is_did_key(principal):
            return DelegationVerdict(
                "not_applicable", "unresolved", "not_applicable", "not_applicable",
                "not_applicable", "not_applicable",
                ("no delegation and non-did:key principal (unresolved)",),
            )
        if leaf_verification_method is None:
            return DelegationVerdict(
                "not_applicable", "unresolved", "not_applicable", "not_applicable",
                "not_applicable", "not_applicable",
                ("no delegation and no signer to bind to the principal (unresolved)",),
            )
        if leaf_verification_method == principal:
            return DelegationVerdict(
                "not_applicable", "verified", "not_applicable", "not_applicable",
                "not_applicable", "not_applicable",
                ("principal signed the act directly; no delegation needed",),
            )
        return DelegationVerdict(
            "not_applicable", "wrong_principal", "not_applicable", "not_applicable",
            "not_applicable", "not_applicable",
            ("no delegation, and the signer is not the principal",),
        )

    chain_integrity = _chain_integrity(chain, reasons)
    principal_binding = _principal_binding(chain, principal, leaf_verification_method, reasons)
    policy_binding = _policy_binding(chain, policy_ref, reasons)
    scope_binding = _scope_binding(chain, scope_ref, reasons)
    temporal_status = _temporal_status(chain, checkpoint, reasons)
    # Revocation transport is unbuilt. A valid time window does not prove that
    # no revocation was published elsewhere.
    revocation_status = "unresolved"
    return DelegationVerdict(
        chain_integrity, principal_binding, policy_binding, scope_binding,
        temporal_status, revocation_status, tuple(reasons),
    )
