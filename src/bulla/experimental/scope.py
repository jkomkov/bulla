"""A sound finite partial order for structured FRSL-1 scopes."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from bulla.experimental.frsl import (
    Formula,
    FRSLError,
    Signature,
    canonical_hash,
    enumerate_structures,
    evaluate,
    normalize_formula,
    normalize_structure,
    structure_to_dict,
    validate_formula,
)


class ScopeOrderStatus(str, enum.Enum):
    LEQ = "LEQ"
    NOT_LEQ = "NOT_LEQ"
    INDETERMINATE = "INDETERMINATE"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class StructuredScope:
    signature: Signature
    predicate: Formula
    reference_max_ground_atoms: int = 16
    reference_max_models: int = 65536

    def __post_init__(self) -> None:
        validate_formula(
            self.predicate,
            signature=self.signature,
            where="structured_scope.predicate",
        )
        if self.predicate != normalize_formula(self.predicate):
            raise ValueError("structured_scope.predicate must be canonical FRSL-1")
        for name in ("reference_max_ground_atoms", "reference_max_models"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @property
    def scope_hash(self) -> str:
        return canonical_hash(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "schema_version": "0.1-experimental",
            "language": "FRSL-1",
            "signature": self.signature.to_dict(),
            "predicate": self.predicate,
            "reference_max_ground_atoms": self.reference_max_ground_atoms,
            "reference_max_models": self.reference_max_models,
        }


@dataclass(frozen=True)
class ScopeOrderResult:
    status: ScopeOrderStatus
    narrower_hash: str
    broader_hash: str
    countermodel: Mapping[str, Any] | None = None
    reason: str | None = None

    def __bool__(self) -> bool:
        raise TypeError("ScopeOrderResult has no truth value; inspect .status")

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "narrower_hash": self.narrower_hash,
            "broader_hash": self.broader_hash,
            "countermodel": dict(self.countermodel) if self.countermodel else None,
            "reason": self.reason,
        }


def scope_leq(
    narrower: StructuredScope, broader: StructuredScope
) -> ScopeOrderResult:
    """Decide semantic implication over the exact finite signature.

    narrower <= broader means every finite structure admitted by narrower is
    admitted by broader.  NOT_LEQ carries a replayable countermodel.
    """
    if not isinstance(narrower, StructuredScope) or not isinstance(
        broader, StructuredScope
    ):
        raise TypeError("scope_leq requires two StructuredScope values")
    if narrower.signature.to_dict() != broader.signature.to_dict():
        return ScopeOrderResult(
            status=ScopeOrderStatus.INVALID_INPUT,
            narrower_hash=narrower.scope_hash,
            broader_hash=broader.scope_hash,
            reason="scope signatures differ; no order comparison is defined",
        )
    max_atoms = min(
        narrower.reference_max_ground_atoms,
        broader.reference_max_ground_atoms,
    )
    max_models = min(
        narrower.reference_max_models,
        broader.reference_max_models,
    )
    try:
        for structure in enumerate_structures(
            narrower.signature,
            max_ground_atoms=max_atoms,
            max_models=max_models,
        ):
            if evaluate(
                narrower.predicate,
                signature=narrower.signature,
                structure=structure,
            ) and not evaluate(
                broader.predicate,
                signature=broader.signature,
                structure=structure,
            ):
                return ScopeOrderResult(
                    status=ScopeOrderStatus.NOT_LEQ,
                    narrower_hash=narrower.scope_hash,
                    broader_hash=broader.scope_hash,
                    countermodel=structure_to_dict(structure),
                    reason=(
                        "countermodel satisfies the proposed narrower scope "
                        "and violates the proposed broader scope"
                    ),
                )
    except FRSLError as exc:
        return ScopeOrderResult(
            status=ScopeOrderStatus.INDETERMINATE,
            narrower_hash=narrower.scope_hash,
            broader_hash=broader.scope_hash,
            reason=str(exc),
        )
    return ScopeOrderResult(
        status=ScopeOrderStatus.LEQ,
        narrower_hash=narrower.scope_hash,
        broader_hash=broader.scope_hash,
    )


def verify_scope_countermodel(
    narrower: StructuredScope,
    broader: StructuredScope,
    result: ScopeOrderResult,
) -> bool:
    if result.status is not ScopeOrderStatus.NOT_LEQ or result.countermodel is None:
        return False
    if (
        result.narrower_hash != narrower.scope_hash
        or result.broader_hash != broader.scope_hash
    ):
        return False
    try:
        structure = normalize_structure(result.countermodel, narrower.signature)
        return evaluate(
            narrower.predicate,
            signature=narrower.signature,
            structure=structure,
        ) and not evaluate(
            broader.predicate,
            signature=broader.signature,
            structure=structure,
        )
    except (FRSLError, KeyError, TypeError):
        return False
