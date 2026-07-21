"""Policy-sanctioned counterfactual repair certificates for reliance decisions."""

from __future__ import annotations

import dataclasses
import itertools
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from bulla.experimental.frsl import canonical_hash
from bulla.reliance import RELY, RelianceDecision, ReliancePolicy, decide


class RepairKind(str, Enum):
    EVIDENCE = "evidence"
    POLICY_SUBSTITUTION = "policy_substitution"
    AUTHORITY = "authority"
    TIME = "time"
    RECOURSE = "recourse"


class RepairTarget(str, Enum):
    VERIFICATION = "verification"
    POLICY = "policy"


@dataclass(frozen=True)
class RepairOption:
    option_id: str
    kind: RepairKind
    target: RepairTarget
    dimension: str
    value: Any
    statement: str
    cost: Mapping[str, int]
    authority_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.option_id or not self.dimension or not self.statement:
            raise ValueError("repair option id, dimension, and statement are required")
        if not isinstance(self.cost, Mapping) or any(
            not isinstance(k, str)
            or not k
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v < 0
            for k, v in self.cost.items()
        ):
            raise ValueError("repair option cost must be non-negative integers")
        if self.target is RepairTarget.POLICY and not self.authority_ref:
            raise ValueError("policy substitutions require a declared authority_ref")

    def to_dict(self) -> dict:
        return {
            "option_id": self.option_id,
            "kind": self.kind.value,
            "target": self.target.value,
            "dimension": self.dimension,
            "value": self.value,
            "statement": self.statement,
            "cost": dict(self.cost),
            "authority_ref": self.authority_ref,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairOption":
        expected = {
            "option_id", "kind", "target", "dimension", "value", "statement",
            "cost", "authority_ref",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError(f"repair option fields must be exactly {sorted(expected)}")
        return cls(
            option_id=value["option_id"],
            kind=RepairKind(value["kind"]),
            target=RepairTarget(value["target"]),
            dimension=value["dimension"],
            value=value["value"],
            statement=value["statement"],
            cost=value["cost"],
            authority_ref=value["authority_ref"],
        )


@dataclass(frozen=True)
class RepairCatalog:
    catalog_id: str
    options: tuple[RepairOption, ...]
    max_combination_size: int = 4

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))
        if not self.catalog_id or not self.options:
            raise ValueError("repair catalog id and options are required")
        if len({x.option_id for x in self.options}) != len(self.options):
            raise ValueError("repair option ids must be unique")
        if not isinstance(self.max_combination_size, int) or not (
            1 <= self.max_combination_size <= 12
        ):
            raise ValueError("max_combination_size must be between 1 and 12")

    @property
    def catalog_hash(self) -> str:
        return canonical_hash(
            {
                "catalog_id": self.catalog_id,
                "max_combination_size": self.max_combination_size,
                "options": [x.to_dict() for x in self.options],
            }
        )

    def to_dict(self) -> dict:
        return {
            "catalog_id": self.catalog_id,
            "max_combination_size": self.max_combination_size,
            "options": [x.to_dict() for x in self.options],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairCatalog":
        expected = {"catalog_id", "max_combination_size", "options"}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError(f"repair catalog fields must be exactly {sorted(expected)}")
        if not isinstance(value["options"], list):
            raise ValueError("repair catalog options must be a list")
        return cls(
            catalog_id=value["catalog_id"],
            max_combination_size=value["max_combination_size"],
            options=tuple(RepairOption.from_dict(x) for x in value["options"]),
        )


@dataclass(frozen=True)
class RepairPlan:
    catalog_hash: str
    option_ids: tuple[str, ...]
    baseline_outcome: str
    repaired_outcome: str
    cost: Mapping[str, int]
    minimality: str
    repaired_view_hash: str
    repaired_policy_hash: str

    @property
    def plan_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "catalog_hash": self.catalog_hash,
            "option_ids": list(self.option_ids),
            "baseline_outcome": self.baseline_outcome,
            "repaired_outcome": self.repaired_outcome,
            "cost": dict(self.cost),
            "minimality": self.minimality,
            "repaired_view_hash": self.repaired_view_hash,
            "repaired_policy_hash": self.repaired_policy_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairPlan":
        expected = {
            "catalog_hash", "option_ids", "baseline_outcome", "repaired_outcome",
            "cost", "minimality", "repaired_view_hash", "repaired_policy_hash",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError(f"repair plan fields must be exactly {sorted(expected)}")
        return cls(
            catalog_hash=value["catalog_hash"],
            option_ids=tuple(value["option_ids"]),
            baseline_outcome=value["baseline_outcome"],
            repaired_outcome=value["repaired_outcome"],
            cost=value["cost"],
            minimality=value["minimality"],
            repaired_view_hash=value["repaired_view_hash"],
            repaired_policy_hash=value["repaired_policy_hash"],
        )


def _policy_with(policy: ReliancePolicy, dimension: str, value: Any) -> ReliancePolicy:
    if dimension not in ReliancePolicy.__dataclass_fields__ or dimension in (
        "name",
        "routing",
    ):
        raise ValueError(f"policy repair cannot change dimension {dimension!r}")
    current = getattr(policy, dimension)
    if current is None:
        accepted = (value,)
    elif isinstance(current, tuple):
        accepted = tuple(dict.fromkeys(current + (value,)))
    elif isinstance(current, bool):
        if not isinstance(value, bool):
            raise ValueError(f"policy boolean {dimension!r} requires a boolean")
        accepted = value
    elif isinstance(current, str):
        if not isinstance(value, str):
            raise ValueError(f"policy field {dimension!r} requires a string")
        accepted = value
    else:
        raise ValueError(f"unsupported policy repair dimension {dimension!r}")
    return dataclasses.replace(policy, **{dimension: accepted})


def apply_repair_options(
    verification_view: Mapping[str, Any],
    policy: ReliancePolicy,
    options: Sequence[RepairOption],
) -> tuple[dict[str, Any], ReliancePolicy, RelianceDecision]:
    view = dict(verification_view)
    repaired_policy = policy
    for option in options:
        if option.target is RepairTarget.VERIFICATION:
            if option.dimension not in view:
                raise ValueError(
                    f"verification repair references unknown dimension {option.dimension!r}"
                )
            view[option.dimension] = option.value
        else:
            repaired_policy = _policy_with(
                repaired_policy,
                option.dimension,
                option.value,
            )
    return view, repaired_policy, decide(view, repaired_policy)


def _aggregate_cost(options: Sequence[RepairOption]) -> dict[str, int]:
    result: dict[str, int] = {}
    for option in options:
        for name, value in option.cost.items():
            result[name] = result.get(name, 0) + value
    return {name: result[name] for name in sorted(result)}


def minimal_repairs(
    verification_view: Mapping[str, Any],
    policy: ReliancePolicy,
    catalog: RepairCatalog,
    *,
    desired_outcome: str = RELY,
) -> tuple[RepairPlan, ...]:
    """Enumerate the inclusion-minimal repair antichain in a declared catalog."""
    baseline = decide(dict(verification_view), policy)
    if baseline.outcome == desired_outcome:
        return ()
    found: list[frozenset[str]] = []
    plans: list[RepairPlan] = []
    limit = min(catalog.max_combination_size, len(catalog.options))
    for size in range(1, limit + 1):
        for subset in itertools.combinations(catalog.options, size):
            option_ids = frozenset(x.option_id for x in subset)
            if any(existing < option_ids for existing in found):
                continue
            view, repaired_policy, decision = apply_repair_options(
                verification_view,
                policy,
                subset,
            )
            if decision.outcome != desired_outcome:
                continue
            found.append(option_ids)
            plans.append(
                RepairPlan(
                    catalog_hash=catalog.catalog_hash,
                    option_ids=tuple(sorted(option_ids)),
                    baseline_outcome=baseline.outcome,
                    repaired_outcome=decision.outcome,
                    cost=_aggregate_cost(subset),
                    minimality="exact-declared-candidate-space",
                    repaired_view_hash=canonical_hash(view),
                    repaired_policy_hash=repaired_policy.policy_hash,
                )
            )
    return tuple(
        sorted(
            plans,
            key=lambda plan: (
                sum(plan.cost.values()),
                len(plan.option_ids),
                plan.option_ids,
            ),
        )
    )


def verify_repair_plan(
    plan: RepairPlan,
    verification_view: Mapping[str, Any],
    policy: ReliancePolicy,
    catalog: RepairCatalog,
) -> bool:
    if plan.catalog_hash != catalog.catalog_hash:
        return False
    by_id = {x.option_id: x for x in catalog.options}
    if not plan.option_ids or any(x not in by_id for x in plan.option_ids):
        return False
    selected = tuple(by_id[x] for x in plan.option_ids)
    try:
        view, repaired_policy, decision = apply_repair_options(
            verification_view,
            policy,
            selected,
        )
    except (ValueError, TypeError):
        return False
    if (
        decide(dict(verification_view), policy).outcome != plan.baseline_outcome
        or decision.outcome != plan.repaired_outcome
        or decision.outcome != RELY
        or canonical_hash(view) != plan.repaired_view_hash
        or repaired_policy.policy_hash != plan.repaired_policy_hash
        or _aggregate_cost(selected) != dict(plan.cost)
        or plan.minimality != "exact-declared-candidate-space"
    ):
        return False
    for index in range(len(selected)):
        smaller = selected[:index] + selected[index + 1 :]
        try:
            _, _, smaller_decision = apply_repair_options(
                verification_view,
                policy,
                smaller,
            )
        except (ValueError, TypeError):
            continue
        if smaller_decision.outcome == RELY:
            return False
    return True
