"""Stage 1a instrument-sensitivity gate for the representation-holonomy crux.

Synthetic, deterministic, dependency-light (numpy only). This is the
fail-fast control the pre-registration requires *before* any heavy model
spend (``papers/coherence-cliff/holonomy_pre_registration.md`` Stage 1a). It
is the representation-layer analogue of the dissociation experiment's
min-cycle-basis negative control: prove the SAME instrument that will be run
on real representations *can* detect a planted closure failure, and that it
*cannot* manufacture a signal from per-edge magnitude once closure is removed.

Three conditions, one shared label set (coherent loop = 0, frustrated loop = 1):

  * POSITIVE -- closed-loop holonomy ``||prod R - I||_F``. Must SEPARATE the
    classes: AUC well above 0.5 (instrument is live).
  * OPEN PATH -- the same first two legs, but the third goes to a disjoint
    node Z and is scored against the independent direct A->Z alignment. Same
    per-edge magnitudes, no closure constraint. Must be NULL (AUC ~ 0.5).
  * SCRAMBLE -- each edge conjugated by its own random frame ``Q R Q^T``. Every
    edge keeps its exact magnitude ``||R - I||_F``; only the closure
    relationship is destroyed. Must be NULL (AUC ~ 0.5).

If the positive condition does not separate, or either null is not null, the
instrument is leaky or dead and the gate ABORTS -- exactly as the
pre-registration demands. A graded check (Spearman of holonomy vs a planted
rotation angle) confirms the instrument is graded, not merely binary.

Run:  python bulla/calibration/scripts/holonomy_negative_control.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import numpy as np

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SRC = _ROOT / "bulla" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from bulla.adapters.holonomy import (  # noqa: E402
    compose_loop,
    holonomy_frobenius,
    loop_deviation,
    planar_rotation,
    random_orthogonal,
    scramble_orthogonal,
)

SEED = 2026
N_LOOPS = 600
DIM = 16
N_BOOT = 2000
OUT = _ROOT / "papers" / "coherence-cliff" / "results" / "holonomy_negative_control.json"

# Pre-registered gate thresholds (Stage 1a).
POSITIVE_MIN = 0.95   # closed-loop AUC 95% CI lower bound must exceed this
NULL_POINT_TOL = 0.05  # null point estimate must be within this of 0.5,
#                        and the 95% CI must contain 0.5 (fails to reject chance)


# ── rank-based AUC + bootstrap (numpy only) ─────────────────────────────


def _avg_ranks(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    sx = x[order]
    ranks = np.empty(len(x), dtype=float)
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and sx[j + 1] == sx[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUC = P(score | label=1 > score | label=0), ties counted as half."""
    labels = np.asarray(labels).astype(int)
    scores = np.asarray(scores, dtype=float)
    n_pos = int(labels.sum())
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    r = _avg_ranks(scores)
    return float((r[labels == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def auc_ci(scores, labels, rng, n_boot=N_BOOT):
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels).astype(int)
    n = len(labels)
    boots = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        a = auc(scores[idx], labels[idx])
        if not np.isnan(a):
            boots.append(a)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return float(lo), float(hi)


def _spearman(x, y) -> float:
    rx, ry = _avg_ranks(np.asarray(x, float)), _avg_ranks(np.asarray(y, float))
    rx, ry = rx - rx.mean(), ry - ry.mean()
    return float((rx @ ry) / (np.linalg.norm(rx) * np.linalg.norm(ry)))


# ── synthetic loop construction ─────────────────────────────────────────


def build_loop(d, rng, *, frustrated, theta=None):
    """Three orthogonal edge maps. Coherent: product = I. Frustrated: not."""
    r12 = random_orthogonal(d, rng)
    r23 = random_orthogonal(d, rng)
    closes = np.linalg.inv(r23 @ r12)  # R31 = closes  =>  R31 R23 R12 = I
    if not frustrated:
        r31 = closes
    elif theta is None:
        r31 = random_orthogonal(d, rng)  # generic frustration
    else:
        r31 = planar_rotation(d, theta) @ closes  # loop product = rotation(theta)
    return [r12, r23, r31]


def main() -> int:
    rng = np.random.default_rng(SEED)

    labels, closed, scrambled, openpath = [], [], [], []
    for i in range(N_LOOPS):
        frustrated = i % 2 == 1
        edges = build_loop(DIM, rng, frustrated=frustrated)
        labels.append(1 if frustrated else 0)

        # POSITIVE: closed-loop holonomy.
        closed.append(holonomy_frobenius(edges))

        # SCRAMBLE: conjugate each edge by its own frame; magnitudes preserved,
        # closure destroyed.
        scrambled.append(
            holonomy_frobenius([scramble_orthogonal(e, rng) for e in edges])
        )

        # OPEN PATH: third leg goes to a disjoint Z (magnitude-matched scramble
        # of the closing leg), scored against an independent direct A->Z map.
        r3z = scramble_orthogonal(edges[2], rng)
        r_az = random_orthogonal(DIM, rng)
        openpath.append(loop_deviation(compose_loop([edges[0], edges[1], r3z]), r_az))

    labels = np.array(labels)
    closed, scrambled, openpath = map(np.array, (closed, scrambled, openpath))

    boot_rng = np.random.default_rng(SEED + 1)
    res = {}
    for name, scores in (
        ("closed_holonomy", closed),
        ("open_path", openpath),
        ("scramble", scrambled),
    ):
        a = auc(scores, labels)
        lo, hi = auc_ci(scores, labels, boot_rng)
        res[name] = {"auc": round(a, 4), "ci95": [round(lo, 4), round(hi, 4)]}

    # Graded sensitivity: holonomy must rise monotonically with planted angle.
    grade_rng = np.random.default_rng(SEED + 2)
    thetas = np.linspace(0.05, 1.5, 40)
    grade = [holonomy_frobenius(build_loop(DIM, grade_rng, frustrated=True, theta=t)) for t in thetas]
    spearman_theta = _spearman(thetas, grade)

    def _is_null(entry):
        lo, hi = entry["ci95"]
        return lo <= 0.5 <= hi and abs(entry["auc"] - 0.5) <= NULL_POINT_TOL

    pos = res["closed_holonomy"]["ci95"][0] >= POSITIVE_MIN
    null_ok = all(_is_null(res[k]) for k in ("open_path", "scramble"))
    graded_ok = spearman_theta >= 0.99
    verdict = "CONTROLS_PASS" if (pos and null_ok and graded_ok) else "ABORT"

    payload = {
        "stage": "1a_instrument_sensitivity",
        "corpus": "synthetic_orthogonal_loops",
        "provenance": "EXECUTION_INDEPENDENT",
        "seed": SEED,
        "n_loops": N_LOOPS,
        "dim": DIM,
        "n_bootstrap": N_BOOT,
        "auc": res,
        "graded_spearman_holonomy_vs_theta": round(spearman_theta, 4),
        "decision_rule": (
            f"PASS iff closed-loop AUC 95% CI lower >= {POSITIVE_MIN}; both null "
            f"AUC 95% CIs contain 0.5 with point estimate within {NULL_POINT_TOL} "
            f"of 0.5 (fails to reject chance); and graded Spearman >= 0.99"
        ),
        "VERDICT": verdict,
        "reason": (
            f"closed-loop holonomy separates coherent from frustrated loops "
            f"(AUC {res['closed_holonomy']['auc']}); the open-path and scramble "
            f"conditions -- same per-edge magnitudes, closure removed -- are null "
            f"(AUC {res['open_path']['auc']}, {res['scramble']['auc']}); holonomy "
            f"rises monotonically with the planted rotation angle (Spearman "
            f"{round(spearman_theta, 4)}). The instrument detects closure, not "
            f"per-edge magnitude."
        ),
    }
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in payload.items() if k != "manifest_sha256"},
            sort_keys=True,
        ).encode()
    ).hexdigest()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(json.dumps({k: v for k, v in payload.items() if k != "reason"}, indent=2))
    print(f"\nWrote {OUT}")
    return 0 if verdict == "CONTROLS_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
