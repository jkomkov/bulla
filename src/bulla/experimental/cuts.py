"""Replay-verifiable minimal refusal and escalation cut explanations."""

from __future__ import annotations

from dataclasses import dataclass

from bulla.executable_form import definition_hash
from bulla.reliance import ESCALATE, REFUSE, RELY, RelianceDecision


@dataclass(frozen=True)
class DecisionCutCertificate:
    outcome: str
    witness_dimension: str | None
    witness_routing: str | None
    repair_frontier: tuple[str, ...]
    decision_hash: str
    minimal_in_observed_view: bool

    def to_dict(self) -> dict:
        return {
            "schema_version": "0.1-experimental",
            "outcome": self.outcome,
            "witness_dimension": self.witness_dimension,
            "witness_routing": self.witness_routing,
            "repair_frontier": list(self.repair_frontier),
            "decision_hash": self.decision_hash,
            "minimal_in_observed_view": self.minimal_in_observed_view,
        }


def _decision_hash(decision: RelianceDecision) -> str:
    return definition_hash(decision.to_dict())


def minimal_decision_cut(decision: RelianceDecision) -> DecisionCutCertificate:
    """Select one canonical sufficient witness and list every repair dimension.

    One REFUSE-routed unmet dimension is sufficient to explain REFUSE.  When no
    clear violation exists, one ESCALATE-routed unmet dimension is sufficient to
    explain ESCALATE.  Reaching RELY requires repairing the full frontier.
    """
    if not isinstance(decision, RelianceDecision):
        raise TypeError("decision must be a RelianceDecision")
    unmet = tuple(decision.unmet)
    frontier = tuple(sorted({entry["dimension"] for entry in unmet}))
    if decision.outcome == RELY:
        witness = None
    elif decision.outcome == REFUSE:
        candidates = [x for x in unmet if x["routing"] == REFUSE]
        if not candidates:
            raise ValueError("REFUSE decision carries no refusal-routed witness")
        witness = min(candidates, key=lambda x: (x["dimension"], str(x["actual"])))
    elif decision.outcome == ESCALATE:
        if any(x["routing"] == REFUSE for x in unmet):
            raise ValueError("ESCALATE decision contains a refusal-routed violation")
        candidates = [x for x in unmet if x["routing"] == ESCALATE]
        if not candidates:
            raise ValueError("ESCALATE decision carries no escalation witness")
        witness = min(candidates, key=lambda x: (x["dimension"], str(x["actual"])))
    else:
        raise ValueError(f"unknown reliance outcome {decision.outcome!r}")
    return DecisionCutCertificate(
        outcome=decision.outcome,
        witness_dimension=witness["dimension"] if witness else None,
        witness_routing=witness["routing"] if witness else None,
        repair_frontier=frontier,
        decision_hash=_decision_hash(decision),
        minimal_in_observed_view=True,
    )


def verify_decision_cut(
    decision: RelianceDecision, certificate: DecisionCutCertificate
) -> bool:
    if certificate.decision_hash != _decision_hash(decision):
        return False
    if certificate.outcome != decision.outcome:
        return False
    expected = minimal_decision_cut(decision)
    return expected == certificate
