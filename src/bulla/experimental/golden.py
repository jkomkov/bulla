"""Golden Suite v0.1 experimental evidence and stress-test primitives.

The Golden Suite is deliberately a *captive* validation surface.  It can make
an eventual outsider replay cheaper and more informative, but its own reports
never claim independent attestation.  Nothing in this module is exported by
``bulla``'s stable package surface.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from bulla.action_receipt import build_action_receipt
from bulla.experimental.frsl import canonical_hash
from bulla.experimental.invention import InventionError


PROFILE = "bulla.golden-suite/0.1-experimental"
BASELINE_ENGINE_COMMIT = "30619618ed74c134aa94cbf7c6f5f8ef440df460"
BASELINE_PILOT_COMMIT = "cbaa41da"
GOLDEN_ACTIONS = frozenset(
    {
        "bulla.golden.freeze",
        "bulla.golden.run",
        "bulla.golden.submit",
        "bulla.golden.reveal",
    }
)
_DIGEST_PREFIX = "sha256:"


def _digest(value: Any) -> str:
    return canonical_hash(value)


def _require_digest(value: str, where: str) -> None:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX) or len(value) != 71:
        raise InventionError(f"{where} must be sha256:<64 lowercase hex>")
    try:
        int(value[len(_DIGEST_PREFIX) :], 16)
    except ValueError as exc:
        raise InventionError(f"{where} must be sha256:<64 lowercase hex>") from exc


def _closed(value: Mapping[str, Any], required: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != required:
        raise InventionError(f"{where} has unknown or missing fields")
    return dict(value)


class OracleClass(str, enum.Enum):
    MACHINE = "MACHINE"
    PROPERTY = "PROPERTY"
    ADJUDICATION = "ADJUDICATION"


class MarginPrecision(str, enum.Enum):
    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"
    UNRESOLVED = "UNRESOLVED"


class MarginDirection(str, enum.Enum):
    HIGHER_IS_SAFER = "HIGHER_IS_SAFER"
    LOWER_IS_SAFER = "LOWER_IS_SAFER"
    ZERO_REQUIRED = "ZERO_REQUIRED"


@dataclass(frozen=True)
class MarginCoordinate:
    name: str
    precision: MarginPrecision
    direction: MarginDirection
    unit: str
    value: int | None = None
    witness_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.unit:
            raise InventionError("margin coordinate requires name and unit")
        if self.precision is MarginPrecision.UNRESOLVED:
            if self.value is not None:
                raise InventionError("unresolved margin cannot carry a numeric value")
        elif not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 0:
            raise InventionError("resolved margin must be a non-negative integer")
        if self.witness_hash is not None:
            _require_digest(self.witness_hash, "margin.witness_hash")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "precision": self.precision.value,
            "direction": self.direction.value,
            "unit": self.unit,
            "value": self.value,
            "witness_hash": self.witness_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MarginCoordinate":
        d = _closed(
            value,
            {"name", "precision", "direction", "unit", "value", "witness_hash"},
            "margin_coordinate",
        )
        return cls(
            name=d["name"],
            precision=MarginPrecision(d["precision"]),
            direction=MarginDirection(d["direction"]),
            unit=d["unit"],
            value=d["value"],
            witness_hash=d["witness_hash"],
        )


@dataclass(frozen=True)
class MarginVector:
    coordinates: tuple[MarginCoordinate, ...]

    def __post_init__(self) -> None:
        items = tuple(self.coordinates)
        names = [item.name for item in items]
        if len(names) != len(set(names)):
            raise InventionError("margin coordinates must have unique names")
        object.__setattr__(self, "coordinates", tuple(sorted(items, key=lambda item: item.name)))

    def __bool__(self) -> bool:
        raise TypeError("MarginVector is non-Boolean and has no aggregate safety score")

    @property
    def vector_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinates": [item.to_dict() for item in self.coordinates],
            "aggregation": "forbidden",
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MarginVector":
        d = _closed(value, {"coordinates", "aggregation"}, "margin_vector")
        if d["aggregation"] != "forbidden" or not isinstance(d["coordinates"], list):
            raise InventionError("margin vector aggregation must be forbidden")
        return cls(tuple(MarginCoordinate.from_dict(item) for item in d["coordinates"]))


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    family: str
    oracle_class: OracleClass
    input_hashes: tuple[str, ...]
    falsification_rule: str
    margin_coordinates: tuple[str, ...]
    resource_bounds: Mapping[str, int]
    provenance: Mapping[str, Any]
    partition: str

    def __post_init__(self) -> None:
        if not self.case_id or not self.family or not self.falsification_rule:
            raise InventionError("golden case requires id, family, and falsification rule")
        if self.partition not in {"design", "holdout"}:
            raise InventionError("golden case partition must be design or holdout")
        hashes = tuple(self.input_hashes)
        if not hashes:
            raise InventionError("golden case requires at least one input hash")
        for value in hashes:
            _require_digest(value, "golden_case.input_hash")
        margins = tuple(self.margin_coordinates)
        if len(margins) != len(set(margins)) or any(not item for item in margins):
            raise InventionError("golden case margin names must be unique and non-empty")
        bounds = dict(self.resource_bounds)
        if any(
            not isinstance(name, str)
            or not name
            or not isinstance(bound, int)
            or isinstance(bound, bool)
            or bound <= 0
            for name, bound in bounds.items()
        ):
            raise InventionError("golden case resource bounds must be positive integers")
        object.__setattr__(self, "input_hashes", hashes)
        object.__setattr__(self, "margin_coordinates", margins)
        object.__setattr__(self, "resource_bounds", bounds)
        object.__setattr__(self, "provenance", dict(self.provenance))

    @property
    def case_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "family": self.family,
            "oracle_class": self.oracle_class.value,
            "input_hashes": list(self.input_hashes),
            "falsification_rule": self.falsification_rule,
            "margin_coordinates": list(self.margin_coordinates),
            "resource_bounds": dict(sorted(self.resource_bounds.items())),
            "provenance": dict(self.provenance),
            "partition": self.partition,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GoldenCase":
        d = _closed(
            value,
            {
                "case_id",
                "family",
                "oracle_class",
                "input_hashes",
                "falsification_rule",
                "margin_coordinates",
                "resource_bounds",
                "provenance",
                "partition",
            },
            "golden_case",
        )
        return cls(
            case_id=d["case_id"],
            family=d["family"],
            oracle_class=OracleClass(d["oracle_class"]),
            input_hashes=tuple(d["input_hashes"]),
            falsification_rule=d["falsification_rule"],
            margin_coordinates=tuple(d["margin_coordinates"]),
            resource_bounds=d["resource_bounds"],
            provenance=d["provenance"],
            partition=d["partition"],
        )


@dataclass(frozen=True)
class OracleCommitment:
    case_id: str
    oracle_hash: str
    commitment: str

    def __post_init__(self) -> None:
        if not self.case_id:
            raise InventionError("oracle commitment requires case_id")
        _require_digest(self.oracle_hash, "oracle_commitment.oracle_hash")
        _require_digest(self.commitment, "oracle_commitment.commitment")

    @classmethod
    def create(cls, case_id: str, oracle_output: Mapping[str, Any], nonce: str) -> "OracleCommitment":
        if not isinstance(nonce, str) or len(nonce.encode("utf-8")) < 32:
            raise InventionError("oracle nonce must contain at least 32 bytes of entropy material")
        oracle_hash = _digest(dict(oracle_output))
        commitment = _digest(
            {
                "domain": "bulla.golden.oracle/0.1",
                "case_id": case_id,
                "oracle_hash": oracle_hash,
                "nonce": nonce,
            }
        )
        return cls(case_id, oracle_hash, commitment)

    def verifies(self, oracle_output: Mapping[str, Any], nonce: str) -> bool:
        try:
            expected = self.create(self.case_id, oracle_output, nonce)
        except InventionError:
            return False
        return expected == self

    def to_dict(self, *, public: bool = True) -> dict[str, Any]:
        result = {"case_id": self.case_id, "commitment": self.commitment}
        if not public:
            result["oracle_hash"] = self.oracle_hash
        return result


def merkle_root(commitments: Iterable[OracleCommitment]) -> str:
    leaves = sorted(item.commitment for item in commitments)
    if not leaves:
        return _digest({"domain": "bulla.golden.empty-merkle/0.1"})
    level = leaves
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            _digest(
                {
                    "domain": "bulla.golden.merkle-node/0.1",
                    "left": level[index],
                    "right": level[index + 1],
                }
            )
            for index in range(0, len(level), 2)
        ]
    return level[0]


@dataclass(frozen=True)
class GoldenSuiteManifest:
    suite_version: str
    baseline_engine_commit: str
    baseline_pilot_commit: str
    candidate_commit: str
    family_manifests: Mapping[str, str]
    source_inventory_hash: str
    case_merkle_root: str
    oracle_commitment_root: str
    verifier_hashes: Mapping[str, str]
    environment_matrix: tuple[Mapping[str, str], ...]
    evidence_status: str = "internally-verified/captive"
    external_replay_status: str = "blocked-by-sprint-scope"
    profile: str = PROFILE

    def __post_init__(self) -> None:
        if self.profile != PROFILE or self.suite_version != "0.1":
            raise InventionError("unsupported Golden Suite profile")
        if self.baseline_engine_commit != BASELINE_ENGINE_COMMIT:
            raise InventionError("Golden Suite baseline engine commit drifted")
        if self.baseline_pilot_commit != BASELINE_PILOT_COMMIT:
            raise InventionError("Golden Suite baseline pilot commit drifted")
        if self.evidence_status != "internally-verified/captive":
            raise InventionError("Golden Suite may not claim independent evidence")
        if self.external_replay_status != "blocked-by-sprint-scope":
            raise InventionError("Golden Suite v0.1 external replay must remain blocked")
        for name, value in {
            **dict(self.family_manifests),
            **dict(self.verifier_hashes),
            "source_inventory": self.source_inventory_hash,
            "case_merkle": self.case_merkle_root,
            "oracle_root": self.oracle_commitment_root,
        }.items():
            _require_digest(value, f"golden_manifest.{name}")
        matrix = tuple(dict(item) for item in self.environment_matrix)
        if not matrix:
            raise InventionError("Golden Suite requires an environment matrix")
        object.__setattr__(self, "family_manifests", dict(sorted(self.family_manifests.items())))
        object.__setattr__(self, "verifier_hashes", dict(sorted(self.verifier_hashes.items())))
        object.__setattr__(self, "environment_matrix", matrix)

    @property
    def manifest_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "suite_version": self.suite_version,
            "baseline_engine_commit": self.baseline_engine_commit,
            "baseline_pilot_commit": self.baseline_pilot_commit,
            "candidate_commit": self.candidate_commit,
            "family_manifests": dict(self.family_manifests),
            "source_inventory_hash": self.source_inventory_hash,
            "case_merkle_root": self.case_merkle_root,
            "oracle_commitment_root": self.oracle_commitment_root,
            "verifier_hashes": dict(self.verifier_hashes),
            "environment_matrix": [dict(item) for item in self.environment_matrix],
            "evidence_status": self.evidence_status,
            "external_replay_status": self.external_replay_status,
        }


@dataclass(frozen=True)
class GoldenCaseResult:
    case_id: str
    observed_exit: str
    certificate_type: str | None
    passed: bool | None
    margin: MarginVector
    runtime_ns: int
    peak_memory_bytes: int
    exhaustion_cause: str | None = None

    def __post_init__(self) -> None:
        if not self.case_id or not self.observed_exit:
            raise InventionError("golden result requires case and exit")
        if self.passed not in {True, False, None}:
            raise InventionError("golden result passed must be true, false, or null")
        for name in ("runtime_ns", "peak_memory_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "observed_exit": self.observed_exit,
            "certificate_type": self.certificate_type,
            "passed": self.passed,
            "margin": self.margin.to_dict(),
            "runtime_ns": self.runtime_ns,
            "peak_memory_bytes": self.peak_memory_bytes,
            "exhaustion_cause": self.exhaustion_cause,
        }


@dataclass(frozen=True)
class GoldenRunReport:
    manifest_hash: str
    backend: str
    environment: Mapping[str, str]
    results: tuple[GoldenCaseResult, ...]
    source_inventory_hash: str
    external_validation: bool = False

    def __post_init__(self) -> None:
        _require_digest(self.manifest_hash, "golden_run.manifest_hash")
        _require_digest(self.source_inventory_hash, "golden_run.source_inventory_hash")
        if self.external_validation:
            raise InventionError("a captive Golden Suite run cannot claim external validation")
        results = tuple(self.results)
        ids = [item.case_id for item in results]
        if not self.backend or len(ids) != len(set(ids)):
            raise InventionError("golden run requires backend and unique case results")
        object.__setattr__(self, "environment", dict(self.environment))
        object.__setattr__(self, "results", tuple(sorted(results, key=lambda item: item.case_id)))

    @property
    def report_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "manifest_hash": self.manifest_hash,
            "backend": self.backend,
            "environment": dict(self.environment),
            "results": [item.to_dict() for item in self.results],
            "source_inventory_hash": self.source_inventory_hash,
            "evidence_status": "internally-verified/captive",
            "external_validation": False,
        }


@dataclass(frozen=True)
class SourceCapture:
    source_id: str
    upstream_owner: str
    source_url: str
    raw_content_hash: str
    captured_at: str
    retrieval_method: str
    redistribution_status: str
    parser_version: str
    direct_capture: bool
    executed_code: bool = False
    activity_proxy: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not all(
            isinstance(item, str) and item
            for item in (
                self.source_id,
                self.upstream_owner,
                self.source_url,
                self.captured_at,
                self.retrieval_method,
                self.redistribution_status,
                self.parser_version,
            )
        ):
            raise InventionError("source capture fields must be non-empty")
        _require_digest(self.raw_content_hash, "source_capture.raw_content_hash")
        if self.executed_code:
            raise InventionError("Golden Suite found-data capture may never execute third-party code")
        if self.redistribution_status not in {"redistributable", "hash-only", "unknown"}:
            raise InventionError("unknown redistribution status")
        if self.activity_proxy is not None and "basis" not in self.activity_proxy:
            raise InventionError("activity proxy must name its basis")

    @property
    def capture_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "upstream_owner": self.upstream_owner,
            "source_url": self.source_url,
            "raw_content_hash": self.raw_content_hash,
            "captured_at": self.captured_at,
            "retrieval_method": self.retrieval_method,
            "redistribution_status": self.redistribution_status,
            "parser_version": self.parser_version,
            "direct_capture": self.direct_capture,
            "executed_code": False,
            "activity_proxy": dict(self.activity_proxy) if self.activity_proxy else None,
        }


@dataclass(frozen=True)
class WitnessOperatorProfile:
    operator_id: str
    controlling_entity: str
    key_custodian: str
    infrastructure_provider: str
    software_lineage: str
    jurisdiction: str
    anchor_domain: str
    attestation_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        fields = (
            self.operator_id,
            self.controlling_entity,
            self.key_custodian,
            self.infrastructure_provider,
            self.software_lineage,
            self.jurisdiction,
            self.anchor_domain,
        )
        if any(not isinstance(item, str) or not item for item in fields):
            raise InventionError("witness operator profile fields must be non-empty")
        refs = tuple(self.attestation_refs)
        if not refs:
            raise InventionError("witness profile requires asserted-basis attestation references")
        for value in refs:
            _require_digest(value, "witness_operator_profile.attestation_ref")
        object.__setattr__(self, "attestation_refs", refs)

    @property
    def profile_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_id": self.operator_id,
            "controlling_entity": self.controlling_entity,
            "key_custodian": self.key_custodian,
            "infrastructure_provider": self.infrastructure_provider,
            "software_lineage": self.software_lineage,
            "jurisdiction": self.jurisdiction,
            "anchor_domain": self.anchor_domain,
            "attestation_refs": list(self.attestation_refs),
            "claim_boundary": "diversity-relative-to-asserted-profiles",
        }


_DIVERSITY_AXES = (
    "controlling_entity",
    "key_custodian",
    "infrastructure_provider",
    "software_lineage",
    "jurisdiction",
    "anchor_domain",
)


@dataclass(frozen=True)
class WitnessDiversityPolicy:
    required_distinct_axes: tuple[str, ...]
    minimum_distinct_axes: int

    def __post_init__(self) -> None:
        axes = tuple(self.required_distinct_axes)
        if len(axes) != len(set(axes)) or not set(axes).issubset(_DIVERSITY_AXES):
            raise InventionError("witness diversity policy has unknown or duplicate axes")
        if not isinstance(self.minimum_distinct_axes, int) or not 1 <= self.minimum_distinct_axes <= len(_DIVERSITY_AXES):
            raise InventionError("invalid witness diversity minimum")
        object.__setattr__(self, "required_distinct_axes", axes)

    @property
    def policy_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_distinct_axes": list(self.required_distinct_axes),
            "minimum_distinct_axes": self.minimum_distinct_axes,
        }


@dataclass(frozen=True)
class WitnessDiversityCertificate:
    operator_profile_hashes: tuple[str, str]
    policy_hash: str
    distinct_axes: tuple[str, ...]
    missing_required_axes: tuple[str, ...]
    passes: bool
    claim_boundary: str = "relative-to-attested-operator-profiles"

    def __post_init__(self) -> None:
        if len(self.operator_profile_hashes) != 2 or len(set(self.operator_profile_hashes)) != 2:
            raise InventionError("diversity certificate requires two distinct profile hashes")
        for value in (*self.operator_profile_hashes, self.policy_hash):
            _require_digest(value, "witness_diversity_certificate.hash")
        if self.claim_boundary != "relative-to-attested-operator-profiles":
            raise InventionError("witness diversity claim boundary may not be widened")

    @property
    def certificate_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_profile_hashes": list(self.operator_profile_hashes),
            "policy_hash": self.policy_hash,
            "distinct_axes": list(self.distinct_axes),
            "missing_required_axes": list(self.missing_required_axes),
            "passes": self.passes,
            "claim_boundary": self.claim_boundary,
        }


def assess_witness_diversity(
    left: WitnessOperatorProfile,
    right: WitnessOperatorProfile,
    policy: WitnessDiversityPolicy,
) -> WitnessDiversityCertificate:
    if left.operator_id == right.operator_id:
        raise InventionError("witness diversity requires distinct operator identities")
    distinct = tuple(axis for axis in _DIVERSITY_AXES if getattr(left, axis) != getattr(right, axis))
    missing = tuple(axis for axis in policy.required_distinct_axes if axis not in distinct)
    passes = not missing and len(distinct) >= policy.minimum_distinct_axes
    return WitnessDiversityCertificate(
        operator_profile_hashes=(left.profile_hash, right.profile_hash),
        policy_hash=policy.policy_hash,
        distinct_axes=distinct,
        missing_required_axes=missing,
        passes=passes,
    )


def authorize_diverse_revision(
    *args: Any,
    diversity_certificate: WitnessDiversityCertificate,
    **kwargs: Any,
) -> Any:
    """Versioned Golden profile wrapper around the existing revision kernel."""
    if not diversity_certificate.passes:
        raise InventionError("revision witness diversity failed before state mutation")
    from bulla.experimental.constitutional import authorize_revision

    return authorize_revision(*args, **kwargs)


@dataclass(frozen=True)
class ModelExpansionNeighborhood:
    closure_warrant_hash: str
    generator: Mapping[str, Any]
    exclusions: tuple[str, ...]
    maximum_expansions: int
    scope: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_digest(self.closure_warrant_hash, "expansion_neighborhood.closure_warrant_hash")
        if not self.generator or "kind" not in self.generator:
            raise InventionError("expansion neighborhood requires a declared generator")
        if not isinstance(self.maximum_expansions, int) or isinstance(self.maximum_expansions, bool) or self.maximum_expansions <= 0:
            raise InventionError("expansion neighborhood maximum must be positive")
        object.__setattr__(self, "generator", dict(self.generator))
        object.__setattr__(self, "exclusions", tuple(self.exclusions))
        object.__setattr__(self, "scope", dict(self.scope))

    @property
    def neighborhood_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "closure_warrant_hash": self.closure_warrant_hash,
            "generator": dict(self.generator),
            "exclusions": list(self.exclusions),
            "maximum_expansions": self.maximum_expansions,
            "scope": dict(self.scope),
        }


@dataclass(frozen=True)
class ClosureStressReport:
    neighborhood_hash: str
    base_outcomes: tuple[str, ...]
    expanded_outcomes: tuple[str, ...]
    within_declared_neighborhood: bool
    required_reserve_microunits: int
    held_reserve_microunits: int
    reserve_shortfall_microunits: int
    closure_breach: bool
    finality_reversal: bool
    new_epoch_required: bool
    term_stale: bool

    def __post_init__(self) -> None:
        _require_digest(self.neighborhood_hash, "closure_stress.neighborhood_hash")
        for name in (
            "required_reserve_microunits",
            "held_reserve_microunits",
            "reserve_shortfall_microunits",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"{name} must be non-negative")
        if self.reserve_shortfall_microunits != max(
            0, self.required_reserve_microunits - self.held_reserve_microunits
        ):
            raise InventionError("closure reserve shortfall is not recomputable")
        if self.new_epoch_required != (not self.within_declared_neighborhood):
            raise InventionError("outside-neighborhood expansion must require a new epoch")
        if self.term_stale != self.new_epoch_required:
            raise InventionError("old term must be stale after closure epoch change")

    @property
    def report_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "neighborhood_hash": self.neighborhood_hash,
            "base_outcomes": list(self.base_outcomes),
            "expanded_outcomes": list(self.expanded_outcomes),
            "within_declared_neighborhood": self.within_declared_neighborhood,
            "required_reserve_microunits": self.required_reserve_microunits,
            "held_reserve_microunits": self.held_reserve_microunits,
            "reserve_shortfall_microunits": self.reserve_shortfall_microunits,
            "closure_breach": self.closure_breach,
            "finality_reversal": self.finality_reversal,
            "new_epoch_required": self.new_epoch_required,
            "term_stale": self.term_stale,
        }


def stress_closure(
    neighborhood: ModelExpansionNeighborhood,
    *,
    base_outcomes: Sequence[str],
    expanded_outcomes: Sequence[str],
    losses_microunits: Mapping[str, int],
    held_reserve_microunits: int,
    model_risk_buffer_microunits: int,
    within_declared_neighborhood: bool,
    was_finalized: bool,
) -> ClosureStressReport:
    base = tuple(sorted(set(base_outcomes)))
    expanded = tuple(sorted(set(expanded_outcomes)))
    if not base or not set(base).issubset(expanded):
        raise InventionError("closure expansion must retain every base outcome")
    unknown = set(expanded) - set(losses_microunits)
    if unknown:
        raise InventionError(f"closure expansion has undeclared consequence classes: {sorted(unknown)}")
    required = max(losses_microunits[item] for item in expanded) + model_risk_buffer_microunits
    closure_breach = set(expanded) != set(base)
    reversal = bool(was_finalized and closure_breach)
    return ClosureStressReport(
        neighborhood_hash=neighborhood.neighborhood_hash,
        base_outcomes=base,
        expanded_outcomes=expanded,
        within_declared_neighborhood=within_declared_neighborhood,
        required_reserve_microunits=required,
        held_reserve_microunits=held_reserve_microunits,
        reserve_shortfall_microunits=max(0, required - held_reserve_microunits),
        closure_breach=closure_breach,
        finality_reversal=reversal,
        new_epoch_required=not within_declared_neighborhood,
        term_stale=not within_declared_neighborhood,
    )


@dataclass(frozen=True)
class AnytimeEnvelopeCertificate:
    problem_hash: str
    positive_region_hashes: tuple[str, ...]
    negative_region_hashes: tuple[str, ...]
    residual_region_hashes: tuple[str, ...]
    search_frontier: Mapping[str, Any]
    model_enumeration_complete: bool
    minimality: str

    def __post_init__(self) -> None:
        _require_digest(self.problem_hash, "anytime.problem_hash")
        regions = (
            tuple(self.positive_region_hashes),
            tuple(self.negative_region_hashes),
            tuple(self.residual_region_hashes),
        )
        for region in regions:
            if len(region) != len(set(region)):
                raise InventionError("anytime regions may not contain duplicates")
            for value in region:
                _require_digest(value, "anytime.region_hash")
        if set(regions[0]) & set(regions[1]) or set(regions[0]) & set(regions[2]) or set(regions[1]) & set(regions[2]):
            raise InventionError("anytime positive, negative, and residual regions must be disjoint")
        if self.minimality not in {"exact-declared-candidate-space", "unresolved"}:
            raise InventionError("unknown anytime minimality status")
        if not self.model_enumeration_complete and (regions[0] or regions[1]):
            raise InventionError("incomplete model enumeration cannot certify terminal regions")
        object.__setattr__(self, "positive_region_hashes", tuple(sorted(regions[0])))
        object.__setattr__(self, "negative_region_hashes", tuple(sorted(regions[1])))
        object.__setattr__(self, "residual_region_hashes", tuple(sorted(regions[2])))
        object.__setattr__(self, "search_frontier", dict(self.search_frontier))

    @property
    def certificate_hash(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_hash": self.problem_hash,
            "positive_region_hashes": list(self.positive_region_hashes),
            "negative_region_hashes": list(self.negative_region_hashes),
            "residual_region_hashes": list(self.residual_region_hashes),
            "search_frontier": dict(self.search_frontier),
            "model_enumeration_complete": self.model_enumeration_complete,
            "minimality": self.minimality,
        }


def anytime_refines(prior: AnytimeEnvelopeCertificate, next_: AnytimeEnvelopeCertificate) -> bool:
    """The answerable-ratchet order for same-problem anytime checkpoints."""
    return bool(
        prior.problem_hash == next_.problem_hash
        and set(prior.positive_region_hashes).issubset(next_.positive_region_hashes)
        and set(prior.negative_region_hashes).issubset(next_.negative_region_hashes)
        and set(next_.residual_region_hashes).issubset(prior.residual_region_hashes)
    )


class EconomicPhase(str, enum.Enum):
    OPEN = "OPEN"
    LOCKED = "LOCKED"
    PROVISIONAL = "PROVISIONAL"
    FINALIZED = "FINALIZED"
    ROUTED = "ROUTED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class EconomicState:
    phase: EconomicPhase = EconomicPhase.OPEN
    epoch: int = 0
    required_reserve_microunits: int = 0
    locked_microunits: int = 0
    released_microunits: int = 0
    action_executed: bool = False
    expiry_step: int = 10

    def __post_init__(self) -> None:
        for name in (
            "epoch",
            "required_reserve_microunits",
            "locked_microunits",
            "released_microunits",
            "expiry_step",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InventionError(f"economic state {name} must be non-negative")
        if self.released_microunits > self.locked_microunits:
            raise InventionError("economic state cannot release more than was locked")

    @property
    def available_lock(self) -> int:
        return self.locked_microunits - self.released_microunits


@dataclass(frozen=True)
class EconomicEvent:
    kind: str
    amount_microunits: int = 0
    epoch: int = 0
    authorized: bool = False
    closure_permitted: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {
            "LOCK",
            "EXECUTE",
            "REFINE",
            "RELEASE",
            "FINALIZE",
            "CONFLICT",
            "ROUTE",
            "REVISE",
            "EXPIRE",
        }:
            raise InventionError("unknown economic event")
        if not isinstance(self.amount_microunits, int) or isinstance(self.amount_microunits, bool) or self.amount_microunits < 0:
            raise InventionError("economic event amount must be non-negative")


@dataclass(frozen=True)
class EconomicTransition:
    prior: EconomicState
    event: EconomicEvent
    next_state: EconomicState
    accepted: bool
    cause: str


def apply_economic_event(state: EconomicState, event: EconomicEvent, *, step: int) -> EconomicTransition:
    """Pure fail-closed shadow-settlement state transition."""
    if state.phase in {
        EconomicPhase.FINALIZED,
        EconomicPhase.ROUTED,
        EconomicPhase.STALE,
        EconomicPhase.EXPIRED,
    }:
        return EconomicTransition(state, event, state, False, "TERMINAL_STATE")
    if event.kind == "CONFLICT":
        return EconomicTransition(state, event, state, False, "CONFLICT_NON_MUTATION")
    if event.kind == "ROUTE":
        next_state = replace(state, phase=EconomicPhase.ROUTED)
        return EconomicTransition(state, event, next_state, True, "ROUTED")
    if event.kind == "EXPIRE" or step >= state.expiry_step:
        next_state = replace(state, phase=EconomicPhase.EXPIRED)
        return EconomicTransition(state, event, next_state, True, "EXPIRED")
    if event.kind == "REVISE":
        if not event.authorized or event.epoch == state.epoch:
            return EconomicTransition(state, event, state, False, "SUPERSESSION_REQUIRED")
        return EconomicTransition(state, event, replace(state, phase=EconomicPhase.STALE), True, "TERM_STALE")
    if event.epoch != state.epoch:
        return EconomicTransition(state, event, state, False, "EPOCH_MISMATCH")
    if event.kind == "LOCK":
        if state.phase is not EconomicPhase.OPEN:
            return EconomicTransition(state, event, state, False, "LOCK_STATE_INVALID")
        if event.amount_microunits < state.required_reserve_microunits:
            return EconomicTransition(state, event, state, False, "LOCK_SHORTFALL")
        return EconomicTransition(
            state,
            event,
            replace(state, phase=EconomicPhase.LOCKED, locked_microunits=event.amount_microunits),
            True,
            "LOCKED",
        )
    if event.kind == "EXECUTE":
        if state.phase is not EconomicPhase.LOCKED or state.available_lock < state.required_reserve_microunits:
            return EconomicTransition(state, event, state, False, "INSUFFICIENT_LOCK")
        return EconomicTransition(
            state, event, replace(state, phase=EconomicPhase.PROVISIONAL, action_executed=True), True, "PROVISIONAL"
        )
    if event.kind == "REFINE":
        if not event.authorized or event.amount_microunits > state.required_reserve_microunits:
            return EconomicTransition(state, event, state, False, "INVALID_REFINEMENT")
        return EconomicTransition(
            state, event, replace(state, required_reserve_microunits=event.amount_microunits), True, "REFINED"
        )
    if event.kind == "RELEASE":
        if state.phase is not EconomicPhase.PROVISIONAL or not state.action_executed:
            return EconomicTransition(state, event, state, False, "RELEASE_STATE_INVALID")
        releasable = state.available_lock - state.required_reserve_microunits
        if not event.authorized or event.amount_microunits > max(0, releasable):
            return EconomicTransition(state, event, state, False, "INVALID_RELEASE")
        return EconomicTransition(
            state,
            event,
            replace(state, released_microunits=state.released_microunits + event.amount_microunits),
            True,
            "RELEASED",
        )
    if event.kind == "FINALIZE":
        if not event.authorized or not event.closure_permitted or not state.action_executed or state.required_reserve_microunits != 0:
            return EconomicTransition(state, event, state, False, "FINALITY_PRECONDITION_FAILED")
        return EconomicTransition(state, event, replace(state, phase=EconomicPhase.FINALIZED), True, "FINALIZED")
    return EconomicTransition(state, event, state, False, "UNREACHABLE")


def economic_invariants(state: EconomicState) -> tuple[str, ...]:
    failures: list[str] = []
    if state.released_microunits > state.locked_microunits:
        failures.append("released_exceeds_locked")
    if state.phase is EconomicPhase.PROVISIONAL and (
        not state.action_executed or state.available_lock < state.required_reserve_microunits
    ):
        failures.append("provisional_without_sufficient_lock")
    if state.phase is EconomicPhase.FINALIZED and (
        not state.action_executed or state.required_reserve_microunits != 0
    ):
        failures.append("invalid_finalized_state")
    return tuple(failures)


def mint_golden_receipt(
    action_type: str,
    *,
    subject: Mapping[str, Any],
    artifact_hash: str,
    envelope: Any,
    timestamp: str,
    producer: Mapping[str, Any],
) -> Any:
    if action_type not in GOLDEN_ACTIONS:
        raise InventionError("unsupported Golden Suite action")
    _require_digest(artifact_hash, "golden_receipt.artifact_hash")
    return build_action_receipt(
        action={"type": action_type, "subject": {"profile": PROFILE, **dict(subject)}},
        diagnostic_ref={"status": "reference", "ref": artifact_hash},
        envelope=envelope,
        evidence_refs=(
            {"name": "golden_artifact", "hash": artifact_hash, "grounding": "execution_verified"},
        ),
        timestamp=timestamp,
        producer=dict(producer),
    )


def sha256_bytes(data: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(data).hexdigest()
