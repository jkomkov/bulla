"""Constitutional controls for Semantic Settlement v0.1.

This module is deliberately profile-level.  The finite refinement kernel
remains reusable, but the settlement path below will not mutate semantic state
without a signed, scope-bound authority receipt.  Closure warrants state the
limits of model enumeration; observation authorizations state an asserted
basis without making a legal-validity claim; conflicts quarantine claims and
route without changing the operative state.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bulla.action_receipt import ActionReceipt, build_action_receipt, sign_action_receipt, verify_receipt
from bulla.envelope import Authority, Bounds, RecourseEnvelope
from bulla.experimental.checkpoint import WitnessCheckpoint, verify_checkpoint
from bulla.experimental.frsl import canonical_hash
from bulla.experimental.invention import InventionError, SeamProblem, SynthesisResult
from bulla.experimental.observability import (
    BURDEN_FIELDS,
    BurdenVector,
    ConservationManifest,
    LogicPassport,
    ObservableOffer,
)
from bulla.experimental.refinement import (
    ConstraintAdmission,
    EnvelopeSnapshot,
    RefinementBundle,
    authority_epoch,
    envelope_snapshot,
    refine_envelope,
    semantic_epoch,
    semantic_state,
)
from bulla.registry import Deed, verify_inclusion_record
from bulla.identity import verify_proof_domain


PROFILE = "bulla.semantic-finality/0.1-experimental"
SCHEMA_VERSION = "0.1-experimental"


def _digest(value: Any) -> str:
    return canonical_hash(value)


def _require_digest(value: str, where: str) -> None:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise InventionError(f"{where} must be a full sha256 digest")
    if any(ch not in "0123456789abcdef" for ch in value[7:]):
        raise InventionError(f"{where} must be lowercase hexadecimal")


@dataclass(frozen=True)
class AuthorityPermission:
    principal: str
    policy: str
    scopes: tuple[str, ...]
    delegations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.principal or not self.policy:
            raise InventionError("authority permission requires principal and policy")
        scopes = tuple(self.scopes)
        if not scopes or len(scopes) != len(set(scopes)) or any(not item for item in scopes):
            raise InventionError("authority permission scopes must be unique and non-empty")
        object.__setattr__(self, "scopes", scopes)
        object.__setattr__(self, "delegations", tuple(self.delegations))

    def permits(self, scope: str) -> bool:
        return scope in self.scopes

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal": self.principal,
            "policy": self.policy,
            "scopes": list(self.scopes),
            "delegations": list(self.delegations),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuthorityPermission":
        if set(value) != {"principal", "policy", "scopes", "delegations"}:
            raise InventionError("AuthorityPermission has unknown or missing fields")
        return cls(value["principal"], value["policy"], tuple(value["scopes"]), tuple(value["delegations"]))


@dataclass(frozen=True)
class AuthorityRegime:
    operative: AuthorityPermission
    refinement: AuthorityPermission
    supersession: AuthorityPermission
    witness_operators: tuple[str, ...]
    forum: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        operators = tuple(self.witness_operators)
        if self.schema_version != SCHEMA_VERSION or not self.forum:
            raise InventionError("unsupported authority regime or empty forum")
        if len(operators) < 2 or len(operators) != len(set(operators)):
            raise InventionError("authority regime requires at least two distinct witness operators")
        object.__setattr__(self, "witness_operators", operators)

    @property
    def regime_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operative": self.operative.to_dict(),
            "refinement": self.refinement.to_dict(),
            "supersession": self.supersession.to_dict(),
            "witness_operators": list(self.witness_operators),
            "forum": self.forum,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuthorityRegime":
        required = {"schema_version", "operative", "refinement", "supersession", "witness_operators", "forum"}
        if set(value) != required:
            raise InventionError("AuthorityRegime has unknown or missing fields")
        return cls(
            operative=AuthorityPermission.from_dict(value["operative"]),
            refinement=AuthorityPermission.from_dict(value["refinement"]),
            supersession=AuthorityPermission.from_dict(value["supersession"]),
            witness_operators=tuple(value["witness_operators"]),
            forum=value["forum"],
            schema_version=value["schema_version"],
        )


class ClosureStatus(str, enum.Enum):
    FINITE_EXACT = "FINITE_EXACT"
    BOUNDED_EXACT = "BOUNDED_EXACT"
    EXPERT_ATTESTED = "EXPERT_ATTESTED"
    EMPIRICALLY_STRESSED = "EMPIRICALLY_STRESSED"
    OPEN_WORLD = "OPEN_WORLD"
    UNKNOWN_COVERAGE = "UNKNOWN_COVERAGE"


@dataclass(frozen=True)
class ModelClosureWarrant:
    status: ClosureStatus
    model_class: Mapping[str, Any]
    generation_method: Mapping[str, Any]
    exclusions: tuple[str, ...]
    domain_authority: Mapping[str, Any]
    adversarial_expansion_evidence: tuple[Mapping[str, Any], ...]
    scope: Mapping[str, Any]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise InventionError("unsupported ModelClosureWarrant schema")
        for name in ("model_class", "generation_method", "domain_authority", "scope"):
            if not isinstance(getattr(self, name), Mapping) or not getattr(self, name):
                raise InventionError(f"ModelClosureWarrant.{name} must be non-empty")
            object.__setattr__(self, name, dict(getattr(self, name)))
        exclusions = tuple(self.exclusions)
        if len(exclusions) != len(set(exclusions)):
            raise InventionError("ModelClosureWarrant exclusions must be unique")
        object.__setattr__(self, "exclusions", exclusions)
        object.__setattr__(
            self,
            "adversarial_expansion_evidence",
            tuple(dict(item) for item in self.adversarial_expansion_evidence),
        )

    @property
    def warrant_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "model_class": dict(self.model_class),
            "generation_method": dict(self.generation_method),
            "exclusions": list(self.exclusions),
            "domain_authority": dict(self.domain_authority),
            "adversarial_expansion_evidence": [dict(item) for item in self.adversarial_expansion_evidence],
            "scope": dict(self.scope),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelClosureWarrant":
        required = {"schema_version", "status", "model_class", "generation_method", "exclusions", "domain_authority", "adversarial_expansion_evidence", "scope"}
        if set(value) != required:
            raise InventionError("ModelClosureWarrant has unknown or missing fields")
        return cls(
            status=ClosureStatus(value["status"]), model_class=value["model_class"],
            generation_method=value["generation_method"], exclusions=tuple(value["exclusions"]),
            domain_authority=value["domain_authority"],
            adversarial_expansion_evidence=tuple(value["adversarial_expansion_evidence"]),
            scope=value["scope"], schema_version=value["schema_version"],
        )


class ObservationAuthorizationBasis(str, enum.Enum):
    CONSENT = "CONSENT"
    CONTRACT = "CONTRACT"
    REGULATION = "REGULATION"
    DUTY = "DUTY"
    ORDER = "ORDER"


@dataclass(frozen=True)
class ObservationAuthorization:
    offer_hash: str
    subject: str
    basis: ObservationAuthorizationBasis
    authorization_ref: str
    purpose: str
    scope: Mapping[str, Any]
    issuer: str
    proof: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_digest(self.offer_hash, "observation_authorization.offer_hash")
        if not all((self.subject, self.authorization_ref, self.purpose, self.issuer)):
            raise InventionError("ObservationAuthorization text fields must be non-empty")
        if not isinstance(self.scope, Mapping) or not self.scope:
            raise InventionError("ObservationAuthorization scope must be non-empty")
        object.__setattr__(self, "scope", dict(self.scope))
        if self.proof is not None:
            object.__setattr__(self, "proof", dict(self.proof))

    @property
    def authorization_hash(self) -> str:
        return _digest(self.unsigned_dict())

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "offer_hash": self.offer_hash,
            "subject": self.subject,
            "basis": self.basis.value,
            "authorization_ref": self.authorization_ref,
            "purpose": self.purpose,
            "scope": dict(self.scope),
            "issuer": self.issuer,
            "legal_validity": "not_asserted",
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "authorization_hash": self.authorization_hash, "proof": dict(self.proof) if self.proof else None}


def sign_observation_authorization(authorization: ObservationAuthorization, signer: Any) -> ObservationAuthorization:
    if signer.issuer != authorization.issuer:
        raise InventionError("observation authorization signer does not match issuer")
    return ObservationAuthorization(
        offer_hash=authorization.offer_hash, subject=authorization.subject,
        basis=authorization.basis, authorization_ref=authorization.authorization_ref,
        purpose=authorization.purpose, scope=authorization.scope, issuer=authorization.issuer,
        proof=signer.sign_domain("authorization", authorization.authorization_hash),
    )


def verify_observation_authorization(authorization: ObservationAuthorization) -> bool:
    if authorization.proof is None:
        return False
    result = verify_proof_domain(
        "authorization", authorization.authorization_hash, dict(authorization.proof)
    )
    return bool(result.authentic and result.issuer == authorization.issuer)


@dataclass(frozen=True)
class ObservationConstitution:
    permitted_observables: tuple[str, ...]
    prohibited_observables: tuple[str, ...]
    purposes: tuple[str, ...]
    prohibited_reuse: tuple[str, ...]
    maximum_burden: BurdenVector
    permitted_providers: tuple[str, ...]
    permitted_warrant_classes: tuple[str, ...]
    permitted_authorization_bases: tuple[ObservationAuthorizationBasis, ...]
    retention_policy: str
    challenge_policy: str

    def __post_init__(self) -> None:
        if set(self.permitted_observables) & set(self.prohibited_observables):
            raise InventionError("observation constitution cannot both permit and prohibit an observable")
        if not self.purposes or not self.permitted_providers or not self.permitted_authorization_bases:
            raise InventionError("observation constitution requires purposes, providers, and bases")
        if not self.retention_policy or not self.challenge_policy:
            raise InventionError("observation constitution requires retention and challenge policies")

    @property
    def constitution_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "permitted_observables": list(self.permitted_observables),
            "prohibited_observables": list(self.prohibited_observables),
            "purposes": list(self.purposes),
            "prohibited_reuse": list(self.prohibited_reuse),
            "maximum_burden": self.maximum_burden.to_dict(),
            "permitted_providers": list(self.permitted_providers),
            "permitted_warrant_classes": list(self.permitted_warrant_classes),
            "permitted_authorization_bases": [item.value for item in self.permitted_authorization_bases],
            "retention_policy": self.retention_policy,
            "challenge_policy": self.challenge_policy,
        }

    def permits(self, offer: ObservableOffer, authorization: ObservationAuthorization, *, purpose: str) -> bool:
        if authorization.offer_hash != offer.offer_hash or authorization.subject not in offer.consent_subjects:
            return False
        if purpose not in self.purposes or authorization.purpose != purpose:
            return False
        if offer.offer_id in self.prohibited_observables:
            return False
        if self.permitted_observables and offer.offer_id not in self.permitted_observables:
            return False
        if offer.provider not in self.permitted_providers:
            return False
        if offer.warrant_profile["kind"] not in self.permitted_warrant_classes:
            return False
        if authorization.basis not in self.permitted_authorization_bases:
            return False
        return all(
            getattr(offer.burden, name) <= getattr(self.maximum_burden, name)
            for name in BURDEN_FIELDS
        )


def filter_observation_offers(
    offers: Sequence[ObservableOffer],
    authorizations: Sequence[ObservationAuthorization],
    constitution: ObservationConstitution,
    *,
    purpose: str,
) -> tuple[ObservableOffer, ...]:
    """Filter constitutionally before any optimizer sees the catalog."""

    by_offer: dict[str, list[ObservationAuthorization]] = {}
    for authorization in authorizations:
        if verify_observation_authorization(authorization):
            by_offer.setdefault(authorization.offer_hash, []).append(authorization)
    permitted: list[ObservableOffer] = []
    for offer in offers:
        candidates = tuple(
            auth for auth in by_offer.get(offer.offer_hash, ())
            if constitution.permits(offer, auth, purpose=purpose)
        )
        if {auth.subject for auth in candidates} == set(offer.consent_subjects):
            permitted.append(offer)
    return tuple(permitted)


def mint_authorization_receipt(
    *, action_type: str, subject: Mapping[str, Any], permission: AuthorityPermission,
    signer: Any, scope: str, timestamp: str,
) -> ActionReceipt:
    if not permission.permits(scope):
        raise InventionError(f"authority does not permit scope {scope!r}")
    if signer.issuer != permission.principal:
        raise InventionError("authorization signer is not the configured authority")
    envelope = RecourseEnvelope(
        authority=Authority(permission.principal, permission.policy, permission.delegations),
        bounds=Bounds(scope=scope),
        retention_class="authority-permanent",
        disclosure_class="auditor",
    )
    receipt = build_action_receipt(
        action={"type": action_type, "subject": dict(subject)},
        diagnostic_ref={"status": "reference", "ref": _digest(dict(subject))},
        envelope=envelope,
        evidence_refs=(), timestamp=timestamp,
        producer={"profile": PROFILE},
    )
    return sign_action_receipt(receipt, signer)


def verify_authorization_receipt(
    receipt: ActionReceipt | Mapping[str, Any], *, action_type: str,
    expected_subject: Mapping[str, Any], permission: AuthorityPermission, scope: str,
) -> bool:
    try:
        wire = receipt.to_dict() if isinstance(receipt, ActionReceipt) else dict(receipt)
        parsed = receipt if isinstance(receipt, ActionReceipt) else ActionReceipt.from_dict(wire)
        verification = verify_receipt(wire)
        proof_issuer = (parsed.authorization or {}).get("issuer")
        authority = parsed.envelope.authority
        return bool(
            verification.ok
            and verification.authority_authentic == "verified"
            and parsed.action == {"type": action_type, "subject": dict(expected_subject)}
            and parsed.envelope.retention_class == "authority-permanent"
            and parsed.envelope.bounds is not None
            and parsed.envelope.bounds.scope == scope
            and authority is not None
            and authority.principal == permission.principal
            and authority.policy == permission.policy
            and proof_issuer == permission.principal
            and permission.permits(scope)
        )
    except (KeyError, TypeError, ValueError):
        return False


def refinement_authorization_subject(
    admission: ConstraintAdmission, prior_snapshot: EnvelopeSnapshot, scope: str,
) -> dict[str, Any]:
    return {
        "admission_hash": admission.admission_hash,
        "prior_snapshot_hash": prior_snapshot.snapshot_hash,
        "scope": scope,
        "authority_epoch": admission.authority_epoch,
        "semantic_epoch": prior_snapshot.semantic_epoch,
    }


def authorized_refine(
    base_problem: SeamProblem, prior_result: SynthesisResult, admission: ConstraintAdmission,
    *, passport: LogicPassport, manifest: ConservationManifest,
    closure_warrant: ModelClosureWarrant, regime: AuthorityRegime,
    authorization_receipt: ActionReceipt | Mapping[str, Any], scope: str,
    prior_admissions: Sequence[ConstraintAdmission] = (),
) -> RefinementBundle:
    bundle = refine_envelope(
        base_problem, prior_result, admission, passport=passport, manifest=manifest,
        prior_admissions=prior_admissions, closure_warrant_hash=closure_warrant.warrant_hash,
    )
    subject = refinement_authorization_subject(admission, bundle.prior_snapshot, scope)
    if not verify_authorization_receipt(
        authorization_receipt, action_type="bulla.semantic.refine.authorize",
        expected_subject=subject, permission=regime.refinement, scope=scope,
    ):
        raise InventionError("refinement authorization failed before state mutation")
    return bundle


@dataclass(frozen=True)
class WitnessInclusion:
    operator: str
    claim_hash: str
    deed_issuer: str
    deed_attestation_hash: str
    expected_leaf: str
    checkpoint: WitnessCheckpoint
    inclusion_record: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_digest(self.claim_hash, "witness_inclusion.claim_hash")
        _require_digest(self.deed_attestation_hash, "witness_inclusion.deed_attestation_hash")
        _require_digest(self.expected_leaf, "witness_inclusion.expected_leaf")
        object.__setattr__(self, "inclusion_record", dict(self.inclusion_record))

    def verifies(self) -> bool:
        checkpoint = verify_checkpoint(self.checkpoint)
        committed_leaf = "sha256:" + Deed(
            self.deed_issuer, self.claim_hash, self.deed_attestation_hash
        ).leaf().hex()
        return bool(
            checkpoint.ok
            and self.operator == self.checkpoint.operator
            and self.deed_issuer == self.operator
            and self.expected_leaf == committed_leaf
            and self.inclusion_record.get("tree_size") == self.checkpoint.tree_size
            and verify_inclusion_record(
                dict(self.inclusion_record), trusted_root=self.checkpoint.root,
                expected_leaf=self.expected_leaf,
            )
        )


@dataclass(frozen=True)
class AuthorizedRevision:
    supersession: Mapping[str, Any]
    authorization_receipt_hash: str
    supersession_receipt_hash: str
    witness_checkpoint_hashes: tuple[str, str]

    @property
    def revision_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "supersession": dict(self.supersession),
            "authorization_receipt_hash": self.authorization_receipt_hash,
            "supersession_receipt_hash": self.supersession_receipt_hash,
            "witness_checkpoint_hashes": list(self.witness_checkpoint_hashes),
        }


def authorize_revision(
    snapshot: EnvelopeSnapshot, *, new_authority: Mapping[str, Any], new_closure_warrant: ModelClosureWarrant,
    reason: str, regime: AuthorityRegime, authorization_receipt: ActionReceipt | Mapping[str, Any],
    supersession_receipt: ActionReceipt | Mapping[str, Any], witness_inclusions: Sequence[WitnessInclusion],
    scope: str,
) -> AuthorizedRevision:
    subject = revision_authorization_subject(
        snapshot, new_authority=new_authority,
        new_closure_warrant=new_closure_warrant, reason=reason, scope=scope,
    )
    if not verify_authorization_receipt(
        authorization_receipt, action_type="bulla.semantic.revise.authorize",
        expected_subject=subject, permission=regime.supersession, scope=scope,
    ):
        raise InventionError("revision authorization failed before state mutation")
    if not verify_authorization_receipt(
        supersession_receipt, action_type="bulla.term.supersede",
        expected_subject=subject, permission=regime.supersession, scope=scope,
    ):
        raise InventionError("term supersession receipt failed before state mutation")
    auth_wire = authorization_receipt.to_dict() if isinstance(authorization_receipt, ActionReceipt) else dict(authorization_receipt)
    sup_wire = supersession_receipt.to_dict() if isinstance(supersession_receipt, ActionReceipt) else dict(supersession_receipt)
    claim_hash = _digest({"authorization": auth_wire["hashes"]["content"], "supersession": sup_wire["hashes"]["content"]})
    witnesses = tuple(witness_inclusions)
    operators = {item.operator for item in witnesses}
    if len(witnesses) != 2 or len(operators) != 2 or not operators.issubset(regime.witness_operators):
        raise InventionError("revision requires exactly two distinct configured witness operators")
    if any(item.claim_hash != claim_hash or not item.verifies() for item in witnesses):
        raise InventionError("revision witness inclusion failed before state mutation")
    return AuthorizedRevision(
        supersession=subject,
        authorization_receipt_hash=auth_wire["hashes"]["attestation"],
        supersession_receipt_hash=sup_wire["hashes"]["attestation"],
        witness_checkpoint_hashes=tuple(item.checkpoint.checkpoint_hash for item in witnesses),
    )


def revision_authorization_subject(
    snapshot: EnvelopeSnapshot, *, new_authority: Mapping[str, Any],
    new_closure_warrant: ModelClosureWarrant, reason: str, scope: str,
) -> dict[str, Any]:
    if not reason or not scope:
        raise InventionError("semantic supersession requires reason and scope")
    next_authority_epoch = authority_epoch(new_authority)
    next_semantic_epoch = semantic_epoch(next_authority_epoch, new_closure_warrant.warrant_hash)
    if next_semantic_epoch == snapshot.semantic_epoch:
        raise InventionError("revision must create a new authority or closure epoch")
    return {
        "snapshot_hash": snapshot.snapshot_hash,
        "prior_authority_epoch": snapshot.authority_epoch,
        "new_authority_epoch": next_authority_epoch,
        "prior_closure_warrant_hash": snapshot.closure_warrant_hash,
        "new_closure_warrant_hash": new_closure_warrant.warrant_hash,
        "prior_semantic_epoch": snapshot.semantic_epoch,
        "new_semantic_epoch": next_semantic_epoch,
        "reason": reason,
        "scope": scope,
    }


class AdmissionOutcome(str, enum.Enum):
    ADMITTED = "ADMITTED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ConflictCertificate:
    prior_state_hash: str
    conflicting_admission_hashes: tuple[str, ...]
    authority_claims: tuple[str, ...]
    empty_joint_model_set: bool
    operative_state_unchanged: bool
    quarantine_hash: str
    forum: str

    @property
    def certificate_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "prior_state_hash": self.prior_state_hash,
            "conflicting_admission_hashes": list(self.conflicting_admission_hashes),
            "authority_claims": list(self.authority_claims),
            "empty_joint_model_set": self.empty_joint_model_set,
            "operative_state_unchanged": self.operative_state_unchanged,
            "quarantine_hash": self.quarantine_hash,
            "forum": self.forum,
            "transition": "ROUTE",
            "cause": "CONFLICT",
        }


@dataclass(frozen=True)
class ConstitutionalAdmissionResult:
    outcome: AdmissionOutcome
    prior_state_hash: str
    next_state_hash: str | None = None
    conflict: ConflictCertificate | None = None

    def __bool__(self) -> bool:
        raise TypeError("ConstitutionalAdmissionResult is not Boolean; inspect outcome")


def constitutional_admission(
    problem: SeamProblem, prior_admissions: Sequence[ConstraintAdmission], admission: ConstraintAdmission,
    *, authority_claims: Sequence[str], forum: str,
) -> ConstitutionalAdmissionResult:
    _, prior = semantic_state(problem, prior_admissions)
    try:
        _, candidate = semantic_state(problem, tuple(prior_admissions) + (admission,))
    except InventionError as exc:
        if "eliminate every semantic world" not in str(exc):
            raise
        hashes = tuple(item.admission_hash for item in tuple(prior_admissions) + (admission,))
        certificate = ConflictCertificate(
            prior_state_hash=prior.state_hash,
            conflicting_admission_hashes=hashes,
            authority_claims=tuple(authority_claims),
            empty_joint_model_set=True,
            operative_state_unchanged=True,
            quarantine_hash=_digest({"admissions": hashes, "status": "quarantined"}),
            forum=forum,
        )
        return ConstitutionalAdmissionResult(AdmissionOutcome.CONFLICT, prior.state_hash, conflict=certificate)
    return ConstitutionalAdmissionResult(AdmissionOutcome.ADMITTED, prior.state_hash, candidate.state_hash)
