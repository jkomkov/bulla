"""Semantic SemVer update assessment (G26)."""

from __future__ import annotations

from dataclasses import dataclass

from bulla.compute.cocycle_pairs import compute_rank_delta
from bulla.diagnostic import diagnose
from bulla.model import Composition


@dataclass(frozen=True)
class SemVerAssessment:
    old_fee: int
    new_fee: int
    delta_r: int
    coherence_preserving: bool
    update_kind: str
    minimum_bridge_delta: int

    def to_dict(self) -> dict:
        return {
            "old_fee": self.old_fee,
            "new_fee": self.new_fee,
            "delta_r": self.delta_r,
            "coherence_preserving": self.coherence_preserving,
            "update_kind": self.update_kind,
            "minimum_bridge_delta": self.minimum_bridge_delta,
        }


def classify_update_kind(delta_r: int) -> str:
    if delta_r <= 0:
        return "semantic-patch"
    if delta_r == 1:
        return "semantic-minor"
    return "semantic-major"


def assess_update(old: Composition, new: Composition) -> SemVerAssessment:
    """Assess semantic compatibility of an interface update.

    ``delta_r`` is defined as ``fee(new) - fee(old)``.
    """
    old_diag = diagnose(old)
    new_diag = diagnose(new)
    delta_r = compute_rank_delta(new, old)
    kind = classify_update_kind(delta_r)
    coherence_preserving = delta_r <= 0
    minimum_bridge_delta = max(0, delta_r)
    return SemVerAssessment(
        old_fee=old_diag.coherence_fee,
        new_fee=new_diag.coherence_fee,
        delta_r=delta_r,
        coherence_preserving=coherence_preserving,
        update_kind=kind,
        minimum_bridge_delta=minimum_bridge_delta,
    )

