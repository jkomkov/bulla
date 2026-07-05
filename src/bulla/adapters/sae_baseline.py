"""B0 reconstruction-loss baseline for G23 A3 Gate 6.

Gate 6 of the A3 plan
(``~/.claude/plans/review-where-we-are-ancient-peach.md``):

    |ρ|(B0, dim H¹) < 0.5 per composition
        OR
    partial-ρ(H¹, adv-success | B0) ≥ 0.3 on AdvBench-100

B0 is a scalar reconstruction-quality proxy per cross-model composition:
the §3b sweep records B0 alongside dim H¹ for each of the 5 compositions
× 3 ablated maps. If dim H¹ is highly correlated with B0 (``|ρ| > 0.5``),
the topological invariant might just be tracking SAE reconstruction
quality rather than real cross-model coordination structure — the modal
A3-WEAK landing zone (per N1 effect-size floor).

# What B0 measures

Each SAE feature carries an ``activation_p99`` scalar: the 99th-percentile
activation magnitude on a held-out reference distribution (per
``SAEFeatureData`` in commit 1a). Higher activation_p99 → feature fires
more confidently on the calibration corpus → SAE reconstructs activations
in this region more reliably.

B0 aggregates ``activation_p99`` across the features used in a cross-
model composition. Two aggregation modes are exposed:

  * ``mean``: average activation_p99 across all features on both sides.
    Most common; smooth across sample sizes.
  * ``max``: max activation_p99 across features. Sensitive to single
    "loud" feature that dominates the composition.
  * ``geometric_mean``: geometric mean (used when activation magnitudes
    span many orders of magnitude).

# Iter-1 vs Iter-2/3

Iter-1 (this commit): pure-Python aggregation over ``SAEFeatureData.activation_p99``
fields. No torch / no HF required; runs in the same dependency-light
slice the rest of the §3a′ tripwires run in.

Iter-2/3: extends with REAL SAE reconstruction-loss calibration from a
held-out activation corpus. The Iter-1 ``compute_b0_baseline`` API stays
the same — only the underlying ``activation_p99`` values change (loaded
via ``sae_lens_backend.load_sae_dictionary`` with a real
``ActivationCorpus``).

# Lazy-import discipline

Pure-Python aggregation; no torch / no sae-lens. SAEDictionary import is
the only dependency, and that module imports without torch (per commit 1a).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from bulla.adapters.sae_compose import CrossModelComposition
from bulla.adapters.sae_data import SAEDictionary, SAEFeatureData


AggregationMode = Literal["mean", "max", "geometric_mean"]


@dataclass(frozen=True)
class B0Baseline:
    """Reconstruction-loss baseline scalar + provenance.

    Records the computed B0 value plus the inputs that produced it so
    receipt structures downstream can canonicalize the gate-6 calculation.

    Attributes:
        value: scalar B0. Always >= 0.
        aggregation: one of ``"mean"``, ``"max"``, ``"geometric_mean"``.
        n_features_a: number of features contributed from side A.
        n_features_b: number of features contributed from side B.
        all_zero: True iff every contributing feature had ``activation_p99 == 0``
            (typically: the SAE was loaded with ``activation_corpus=None``).
            Gate 6 SHOULD be skipped when this is True — there's no signal
            to correlate against.
    """

    value: float
    aggregation: str
    n_features_a: int
    n_features_b: int
    all_zero: bool


def _aggregate(values: tuple[float, ...], mode: AggregationMode) -> float:
    """Reduce a tuple of nonneg floats by the requested aggregation mode."""
    if not values:
        raise ValueError("cannot aggregate empty values tuple")
    for i, v in enumerate(values):
        if v < 0:
            raise ValueError(
                f"activation_p99 must be nonneg; values[{i}] = {v}"
            )
    if mode == "mean":
        return sum(values) / len(values)
    if mode == "max":
        return max(values)
    if mode == "geometric_mean":
        # Stable: log-sum-mean-exp; treat zeros as eps for log-stability,
        # then rescale. If ALL values are zero, return 0.
        if all(v == 0.0 for v in values):
            return 0.0
        # Drop zeros before geometric-mean; document via len.
        nonzero = tuple(v for v in values if v > 0)
        if not nonzero:
            return 0.0
        return math.exp(sum(math.log(v) for v in nonzero) / len(nonzero))
    raise ValueError(f"unknown aggregation mode: {mode!r}")


def compute_b0_baseline(
    *,
    dict_a: SAEDictionary,
    dict_b: SAEDictionary,
    composition: CrossModelComposition,
    aggregation: AggregationMode = "mean",
) -> B0Baseline:
    """Compute B0 reconstruction-loss baseline for a cross-model composition.

    Pulls the ``activation_p99`` of every feature touched by an edge in
    the composition and aggregates across both sides. Features not
    referenced by any edge are excluded (B0 is per-composition, not
    per-dictionary, so an unused feature shouldn't influence the
    baseline).

    Tie-in to Gate 6: across the 5 compositions in the §3b sweep, the
    sweep records ``(B0, dim H¹)`` per composition. The pre-registered
    threshold is ``|ρ|(B0, dim H¹) < 0.5``; values in [0.5, 0.7] land
    in the modal A3-WEAK zone, > 0.7 fails Gate 6.

    Args:
        dict_a: side-A SAE dictionary (typically Gemma-2-2B at L20).
            Must have ``model_id`` matching the composition's side-A
            tools.
        dict_b: side-B SAE dictionary (typically GPT-2-Small at L11).
            Must have ``model_id`` matching the composition's side-B
            tools.
        composition: the cross-model composition whose edges select
            which features contribute to B0.
        aggregation: how to reduce per-feature ``activation_p99`` to
            a scalar. Defaults to ``"mean"`` (smooth, robust to outliers).

    Returns:
        B0Baseline carrying value + provenance.

    Raises:
        ValueError: if any composition edge references a feature_id
            outside the dictionary range, or if the dictionary
            ``model_id`` does not match the composition's expected
            sides.
    """
    # Collect referenced feature_ids per side
    referenced_a: set[int] = set()
    referenced_b: set[int] = set()
    for idx_a, idx_b in composition.cross_model_edges:
        spec_a = composition.features_a[idx_a]
        spec_b = composition.features_b[idx_b]
        # Sanity: composition features' model_id must match the dicts'
        if spec_a.model_id != dict_a.model_id:
            raise ValueError(
                f"composition.features_a[{idx_a}].model_id="
                f"{spec_a.model_id!r} != dict_a.model_id="
                f"{dict_a.model_id!r}"
            )
        if spec_b.model_id != dict_b.model_id:
            raise ValueError(
                f"composition.features_b[{idx_b}].model_id="
                f"{spec_b.model_id!r} != dict_b.model_id="
                f"{dict_b.model_id!r}"
            )
        referenced_a.add(spec_a.feature_id)
        referenced_b.add(spec_b.feature_id)

    # Pull activation_p99 from the dictionaries by feature_id
    def _pull(d: SAEDictionary, ids: set[int]) -> tuple[float, ...]:
        out = []
        for fid in sorted(ids):
            if fid >= len(d.features):
                raise ValueError(
                    f"feature_id={fid} out of range for {d.model_id} "
                    f"dict (n_features={len(d.features)})"
                )
            f: SAEFeatureData = d.features[fid]
            out.append(float(f.activation_p99))
        return tuple(out)

    vals_a = _pull(dict_a, referenced_a)
    vals_b = _pull(dict_b, referenced_b)
    all_vals = vals_a + vals_b

    # Edge case: no edges → no features referenced → B0 not defined.
    if not all_vals:
        return B0Baseline(
            value=0.0,
            aggregation=aggregation,
            n_features_a=0,
            n_features_b=0,
            all_zero=True,
        )

    all_zero = all(v == 0.0 for v in all_vals)
    return B0Baseline(
        value=_aggregate(all_vals, aggregation),
        aggregation=aggregation,
        n_features_a=len(vals_a),
        n_features_b=len(vals_b),
        all_zero=all_zero,
    )
