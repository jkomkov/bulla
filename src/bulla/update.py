"""Update protocol — coherence-preserving interface evolution.

When two MCP server manifests differ — a SEP-1400 minor-version bump,
a SEP-1575 compatibility class change, an enum narrowing, a field rename
— `diff_classify` decides whether the difference preserves witness rank
(i.e., whether existing receipts remain valid without re-derivation),
and `repair` outputs the minimum patch making a non-preserving update
preserving.

This is the operational realization of:

  - Conjecture 9.5 (Coherence-Preserving Update) — the chain-homotopy
    invariance of witness rank under interface updates.
  - Theorem 9.5-B (Operational characterization) — the polynomial-time
    decision procedure via mapping-cone acyclicity test.
  - §6.2 Repair Duality — minimum-disclosure repair has cardinality
    exactly r(G), specialized to updates: failing cocycles ARE the
    minimum-repair set.

The math is documented in `papers/composition-doctrine/notes/9-5-attack-phase-{a,b,c}.md`.
The architecture is documented in `bulla/docs/UPDATE-PROTOCOL.md`.

**Status: skeleton (Sprint 2 Day 3).** The data types and entry-point
signatures are stable; the implementations are gated on:
  1. Phase B Lean refinement of `mappingConeUpdateClass` with concrete
     cellular-sheaf rank computations.
  2. Empirical validation on the 703-composition Bulla corpus.

Concrete rank-test implementation (Phase E): build C^•(G), C^•(G') as
rational matrices D, D'; build the chain map f^• from update data;
compute mapping cone C_f; test H^0(C_f) = H^1(C_f) = 0 via rank tests
over Q; if both vanish, extract the chain homotopy h via Moore-Penrose
pseudoinverse splitting (consistent with bulla.witness_geometry's
existing exact-rational discipline).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

from bulla.model import Composition


@dataclass(frozen=True)
class Cocycle:
    """A 1-cocycle in the seam complex of a composition.

    Failing cocycles are the basis of `H^1(cone f^•)` when the mapping-
    cone test fails — by §6.2 repair duality, they are exactly the
    minimum-disclosure repair set making the update preserving.
    """

    basis_vector: tuple[Fraction, ...]
    """Coefficients of the cocycle in the seam-complex C^1 basis."""

    context: str
    """The MCP server.tool context where this cocycle is concentrated."""

    field: str
    """The field name (within `context`) carrying the cocycle."""


@dataclass(frozen=True)
class ChainHomotopy:
    """Explicit chain-homotopy data witnessing a coherence-preserving update.

    The four matrices `(f_dot, g_dot, h, h_prime)` together satisfy:
      f_dot ∘ g_dot - id = δ ∘ h + h ∘ δ
      g_dot ∘ f_dot - id = δ ∘ h_prime + h_prime ∘ δ

    Carried on a coherence-preservation certificate; verifiable by any
    consumer with O((|G|+|G'|)^2) linear-algebra checks (no proof
    re-derivation needed).
    """

    f_dot: tuple[tuple[Fraction, ...], ...]
    """Chain map C^•(G) → C^•(G')."""

    g_dot: tuple[tuple[Fraction, ...], ...]
    """Chain map C^•(G') → C^•(G) (the inverse-up-to-homotopy)."""

    h: tuple[tuple[Fraction, ...], ...]
    """Homotopy g_dot ∘ f_dot ∼ id_{C^•(G)}."""

    h_prime: tuple[tuple[Fraction, ...], ...]
    """Homotopy f_dot ∘ g_dot ∼ id_{C^•(G')}."""


@dataclass(frozen=True)
class CoherencePreservationCertificate:
    """Output of `diff_classify`: machine-verifiable coherence claim.

    When `preserves_coherence is True`, `chain_homotopy` is non-None and
    witnesses receipt-transport (Theorem 9.5-A.iii). When False,
    `failing_cocycles` lists the minimum-repair basis (§6.2 duality);
    `bulla repair` consumes this list to construct a patched manifest.
    """

    schema: str = "bulla.update-certificate.v1"
    """Certificate format version. Bulla consumers verify this schema string."""

    old_manifest_hash: str = ""
    """SHA-256 of the canonical-form old manifest (Composition.canonical_hash)."""

    new_manifest_hash: str = ""
    """SHA-256 of the canonical-form new manifest."""

    preserves_coherence: bool = False
    """Whether the update preserves witness rank and receipts."""

    witness_rank_old: int = 0
    """r(G) for the old composition."""

    witness_rank_new: int = 0
    """r(G') for the new composition. Equals witness_rank_old iff preserves."""

    chain_homotopy: Optional[ChainHomotopy] = None
    """Explicit chain-homotopy data when preserves_coherence is True."""

    failing_cocycles: tuple[Cocycle, ...] = ()
    """Minimum-repair cocycle basis when preserves_coherence is False."""

    minimum_repair_cardinality: int = 0
    """`len(failing_cocycles)`; equals `r(cone f^•)` by §6.2."""

    decision_procedure: str = "bulla.mapping-cone-acyclicity@v0"
    """Algorithm tag for cross-version verifiability."""


@dataclass(frozen=True)
class RepairCertificate:
    """Output of `repair`: patched manifest + minimum-disclosure receipt.

    The repair disclosures are exactly the failing cocycles from the
    underlying coherence-preservation certificate, by §6.2 duality.
    Verifying receipt: `diff_classify(old, patched_new)` must return
    `preserves_coherence: True` (idempotency check).
    """

    coherence_certificate: CoherencePreservationCertificate
    """The underlying diff_classify certificate (preserves_coherence=False)."""

    repair_disclosures: tuple[Cocycle, ...]
    """Disclosures added to make the update coherence-preserving.
    Equal to coherence_certificate.failing_cocycles by §6.2."""

    parent_receipt_hashes: tuple[str, ...]
    """Receipt-chain extension: receipts for `old` are valid for the
    patched manifest via this chain."""


# Sentinel for unimplemented operations (Phase B gating).
_PHASE_B_GATE = (
    "diff_classify and repair require Phase B cellular-sheaf rank-test "
    "implementation. The data types and signatures are stable; the "
    "implementation is gated on the Lean formalization at "
    "papers/composition-doctrine/lean/CompositionDoctrine/Update.lean "
    "(mappingConeUpdateClass def) being instantiated with concrete "
    "rank computations from bulla.witness_geometry."
)


def diff_classify(
    old: Composition, new: Composition,
    *,
    linear_data: Optional[dict] = None,
) -> CoherencePreservationCertificate:
    """Decide whether `new` is a coherence-preserving update of `old`.

    Implements the mapping-cone acyclicity test (Theorem 9.5-B):
        1. Build C^•(old), C^•(new) as rational coboundary matrices D, D'.
        2. Build the chain map f^• from the schema-diff between manifests.
        3. Compute mapping cone C_f.
        4. Test H^0(C_f) = 0 ∧ H^1(C_f) = 0 via rank tests over Q.
        5. If both vanish, extract chain homotopy h via Moore-Penrose
           pseudoinverse (consistent with bulla.witness_geometry).

    Time complexity: O((|G|+|u|)^ω) with exact rational arithmetic,
    ω < 2.373.

    Args:
        old: pre-update composition.
        new: post-update composition.
        linear_data: optional explicit chain-map data, used to disambiguate
            textual renames whose linear extension is ambiguous (logic-drift
            cases). When omitted, the function attempts to infer chain-map
            data from the manifest diff; failures yield a certificate with
            `decision_procedure` set to "requires-linear-data".

    Returns:
        A `CoherencePreservationCertificate`. Check `.preserves_coherence`
        for the verdict; consume `.failing_cocycles` if False to drive
        `repair`.

    Raises:
        NotImplementedError: Phase B cellular-sheaf realization required.
            See module docstring for sequencing.
    """
    raise NotImplementedError(_PHASE_B_GATE)


def repair(
    old: Composition, new: Composition,
    *,
    linear_data: Optional[dict] = None,
    max_disclosures: Optional[int] = None,
) -> tuple[Composition, RepairCertificate]:
    """Construct the minimum-disclosure patch making `new` a coherence-
    preserving update of `old`.

    By §6.2 repair duality, the minimum patch consists of disclosures
    promoting the failing cocycles (output by `diff_classify`) from
    latent to observable. The result satisfies the idempotency check:
    `diff_classify(old, patched_new).preserves_coherence == True`.

    Args:
        old: pre-update composition.
        new: post-update composition.
        linear_data: same as `diff_classify`.
        max_disclosures: refuse repair if minimum patch exceeds this many
            disclosures. None = no limit.

    Returns:
        Tuple of (patched_new_composition, RepairCertificate). The
        patched composition has the minimum disclosures applied; the
        certificate records the patch structure for receipt-chain
        extension.

    Raises:
        NotImplementedError: Phase B cellular-sheaf realization required.
        ValueError: when `new` is already a coherence-preserving update
            of `old` (no repair needed; use `diff_classify` directly).
        RuntimeError: when minimum repair exceeds `max_disclosures`.
    """
    raise NotImplementedError(_PHASE_B_GATE)


def is_implementation_available() -> bool:
    """Return True iff the cellular-sheaf rank-test implementation is loaded.

    Currently always returns False; the Phase B implementation will register
    here when it lands. Use this to feature-gate calling code:

        if bulla.update.is_implementation_available():
            cert = bulla.update.diff_classify(old, new)
        else:
            # Fall back to manual review or schema-equality heuristics.
            ...
    """
    return False


__all__ = [
    "Cocycle",
    "ChainHomotopy",
    "CoherencePreservationCertificate",
    "RepairCertificate",
    "diff_classify",
    "repair",
    "is_implementation_available",
]
