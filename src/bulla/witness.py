"""Witness kernel: deterministic measurement → receipt pipeline.

Layer A (measurement): Diagnostic — already exists in diagnostic.py
Layer B (binding):     WitnessReceipt — produced here
Layer C (judgment):    Disposition — resolved here from policy

The kernel is intentionally small and stateless. Given a Diagnostic
and a composition hash, it produces a WitnessReceipt with content-
addressable hashes. No network, no side effects, no policy opinions
beyond the disposition thresholds.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from bulla import __version__
from bulla.model import (
    BoundaryObligation,
    BridgePatch,
    Composition,
    ContradictionReport,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    PackRef,
    PolicyProfile,
    SchemaContradiction,
    WitnessBasis,
    WitnessReceipt,
)

RECEIPT_VERSION = "0.1.0"
DEFAULT_POLICY = DEFAULT_POLICY_PROFILE


def _resolve_disposition(
    diag: Diagnostic,
    policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
    unknown_dimensions: int = 0,
    unmet_obligations: int = 0,
    contradiction_count: int = 0,
    structural_contradiction_score: int = 0,
) -> Disposition:
    """Map measurement to judgment under a named policy.

    Reasons over a 2D risk surface (fee x contradiction_score):

      fee=0, contradictions=0  -> PROCEED
      fee>0, contradictions=0  -> PROCEED_WITH_BRIDGE / REFUSE
      fee=0, contradictions>0  -> PROCEED_WITH_CAUTION
      fee>0, contradictions>0  -> REFUSE (both axes hot)

    Priority chain (first match wins):

     1. blind_spots > 0 AND fee > max_fee -> refuse
     2. unknown > max_unknown (when >= 0) -> refuse
     3. unmet_obligations > max_unmet_obligations (when >= 0) -> refuse
     4. contradiction_count > max_contradictions (when >= 0) -> refuse
     5. structural > max_structural_contradictions (when >= 0) -> refuse
     6. require_bridge AND blind_spots > 0 -> bridge
     7. blind_spots > max_blind_spots -> bridge
     8. structural > 0 -> caution (incompatibility without opacity)
     9. fee > max_fee -> receipt
    10. Otherwise -> proceed

    Note on caution vs. threshold: PROCEED_WITH_CAUTION fires on ANY
    nonzero structural_contradiction_score, independent of the
    max_structural_contradictions threshold. The threshold controls the
    refuse boundary ("how many before I block"), not the caution boundary.
    Any visible schema incompatibility is a real signal the agent should
    know about, even if the policy tolerates it.
    """
    has_blind_spots = diag.n_unbridged > 0
    has_fee = diag.coherence_fee > policy.max_fee
    has_structural = structural_contradiction_score > 0
    over_blind_spots = len(diag.blind_spots) > policy.max_blind_spots
    over_unknown = (
        policy.max_unknown >= 0 and unknown_dimensions > policy.max_unknown
    )
    over_unmet = (
        policy.max_unmet_obligations >= 0
        and unmet_obligations > policy.max_unmet_obligations
    )
    over_contradictions = (
        policy.max_contradictions >= 0
        and contradiction_count > policy.max_contradictions
    )
    over_structural = (
        policy.max_structural_contradictions >= 0
        and structural_contradiction_score > policy.max_structural_contradictions
    )
    needs_bridge = policy.require_bridge and has_blind_spots

    if has_blind_spots and has_fee:
        return Disposition.REFUSE_PENDING_DISCLOSURE
    if over_unknown:
        return Disposition.REFUSE_PENDING_DISCLOSURE
    if over_unmet:
        return Disposition.REFUSE_PENDING_DISCLOSURE
    if over_contradictions:
        return Disposition.REFUSE_PENDING_DISCLOSURE
    if over_structural:
        return Disposition.REFUSE_PENDING_DISCLOSURE
    if needs_bridge or over_blind_spots:
        return Disposition.PROCEED_WITH_BRIDGE
    if has_structural:
        return Disposition.PROCEED_WITH_CAUTION
    if has_fee:
        return Disposition.PROCEED_WITH_RECEIPT
    return Disposition.PROCEED


def _diagnostic_to_patches(diag: Diagnostic) -> tuple[BridgePatch, ...]:
    """Convert Bridge recommendations to machine-actionable BridgePatch objects."""
    patches: list[BridgePatch] = []
    for br in diag.bridges:
        for tool in br.add_to:
            patches.append(
                BridgePatch(
                    target_tool=tool,
                    dimension=br.eliminates,
                    field=br.field,
                    action="expose",
                    eliminates_blind_spot=br.eliminates,
                    expected_fee_delta=0,  # per-patch delta requires re-diagnosis
                )
            )
    return tuple(patches)


def witness(
    diag: Diagnostic,
    comp: Composition,
    unknown_dimensions: int = 0,
    policy_profile: PolicyProfile = DEFAULT_POLICY_PROFILE,
    parent_receipt_hash: str | None = None,
    parent_receipt_hashes: tuple[str, ...] | None = None,
    active_packs: tuple[PackRef, ...] = (),
    witness_basis: WitnessBasis | None = None,
    inline_dimensions: dict | None = None,
    boundary_obligations: tuple[BoundaryObligation, ...] | None = None,
    contradictions: tuple[ContradictionReport, ...] | None = None,
    unmet_obligations: int = 0,
    contradiction_count: int = 0,
    structural_contradictions: tuple[SchemaContradiction, ...] | None = None,
    contradiction_score: int = 0,
) -> WitnessReceipt:
    """Produce a WitnessReceipt from a Diagnostic and Composition.

    This is the core witness function. It is deterministic given the
    same inputs (except timestamp). Everything an agent needs to decide
    whether to proceed is in the receipt.

    ``parent_receipt_hash`` is a convenience parameter for single-parent
    chains. ``parent_receipt_hashes`` is the DAG-capable parameter
    accepting multiple parents as a precedence-ordered tuple (later
    entries override earlier ones, consistent with the pack stack).

    Provide at most one of these; if both are given, ``ValueError``
    is raised. A single parent supplied via ``parent_receipt_hash``
    is stored as a 1-tuple on the receipt.

    ``inline_dimensions`` embeds discovered pack content directly in
    the receipt. When None, the field is omitted from the receipt hash
    for backward compatibility with pre-v0.23.0 receipts.
    """
    if parent_receipt_hash is not None and parent_receipt_hashes is not None:
        raise ValueError(
            "Provide parent_receipt_hash or parent_receipt_hashes, not both"
        )

    resolved_parents: tuple[str, ...] | None = parent_receipt_hashes
    if parent_receipt_hash is not None:
        resolved_parents = (parent_receipt_hash,)

    effective_unknown = (
        witness_basis.unknown if witness_basis is not None
        else unknown_dimensions
    )

    convention_contradiction_count = (
        contradiction_count
        if contradiction_count
        else (len(contradictions) if contradictions else 0)
    )

    patches = _diagnostic_to_patches(diag)
    disposition = _resolve_disposition(
        diag,
        policy_profile,
        effective_unknown,
        unmet_obligations=unmet_obligations,
        contradiction_count=convention_contradiction_count,
        structural_contradiction_score=contradiction_score,
    )

    return WitnessReceipt(
        receipt_version=RECEIPT_VERSION,
        kernel_version=__version__,
        composition_hash=comp.canonical_hash(),
        diagnostic_hash=diag.content_hash(),
        policy_profile=policy_profile,
        fee=diag.coherence_fee,
        blind_spots_count=len(diag.blind_spots),
        bridges_required=diag.n_unbridged,
        unknown_dimensions=effective_unknown,
        disposition=disposition,
        timestamp=datetime.now(timezone.utc).isoformat(),
        patches=patches,
        parent_receipt_hashes=resolved_parents,
        active_packs=active_packs,
        witness_basis=witness_basis,
        inline_dimensions=inline_dimensions,
        boundary_obligations=boundary_obligations,
        contradictions=contradictions,
        unmet_obligations=unmet_obligations,
        structural_contradictions=structural_contradictions,
        contradiction_score=contradiction_score,
    )


# ── Verification ─────────────────────────────────────────────────────


def verify_receipt_consistency(
    receipt: WitnessReceipt,
    comp: Composition,
    diag: Diagnostic,
) -> tuple[bool, list[str]]:
    """Check that a receipt is consistent with its composition and diagnostic.

    Requires the kernel's objects. Returns ``(is_valid, violations)``.
    Use when you have the original composition and diagnostic and want
    to confirm the receipt binds them correctly.
    """
    violations: list[str] = []
    if receipt.composition_hash != comp.canonical_hash():
        violations.append("composition_hash mismatch")
    if receipt.diagnostic_hash != diag.content_hash():
        violations.append("diagnostic_hash mismatch")
    if receipt.fee != diag.coherence_fee:
        violations.append(
            f"fee {receipt.fee} != diagnostic fee {diag.coherence_fee}"
        )
    if receipt.blind_spots_count != len(diag.blind_spots):
        violations.append("blind_spots_count mismatch")
    if receipt.bridges_required != diag.n_unbridged:
        violations.append("bridges_required mismatch")
    if receipt.witness_basis is not None:
        if receipt.unknown_dimensions != receipt.witness_basis.unknown:
            violations.append(
                "unknown_dimensions != witness_basis.unknown"
            )
    convention_contradictions = len(receipt.contradictions) if receipt.contradictions else 0
    expected = _resolve_disposition(
        diag, receipt.policy_profile, receipt.unknown_dimensions,
        unmet_obligations=receipt.unmet_obligations,
        contradiction_count=convention_contradictions,
        structural_contradiction_score=receipt.contradiction_score,
    )
    if receipt.disposition != expected:
        violations.append(
            f"disposition {receipt.disposition.value} "
            f"!= expected {expected.value}"
        )
    return (len(violations) == 0, violations)


_HASH_EXCLUDED_KEYS = frozenset({"receipt_hash", "anchor_ref"})


def verify_receipt_integrity(receipt_dict: dict) -> bool:
    """Tamper detection: recompute receipt hash from a serialized dict.

    Self-contained — requires only the dict (e.g. from JSON or an MCP
    response), not the kernel or any original objects.

    The contract: ``receipt_hash`` covers every field in ``to_dict()``
    except ``receipt_hash`` itself and ``anchor_ref``. This function
    reconstructs the hash input by excluding those two keys, so it
    survives future field additions without code changes.

    Verification requires the **serialized dict** produced by
    ``WitnessReceipt.to_dict()``, which includes the timestamp.
    """
    claimed = receipt_dict.get("receipt_hash")
    if claimed is None:
        return False

    obj = {k: v for k, v in receipt_dict.items()
           if k not in _HASH_EXCLUDED_KEYS}
    computed = hashlib.sha256(
        json.dumps(obj, sort_keys=True).encode()
    ).hexdigest()
    return computed == claimed
