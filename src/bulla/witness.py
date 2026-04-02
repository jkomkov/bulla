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
    BridgePatch,
    Composition,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    PackRef,
    PolicyProfile,
    WitnessBasis,
    WitnessReceipt,
)

RECEIPT_VERSION = "0.1.0"
DEFAULT_POLICY = DEFAULT_POLICY_PROFILE


def _resolve_disposition(
    diag: Diagnostic,
    policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
    unknown_dimensions: int = 0,
) -> Disposition:
    """Map measurement to judgment under a named policy.

    Uses the PolicyProfile thresholds to determine disposition.
    The profile is recorded in the receipt so consumers know which
    judgment logic was applied.
    """
    has_blind_spots = diag.n_unbridged > 0
    has_fee = diag.coherence_fee > policy.max_fee
    over_blind_spots = len(diag.blind_spots) > policy.max_blind_spots
    over_unknown = (
        policy.max_unknown >= 0 and unknown_dimensions > policy.max_unknown
    )
    needs_bridge = policy.require_bridge and has_blind_spots

    if has_blind_spots and has_fee:
        return Disposition.REFUSE_PENDING_DISCLOSURE
    if over_unknown:
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
    parent_receipt_hash: str | None = None,
    active_packs: tuple[PackRef, ...] = (),
    witness_basis: WitnessBasis | None = None,
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

    ``parent_receipt_hash`` links this receipt to a prior witness event
    (e.g. the original receipt before bridge repair). Enables receipt
    chains: original -> repair -> patched.

    ``active_packs`` records the lexical constitution in force: which
    convention packs were active, in precedence order. Order is
    semantics — later packs override earlier ones.

    ``witness_basis`` records the epistemic provenance of the
    composition's conventions. The kernel does not compute this;
    the caller attests it. When provided, ``witness_basis.unknown``
    overrides the ``unknown_dimensions`` parameter to ensure
    consistency — the receipt cannot record a basis that disagrees
    with the unknown count used for policy judgment.
    """
    effective_unknown = (
        witness_basis.unknown if witness_basis is not None
        else unknown_dimensions
    )

    patches = _diagnostic_to_patches(diag)
    disposition = _resolve_disposition(diag, policy_profile, effective_unknown)

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
        parent_receipt_hash=parent_receipt_hash,
        active_packs=active_packs,
        witness_basis=witness_basis,
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
    expected = _resolve_disposition(
        diag, receipt.policy_profile, receipt.unknown_dimensions,
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
