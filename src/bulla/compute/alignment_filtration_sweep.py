"""Alignment-quality filtration sweep for G23 cross-model compositions.

Shared-pair design: both restriction maps (Procrustes, Neuronpedia) are
evaluated on the SAME locked §3b' pairs, producing compositions with
identical topology. The only variable is the quality metric:

  - Procrustes: quality = cosine(D_a[a_i] @ R, D_b[b_i]) after global
    decoder SVD rotation. Non-circular — these pairs were NOT selected
    by decoder geometry.
  - Neuronpedia: quality = cosine(emb_gemma[a_i], emb_gpt2[b_i]) from
    label embeddings. Circular by design — these pairs WERE selected by
    maximizing this metric, so quality ≈ 1.0. The instant death under
    filtration reflects that the Neuronpedia map is maximally resolving
    on its own pairs.

The bottleneck distance measures how differently the two maps evaluate
the same cross-model edges, isolating the quality-metric effect from
topology and pairing effects.

# Stability bound

The bottleneck stability theorem guarantees:
  d_B(dgm_proc, dgm_neuro) ≤ ||q_proc - q_neuro||_∞
where the RHS is the max per-edge quality difference. The tightness
ratio d_B / ||Δq||_∞ measures how much of the maximum possible
separation the maps realize.

# Edge quality assignment for 4-cycle topology

Each 2-pair group in C2-C5 forms a 4-cycle with alternating forward
(a_i → b_i) and backward (b_i → a_next) edges. Quality assignment:
  - Forward edge: quality = alignment cosine of the pair (a_i, b_i)
  - Backward edge: quality = min(adjacent pair qualities)
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Sequence

from bulla.compute.a3 import (
    COMPOSITION_SPECS,
    _build_composition_for_leaf,
)
from bulla.persistent import (
    bottleneck_distance,
    compute_alignment_barcode,
    compute_alignment_h1,
    Bar,
)


# ── Result dataclass ──────────────────────────────────────────────────


@dataclass(frozen=True)
class AlignmentFiltrationResult:
    """Per-composition comparison of alignment barcodes between maps.

    Shared-pair design: both maps evaluated on the same locked pairs.
    """

    composition_id: str
    n_pairs: int
    n_edges: int
    # Per-map fee trajectories (serialized as JSON strings for CSV)
    procrustes_fee_trajectory: str  # JSON: list of [eps, fee]
    neuronpedia_fee_trajectory: str
    # Per-map barcode summaries
    procrustes_n_bars: int
    neuronpedia_n_bars: int
    procrustes_total_persistence: float
    neuronpedia_total_persistence: float
    # The key measurement
    bottleneck_distance: float
    feature_sensitive: bool  # bottleneck_distance > 0
    # Stability bound: d_B ≤ ||q_proc - q_neuro||_∞
    stability_bound: float   # max |q_proc(e) - q_neuro(e)| over all edges
    tightness_ratio: float   # bottleneck_distance / stability_bound


# ── Per-pair quality extraction ──────────────────────────────────────


def procrustes_per_pair_cosines(
    *,
    decoder_a,  # torch.Tensor (n_a, d_model_a)
    decoder_b,  # torch.Tensor (n_b, d_model_b)
    pairs: Sequence[tuple[int, int]],
) -> list[float]:
    """Compute per-pair cosine similarity under Procrustes alignment.

    Fits R = argmin ||D_a R - D_b||_F via SVD on D_a^T D_b (truncated
    to d_min = min(d_model_a, d_model_b)), then for each pair (a_i, b_i):
    quality_i = cosine(D_a[a_i] @ R, D_b[b_i]).

    Returns a list of cosines in the same order as `pairs`, each in [-1, 1].
    """
    import torch

    D_a = decoder_a.float()
    D_b = decoder_b.float()
    d_min = min(D_a.shape[1], D_b.shape[1])
    D_a = D_a[:, :d_min]
    D_b = D_b[:, :d_min]
    n = min(D_a.shape[0], D_b.shape[0])

    # Procrustes rotation
    M = D_a[:n].T @ D_b[:n]
    U, _S, Vt = torch.linalg.svd(M, full_matrices=False)
    R = U @ Vt

    cosines: list[float] = []
    for a_id, b_id in pairs:
        d_src = D_a[a_id]
        d_tgt = D_b[b_id]
        projected = d_src @ R
        denom = float(
            torch.linalg.norm(projected) * torch.linalg.norm(d_tgt) + 1e-12
        )
        num = float((projected * d_tgt).sum())
        cosines.append(num / denom)
    return cosines


def neuronpedia_per_pair_cosines(
    *,
    emb_gemma,  # np.ndarray (n_gemma, emb_dim)
    emb_gpt2,   # np.ndarray (n_gpt2, emb_dim)
    pairs: Sequence[tuple[int, int]],
) -> list[float]:
    """Compute per-pair cosine similarity from label embeddings.

    For each pair (gemma_id, gpt2_id): quality = cosine(emb_gemma[gemma_id],
    emb_gpt2[gpt2_id]).

    Returns a list of cosines in the same order as `pairs`, each in [-1, 1].
    """
    import numpy as np

    cosines: list[float] = []
    for a_id, b_id in pairs:
        va = emb_gemma[a_id]
        vb = emb_gpt2[b_id]
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb) + 1e-12)
        cosines.append(float(np.dot(va, vb) / denom))
    return cosines


# ── Edge quality assignment for 4-cycle topology ──────────────────────


def assign_edge_qualities(
    pairs: Sequence[tuple[int, int]],
    per_pair_cosines: list[float],
) -> dict[int, float]:
    """Map per-pair alignment qualities to per-edge qualities in 4-cycle topology.

    Reproduces the edge construction from a3.py _build_composition_for_leaf:
    pairs are grouped into consecutive 2-pair groups, each forming a 4-cycle
    with alternating forward/backward edges.

    Forward edge a_i → b_i: quality = cosine of pair (a_i, b_i)
    Backward edge b_i → a_next: quality = min(cosine_i, cosine_{next})

    Returns dict[edge_index, quality] matching the composition's edge ordering.
    """
    edge_qualities: dict[int, float] = {}
    edge_idx = 0

    for g in range(0, len(pairs), 2):
        group_pairs = list(pairs[g:g + 2])
        group_cosines = list(per_pair_cosines[g:g + 2])
        if len(group_pairs) < 2:
            break
        k = len(group_pairs)

        for i in range(k):
            # Forward edge: quality = cosine of pair i
            # Clamp to [0, 1] — negative cosines treated as quality 0
            fwd_q = max(0.0, group_cosines[i])
            edge_qualities[edge_idx] = fwd_q
            edge_idx += 1

            # Backward edge: quality = min of adjacent pairs
            next_i = (i + 1) % k
            bwd_q = max(0.0, min(group_cosines[i], group_cosines[next_i]))
            edge_qualities[edge_idx] = bwd_q
            edge_idx += 1

    return edge_qualities


# ── Main sweep ────────────────────────────────────────────────────────


def run_alignment_filtration_sweep(
    *,
    output_csv: Path,
    pairing_artifacts_dir: Path,
    device: str = "cpu",
    eps_step: float = 0.05,
) -> list[AlignmentFiltrationResult]:
    """Run shared-pair alignment-quality filtration sweep on C2-C5.

    Shared-pair design: both maps are evaluated on the SAME locked §3b'
    pairs. One composition per composition size, two quality metrics.

    1. Loads SAE decoder matrices + Neuronpedia label embeddings.
    2. For each composition C2-C5 on shared pairs:
       a. Computes Procrustes decoder cosines (non-circular).
       b. Computes Neuronpedia label cosines (circular by design).
       c. Assigns edge qualities per map on the same composition.
       d. Computes alignment barcode per map.
       e. Compares via bottleneck distance + stability bound.
    3. Writes results to CSV.
    """
    import numpy as np
    import torch

    from bulla.adapters.sae_lens_backend import _load_sae_model_tokenizer

    # Load locked §3b' pairs (shared across both maps)
    artifacts_path = pairing_artifacts_dir / "g23_a3_pairing_artifacts.json"
    artifacts = json.loads(artifacts_path.read_text())
    locked_pairs: list[tuple[int, int]] = [
        (int(a), int(b)) for a, b in artifacts["disjoint_pairs"]
    ]

    # Load SAE decoder matrices
    print("Loading Gemma-2-2B SAE...", file=sys.stderr, flush=True)
    sae_a, _model_a, _tok_a = _load_sae_model_tokenizer(
        model_id="gemma2-2b", layer=20, device=device,
    )
    print("Loading GPT-2-Small SAE...", file=sys.stderr, flush=True)
    sae_b, _model_b, _tok_b = _load_sae_model_tokenizer(
        model_id="gpt2-small", layer=11, device=device,
    )
    decoder_a = sae_a.W_dec.detach().cpu()
    decoder_b = sae_b.W_dec.detach().cpu()
    del _model_a, _tok_a, _model_b, _tok_b, sae_a, sae_b

    # Load cached Neuronpedia label embeddings
    emb_gemma_path = pairing_artifacts_dir / "g23_a3_label_embeddings_gemma.npy"
    emb_gpt2_path = pairing_artifacts_dir / "g23_a3_label_embeddings_gpt2.npy"
    print("Loading label embeddings...", file=sys.stderr, flush=True)
    emb_gemma = np.load(emb_gemma_path)
    emb_gpt2 = np.load(emb_gpt2_path)

    from bulla.persistent import _eps_grid
    grid = _eps_grid(eps_step)

    results: list[AlignmentFiltrationResult] = []

    for cid in ("C2", "C3", "C4", "C5"):
        spec = COMPOSITION_SPECS[cid]
        n_target = spec["n_pairs"]
        print(f"\n=== {cid} ({n_target} pairs, shared-pair design) ===",
              file=sys.stderr, flush=True)

        # Shared pairs and shared composition
        shared_pairs = tuple(locked_pairs[:n_target])
        comp = _build_composition_for_leaf(
            composition_id=cid,
            pairs=shared_pairs,
            side_a_features=None,
            side_b_features=None,
        )

        # -- Procrustes quality on shared pairs (non-circular) --
        proc_cosines = procrustes_per_pair_cosines(
            decoder_a=decoder_a,
            decoder_b=decoder_b,
            pairs=shared_pairs,
        )
        proc_edge_q = assign_edge_qualities(shared_pairs, proc_cosines)
        proc_bars = compute_alignment_barcode(
            comp, proc_edge_q, eps_step=eps_step,
        )
        proc_trajectory = []
        for eps in grid:
            r = compute_alignment_h1(comp, proc_edge_q, eps)
            proc_trajectory.append([round(eps, 4), r.fee])

        # -- Neuronpedia quality on shared pairs (circular by design) --
        neuro_cosines = neuronpedia_per_pair_cosines(
            emb_gemma=emb_gemma,
            emb_gpt2=emb_gpt2,
            pairs=shared_pairs,
        )
        neuro_edge_q = assign_edge_qualities(shared_pairs, neuro_cosines)
        neuro_bars = compute_alignment_barcode(
            comp, neuro_edge_q, eps_step=eps_step,
        )
        neuro_trajectory = []
        for eps in grid:
            r = compute_alignment_h1(comp, neuro_edge_q, eps)
            neuro_trajectory.append([round(eps, 4), r.fee])

        # Bottleneck distance
        bn = bottleneck_distance(proc_bars, neuro_bars)

        # Stability bound: d_B ≤ max|q_proc(e) - q_neuro(e)|
        max_delta_q = max(
            abs(proc_edge_q[i] - neuro_edge_q[i])
            for i in range(len(comp.edges))
        )
        tightness = bn / max_delta_q if max_delta_q > 0 else 0.0

        # Summary stats
        proc_tp = sum(b.death_eps - b.birth_eps for b in proc_bars)
        neuro_tp = sum(b.death_eps - b.birth_eps for b in neuro_bars)

        result = AlignmentFiltrationResult(
            composition_id=cid,
            n_pairs=n_target,
            n_edges=len(comp.edges),
            procrustes_fee_trajectory=json.dumps(proc_trajectory),
            neuronpedia_fee_trajectory=json.dumps(neuro_trajectory),
            procrustes_n_bars=len(proc_bars),
            neuronpedia_n_bars=len(neuro_bars),
            procrustes_total_persistence=round(proc_tp, 4),
            neuronpedia_total_persistence=round(neuro_tp, 4),
            bottleneck_distance=round(bn, 6),
            feature_sensitive=(bn > 0),
            stability_bound=round(max_delta_q, 6),
            tightness_ratio=round(tightness, 6),
        )
        results.append(result)

        # Print intermediate results
        print(f"  Procrustes pair cosines: {[round(c,3) for c in proc_cosines]}", file=sys.stderr)
        print(f"  Neuronpedia pair cosines: {[round(c,3) for c in neuro_cosines]}", file=sys.stderr)
        print(f"  Procrustes bars: {len(proc_bars)}, total_persistence={proc_tp:.4f}", file=sys.stderr)
        print(f"  Neuronpedia bars: {len(neuro_bars)}, total_persistence={neuro_tp:.4f}", file=sys.stderr)
        print(f"  Bottleneck: {bn:.6f}, stability bound: {max_delta_q:.6f}, "
              f"tightness: {tightness:.4f}", file=sys.stderr)
        print(f"  Feature-sensitive: {bn > 0}", file=sys.stderr, flush=True)

    # Write CSV
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(results[0]).keys()) if results else []
    with output_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow(asdict(r))

    return results


# ── CLI ───────────────────────────────────────────────────────────────


def _cli() -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="G23 alignment-quality filtration sweep"
    )
    p.add_argument(
        "--pairing-dir", type=Path, required=True,
        help="Directory with §3b' pairing artifacts and label embeddings",
    )
    p.add_argument(
        "--output-csv", type=Path,
        help="Where to write the sweep CSV (default: pairing-dir/g23_alignment_filtration.csv)",
    )
    p.add_argument(
        "--eps-step", type=float, default=0.05,
        help="Epsilon step for the filtration sweep (default: 0.05 → 21 steps)",
    )
    args = p.parse_args()

    output = args.output_csv or (args.pairing_dir / "g23_alignment_filtration.csv")

    results = run_alignment_filtration_sweep(
        output_csv=output,
        pairing_artifacts_dir=args.pairing_dir,
        eps_step=args.eps_step,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("ALIGNMENT-QUALITY FILTRATION SWEEP RESULTS")
    print("=" * 60)
    for r in results:
        print(f"\n{r.composition_id} ({r.n_pairs} pairs, {r.n_edges} edges):")
        print(f"  Procrustes:  {r.procrustes_n_bars} bars, persistence={r.procrustes_total_persistence:.4f}")
        print(f"  Neuronpedia: {r.neuronpedia_n_bars} bars, persistence={r.neuronpedia_total_persistence:.4f}")
        print(f"  Bottleneck distance: {r.bottleneck_distance:.6f}")
        print(f"  Stability bound: {r.stability_bound:.6f}, tightness: {r.tightness_ratio:.4f}")
        print(f"  Feature-sensitive: {r.feature_sensitive}")

    any_sensitive = any(r.feature_sensitive for r in results)
    print(f"\nOverall: {'FEATURE-SENSITIVE' if any_sensitive else 'TOPOLOGY-DETERMINED'}")
    print(f"CSV written to: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
