"""Representation-holonomy gate — Stage 1b/2 (the crux experiment).

Pre-registration: `papers/coherence-cliff/holonomy_pre_registration.md` (+ the
2026-06-18 §10 amendment: the primary label is grounded decision dispersion vs gold,
supplied by `grounded_decision_oracle.py`). This script is the *predictor + baseline +
decision-rule* half; it imports the label from the oracle, never the reverse.

ITEM. One item is a **concept-loop**: a probe concept `c` × a cycle of ≥3 independently
trained model representations `(a → b → c_model → a)`. Edge maps `R_e` are the orthogonal
Procrustes alignments fit on a held-out **anchor** concept set (never `c`, to avoid
leakage). The representations are made cross-model-comparable via **relative
representations** (each concept = its cosine profile to the anchor set, an
`n_anchor`-dim vector), so Procrustes is well-defined even across different `d_model`.

PREDICTOR (per item): the concept-transport deviation `‖H x_c − x_c‖`, `H` = the loop
product — how far `c`'s meaning fails to return after going around the model loop. This
is concept-specific, matching the per-concept-loop label.

LABEL (per item): `grounded_decision_oracle.loop_dispersion_label` — fraction of the
loop's models that get `c`'s grounded gold decision wrong. Set-based, gold-anchored, no
loop-closure (see the amendment).

STEELMANNED BASELINES (per item): max/sum single-edge transport residual (the
"a local check would have caught it" battery), mean/max pairwise embedding distance of
`c` across the loop (the Platonic-convergence baseline), open-path A→B→C residual (the
edge-structure twin), and β₁/cycle count (topology only). Holonomy must beat the BEST.

PRIMARY STATISTIC: conditional `ΔAUC = AUC(predictor) − max_k AUC(baseline_k)` on
**decoupled strata** (within-stratum |corr(best baseline, predictor)| < 0.2), with a
selection-safe paired bootstrap CI. Decision rule per pre-reg §6.

SPLIT. Representation extraction (`extract_relative_reps`, lazy torch/sae_lens) runs on
Colab/GPU. The analysis + stats + decision half runs locally; `--smoke` validates it
end-to-end on synthetic reps with planted frustration (validates infrastructure, makes
no real claim — the analogue of the synthetic negative control).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[3]
sys.path.insert(0, str(_ROOT / "bulla" / "src"))
sys.path.insert(0, str(_HERE.parent))  # for grounded_decision_oracle (same dir)

from bulla.adapters.holonomy import (  # noqa: E402  (predictor side — allowed here, NOT in the oracle)
    compose_loop,
    procrustes_rotation,
    scramble_orthogonal,
)

SEED = 2026
DECOUPLE_THRESH = 0.2
N_STRATA = 4
N_BOOT = 2000
OUT_DIR = _ROOT / "papers" / "coherence-cliff" / "results"


# ----------------------------- statistics (self-contained) -----------------------------

def _avg_ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), float)
    sx = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sx[j + 1] == sx[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank-based AUC (ties = ½). 0.5 if a class is absent."""
    labels = np.asarray(labels)
    n_pos = int(labels.sum())
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    r = _avg_ranks(np.asarray(scores, float))
    return (r[labels == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if a.std() < 1e-12 or b.std() < 1e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def conditional_auc_gap(
    predictor: np.ndarray,
    baselines: dict[str, np.ndarray],
    y: np.ndarray,
    rng: np.random.Generator,
    *,
    n_boot: int = N_BOOT,
    decouple_thresh: float = DECOUPLE_THRESH,
    n_strata: int = N_STRATA,
) -> dict:
    """ΔAUC(predictor − best baseline) on decoupled strata, selection-safe bootstrap.

    Decoupling: stratify by the full-sample best baseline into `n_strata` quantile bins,
    keep bins whose within-bin |corr(baseline, predictor)| < `decouple_thresh`, pool them.
    On every bootstrap resample the argmax baseline is RE-selected before differencing,
    so the gap is not inflated by selecting the winner post-hoc.
    """
    predictor = np.asarray(predictor, float)
    y = np.asarray(y)
    names = list(baselines)
    B = {k: np.asarray(v, float) for k, v in baselines.items()}
    full_aucs = {k: auc(B[k], y) for k in names}
    best = max(full_aucs, key=full_aucs.get)
    b = B[best]

    order = np.argsort(b, kind="mergesort")
    keep: list[int] = []
    for s in np.array_split(order, n_strata):
        if len(s) < 3:
            continue
        if abs(_pearson(b[s], predictor[s])) < decouple_thresh:
            keep.extend(int(i) for i in s)
    keep = np.array(sorted(keep))
    result = {
        "best_baseline": best,
        "full_sample_aucs": {k: round(full_aucs[k], 4) for k in names},
        "n_total": int(len(y)),
        "n_decoupled": int(len(keep)),
    }
    if len(keep) < 10 or y[keep].min() == y[keep].max():
        result.update(decoupled_predictor_auc=None, delta_auc=None, ci95=[None, None],
                      insufficient_decoupled=True)
        return result

    pk, yk = predictor[keep], y[keep]
    Bk = {k: B[k][keep] for k in names}
    point = auc(pk, yk) - max(auc(Bk[k], yk) for k in names)
    gaps = []
    n = len(keep)
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        yy = yk[idx]
        if yy.min() == yy.max():
            continue
        gaps.append(auc(pk[idx], yy) - max(auc(Bk[k][idx], yy) for k in names))
    lo, hi = (float(np.percentile(gaps, 2.5)), float(np.percentile(gaps, 97.5))) if gaps else (None, None)
    result.update(
        decoupled_predictor_auc=round(float(auc(pk, yk)), 4),
        delta_auc=round(float(point), 4),
        ci95=[None if lo is None else round(lo, 4), None if hi is None else round(hi, 4)],
        insufficient_decoupled=False,
    )
    return result


def verdict_from_gap(gap: dict, base_rate: float) -> tuple[str, str]:
    """Pre-reg §6 decision rule against the conditional ΔAUC."""
    if not (0.2 <= base_rate <= 0.8):
        return "VOID", f"grounded base rate {base_rate:.3f} ∉ [0.2, 0.8] — corpus degenerate, regenerate"
    if gap.get("insufficient_decoupled"):
        return "OUTCOME_4_BOUNDED", (
            f"only {gap['n_decoupled']} decoupled items — models too convergent to "
            f"decouple holonomy from edge residual (Stage-0 cell floor unmet)")
    lo, hi = gap["ci95"]
    if lo is not None and lo >= 0.10:
        return "OUTCOME_1_VALIDATED", f"ΔAUC 95% CI lower {lo:+.3f} ≥ +0.10 on decoupled strata"
    if hi is not None and hi <= 0.05:
        return "OUTCOME_2_FALSIFIED", (
            f"ΔAUC 95% CI upper {hi:+.3f} ≤ +0.05 — a local baseline ({gap['best_baseline']}) "
            f"ties/beats holonomy even decoupled; representation-layer claim collapses")
    return "UNDERPOWERED", f"ΔAUC CI {gap['ci95']} straddles — raise N (more concepts × model-triples)"


# ----------------------------- representations & loops -----------------------------

def _cos(u: np.ndarray, v: np.ndarray) -> float:
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    return 0.0 if nu < 1e-12 or nv < 1e-12 else float(u @ v / (nu * nv))


def relative_reps(
    raw: dict[tuple[str, str], np.ndarray], models: list[str], concepts: list[str], anchors: list[str]
) -> dict[str, np.ndarray]:
    """Relative representation: rel[model] is (n_concepts, n_anchors), row c = cosine
    profile of `c`'s raw rep to the anchor concepts IN THAT MODEL'S OWN SPACE. Makes
    different-d_model models comparable in a shared n_anchors space (Moschella et al.)."""
    cidx = {c: i for i, c in enumerate(concepts)}
    out = {}
    for m in models:
        R = np.zeros((len(concepts), len(anchors)))
        for c in concepts:
            for j, a in enumerate(anchors):
                R[cidx[c], j] = _cos(raw[(m, c)], raw[(m, a)])
        out[m] = R
    return out


def fit_edge(rel_i: np.ndarray, rel_j: np.ndarray, anchor_rows: np.ndarray) -> np.ndarray:
    """Orthogonal Procrustes from model i's frame to model j's, fit on anchor rows only.
    Returns (n_anchors, n_anchors). Reuses the instrument's numpy procrustes."""
    return procrustes_rotation(rel_i[anchor_rows], rel_j[anchor_rows])


def scores_from_edges(edges: list[np.ndarray], x_c: np.ndarray, embed_vecs: list[np.ndarray]) -> dict:
    """Predictor + steelmanned baselines for one concept-loop, given the edge maps,
    the concept vector `x_c` (in the loop's base frame), and the concept's per-model
    representations `embed_vecs`. The predictor is loop-CLOSURE (transport deviation);
    the baselines are deliberately closure-blind (per-edge, embedding, open-path)."""
    K = len(edges)
    x = np.asarray(x_c, float)
    nx = np.linalg.norm(x) + 1e-12
    H = compose_loop(edges)
    edge_res, xt = [], x.copy()
    for R in edges:
        nxt = np.linalg.norm(xt) + 1e-12
        edge_res.append(float(np.linalg.norm(R @ xt - xt) / nxt))
        xt = R @ xt
    H_open = compose_loop(edges[:-1]) if K > 2 else edges[0]
    emb = np.asarray(embed_vecs, float)
    pdist = [1.0 - _cos(emb[a], emb[b]) for a in range(len(emb)) for b in range(a + 1, len(emb))]
    return {
        "predictor": float(np.linalg.norm(H @ x - x) / nx),     # concept-transport deviation (closure)
        "max_edge_residual": float(max(edge_res)),
        "sum_edge_residual": float(sum(edge_res)),
        "mean_embed_dist": float(np.mean(pdist)) if pdist else 0.0,
        "max_embed_dist": float(max(pdist)) if pdist else 0.0,
        "open_path_residual": float(np.linalg.norm(H_open @ x - x) / nx),  # no return = closure-blind twin
        "cycle_count": float(K),                                  # β₁ of a single cycle; topology-only control
        "loop_holonomy_frob": float(np.linalg.norm(H - np.eye(H.shape[0]))),  # secondary (concept-independent)
    }


def item_scores(
    rel: dict[str, np.ndarray], loop: list[str], c_row: int, anchor_rows: np.ndarray
) -> dict:
    """One concept-loop on REAL relative reps: fit edges via Procrustes on anchors
    (never `c`), then score. `loop` = ordered model cycle."""
    K = len(loop)
    edges = [fit_edge(rel[loop[k]], rel[loop[(k + 1) % K]], anchor_rows) for k in range(K)]
    embed_vecs = [rel[m][c_row] for m in loop]
    return scores_from_edges(edges, rel[loop[0]][c_row], embed_vecs)


BASELINE_KEYS = (
    "max_edge_residual", "sum_edge_residual", "mean_embed_dist",
    "max_embed_dist", "open_path_residual", "cycle_count",
)


def run_analysis(items: list[dict], labels_binary: np.ndarray, base_rate: float, *,
                 seed: int = SEED, scramble_control: list[dict] | None = None) -> dict:
    """The locally-runnable half: predictor vs steelmanned baselines → conditional
    ΔAUC → verdict. Optionally checks the scramble control is null on the same items."""
    rng = np.random.default_rng(seed)
    pred = np.array([it["predictor"] for it in items])
    baselines = {k: np.array([it[k] for it in items]) for k in BASELINE_KEYS}
    gap = conditional_auc_gap(pred, baselines, labels_binary, rng)
    verdict, reason = verdict_from_gap(gap, base_rate)
    out = {"base_rate": round(base_rate, 4), "gap": gap, "VERDICT": verdict, "reason": reason}
    if scramble_control is not None:
        sc = np.array([it["predictor"] for it in scramble_control])
        out["scramble_control_auc"] = round(float(auc(sc, labels_binary)), 4)
    return out


# ----------------------------- Colab-gated extraction -----------------------------

def extract_relative_reps(*, model_layers, concepts, anchors, device="cuda", top_k=50):  # pragma: no cover
    """MODEL-GATED (Colab/GPU). Build cross-model-comparable relative reps.

    `model_layers` = list of (model_id, layer) tuples (e.g. a filtered `supported_models()`)
    — the registry uses PER-MODEL layers (gemma@20, llama@24, mistral@24), so a single
    shared layer would not resolve. Within a model, every concept is represented over the
    SAME fixed feature basis (the union of its top-K SAE features across all concepts), so
    the per-concept cosine profiles `relative_reps` builds are well-defined (SAE widths
    differ across models — 16k/32k/65k — but relative reps live in the shared n_anchor
    space, so that is fine). Lazy-imports the heavy backend; the module stays locally
    importable for --smoke."""
    from bulla.adapters.sae_lens_backend import _load_sae_model_tokenizer, _run_probe_inference

    cids = [c.concept_id for c in concepts]
    aids = [a.concept_id for a in anchors]
    raw: dict[tuple[str, str], np.ndarray] = {}
    for m, layer in model_layers:
        sae, model, tok = _load_sae_model_tokenizer(model_id=m, layer=layer, device=device)
        topks: dict[str, dict[int, float]] = {}
        basis: set[int] = set()
        for c in concepts:
            tk = _run_probe_inference(sae=sae, model=model, tokenizer=tok, layer=layer,
                                      probe_text=c.prompt, top_k=top_k)
            topks[c.concept_id] = dict(tk)
            basis.update(fid for fid, _ in tk)
        bidx = {f: i for i, f in enumerate(sorted(basis))}
        for c in concepts:
            v = np.zeros(len(bidx))
            for fid, act in topks[c.concept_id].items():
                v[bidx[fid]] = act
            raw[(m, c.concept_id)] = v
    return relative_reps(raw, [m for m, _ in model_layers], cids, aids)


# ----------------------------- synthetic smoke (local) -----------------------------

def _smoke(seed: int = SEED) -> int:
    """Synthetic positive control for the ANALYSIS half (no models). Per item, plant
    frustration in the edge-map CLOSURE (mirroring holonomy_negative_control.build_loop):
    coherent loops close (H≈I), frustrated loops do not — while the concept vector and
    its per-model embeddings are held fixed across frustrated/coherent, so embedding
    distance and per-edge residual are CLOSURE-BLIND by construction. Acceptance:
      (1) predictor recovers planted frustration (AUC ≥ 0.90),
      (2) the scramble twin and the open-path baseline are null (AUC ≈ 0.5),
      (3) conditional ΔAUC + the decision rule run end-to-end and VALIDATE on synthetic.
    This validates infrastructure only; it makes no claim about real representations."""
    rng = np.random.default_rng(seed)
    from bulla.adapters.holonomy import random_orthogonal

    d, n = 16, 600
    items, scram, labels = [], [], []
    for k in range(n):
        frustrated = k % 2 == 1
        r12 = random_orthogonal(d, rng)
        r23 = random_orthogonal(d, rng)
        closes = np.linalg.inv(r23 @ r12)               # the edge that would close the loop
        r31 = random_orthogonal(d, rng) if frustrated else closes
        edges = [r12, r23, r31]
        x = rng.standard_normal(d)                       # concept vector (intrinsic, frustration-free)
        embed_vecs = [x + 0.05 * rng.standard_normal(d) for _ in edges]  # fixed across frustration
        items.append(scores_from_edges(edges, x, embed_vecs))
        scram.append(scores_from_edges([scramble_orthogonal(e, rng) for e in edges], x, embed_vecs))
        labels.append(1 if frustrated else 0)
    labels = np.array(labels)
    base_rate = float(labels.mean())

    pos_auc = auc(np.array([it["predictor"] for it in items]), labels)
    scr_auc = auc(np.array([it["predictor"] for it in scram]), labels)
    open_auc = auc(np.array([it["open_path_residual"] for it in items]), labels)
    res = run_analysis(items, labels, base_rate, seed=seed, scramble_control=scram)

    checks = {
        "predictor_recovers_frustration (AUC>=0.90)": bool(pos_auc >= 0.90),
        "scramble_null (|AUC-0.5|<=0.07)": bool(abs(scr_auc - 0.5) <= 0.07),
        "open_path_null (|AUC-0.5|<=0.07)": bool(abs(open_auc - 0.5) <= 0.07),
        "pipeline_validates_on_synthetic": bool(res["VERDICT"] == "OUTCOME_1_VALIDATED"),
    }
    ok = all(checks.values())
    print(json.dumps({
        "smoke": "representation_gate",
        "positive_control_predictor_auc": round(float(pos_auc), 4),
        "scramble_twin_auc": round(float(scr_auc), 4),
        "open_path_auc": round(float(open_auc), 4),
        "base_rate": round(base_rate, 4),
        "checks": checks,
        "analysis": res,
        "PASS": ok,
    }, indent=2))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true", help="local synthetic infra check (no models)")
    args = ap.parse_args()
    if args.smoke:
        return _smoke()
    print("Real Stage-1b/2 runs are Colab-gated; see "
          "papers/coherence-cliff/REPRESENTATION_GATE_COLAB_RUNBOOK.md. Use --smoke locally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
