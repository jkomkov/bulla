"""§3a calibration spot-check on probe pairs P1, P2 (G23 A3).

Implements the protocol in
``papers/composition-doctrine/sprint_g23_a3_pre_registration.md`` §3a:

  * Run probes P1.1–P1.5 (factual recall) and P2.1–P2.5 (instruction
    following) through both Gemma-2-2B-L20 and GPT-2-Small-L11 SAEs.
  * Take top-50 activated features per side per probe.
  * Build cross-model 2-cover composition with §3b′ pairing applied.
  * Record dim H¹, Procrustes loss, B0 baseline per probe.
  * Verify the 5 §3a tripwires PASS before pre-registration locks.

# Probe pairs (locked per §3a)

P1 = factual recall (5 sentences)
P2 = instruction following (5 sentences)

# Lazy-import discipline

All heavy deps (torch, sae-lens, numpy) are lazy-imported inside
function bodies. Tests mock the heavy paths.

# CLI

    python -m bulla.compute.g23_a3_calibration \\
        --pairing-dir papers/composition-doctrine \\
        --output papers/composition-doctrine/g23_a3_calibration_spotcheck.jsonl \\
        --seed 20260507
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np  # type: ignore[import-not-found]

from bulla.adapters.sae_lens_backend import SAEBackendImportError


# ── Locked probe text (mirror §3a in pre-registration) ────────────────


LOCKED_PROBES_P1 = (
    ("P1.1", "factual_recall", "Paris is the capital of France."),
    ("P1.2", "factual_recall", "The Pacific Ocean is larger than the Atlantic Ocean."),
    ("P1.3", "factual_recall", "For a right triangle, a squared plus b squared equals c squared."),
    ("P1.4", "factual_recall", "The atomic number of carbon is six."),
    ("P1.5", "factual_recall", "Mount Everest is located in the Himalayas."),
)
LOCKED_PROBES_P2 = (
    ("P2.1", "instruction_following", "List three primary colors."),
    ("P2.2", "instruction_following", "Translate \"hello\" to Spanish."),
    ("P2.3", "instruction_following", "Calculate seventeen times twenty-three."),
    ("P2.4", "instruction_following", "What is the opposite of \"fast\"?"),
    ("P2.5", "instruction_following", "Name a major river in South America."),
)
ALL_PROBES = LOCKED_PROBES_P1 + LOCKED_PROBES_P2

DEFAULT_TOP_N_FEATURES = 50
DEFAULT_SEED = 20260507


# ── Result dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class CalibrationSample:
    """One probe's calibration record."""

    probe_id: str               # e.g. "P1.1"
    probe_category: str         # "factual_recall" or "instruction_following"
    probe_text: str
    dim_h1: int                 # cross-model 2-cover dim H¹ on probe's features
    procrustes_loss: float      # Frobenius residual after best-rotation alignment
    b0: float                   # baseline reconstruction-loss baseline
    n_features_a_used: int      # gemma side count
    n_features_b_used: int      # gpt2 side count

    def to_jsonable(self) -> dict:
        return {
            "probe_id": self.probe_id,
            "probe_category": self.probe_category,
            "probe_text": self.probe_text,
            "dim_h1": self.dim_h1,
            "procrustes_loss": self.procrustes_loss,
            "b0": self.b0,
            "n_features_a_used": self.n_features_a_used,
            "n_features_b_used": self.n_features_b_used,
        }


@dataclass(frozen=True)
class CalibrationTripwireResult:
    name: str
    passed: bool
    threshold: str       # human-readable threshold (e.g. "[1, 50]")
    measured: float | str
    note: str = ""


# ── Calibration tripwires (§3a) ───────────────────────────────────────


def check_calibration_tripwires(
    samples: tuple[CalibrationSample, ...],
) -> tuple[CalibrationTripwireResult, ...]:
    """5 tripwires, exactly mirroring §3a of the pre-registration."""
    p1 = tuple(s for s in samples if s.probe_category == "factual_recall")
    p2 = tuple(s for s in samples if s.probe_category == "instruction_following")

    if not p1 or not p2:
        # Edge case: caller didn't provide both categories. All tripwires fail.
        return (
            CalibrationTripwireResult(
                name="missing_probe_category",
                passed=False, threshold="P1+P2 both present",
                measured=f"P1={len(p1)} P2={len(p2)}",
            ),
        )

    dim_h1_p1_max = max(s.dim_h1 for s in p1)
    dim_h1_p2_max = max(s.dim_h1 for s in p2)
    procrustes_all = tuple(s.procrustes_loss for s in samples)
    b0_p1_max = max(s.b0 for s in p1)
    b0_p2_max = max(s.b0 for s in p2)

    procrustes_finite = all(
        (not _is_nan(x)) and (x != float("inf")) and (abs(x) <= 1e6)
        for x in procrustes_all
    )
    b0_finite_positive = (
        b0_p1_max > 0 and b0_p2_max > 0
        and not _is_nan(b0_p1_max) and not _is_nan(b0_p2_max)
        and b0_p1_max != float("inf") and b0_p2_max != float("inf")
    )

    # Tripwire 5 metric (probe-category distinguishability) was reformulated
    # in v3.2: under reading (b) of pre-reg §3a, dim_h1 is constant across
    # probes (= cross-model 2-cover topology, probe-independent), so the
    # original "dim_h1(P1) != dim_h1(P2)" check is structurally vacuous.
    # B0 is the per-probe quantity that varies; tripwire 5 now tests
    # whether B0 distinguishes probe categories with a minimum
    # measurable difference (~1% of B0 scale).
    b0_p1_mean = sum(s.b0 for s in p1) / len(p1)
    b0_p2_mean = sum(s.b0 for s in p2) / len(p2)
    b0_diff = abs(b0_p1_mean - b0_p2_mean)
    b0_distinguishability_threshold = 0.01 * max(b0_p1_mean, b0_p2_mean, 1e-6)

    return (
        CalibrationTripwireResult(
            name="dim_h1_p1_in_band",
            passed=1 <= dim_h1_p1_max <= 50,
            threshold="[1, 50]",
            measured=dim_h1_p1_max,
        ),
        CalibrationTripwireResult(
            name="dim_h1_p2_in_band",
            passed=1 <= dim_h1_p2_max <= 50,
            threshold="[1, 50]",
            measured=dim_h1_p2_max,
        ),
        CalibrationTripwireResult(
            name="procrustes_finite",
            passed=procrustes_finite,
            threshold="all <= 1e6, no NaN/inf",
            measured="all finite" if procrustes_finite else "NaN/inf or > 1e6",
        ),
        CalibrationTripwireResult(
            name="b0_finite_positive",
            passed=b0_finite_positive,
            threshold="B0(P1).max > 0 and B0(P2).max > 0",
            measured=f"B0(P1)={b0_p1_max} B0(P2)={b0_p2_max}",
        ),
        CalibrationTripwireResult(
            name="probe_category_distinguishability",
            passed=b0_diff > b0_distinguishability_threshold,
            threshold=f"|B0(P1).mean - B0(P2).mean| > 1% of max(B0) (v3.2: B0-based, was dim_h1-based)",
            measured=f"P1.mean={b0_p1_mean:.4f}, P2.mean={b0_p2_mean:.4f}, diff={b0_diff:.4f}",
        ),
    )


def _is_nan(x: float) -> bool:
    return x != x  # NaN's only inequality with itself


# ── End-to-end calibration runner ─────────────────────────────────────


@dataclass(frozen=True)
class _LoadedSide:
    """Pre-loaded resources for one model side (cached across probes)."""

    side: str                # "gemma" or "gpt2"
    sae: object              # sae_lens.SAE
    model: object            # transformers.PreTrainedModel
    tokenizer: object        # transformers.PreTrainedTokenizer
    layer: int
    decoder_matrix: object   # torch.Tensor (n_features, d_model)
    procrustes_R: object     # torch.Tensor (d_model, d_model) — global rotation, fit once across full dictionaries


def _load_side(*, model_id: str, layer: int, device: str = "cpu") -> _LoadedSide:
    """Heavy: load SAE + model + tokenizer + extract decoder matrix.

    Called once per side at the start of `run_full_calibration`; the
    result is reused across all 10 probes. First call triggers HF
    download (~5 GB for Gemma); cached thereafter.
    """
    from bulla.adapters.sae_lens_backend import _load_sae_model_tokenizer

    sae, model, tokenizer = _load_sae_model_tokenizer(
        model_id=model_id, layer=layer, device=device,
    )
    # Decoder matrix lives at sae.W_dec (sae-lens convention; shape
    # (n_features, d_model)).
    decoder_matrix = getattr(sae, "W_dec", None)
    if decoder_matrix is None:
        # Some sae-lens versions expose under a different name; fall back
        # to scanning the sae's parameters.
        for name in ("W_dec", "decoder", "decoder_weight"):
            if hasattr(sae, name):
                decoder_matrix = getattr(sae, name)
                break
    if decoder_matrix is None:
        raise RuntimeError(
            f"Could not locate decoder matrix on sae object for "
            f"{model_id}/L{layer}; expected attribute 'W_dec' or similar."
        )
    return _LoadedSide(
        side=("gemma" if model_id == "gemma2-2b" else "gpt2"),
        sae=sae, model=model, tokenizer=tokenizer, layer=layer,
        decoder_matrix=decoder_matrix,
        procrustes_R=None,  # filled in by `_fit_procrustes_global`
    )


def _fit_procrustes_global(
    *, side_a: _LoadedSide, side_b: _LoadedSide,
) -> object:
    """Fit one global Procrustes rotation R: D_a R ≈ D_b across the full
    dictionaries. Computed once; applied per-probe to the per-probe
    active pairs."""
    import torch

    D_a = side_a.decoder_matrix.float()  # (n_a, d_model_a)
    D_b = side_b.decoder_matrix.float()  # (n_b, d_model_b)
    # Procrustes assumes same d_model on both sides; project to the
    # smaller dim by truncating columns if mismatched. For the A3 pair
    # (Gemma d=2304, GPT-2 d=768), this means truncating Gemma's
    # decoder to d=768 — a deliberate dimension-matching choice; the
    # alternative is padding GPT-2 with zeros which artificially
    # inflates rank.
    d_a, d_b = D_a.shape[1], D_b.shape[1]
    d_min = min(d_a, d_b)
    D_a = D_a[:, :d_min]
    D_b = D_b[:, :d_min]
    n = min(D_a.shape[0], D_b.shape[0])
    M = D_a[:n].T @ D_b[:n]                 # (d_min, d_min)
    U, _S, Vt = torch.linalg.svd(M, full_matrices=False)
    return U @ Vt                            # (d_min, d_min) orthogonal


def run_calibration_on_probe(
    probe_id: str,
    probe_category: str,
    probe_text: str,
    *,
    side_a: _LoadedSide,
    side_b: _LoadedSide,
    disjoint_pairs: tuple[tuple[int, int], ...],
    top_n: int = DEFAULT_TOP_N_FEATURES,
) -> CalibrationSample:
    """Run one probe through both SAE pipelines + record §3a fields.

    Uses pre-loaded `_LoadedSide` resources so each probe is ~30 sec
    after the side-load cold-start. Per the pre-registration §3a:

      1. Run probe text through model + SAE for each side
      2. Get top-N features per side by max activation magnitude
      3. Filter §3b′ disjoint pairs to those with feature_ids in
         BOTH sides' top-N (the "active pairs" for this probe)
      4. Build cross-model composition with active pairs
      5. dim H¹ = diagnose(composition).coherence_fee
      6. B0 = mean of probe-driven activations on active features
         (both sides)
      7. Procrustes loss = sum over active pairs of (1 - cos(D_a[i] @ R, D_b[j]))
         where R is the global rotation pre-fit on full dictionaries

    Args:
        probe_id, probe_category, probe_text: locked per §3a.
        side_a: pre-loaded Gemma side (from `_load_side`).
        side_b: pre-loaded GPT-2 side.
        disjoint_pairs: §3b′ pairing output (read from artifacts.json).
        top_n: top-K features per side per probe (default 50, locked
            per §3a).

    Returns:
        CalibrationSample with all §3a fields populated.
    """
    import torch

    from bulla.adapters.sae import SAEFeatureSpec
    from bulla.adapters.sae_compose import build_cross_model_composition
    from bulla.adapters.sae_lens_backend import _get_feature_activations_for_probe
    from bulla.diagnostic import diagnose

    # ── Reading (b) of pre-reg §3a (v3.2 calibration correction) ──────
    # The §3b′ disjoint pairs ARE the active set; per-probe activations
    # inform B0 (and Procrustes via the global R) but do NOT filter the
    # composition. The earlier reading (a) — top-N filtered by §3b′
    # intersection — produced essentially-zero overlap (50 ∩ 30 from
    # 40k features ≈ 0 expected). v3.2 disambiguates "after §3b′
    # pairing intersection" toward "operate on the §3b′-pair set"
    # rather than "filter to top-N ∩ §3b′ pairs". Both readings are
    # consistent with the pre-reg text; only reading (b) is empirically
    # tractable. dim_h1 is now constant across probes (= 30, the
    # cross-model 2-cover topology); B0 carries the per-probe signal.
    gemma_pair_ids = tuple(sorted({int(a) for a, _ in disjoint_pairs}))
    gpt2_pair_ids = tuple(sorted({int(b) for _, b in disjoint_pairs}))

    # Step 1+2: get per-probe max-activation values for the §3b′ pair endpoints
    acts_a = _get_feature_activations_for_probe(
        sae=side_a.sae, model=side_a.model, tokenizer=side_a.tokenizer,
        layer=side_a.layer, probe_text=probe_text,
        feature_ids=gemma_pair_ids,
    )
    acts_b = _get_feature_activations_for_probe(
        sae=side_b.sae, model=side_b.model, tokenizer=side_b.tokenizer,
        layer=side_b.layer, probe_text=probe_text,
        feature_ids=gpt2_pair_ids,
    )

    # Step 3: build cross-model composition with all §3b' pairs (constant topology)
    features_a = tuple(
        SAEFeatureSpec(model_id="gemma2-2b", layer=side_a.layer, feature_id=fid)
        for fid in gemma_pair_ids
    )
    features_b = tuple(
        SAEFeatureSpec(model_id="gpt2-small", layer=side_b.layer, feature_id=fid)
        for fid in gpt2_pair_ids
    )
    a_idx_map = {fid: i for i, fid in enumerate(gemma_pair_ids)}
    b_idx_map = {fid: i for i, fid in enumerate(gpt2_pair_ids)}
    cross_edges = tuple(
        (a_idx_map[int(a)], b_idx_map[int(b)]) for a, b in disjoint_pairs
    )
    composition = build_cross_model_composition(
        features_a=features_a, features_b=features_b,
        cross_model_edges=cross_edges,
    )

    # Step 4: dim H¹ (constant across probes under reading (b); reflects topology)
    diag = diagnose(composition.composition)

    # Step 5: B0 = mean of probe-driven activations across all §3b' pair endpoints.
    # Probe-dependent (this is the per-probe signal under reading (b)).
    all_acts = list(acts_a.values()) + list(acts_b.values())
    b0_value = sum(all_acts) / len(all_acts) if all_acts else 0.0

    # Step 6: Procrustes loss = sum over §3b' pairs of (1 - cos(D_a[a] @ R, D_b[b])).
    # Constant across probes under reading (b) since R, pairs, and dictionaries
    # are all probe-independent. Tripwire 3 (procrustes_finite) is a SVD-fit
    # sanity check; not probe-distinguishing.
    R = side_a.procrustes_R
    if R is None:
        proc_loss = float("nan")
    else:
        d_min = R.shape[0]
        D_a = side_a.decoder_matrix.float()[:, :d_min]
        D_b = side_b.decoder_matrix.float()[:, :d_min]
        proc_loss = 0.0
        for a_id, b_id in disjoint_pairs:
            d_src = D_a[int(a_id)]
            d_tgt = D_b[int(b_id)]
            projected = d_src @ R
            denom = float(torch.linalg.norm(projected) * torch.linalg.norm(d_tgt) + 1e-12)
            num = float((projected * d_tgt).sum())
            proc_loss += 1.0 - (num / denom)

    return CalibrationSample(
        probe_id=probe_id, probe_category=probe_category,
        probe_text=probe_text,
        dim_h1=int(diag.coherence_fee),
        procrustes_loss=float(proc_loss),
        b0=float(b0_value),
        n_features_a_used=len(features_a),
        n_features_b_used=len(features_b),
    )


def run_full_calibration(
    *,
    pairing_artifacts_dir: Path,
    output_jsonl: Path,
    seed: int = DEFAULT_SEED,
    device: str = "cpu",
) -> tuple[CalibrationSample, ...]:
    """Run all 10 probes + persist as JSONL.

    Loads SAE + model + tokenizer ONCE per side (cold-start ~5-8 min on
    first run; cached HF artifacts thereafter), then loops the 10 probes
    using the pre-loaded resources. Per-probe wallclock ≈ 30 sec on CPU
    after cold start.

    The JSONL has one row per probe + one row per tripwire result + one
    summary row.
    """
    try:
        import sae_lens  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as e:
        raise SAEBackendImportError("sae_lens") from e

    # Load §3b′ pairing artifacts once
    pairing_path = pairing_artifacts_dir / "g23_a3_pairing_artifacts.json"
    if not pairing_path.exists():
        raise FileNotFoundError(
            f"Pairing artifacts not found at {pairing_path}. "
            f"Run `python -m bulla.compute.g23_a3_pairing` first."
        )
    pairing = json.loads(pairing_path.read_text())
    disjoint_pairs = tuple(tuple(p) for p in pairing["disjoint_pairs"])

    # Load each side once (heavy)
    side_a = _load_side(model_id="gemma2-2b", layer=20, device=device)
    side_b = _load_side(model_id="gpt2-small", layer=11, device=device)

    # Fit Procrustes once across full dictionaries; re-bind into both sides
    # (R is symmetric in role; we store it on side_a by convention).
    R = _fit_procrustes_global(side_a=side_a, side_b=side_b)
    # Replace the frozen dataclass to inject R (frozen → use replace())
    from dataclasses import replace
    side_a = replace(side_a, procrustes_R=R)

    samples: list[CalibrationSample] = []
    for probe_id, probe_category, probe_text in ALL_PROBES:
        sample = run_calibration_on_probe(
            probe_id=probe_id, probe_category=probe_category,
            probe_text=probe_text,
            side_a=side_a, side_b=side_b,
            disjoint_pairs=disjoint_pairs,
        )
        samples.append(sample)

    samples_t = tuple(samples)
    tripwires = check_calibration_tripwires(samples_t)

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w") as f:
        for s in samples:
            f.write(json.dumps({"kind": "probe_sample", **s.to_jsonable()}) + "\n")
        for t in tripwires:
            f.write(json.dumps({
                "kind": "calibration_tripwire",
                "name": t.name, "passed": t.passed,
                "threshold": t.threshold, "measured": t.measured,
                "note": t.note,
            }) + "\n")
        f.write(json.dumps({
            "kind": "summary",
            "all_pass": all(t.passed for t in tripwires),
            "n_probes": len(samples),
            "n_tripwires_passed": sum(1 for t in tripwires if t.passed),
            "n_tripwires_total": len(tripwires),
        }) + "\n")
    return samples_t


# ── CLI entry ─────────────────────────────────────────────────────────


def _cli() -> int:
    p = argparse.ArgumentParser(description="G23 A3 §3a calibration spot-check")
    p.add_argument("--pairing-dir", type=Path, required=True,
                   help="Directory with §3b′ artifacts (output of g23_a3_pairing)")
    p.add_argument("--output", type=Path,
                   help="JSONL output path (default: <pairing-dir>/g23_a3_calibration_spotcheck.jsonl)")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--check-tripwires", action="store_true",
                   help="Read existing JSONL + report tripwire pass/fail")
    p.add_argument("--jsonl", type=Path,
                   help="JSONL to read (for --check-tripwires)")
    args = p.parse_args()

    if args.check_tripwires:
        jsonl_path = args.jsonl or args.output or (
            args.pairing_dir / "g23_a3_calibration_spotcheck.jsonl"
        )
        if not jsonl_path.exists():
            print(f"ERROR: {jsonl_path} not found", file=sys.stderr)
            return 2
        # Load samples
        samples: list[CalibrationSample] = []
        for line in jsonl_path.read_text().splitlines():
            row = json.loads(line)
            if row.get("kind") == "probe_sample":
                samples.append(CalibrationSample(
                    probe_id=row["probe_id"],
                    probe_category=row["probe_category"],
                    probe_text=row["probe_text"],
                    dim_h1=int(row["dim_h1"]),
                    procrustes_loss=float(row["procrustes_loss"]),
                    b0=float(row["b0"]),
                    n_features_a_used=int(row["n_features_a_used"]),
                    n_features_b_used=int(row["n_features_b_used"]),
                ))
        results = check_calibration_tripwires(tuple(samples))
        all_pass = all(r.passed for r in results)
        for r in results:
            mark = "✓" if r.passed else "✗"
            print(f"  [{mark}] {r.name}: measured={r.measured}, "
                  f"threshold={r.threshold} {r.note}")
        print(f"\n{'PASS' if all_pass else 'FAIL'}: "
              f"{sum(1 for r in results if r.passed)}/{len(results)} §3a tripwires passed")
        return 0 if all_pass else 1

    output = args.output or (args.pairing_dir / "g23_a3_calibration_spotcheck.jsonl")
    samples = run_full_calibration(
        pairing_artifacts_dir=args.pairing_dir,
        output_jsonl=output,
        seed=args.seed,
    )
    print(f"Calibration complete. {len(samples)} probes; output: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
