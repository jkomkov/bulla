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
from datetime import datetime, timezone

from bulla import __version__
from bulla.model import (
    BridgePatch,
    Composition,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    PolicyProfile,
    WitnessReceipt,
)

RECEIPT_VERSION = "0.1.0"
DEFAULT_POLICY = DEFAULT_POLICY_PROFILE


def _resolve_disposition(
    diag: Diagnostic,
    policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
) -> Disposition:
    """Map measurement to judgment under a named policy.

    Uses the PolicyProfile thresholds to determine disposition.
    The profile is recorded in the receipt so consumers know which
    judgment logic was applied.
    """
    has_blind_spots = diag.n_unbridged > 0
    has_fee = diag.coherence_fee > policy.max_fee
    over_blind_spots = len(diag.blind_spots) > policy.max_blind_spots
    needs_bridge = policy.require_bridge and has_blind_spots

    if has_blind_spots and has_fee:
        return Disposition.REFUSE_PENDING_DISCLOSURE
    if needs_bridge or over_blind_spots:
        return Disposition.PROCEED_WITH_BRIDGE
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
) -> WitnessReceipt:
    """Produce a WitnessReceipt from a Diagnostic and Composition.

    This is the core witness function. It is deterministic given the
    same inputs (except timestamp). Everything an agent needs to decide
    whether to proceed is in the receipt.

    Uses ``Composition.canonical_hash()`` for composition identity —
    hashes structure, not presentation. Two YAML files with different
    formatting but identical semantics produce the same composition hash.

    ``policy_profile`` is a PolicyProfile with explicit thresholds.
    Recorded in the receipt so consumers can verify the disposition
    follows from the measurement under the stated policy.
    """
    patches = _diagnostic_to_patches(diag)
    disposition = _resolve_disposition(diag, policy_profile)

    return WitnessReceipt(
        receipt_version=RECEIPT_VERSION,
        kernel_version=__version__,
        composition_hash=comp.canonical_hash(),
        diagnostic_hash=diag.content_hash(),
        policy_profile=policy_profile,
        fee=diag.coherence_fee,
        blind_spots_count=len(diag.blind_spots),
        bridges_required=diag.n_unbridged,
        unknown_dimensions=unknown_dimensions,
        disposition=disposition,
        timestamp=datetime.now(timezone.utc).isoformat(),
        patches=patches,
    )
