"""Finite, model-relative query answerability under mobile corruption.

This module is a source-only research instrument.  It does not establish that
an action occurred, that a log is non-equivocating, that authority is valid, or
that a remedy is reachable.  It answers one narrower question:

    Under the declared finite execution, custody, retention, and corruption
    model, can a later challenger still distinguish every pair of executions
    that disagree on one consequential query?

The implementation is deliberately exact and bounded.  It enumerates admissible
mobile-corruption schedules in deterministic order, emits a replayable erasure
counterexample when one exists, and fails closed when the state limit is
exceeded.  No output is an ActionReceipt or part of the stable Bulla API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import itertools
import re
from typing import Any, Iterable, Iterator, Mapping, Sequence

from bulla._canonical import canonical_jcs_int


PROFILE = "bulla.query-answerability/0.1-experimental"
CATALOG_PROFILE = "bulla.query-answerability-catalog/0.1-experimental"
PLAN_PROFILE = "bulla.query-answerability-plan/0.1-experimental"
CHALLENGER = "@challenger"
DEFAULT_STATE_LIMIT = 1_000_000
MAX_CANDIDATES = 20
EDGE_KINDS = frozenset({"retain", "transfer", "commit", "deliver"})
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

ASSUMPTIONS = {
    "classification": "MODEL_RELATIVE",
    "time": (
        "discrete custody epochs with unit-step edges; only delivery enters "
        "the later challenge epoch"
    ),
    "corruption": (
        "a corrupted holder-time cannot observe, retain, transfer, commit, "
        "or deliver evidence; erased evidence does not resurrect"
    ),
    "integrity": (
        "uncompromised signatures and occurrence-bound committed values "
        "cannot be forged or rewritten"
    ),
    "observation": "the challenger sees only atoms on surviving custody paths",
    "not_assessed": [
        "non_equivocation",
        "authority_binding",
        "remedy_reachability",
    ],
}


class AnswerabilityError(ValueError):
    """Base class for model and bounded-search failures."""


class ModelInvalid(AnswerabilityError):
    """The submitted finite model is malformed or semantically inconsistent."""


class SearchLimitExceeded(AnswerabilityError):
    """The exact bounded search crossed the declared state limit."""


class AssessmentStatus(str, Enum):
    ANSWERABLE = "ANSWERABLE"
    NOT_ANSWERABLE = "NOT_ANSWERABLE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    MODEL_INVALID = "MODEL_INVALID"


class PlanStatus(str, Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    MODEL_INVALID = "MODEL_INVALID"


def _digest(value: Any) -> str:
    encoded = canonical_jcs_int(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ModelInvalid(f"{label} keys mismatch; missing={missing}, extra={extra}")


def _require_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ModelInvalid(f"{label} must be a non-empty, surrounding-whitespace-free string")
    return value


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise ModelInvalid(f"{label} must be a sha256:<64 lowercase hex> digest")
    return value


def _require_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModelInvalid(f"{label} must be a non-negative integer")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ModelInvalid(f"{label} must be Boolean")
    return value


def _unique(values: Iterable[str], label: str) -> None:
    materialized = list(values)
    if len(materialized) != len(set(materialized)):
        raise ModelInvalid(f"{label} identifiers must be unique")


@dataclass(frozen=True)
class ControlGroup:
    group_id: str
    holders: tuple[str, ...]
    corruptible: bool = True

    def __post_init__(self) -> None:
        _require_identifier(self.group_id, "control_group.group_id")
        if not self.holders:
            raise ModelInvalid("control_group.holders must not be empty")
        for holder in self.holders:
            _require_identifier(holder, "control_group.holder")
            if holder == CHALLENGER:
                raise ModelInvalid(f"{CHALLENGER} is reserved for challenge delivery")
        _unique(self.holders, f"control_group[{self.group_id}].holders")
        _require_bool(self.corruptible, "control_group.corruptible")

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "holders": list(self.holders),
            "corruptible": self.corruptible,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ControlGroup":
        _strict_keys(value, {"group_id", "holders", "corruptible"}, "control_group")
        holders = value["holders"]
        if not isinstance(holders, list):
            raise ModelInvalid("control_group.holders must be an array")
        return cls(
            _require_identifier(value["group_id"], "control_group.group_id"),
            tuple(_require_identifier(item, "control_group.holder") for item in holders),
            _require_bool(value["corruptible"], "control_group.corruptible"),
        )


@dataclass(frozen=True)
class ExecutionClass:
    execution_id: str
    query_value: str

    def __post_init__(self) -> None:
        _require_identifier(self.execution_id, "execution.execution_id")
        _require_digest(self.query_value, "execution.query_value")

    def to_dict(self) -> dict[str, str]:
        return {
            "execution_id": self.execution_id,
            "query_value": self.query_value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutionClass":
        _strict_keys(value, {"execution_id", "query_value"}, "execution")
        return cls(
            _require_identifier(value["execution_id"], "execution.execution_id"),
            _require_digest(value["query_value"], "execution.query_value"),
        )


@dataclass(frozen=True)
class EvidenceAtom:
    atom_id: str
    occurrence_binding: str
    observed_at: int
    holder: str
    values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_identifier(self.atom_id, "evidence_atom.atom_id")
        _require_digest(self.occurrence_binding, "evidence_atom.occurrence_binding")
        _require_nonnegative_int(self.observed_at, "evidence_atom.observed_at")
        _require_identifier(self.holder, "evidence_atom.holder")
        if not self.values:
            raise ModelInvalid("evidence_atom.values must not be empty")
        execution_ids = []
        for execution_id, value_digest in self.values:
            execution_ids.append(_require_identifier(execution_id, "evidence_atom.execution_id"))
            _require_digest(value_digest, "evidence_atom.value_digest")
        _unique(execution_ids, f"evidence_atom[{self.atom_id}].values")

    def value_map(self) -> dict[str, str]:
        return dict(self.values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "occurrence_binding": self.occurrence_binding,
            "observed_at": self.observed_at,
            "holder": self.holder,
            "values": {
                execution_id: value_digest
                for execution_id, value_digest in sorted(self.values)
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceAtom":
        _strict_keys(
            value,
            {"atom_id", "occurrence_binding", "observed_at", "holder", "values"},
            "evidence_atom",
        )
        values = value["values"]
        if not isinstance(values, dict):
            raise ModelInvalid("evidence_atom.values must be an object")
        return cls(
            _require_identifier(value["atom_id"], "evidence_atom.atom_id"),
            _require_digest(
                value["occurrence_binding"], "evidence_atom.occurrence_binding"
            ),
            _require_nonnegative_int(value["observed_at"], "evidence_atom.observed_at"),
            _require_identifier(value["holder"], "evidence_atom.holder"),
            tuple(
                sorted(
                    (
                        _require_identifier(execution_id, "evidence_atom.execution_id"),
                        _require_digest(value_digest, "evidence_atom.value_digest"),
                    )
                    for execution_id, value_digest in values.items()
                )
            ),
        )


@dataclass(frozen=True)
class CustodyEdge:
    atom_id: str
    source_holder: str
    target_holder: str
    source_epoch: int
    target_epoch: int
    kind: str

    def __post_init__(self) -> None:
        _require_identifier(self.atom_id, "custody_edge.atom_id")
        _require_identifier(self.source_holder, "custody_edge.source_holder")
        _require_identifier(self.target_holder, "custody_edge.target_holder")
        _require_nonnegative_int(self.source_epoch, "custody_edge.source_epoch")
        _require_nonnegative_int(self.target_epoch, "custody_edge.target_epoch")
        if self.kind not in EDGE_KINDS:
            raise ModelInvalid(
                f"custody_edge.kind must be one of {sorted(EDGE_KINDS)}, got {self.kind!r}"
            )
        if self.target_epoch != self.source_epoch + 1:
            raise ModelInvalid("custody edges must advance exactly one epoch")
        if self.kind == "retain" and self.source_holder != self.target_holder:
            raise ModelInvalid("retain edges must keep the same holder")
        if self.kind == "deliver" and self.target_holder != CHALLENGER:
            raise ModelInvalid(f"deliver edges must target {CHALLENGER}")
        if self.kind != "deliver" and self.target_holder == CHALLENGER:
            raise ModelInvalid(f"only deliver edges may target {CHALLENGER}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "source_holder": self.source_holder,
            "target_holder": self.target_holder,
            "source_epoch": self.source_epoch,
            "target_epoch": self.target_epoch,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CustodyEdge":
        _strict_keys(
            value,
            {
                "atom_id",
                "source_holder",
                "target_holder",
                "source_epoch",
                "target_epoch",
                "kind",
            },
            "custody_edge",
        )
        return cls(
            _require_identifier(value["atom_id"], "custody_edge.atom_id"),
            _require_identifier(value["source_holder"], "custody_edge.source_holder"),
            _require_identifier(value["target_holder"], "custody_edge.target_holder"),
            _require_nonnegative_int(value["source_epoch"], "custody_edge.source_epoch"),
            _require_nonnegative_int(value["target_epoch"], "custody_edge.target_epoch"),
            _require_identifier(value["kind"], "custody_edge.kind"),
        )


@dataclass(frozen=True)
class MobileAdversaryPolicy:
    peak_budget: int
    fixed_corruptions: tuple[tuple[int, str], ...] = ()

    def __post_init__(self) -> None:
        _require_nonnegative_int(self.peak_budget, "adversary.peak_budget")
        seen: set[tuple[int, str]] = set()
        for epoch, group_id in self.fixed_corruptions:
            _require_nonnegative_int(epoch, "adversary.fixed_corruption.epoch")
            _require_identifier(group_id, "adversary.fixed_corruption.group_id")
            key = (epoch, group_id)
            if key in seen:
                raise ModelInvalid("adversary.fixed_corruptions must be unique")
            seen.add(key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "peak_budget": self.peak_budget,
            "fixed_corruptions": [
                {"epoch": epoch, "group_id": group_id}
                for epoch, group_id in sorted(self.fixed_corruptions)
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MobileAdversaryPolicy":
        _strict_keys(value, {"peak_budget", "fixed_corruptions"}, "adversary")
        fixed = value["fixed_corruptions"]
        if not isinstance(fixed, list):
            raise ModelInvalid("adversary.fixed_corruptions must be an array")
        parsed = []
        for item in fixed:
            if not isinstance(item, dict):
                raise ModelInvalid("fixed corruption entries must be objects")
            _strict_keys(item, {"epoch", "group_id"}, "fixed_corruption")
            parsed.append(
                (
                    _require_nonnegative_int(item["epoch"], "fixed_corruption.epoch"),
                    _require_identifier(item["group_id"], "fixed_corruption.group_id"),
                )
            )
        return cls(
            _require_nonnegative_int(value["peak_budget"], "adversary.peak_budget"),
            tuple(sorted(parsed)),
        )


@dataclass(frozen=True)
class AnswerabilityProblem:
    query_id: str
    occurrence_binding: str
    challenge_epoch: int
    control_groups: tuple[ControlGroup, ...]
    executions: tuple[ExecutionClass, ...]
    evidence_atoms: tuple[EvidenceAtom, ...]
    custody_edges: tuple[CustodyEdge, ...]
    adversary: MobileAdversaryPolicy
    profile: str = PROFILE

    def __post_init__(self) -> None:
        if self.profile != PROFILE:
            raise ModelInvalid(f"problem.profile must be {PROFILE!r}")
        _require_identifier(self.query_id, "problem.query_id")
        _require_digest(self.occurrence_binding, "problem.occurrence_binding")
        _require_nonnegative_int(self.challenge_epoch, "problem.challenge_epoch")
        if self.challenge_epoch < 1:
            raise ModelInvalid("problem.challenge_epoch must be at least 1")
        if len(self.executions) < 2:
            raise ModelInvalid("problem requires at least two execution classes")

        group_ids = [group.group_id for group in self.control_groups]
        _unique(group_ids, "control_group")
        execution_ids = [execution.execution_id for execution in self.executions]
        _unique(execution_ids, "execution")
        atom_ids = [atom.atom_id for atom in self.evidence_atoms]
        _unique(atom_ids, "evidence_atom")

        holder_to_group: dict[str, str] = {}
        for group in self.control_groups:
            for holder in group.holders:
                if holder in holder_to_group:
                    raise ModelInvalid(
                        f"holder {holder!r} belongs to multiple control groups"
                    )
                holder_to_group[holder] = group.group_id
        if not holder_to_group:
            raise ModelInvalid("problem requires at least one control group and holder")

        execution_set = set(execution_ids)
        for atom in self.evidence_atoms:
            if atom.occurrence_binding != self.occurrence_binding:
                raise ModelInvalid(
                    f"evidence atom {atom.atom_id!r} has inconsistent occurrence binding"
                )
            if atom.holder not in holder_to_group:
                raise ModelInvalid(
                    f"evidence atom {atom.atom_id!r} names unknown holder {atom.holder!r}"
                )
            if atom.observed_at >= self.challenge_epoch:
                raise ModelInvalid(
                    f"evidence atom {atom.atom_id!r} must be observed before challenge"
                )
            if set(atom.value_map()) != execution_set:
                raise ModelInvalid(
                    f"evidence atom {atom.atom_id!r} must define exactly every execution"
                )

        atom_by_id = {atom.atom_id: atom for atom in self.evidence_atoms}
        for edge in self.custody_edges:
            if edge.atom_id not in atom_by_id:
                raise ModelInvalid(f"custody edge names unknown atom {edge.atom_id!r}")
            if edge.source_holder not in holder_to_group:
                raise ModelInvalid(
                    f"custody edge names unknown source holder {edge.source_holder!r}"
                )
            if edge.target_holder != CHALLENGER and edge.target_holder not in holder_to_group:
                raise ModelInvalid(
                    f"custody edge names unknown target holder {edge.target_holder!r}"
                )
            if edge.source_epoch < atom_by_id[edge.atom_id].observed_at:
                raise ModelInvalid(
                    f"custody edge for {edge.atom_id!r} begins before observation"
                )
            if edge.target_epoch > self.challenge_epoch:
                raise ModelInvalid("custody edge extends beyond the challenge epoch")
            if edge.kind == "deliver" and edge.target_epoch != self.challenge_epoch:
                raise ModelInvalid("deliver edges must end at the challenge epoch")
            if edge.kind != "deliver" and edge.target_epoch == self.challenge_epoch:
                raise ModelInvalid(
                    "only deliver edges may enter the challenge epoch"
                )

        group_by_id = {group.group_id: group for group in self.control_groups}
        corruptible_count = sum(group.corruptible for group in self.control_groups)
        if self.adversary.peak_budget > corruptible_count:
            raise ModelInvalid(
                "adversary.peak_budget exceeds the number of corruptible control groups"
            )
        for epoch, group_id in self.adversary.fixed_corruptions:
            if epoch >= self.challenge_epoch:
                raise ModelInvalid(
                    "fixed corruption must occur before the challenge epoch"
                )
            group = group_by_id.get(group_id)
            if group is None:
                raise ModelInvalid(f"fixed corruption names unknown group {group_id!r}")
            if not group.corruptible:
                raise ModelInvalid(
                    f"fixed corruption cannot name non-corruptible group {group_id!r}"
                )

    def holder_groups(self) -> dict[str, str]:
        return {
            holder: group.group_id
            for group in self.control_groups
            for holder in group.holders
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "query_id": self.query_id,
            "occurrence_binding": self.occurrence_binding,
            "challenge_epoch": self.challenge_epoch,
            "control_groups": [
                group.to_dict() for group in sorted(self.control_groups, key=lambda x: x.group_id)
            ],
            "executions": [
                execution.to_dict()
                for execution in sorted(self.executions, key=lambda x: x.execution_id)
            ],
            "evidence_atoms": [
                atom.to_dict() for atom in sorted(self.evidence_atoms, key=lambda x: x.atom_id)
            ],
            "custody_edges": [
                edge.to_dict()
                for edge in sorted(
                    self.custody_edges,
                    key=lambda x: (
                        x.atom_id,
                        x.source_epoch,
                        x.source_holder,
                        x.target_epoch,
                        x.target_holder,
                        x.kind,
                    ),
                )
            ],
            "adversary": self.adversary.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AnswerabilityProblem":
        _strict_keys(
            value,
            {
                "profile",
                "query_id",
                "occurrence_binding",
                "challenge_epoch",
                "control_groups",
                "executions",
                "evidence_atoms",
                "custody_edges",
                "adversary",
            },
            "problem",
        )
        for key in ("control_groups", "executions", "evidence_atoms", "custody_edges"):
            if not isinstance(value[key], list):
                raise ModelInvalid(f"problem.{key} must be an array")
        if not isinstance(value["adversary"], dict):
            raise ModelInvalid("problem.adversary must be an object")
        return cls(
            query_id=_require_identifier(value["query_id"], "problem.query_id"),
            occurrence_binding=_require_digest(
                value["occurrence_binding"], "problem.occurrence_binding"
            ),
            challenge_epoch=_require_nonnegative_int(
                value["challenge_epoch"], "problem.challenge_epoch"
            ),
            control_groups=tuple(
                ControlGroup.from_dict(item) for item in value["control_groups"]
            ),
            executions=tuple(
                ExecutionClass.from_dict(item) for item in value["executions"]
            ),
            evidence_atoms=tuple(
                EvidenceAtom.from_dict(item) for item in value["evidence_atoms"]
            ),
            custody_edges=tuple(
                CustodyEdge.from_dict(item) for item in value["custody_edges"]
            ),
            adversary=MobileAdversaryPolicy.from_dict(value["adversary"]),
            profile=_require_identifier(value["profile"], "problem.profile"),
        )


@dataclass(frozen=True)
class AnswerabilityAssessment:
    status: AssessmentStatus
    problem_digest: str
    assumptions_digest: str
    query_id: str | None
    occurrence_binding: str | None
    assessed_budget: int | None
    robustness_margin: int | None
    unbounded_within_model: bool
    states_explored: int
    counterexample: Mapping[str, Any] | None = None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PROFILE,
            "classification": "MODEL_RELATIVE",
            "status": self.status.value,
            "problem_digest": self.problem_digest,
            "assumptions_digest": self.assumptions_digest,
            "query_id": self.query_id,
            "occurrence_binding": self.occurrence_binding,
            "assessed_budget": self.assessed_budget,
            "robustness_margin": self.robustness_margin,
            "unbounded_within_model": self.unbounded_within_model,
            "states_explored": self.states_explored,
            "property_scope": {
                "occurrence_query": "assessed",
                "non_equivocation": "not_assessed",
                "authority_binding": "not_assessed",
                "remedy_reachability": "not_assessed",
                "external_validation": "none",
            },
            "counterexample": dict(self.counterexample) if self.counterexample else None,
            "errors": list(self.errors),
        }


@dataclass
class _SearchCounter:
    limit: int
    explored: int = 0

    def tick(self) -> None:
        self.explored += 1
        if self.explored > self.limit:
            raise SearchLimitExceeded(
                f"exact search exceeded the {self.limit} state limit"
            )


def _problem_digest(problem: AnswerabilityProblem) -> str:
    return _digest(problem.to_dict())


def _raw_problem_digest(document: Any) -> str:
    try:
        return _digest(document)
    except (TypeError, ValueError):
        return "sha256:" + "0" * 64


def _fixed_by_epoch(problem: AnswerabilityProblem) -> tuple[frozenset[str], ...]:
    fixed = [set() for _ in range(problem.challenge_epoch)]
    for epoch, group_id in problem.adversary.fixed_corruptions:
        fixed[epoch].add(group_id)
    return tuple(frozenset(groups) for groups in fixed)


def _mobile_choices(groups: tuple[str, ...], budget: int) -> tuple[tuple[str, ...], ...]:
    return tuple(
        combination
        for size in range(budget + 1)
        for combination in itertools.combinations(groups, size)
    )


def _schedules(
    problem: AnswerabilityProblem,
    budget: int,
    counter: _SearchCounter,
) -> Iterator[tuple[frozenset[str], ...]]:
    groups = tuple(
        sorted(group.group_id for group in problem.control_groups if group.corruptible)
    )
    fixed = _fixed_by_epoch(problem)
    choices_by_epoch = []
    for epoch in range(problem.challenge_epoch):
        available = tuple(group for group in groups if group not in fixed[epoch])
        choices_by_epoch.append(_mobile_choices(available, min(budget, len(available))))

    current: list[frozenset[str]] = []

    def walk(epoch: int) -> Iterator[tuple[frozenset[str], ...]]:
        if epoch == len(choices_by_epoch):
            counter.tick()
            yield tuple(current)
            return
        for mobile in choices_by_epoch[epoch]:
            current.append(frozenset(mobile))
            yield from walk(epoch + 1)
            current.pop()

    yield from walk(0)


def _corrupted(
    problem: AnswerabilityProblem,
    mobile_schedule: Sequence[frozenset[str]],
    epoch: int,
) -> frozenset[str]:
    fixed = {
        group_id
        for fixed_epoch, group_id in problem.adversary.fixed_corruptions
        if fixed_epoch == epoch
    }
    return frozenset(fixed | set(mobile_schedule[epoch]))


def _node_name(holder: str, epoch: int) -> str:
    return f"{holder}@{epoch}"


def _reachable_for_atom(
    problem: AnswerabilityProblem,
    atom: EvidenceAtom,
    mobile_schedule: Sequence[frozenset[str]],
) -> tuple[bool, tuple[str, ...]]:
    holder_groups = problem.holder_groups()

    def live(holder: str, epoch: int) -> bool:
        if holder == CHALLENGER:
            return True
        return holder_groups[holder] not in _corrupted(problem, mobile_schedule, epoch)

    origin = (atom.holder, atom.observed_at)
    if not live(*origin):
        return False, ()
    adjacency: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for edge in problem.custody_edges:
        if edge.atom_id != atom.atom_id:
            continue
        source = (edge.source_holder, edge.source_epoch)
        target = (edge.target_holder, edge.target_epoch)
        adjacency.setdefault(source, []).append(target)
    for targets in adjacency.values():
        targets.sort(key=lambda item: (item[1], item[0]))

    queue = [origin]
    seen = {origin}
    while queue:
        source = queue.pop(0)
        for target in adjacency.get(source, []):
            if not live(*source) or not live(*target):
                continue
            if target not in seen:
                seen.add(target)
                queue.append(target)
    challenger = (CHALLENGER, problem.challenge_epoch)
    names = tuple(
        _node_name(holder, epoch)
        for holder, epoch in sorted(seen, key=lambda item: (item[1], item[0]))
    )
    return challenger in seen, names


def _disagreeing_pairs(problem: AnswerabilityProblem) -> tuple[tuple[str, str], ...]:
    executions = sorted(problem.executions, key=lambda item: item.execution_id)
    return tuple(
        (left.execution_id, right.execution_id)
        for index, left in enumerate(executions)
        for right in executions[index + 1 :]
        if left.query_value != right.query_value
    )


def _distinguishing_atoms(
    problem: AnswerabilityProblem,
    pair: tuple[str, str],
) -> tuple[EvidenceAtom, ...]:
    left, right = pair
    return tuple(
        atom
        for atom in sorted(problem.evidence_atoms, key=lambda item: item.atom_id)
        if atom.value_map()[left] != atom.value_map()[right]
    )


def _pair_erased(
    problem: AnswerabilityProblem,
    pair: tuple[str, str],
    mobile_schedule: Sequence[frozenset[str]],
) -> tuple[bool, tuple[dict[str, Any], ...]]:
    failures = []
    delivered_distinction = False
    for atom in _distinguishing_atoms(problem, pair):
        delivered, reachable = _reachable_for_atom(problem, atom, mobile_schedule)
        delivered_distinction = delivered_distinction or delivered
        if not delivered:
            failures.append(
                {
                    "atom_id": atom.atom_id,
                    "reachable_nodes": list(reachable),
                    "challenger_reached": False,
                }
            )
    return not delivered_distinction, tuple(failures)


def _serialized_schedule(
    problem: AnswerabilityProblem,
    mobile_schedule: Sequence[frozenset[str]],
) -> list[dict[str, Any]]:
    fixed = _fixed_by_epoch(problem)
    return [
        {
            "epoch": epoch,
            "mobile_groups": sorted(mobile_schedule[epoch]),
            "fixed_groups": sorted(fixed[epoch]),
            "corrupted_groups": sorted(
                set(mobile_schedule[epoch]) | set(fixed[epoch])
            ),
        }
        for epoch in range(problem.challenge_epoch)
    ]


def _minimum_erasure(
    problem: AnswerabilityProblem,
    *,
    state_limit: int,
) -> tuple[int | None, Mapping[str, Any] | None, int]:
    pairs = _disagreeing_pairs(problem)
    if not pairs:
        raise ModelInvalid(
            "problem has no pair of executions that disagrees on the query"
        )
    counter = _SearchCounter(state_limit)
    corruptible_count = sum(group.corruptible for group in problem.control_groups)
    for budget in range(corruptible_count + 1):
        for pair in pairs:
            distinguishing = _distinguishing_atoms(problem, pair)
            for schedule in _schedules(problem, budget, counter):
                erased, failures = _pair_erased(problem, pair, schedule)
                if erased:
                    return (
                        budget,
                        {
                            "execution_pair": list(pair),
                            "query_values": [
                                next(
                                    item.query_value
                                    for item in problem.executions
                                    if item.execution_id == execution_id
                                )
                                for execution_id in pair
                            ],
                            "distinguishing_atoms": [
                                atom.atom_id for atom in distinguishing
                            ],
                            "corruption_schedule": _serialized_schedule(
                                problem, schedule
                            ),
                            "failed_custody_paths": list(failures),
                        },
                        counter.explored,
                    )
    return None, None, counter.explored


def assess(
    problem: AnswerabilityProblem,
    *,
    state_limit: int = DEFAULT_STATE_LIMIT,
) -> AnswerabilityAssessment:
    """Compute the exact model-relative answerability margin."""
    _require_nonnegative_int(state_limit, "state_limit")
    problem_digest = _problem_digest(problem)
    assumptions_digest = _digest(ASSUMPTIONS)
    try:
        margin, counterexample, explored = _minimum_erasure(
            problem, state_limit=state_limit
        )
    except SearchLimitExceeded as exc:
        return AnswerabilityAssessment(
            AssessmentStatus.LIMIT_EXCEEDED,
            problem_digest,
            assumptions_digest,
            problem.query_id,
            problem.occurrence_binding,
            problem.adversary.peak_budget,
            None,
            False,
            state_limit + 1,
            errors=(str(exc),),
        )
    status = (
        AssessmentStatus.ANSWERABLE
        if margin is None or margin > problem.adversary.peak_budget
        else AssessmentStatus.NOT_ANSWERABLE
    )
    return AnswerabilityAssessment(
        status,
        problem_digest,
        assumptions_digest,
        problem.query_id,
        problem.occurrence_binding,
        problem.adversary.peak_budget,
        margin,
        margin is None,
        explored,
        counterexample=counterexample if status is AssessmentStatus.NOT_ANSWERABLE else None,
    )


def assess_document(
    document: Any,
    *,
    state_limit: int = DEFAULT_STATE_LIMIT,
) -> AnswerabilityAssessment:
    """Parse and assess one raw problem document without throwing on bad input."""
    try:
        if not isinstance(document, dict):
            raise ModelInvalid("problem document must be an object")
        problem = AnswerabilityProblem.from_dict(document)
        return assess(problem, state_limit=state_limit)
    except ModelInvalid as exc:
        return AnswerabilityAssessment(
            AssessmentStatus.MODEL_INVALID,
            _raw_problem_digest(document),
            _digest(ASSUMPTIONS),
            document.get("query_id") if isinstance(document, dict) else None,
            document.get("occurrence_binding") if isinstance(document, dict) else None,
            None,
            None,
            False,
            0,
            errors=(str(exc),),
        )


def verify_assessment_document(
    problem_document: Any,
    assessment_document: Any,
    *,
    state_limit: int = DEFAULT_STATE_LIMIT,
) -> tuple[bool, tuple[str, ...]]:
    """Recompute an assessment and compare its full canonical document."""
    if not isinstance(assessment_document, dict):
        return False, ("assessment document must be an object",)
    expected = assess_document(problem_document, state_limit=state_limit).to_dict()
    if expected == assessment_document:
        return True, ()
    reasons = []
    for key in sorted(set(expected) | set(assessment_document)):
        if expected.get(key) != assessment_document.get(key):
            reasons.append(f"assessment field {key!r} does not replay")
    return False, tuple(reasons)


@dataclass(frozen=True)
class WitnessCandidate:
    candidate_id: str
    cost: int
    control_groups: tuple[ControlGroup, ...]
    evidence_atoms: tuple[EvidenceAtom, ...]
    custody_edges: tuple[CustodyEdge, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.candidate_id, "candidate.candidate_id")
        _require_nonnegative_int(self.cost, "candidate.cost")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "cost": self.cost,
            "control_groups": [
                item.to_dict() for item in sorted(self.control_groups, key=lambda x: x.group_id)
            ],
            "evidence_atoms": [
                item.to_dict() for item in sorted(self.evidence_atoms, key=lambda x: x.atom_id)
            ],
            "custody_edges": [
                item.to_dict()
                for item in sorted(
                    self.custody_edges,
                    key=lambda x: (
                        x.atom_id,
                        x.source_epoch,
                        x.source_holder,
                        x.target_epoch,
                        x.target_holder,
                        x.kind,
                    ),
                )
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WitnessCandidate":
        _strict_keys(
            value,
            {
                "candidate_id",
                "cost",
                "control_groups",
                "evidence_atoms",
                "custody_edges",
            },
            "candidate",
        )
        for key in ("control_groups", "evidence_atoms", "custody_edges"):
            if not isinstance(value[key], list):
                raise ModelInvalid(f"candidate.{key} must be an array")
        return cls(
            _require_identifier(value["candidate_id"], "candidate.candidate_id"),
            _require_nonnegative_int(value["cost"], "candidate.cost"),
            tuple(ControlGroup.from_dict(item) for item in value["control_groups"]),
            tuple(EvidenceAtom.from_dict(item) for item in value["evidence_atoms"]),
            tuple(CustodyEdge.from_dict(item) for item in value["custody_edges"]),
        )


@dataclass(frozen=True)
class WitnessPlan:
    status: PlanStatus
    problem_digest: str
    selected_candidates: tuple[str, ...]
    total_cost: int | None
    assessment: Mapping[str, Any] | None
    candidate_sets_explored: int
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": PLAN_PROFILE,
            "classification": "MODEL_RELATIVE",
            "status": self.status.value,
            "problem_digest": self.problem_digest,
            "selected_candidates": list(self.selected_candidates),
            "total_cost": self.total_cost,
            "candidate_sets_explored": self.candidate_sets_explored,
            "assessment": dict(self.assessment) if self.assessment else None,
            "errors": list(self.errors),
        }


def parse_candidate_catalog(document: Any) -> tuple[WitnessCandidate, ...]:
    if not isinstance(document, dict):
        raise ModelInvalid("candidate catalog must be an object")
    _strict_keys(document, {"profile", "candidates"}, "candidate_catalog")
    if document["profile"] != CATALOG_PROFILE:
        raise ModelInvalid(f"candidate catalog profile must be {CATALOG_PROFILE!r}")
    if not isinstance(document["candidates"], list):
        raise ModelInvalid("candidate catalog candidates must be an array")
    candidates = tuple(WitnessCandidate.from_dict(item) for item in document["candidates"])
    _unique((candidate.candidate_id for candidate in candidates), "candidate")
    if len(candidates) > MAX_CANDIDATES:
        raise ModelInvalid(
            f"candidate catalog exceeds the exact v0.1 limit of {MAX_CANDIDATES}"
        )
    _unique(
        (
            group.group_id
            for candidate in candidates
            for group in candidate.control_groups
        ),
        "candidate control_group",
    )
    _unique(
        (
            atom.atom_id
            for candidate in candidates
            for atom in candidate.evidence_atoms
        ),
        "candidate evidence_atom",
    )
    return tuple(sorted(candidates, key=lambda item: item.candidate_id))


def _augment_problem(
    problem: AnswerabilityProblem,
    candidates: Sequence[WitnessCandidate],
) -> AnswerabilityProblem:
    return AnswerabilityProblem(
        query_id=problem.query_id,
        occurrence_binding=problem.occurrence_binding,
        challenge_epoch=problem.challenge_epoch,
        control_groups=problem.control_groups
        + tuple(group for candidate in candidates for group in candidate.control_groups),
        executions=problem.executions,
        evidence_atoms=problem.evidence_atoms
        + tuple(atom for candidate in candidates for atom in candidate.evidence_atoms),
        custody_edges=problem.custody_edges
        + tuple(edge for candidate in candidates for edge in candidate.custody_edges),
        adversary=problem.adversary,
    )


def plan_witnesses(
    problem: AnswerabilityProblem,
    candidates: Sequence[WitnessCandidate],
    *,
    state_limit: int = DEFAULT_STATE_LIMIT,
) -> WitnessPlan:
    """Select an exact minimum-cost candidate set, with deterministic ties."""
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    if len(ordered) > MAX_CANDIDATES:
        return WitnessPlan(
            PlanStatus.MODEL_INVALID,
            _problem_digest(problem),
            (),
            None,
            None,
            0,
            errors=(
                f"candidate catalog exceeds the exact v0.1 limit of {MAX_CANDIDATES}",
            ),
        )
    explored = 0
    best: tuple[int, tuple[str, ...], AnswerabilityAssessment] | None = None
    strongest_failure: (
        tuple[int, int, tuple[str, ...], AnswerabilityAssessment] | None
    ) = None
    try:
        # Candidate identifiers must describe one composable catalog even if
        # the optimum uses only a subset.  Reject name/control collisions once
        # instead of aborting midway through an otherwise deterministic search.
        _augment_problem(problem, ordered)
        for mask in range(1 << len(ordered)):
            explored += 1
            if explored > DEFAULT_STATE_LIMIT:
                raise SearchLimitExceeded(
                    f"exact planner exceeded {DEFAULT_STATE_LIMIT} candidate sets"
                )
            selected = tuple(
                candidate for index, candidate in enumerate(ordered) if mask & (1 << index)
            )
            cost = sum(candidate.cost for candidate in selected)
            ids = tuple(candidate.candidate_id for candidate in selected)
            if best is not None and (cost, ids) >= (best[0], best[1]):
                continue
            augmented = _augment_problem(problem, selected)
            assessment = assess(augmented, state_limit=state_limit)
            if assessment.status is AssessmentStatus.LIMIT_EXCEEDED:
                raise SearchLimitExceeded(
                    "an exact candidate assessment exceeded the state limit"
                )
            if assessment.status is AssessmentStatus.ANSWERABLE:
                best = (cost, ids, assessment)
            elif assessment.status is AssessmentStatus.NOT_ANSWERABLE:
                margin = (
                    assessment.robustness_margin
                    if assessment.robustness_margin is not None
                    else len(problem.control_groups) + len(ordered) + 1
                )
                candidate_failure = (margin, cost, ids, assessment)
                if strongest_failure is None:
                    strongest_failure = candidate_failure
                else:
                    old_margin, old_cost, old_ids, _ = strongest_failure
                    if (
                        margin > old_margin
                        or (margin == old_margin and cost < old_cost)
                        or (
                            margin == old_margin
                            and cost == old_cost
                            and ids < old_ids
                        )
                    ):
                        strongest_failure = candidate_failure
    except (ModelInvalid, SearchLimitExceeded) as exc:
        status = (
            PlanStatus.LIMIT_EXCEEDED
            if isinstance(exc, SearchLimitExceeded)
            else PlanStatus.MODEL_INVALID
        )
        return WitnessPlan(
            status,
            _problem_digest(problem),
            strongest_failure[2] if strongest_failure else (),
            strongest_failure[1] if strongest_failure else None,
            strongest_failure[3].to_dict() if strongest_failure else None,
            explored,
            errors=(str(exc),),
        )
    if best is None:
        return WitnessPlan(
            PlanStatus.INFEASIBLE,
            _problem_digest(problem),
            strongest_failure[2] if strongest_failure else (),
            strongest_failure[1] if strongest_failure else None,
            strongest_failure[3].to_dict() if strongest_failure else None,
            explored,
        )
    return WitnessPlan(
        PlanStatus.FEASIBLE,
        _problem_digest(problem),
        best[1],
        best[0],
        best[2].to_dict(),
        explored,
    )


def plan_document(
    problem_document: Any,
    catalog_document: Any,
    *,
    state_limit: int = DEFAULT_STATE_LIMIT,
) -> WitnessPlan:
    try:
        if not isinstance(problem_document, dict):
            raise ModelInvalid("problem document must be an object")
        problem = AnswerabilityProblem.from_dict(problem_document)
        candidates = parse_candidate_catalog(catalog_document)
        return plan_witnesses(problem, candidates, state_limit=state_limit)
    except ModelInvalid as exc:
        return WitnessPlan(
            PlanStatus.MODEL_INVALID,
            _raw_problem_digest(problem_document),
            (),
            None,
            None,
            0,
            errors=(str(exc),),
        )
