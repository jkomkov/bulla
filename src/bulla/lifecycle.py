"""Receipt lifecycle: diffing, validation, and invalidation.

Extends the witness receipt into deployment infrastructure by adding:
- diff: what changed between two receipts
- validate: is a receipt still valid for a composition
- invalidation conditions: when does a receipt become stale

Three orthogonal questions, kept separate:
- binding_status: does this receipt still bind the current composition?
- delta_status: compared to a baseline, did the situation worsen?
- admissibility: under the stated policy, may this composition proceed?

This module sits alongside witness.py (which produces receipts) and
adds the temporal and comparative semantics that turn receipts into
deployment gates and regression detectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bulla.model import (
    Composition,
    Diagnostic,
    Disposition,
    PolicyProfile,
    WitnessReceipt,
)


# ── Invalidation ───────────────────────��──────────────────────────


class InvalidationReason(Enum):
    """Why a receipt is no longer valid for a composition."""

    VALID = "valid"
    COMPOSITION_CHANGED = "composition_changed"
    FEE_INCREASED = "fee_increased"
    DISPOSITION_WORSENED = "disposition_worsened"
    NEW_BLIND_SPOTS = "new_blind_spots"
    NEW_CONTRADICTIONS = "new_contradictions"
    POLICY_CHANGED = "policy_changed"


# Disposition severity ordering: lower is better
_DISPOSITION_RANK = {
    Disposition.PROCEED: 0,
    Disposition.PROCEED_WITH_RECEIPT: 1,
    Disposition.PROCEED_WITH_CAUTION: 2,
    Disposition.PROCEED_WITH_BRIDGE: 3,
    Disposition.REFUSE_PENDING_DISCLOSURE: 4,
    Disposition.REFUSE_PENDING_HUMAN_REVIEW: 5,
}


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a receipt against a current composition.

    Answers the binding question: does this receipt still apply to
    this composition? Separate from regression (did things get worse)
    and admissibility (may this proceed under policy).
    """

    valid: bool
    reasons: tuple[InvalidationReason, ...]
    details: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "reasons": [r.value for r in self.reasons],
            "details": list(self.details),
        }


def validate_receipt(
    receipt: WitnessReceipt,
    current_comp: Composition,
    current_diag: Diagnostic,
    *,
    current_policy: PolicyProfile | None = None,
    current_convention_contradiction_count: int = 0,
    current_structural_contradiction_score: int = 0,
    current_unmet_obligations: int = 0,
    current_unknown_dimensions: int = 0,
) -> ValidationResult:
    """Check whether an existing receipt is still valid for a composition.

    A receipt is invalid if any of the following hold:
    - The composition's structural identity has changed (staleness)
    - The policy profile has changed (staleness)
    - The coherence fee has increased (regression)
    - The disposition has worsened (regression)
    - New blind spots have appeared (regression)
    - New contradictions have appeared (regression)

    A receipt remains valid only if the composition is unchanged,
    the policy is unchanged, AND the diagnostic is the same or better.

    Pass ``current_policy`` to check for policy staleness. If None,
    the receipt's own policy is used (no policy-change detection).

    The disposition check distinguishes two contradiction axes that
    the witness resolver tracks separately:
    - ``current_convention_contradiction_count``: convention-level
      contradictions (maps to ``contradiction_count`` in the resolver)
    - ``current_structural_contradiction_score``: schema-level
      contradictions (maps to ``structural_contradiction_score``)
    If omitted, disposition is compared using only the diagnostic's
    fee and blind spots.
    """
    reasons: list[InvalidationReason] = []
    details: list[str] = []

    # Staleness: composition identity changed
    current_hash = current_comp.canonical_hash()
    if receipt.composition_hash != current_hash:
        reasons.append(InvalidationReason.COMPOSITION_CHANGED)
        details.append(
            f"composition hash changed: {receipt.composition_hash[:12]}... "
            f"→ {current_hash[:12]}..."
        )

    # Staleness: policy changed
    if current_policy is not None:
        if receipt.policy_profile.to_dict() != current_policy.to_dict():
            reasons.append(InvalidationReason.POLICY_CHANGED)
            details.append(
                f"policy changed: {receipt.policy_profile.name} "
                f"→ {current_policy.name}"
            )

    # Regression: fee increased
    if current_diag.coherence_fee > receipt.fee:
        reasons.append(InvalidationReason.FEE_INCREASED)
        details.append(
            f"fee increased: {receipt.fee} → {current_diag.coherence_fee}"
        )

    # Regression: disposition worsened (with full context)
    from bulla.witness import _resolve_disposition
    effective_policy = current_policy if current_policy is not None else receipt.policy_profile
    current_disposition = _resolve_disposition(
        current_diag,
        effective_policy,
        unknown_dimensions=current_unknown_dimensions,
        unmet_obligations=current_unmet_obligations,
        contradiction_count=current_convention_contradiction_count,
        structural_contradiction_score=current_structural_contradiction_score,
    )
    receipt_rank = _DISPOSITION_RANK.get(receipt.disposition, 99)
    current_rank = _DISPOSITION_RANK.get(current_disposition, 99)
    if current_rank > receipt_rank:
        reasons.append(InvalidationReason.DISPOSITION_WORSENED)
        details.append(
            f"disposition worsened: {receipt.disposition.value} "
            f"→ {current_disposition.value}"
        )

    # Regression: new blind spots
    if len(current_diag.blind_spots) > receipt.blind_spots_count:
        reasons.append(InvalidationReason.NEW_BLIND_SPOTS)
        details.append(
            f"blind spots increased: {receipt.blind_spots_count} "
            f"→ {len(current_diag.blind_spots)}"
        )

    # Regression: new contradictions (structural score is the receipt's stored axis)
    if current_structural_contradiction_score > receipt.contradiction_score:
        reasons.append(InvalidationReason.NEW_CONTRADICTIONS)
        details.append(
            f"structural contradiction score increased: "
            f"{receipt.contradiction_score} "
            f"→ {current_structural_contradiction_score}"
        )

    if not reasons:
        return ValidationResult(
            valid=True,
            reasons=(InvalidationReason.VALID,),
            details=("receipt is still valid for this composition",),
        )

    return ValidationResult(
        valid=False,
        reasons=tuple(reasons),
        details=tuple(details),
    )


# ── Diffing ────────────────────���──────────────────────────────────


@dataclass(frozen=True)
class ReceiptDiff:
    """Structured comparison between two receipts.

    Captures what changed between a baseline receipt and a current
    receipt, with semantic classification of the change.

    ``is_stale`` and ``is_regression`` are separate:
    - stale: the baseline no longer binds the current composition
    - regression: the measured situation got worse

    A composition change that does not worsen metrics is stale but
    not regressed. A fee increase on an unchanged composition is
    regressed but not stale. Both should fail a deployment gate.
    """

    composition_changed: bool
    fee_delta: int  # positive = regression, negative = improvement
    disposition_changed: bool
    disposition_worsened: bool
    blind_spots_delta: int
    new_blind_spot_dimensions: tuple[str, ...]
    resolved_blind_spot_dimensions: tuple[str, ...]
    contradiction_delta: int
    policy_changed: bool
    is_stale: bool  # composition or policy changed
    is_regression: bool  # any metric worsened

    @property
    def should_fail_gate(self) -> bool:
        """Whether this diff should cause a deployment gate to fail.

        Fails on staleness OR regression. A changed composition that
        happens to have the same fee still fails because the baseline
        no longer binds the current state.
        """
        return self.is_stale or self.is_regression

    def to_dict(self) -> dict:
        return {
            "composition_changed": self.composition_changed,
            "fee_delta": self.fee_delta,
            "disposition_changed": self.disposition_changed,
            "disposition_worsened": self.disposition_worsened,
            "blind_spots_delta": self.blind_spots_delta,
            "new_blind_spot_dimensions": list(self.new_blind_spot_dimensions),
            "resolved_blind_spot_dimensions": list(
                self.resolved_blind_spot_dimensions
            ),
            "contradiction_delta": self.contradiction_delta,
            "policy_changed": self.policy_changed,
            "is_stale": self.is_stale,
            "is_regression": self.is_regression,
            "should_fail_gate": self.should_fail_gate,
        }

    def summary(self) -> str:
        """Human-readable one-line summary."""
        parts: list[str] = []
        if self.composition_changed:
            parts.append("composition changed (stale)")
        if self.fee_delta > 0:
            parts.append(f"fee +{self.fee_delta} (regression)")
        elif self.fee_delta < 0:
            parts.append(f"fee {self.fee_delta} (improvement)")
        if self.disposition_worsened:
            parts.append("disposition worsened")
        elif self.disposition_changed:
            parts.append("disposition changed")
        if self.blind_spots_delta > 0:
            parts.append(f"+{self.blind_spots_delta} blind spots")
        elif self.blind_spots_delta < 0:
            parts.append(f"{self.blind_spots_delta} blind spots")
        if self.contradiction_delta > 0:
            parts.append(f"+{self.contradiction_delta} contradictions")
        if self.policy_changed:
            parts.append("policy changed")

        return "; ".join(parts) if parts else "no change"


def diff_receipts(
    baseline: WitnessReceipt,
    current: WitnessReceipt,
) -> ReceiptDiff:
    """Compare two receipts and return a structured diff.

    The baseline is the "before" receipt (e.g., from the last
    successful deployment). The current is the "after" receipt
    (e.g., from the current state after a schema change).
    """
    composition_changed = (
        baseline.composition_hash != current.composition_hash
    )

    fee_delta = current.fee - baseline.fee

    disposition_changed = baseline.disposition != current.disposition
    baseline_rank = _DISPOSITION_RANK.get(baseline.disposition, 99)
    current_rank = _DISPOSITION_RANK.get(current.disposition, 99)
    disposition_worsened = current_rank > baseline_rank

    blind_spots_delta = current.blind_spots_count - baseline.blind_spots_count

    # Compute blind spot dimension changes from patches
    baseline_dims = {bs.dimension for bs in baseline.patches} if baseline.patches else set()
    current_dims = {bs.dimension for bs in current.patches} if current.patches else set()
    new_dims = tuple(sorted(current_dims - baseline_dims))
    resolved_dims = tuple(sorted(baseline_dims - current_dims))

    contradiction_delta = current.contradiction_score - baseline.contradiction_score

    policy_changed = (
        baseline.policy_profile.to_dict() != current.policy_profile.to_dict()
    )

    is_stale = composition_changed or policy_changed

    is_regression = (
        fee_delta > 0
        or disposition_worsened
        or blind_spots_delta > 0
        or contradiction_delta > 0
    )

    return ReceiptDiff(
        composition_changed=composition_changed,
        fee_delta=fee_delta,
        disposition_changed=disposition_changed,
        disposition_worsened=disposition_worsened,
        blind_spots_delta=blind_spots_delta,
        new_blind_spot_dimensions=new_dims,
        resolved_blind_spot_dimensions=resolved_dims,
        contradiction_delta=contradiction_delta,
        policy_changed=policy_changed,
        is_stale=is_stale,
        is_regression=is_regression,
    )


# ── Receipt reconstruction ────────────────────────────────────────


def receipt_from_dict(d: dict) -> WitnessReceipt:
    """Reconstruct a WitnessReceipt from a serialized dict.

    Handles all fields including patches, contradictions,
    boundary_obligations, witness_basis, anchor_ref, and the full
    policy profile. This is the canonical deserialization path for
    receipts loaded from JSON files.

    **Fidelity levels:**
    - Verification fidelity: use ``verify_receipt_integrity(d)``
      on the original dict. Works for all receipt versions.
    - Model fidelity: this function loads any receipt version into
      the current in-memory type. Legacy ``parent_receipt_hash``
      (singular) is normalized to ``parent_receipt_hashes`` (tuple).
    - Round-trip fidelity: ``receipt_from_dict(r.to_dict())`` is
      hash-preserving for current-version receipts. For pre-v0.24.0
      receipts with the legacy singular key, the reconstructed
      object will produce a different ``receipt_hash`` on
      reserialization because the key name changes. Use
      ``verify_receipt_integrity()`` on the original dict for
      hash verification of legacy receipts.
    """
    from bulla.model import (
        BoundaryObligation,
        BridgePatch,
        ContradictionReport,
        ContradictionSeverity,
        PackRef,
        PolicyProfile,
        SchemaContradiction,
        WitnessBasis,
    )

    # Policy profile
    pp = d.get("policy_profile", {})
    policy = PolicyProfile(
        name=pp.get("name", ""),
        max_blind_spots=pp.get("max_blind_spots", 0),
        max_fee=pp.get("max_fee", 0),
        max_unknown=pp.get("max_unknown", -1),
        require_bridge=pp.get("require_bridge", True),
        max_unmet_obligations=pp.get("max_unmet_obligations", -1),
        max_contradictions=pp.get("max_contradictions", -1),
        max_structural_contradictions=pp.get("max_structural_contradictions", -1),
    )

    # Patches
    patches_raw = d.get("patches", [])
    patches = tuple(
        BridgePatch(
            target_tool=p.get("target_tool", ""),
            dimension=p.get("dimension", ""),
            field=p.get("field", ""),
            action=p.get("action", "expose"),
            eliminates_blind_spot=p.get("eliminates", ""),
            expected_fee_delta=p.get("expected_fee_delta", 0),
        )
        for p in patches_raw
    )

    # Active packs (Extension C: tolerate optional derives_from field).
    from bulla.model import StandardProvenance

    packs_raw = d.get("active_packs", [])
    active_packs_list: list[PackRef] = []
    for p in packs_raw:
        derives_raw = p.get("derives_from")
        derives = (
            StandardProvenance.from_dict(derives_raw)
            if isinstance(derives_raw, dict)
            else None
        )
        active_packs_list.append(
            PackRef(
                name=p.get("name", ""),
                version=p.get("version", ""),
                hash=p.get("hash", ""),
                derives_from=derives,
            )
        )
    active_packs = tuple(active_packs_list)

    # Witness basis
    wb_raw = d.get("witness_basis")
    witness_basis = None
    if wb_raw and isinstance(wb_raw, dict):
        witness_basis = WitnessBasis(
            declared=wb_raw.get("declared", 0),
            inferred=wb_raw.get("inferred", 0),
            unknown=wb_raw.get("unknown", 0),
            discovered=wb_raw.get("discovered", 0),
        )

    # Parent receipt hashes (with backward compatibility for singular form)
    parent_hashes_raw = d.get("parent_receipt_hashes")
    if parent_hashes_raw:
        parent_hashes: tuple[str, ...] | None = tuple(parent_hashes_raw)
    elif d.get("parent_receipt_hash"):
        # Legacy singular form (pre-v0.24.0)
        parent_hashes = (d["parent_receipt_hash"],)
    else:
        parent_hashes = None

    # Anchor ref (external publication proof, not part of receipt hash)
    anchor_ref = d.get("anchor_ref")

    # Boundary obligations
    obligations_raw = d.get("boundary_obligations")
    obligations = None
    if obligations_raw:
        obligations = tuple(
            BoundaryObligation(
                placeholder_tool=o.get("placeholder_tool", ""),
                dimension=o.get("dimension", ""),
                field=o.get("field", ""),
                source_edge=o.get("source_edge", ""),
                expected_value=o.get("expected_value", ""),
            )
            for o in obligations_raw
        )

    # Contradictions
    contradictions_raw = d.get("contradictions")
    contradictions = None
    if contradictions_raw:
        contradictions = tuple(
            ContradictionReport(
                dimension=c.get("dimension", ""),
                values=tuple(c.get("values", ())),
                sources=tuple(c.get("sources", ())),
                severity=ContradictionSeverity(c.get("severity", "mismatch")),
            )
            for c in contradictions_raw
        )

    # Structural contradictions
    structural_raw = d.get("structural_contradictions")
    structural = None
    if structural_raw:
        structural = tuple(
            SchemaContradiction.from_dict(s) for s in structural_raw
        )

    # Pack attributions (Extension A — Standards Ingest sprint).
    # Optional list of hash-references to NOTICES.md entries that the
    # standards bodies underlying ``active_packs`` require crediting.
    # None when no active pack carries an attribution requirement.
    attributions_raw = d.get("pack_attributions")
    pack_attributions: tuple[str, ...] | None = (
        tuple(attributions_raw) if attributions_raw else None
    )

    return WitnessReceipt(
        receipt_version=d.get("receipt_version", "0.1.0"),
        kernel_version=d.get("kernel_version", ""),
        composition_hash=d.get("composition_hash", ""),
        diagnostic_hash=d.get("diagnostic_hash", ""),
        policy_profile=policy,
        fee=d.get("fee", 0),
        blind_spots_count=d.get("blind_spots_count", 0),
        bridges_required=d.get("bridges_required", 0),
        unknown_dimensions=d.get("unknown_dimensions", 0),
        disposition=Disposition(d.get("disposition", "proceed")),
        timestamp=d.get("timestamp", ""),
        patches=patches,
        anchor_ref=anchor_ref,
        parent_receipt_hashes=parent_hashes,
        active_packs=active_packs,
        witness_basis=witness_basis,
        inline_dimensions=d.get("inline_dimensions"),
        boundary_obligations=obligations,
        contradictions=contradictions,
        unmet_obligations=d.get("unmet_obligations", 0),
        structural_contradictions=structural,
        contradiction_score=d.get("contradiction_score", 0),
        pack_attributions=pack_attributions,
    )
