#!/usr/bin/env python3
"""S1a — substrate-wiring + fee-constructibility check (NOT a calibration or deflation test).

A WARM-UP for the actuary question ("is the coherence fee a sound premium variable?") that
establishes the two PRECONDITIONS for the measured test (S1b) and nothing more. It reuses the
EXECUTION_INDEPENDENT git substrate of ``repair_closes_loop_git.py`` and sweeps the fee 0..K by
stacking K *distinct* hidden path-root conventions: a K-consumer star whose fee is verified == K.

ESTABLISHES (the only genuine empirical content):
  1. the EXECUTION_INDEPENDENT substrate is wired — real ``git show`` rejects the absolute
     convention and accepts the repo-relative one (measured once per file; git is deterministic);
  2. ``fee == k`` is constructible on demand — bulla's schema-only fee counts the conventions.

DOES NOT establish a calibration or a deflation. Breach here is a per-crossing **Bernoulli
emission model** (``rng.random() < P_ABSOLUTE``), so the breach-rate curve is exactly the
arithmetic ``1-(1-p)^k`` (printed alongside; it matches) and **cannot fail for any p>0**. A test
whose negative outcome is unreachable is not a test (the program's own bessel_saturation lesson).
The genuine measured calibration — breach from real value propagation, with a control that *can*
return no-breach on a high-fee composition — is S1b.

Nor can it distinguish fee from the cheap baseline even in principle: on this single-dimension
substrate the schema fee EQUALS the schema convention-distance (Hamming) exactly (corr = 1 by
construction). The decoupling needs the multi-dimension quantized oracle of S1b
(``papers/coherence-cliff/results/convention_distance_collapse.md:13-16``).

Label provenance: EXECUTION_INDEPENDENT (git's own exit codes, authored by neither us nor the fee;
measured once per (file, convention) — git is deterministic). The population samples the EMISSION
convention only. Deterministic (seed 2026). Read-only on the repo; writes one results JSON.
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

SEED = 2026
MAX_FEE = 6
INSTANCES_PER_FEE = 400
P_ABSOLUTE = 0.3   # P(a producer emits the absolute, breaching path convention at runtime)


def _repo_root() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


REPO = _repo_root()


def git_show_ok(pathspec: str) -> bool:
    """REAL execution: ``git show HEAD:<pathspec>`` exits 0 iff the object exists under that
    pathspec in HEAD. The label is git's own exit code — EXECUTION_INDEPENDENT of the fee."""
    return subprocess.run(
        ["git", "-C", REPO, "show", f"HEAD:{pathspec}"],
        capture_output=True, text=True,
    ).returncode == 0


def star(k: int) -> Composition:
    """One filesystem producer feeding k git consumers, each across a DISTINCT hidden
    path-root convention. Verified fee == k (schema-only, deterministic)."""
    dims = [f"path_root_{i}" for i in range(k)]
    tools = [ToolSpec("filesystem", tuple(dims), ())]
    edges = []
    for i, d in enumerate(dims):
        tools.append(ToolSpec(f"git{i}", (d,), ()))
        edges.append(Edge("filesystem", f"git{i}", (SemanticDimension(d, d, d),)))
    return Composition(f"star_{k}", tuple(tools), tuple(edges))


def main() -> int:
    rng = random.Random(SEED)
    tracked = subprocess.run(
        ["git", "-C", REPO, "ls-files"], capture_output=True, text=True, check=True,
    ).stdout.split()
    rels = [p for p in tracked if p.endswith(".md")][:MAX_FEE]
    if len(rels) < MAX_FEE:
        print(f"INVALID CONTROL: need >= {MAX_FEE} tracked .md files, found {len(rels)}")
        return 2

    # Substrate gate (verify-before-record): the EXECUTION_INDEPENDENT label is only
    # meaningful if REAL git rejects the absolute convention and accepts the repo-relative
    # one, for every file. git is deterministic, so each label is measured exactly once.
    for r in rels:
        ok_rel = git_show_ok(r)
        ok_abs = git_show_ok(str(Path(REPO) / r))
        if not ok_rel or ok_abs:
            print(f"INVALID CONTROL: git did not reject abs / accept rel for {r} "
                  f"(ok_rel={ok_rel}, ok_abs={ok_abs})")
            return 2
    print(f"substrate gate: real git rejects ABS and accepts REL for all {len(rels)} files "
          f"(EXECUTION_INDEPENDENT label confirmed)")

    rows = []
    for k in range(0, MAX_FEE + 1):
        comp = star(k)
        fee = diagnose(comp).coherence_fee
        if fee != k:                       # verify-before-record: fee must equal intended k
            print(f"INVALID CONTROL: star({k}) has fee={fee} != {k}")
            return 2
        files = rels[:k]
        breaches = 0
        total_failures = 0
        for _ in range(INSTANCES_PER_FEE):
            # each of the k crossings emits the absolute (breach-prone) convention w.p. p;
            # an absolute emission IS a real git failure (established by the substrate gate).
            n_fail = sum(1 for _r in files if rng.random() < P_ABSOLUTE)
            total_failures += n_fail
            if n_fail >= 1:
                breaches += 1
        analytic = 1.0 - (1.0 - P_ABSOLUTE) ** k   # P(>=1 breach | fee=k), Bernoulli-per-crossing
        rows.append({
            "fee": fee,
            "schema_hamming": k,           # = fee exactly on this 1-D substrate, by construction
            "breach_rate": round(breaches / INSTANCES_PER_FEE, 4),
            "mean_failures": round(total_failures / INSTANCES_PER_FEE, 4),
            "analytic_breach_rate": round(analytic, 4),
        })
        print(f"fee={fee:>2}  breach_rate={rows[-1]['breach_rate']:.3f}  "
              f"(analytic {analytic:.3f})  mean_failures={rows[-1]['mean_failures']:.3f}")

    br = [r["breach_rate"] for r in rows]
    # Monotone non-decreasing (sampling tolerance) AND a genuine end-to-end rise.
    monotone = all(br[i + 1] >= br[i] - 0.03 for i in range(len(br) - 1)) and br[-1] > br[0] + 0.1
    fee_equals_hamming = all(r["fee"] == r["schema_hamming"] for r in rows)
    verdict = "PROCEED" if monotone else "DEFLATE"

    print()
    print(f"provenance: EXECUTION_INDEPENDENT (labels = real `git show` exit codes, fee-independent)")
    print(f"substrate wired + fee==k constructible: {monotone}   fee == schema_hamming (collinear): {fee_equals_hamming}")
    print(f"VERDICT: {verdict} — substrate-wiring check, NOT a calibration: breach is a Bernoulli model "
          f"(curve == 1-(1-p)^k), so this cannot deflate. Preconditions for S1b met; the measured, "
          f"decoupled calibration is S1b.")

    out = Path(REPO) / "bulla" / "calibration" / "results" / "fee_breach_calibration_git.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "experiment": "fee_breach_calibration_git (S1a — substrate-wiring + fee-constructibility check; NOT a calibration)",
        "substrate": "K-consumer filesystem->git star; fee == K verified; label = real `git show` exit code",
        "provenance": "EXECUTION_INDEPENDENT",
        "seed": SEED,
        "p_absolute": P_ABSOLUTE,
        "instances_per_fee": INSTANCES_PER_FEE,
        "curve": rows,
        "breach_is_modeled_bernoulli": True,
        "is_calibration": False,
        "is_deflation_test": False,
        "substrate_wired_and_fee_constructible": monotone,
        "fee_equals_schema_hamming": fee_equals_hamming,
        "VERDICT": verdict,
        "scope": ("NOT a calibration and NOT a deflation test. Breach here is a per-crossing Bernoulli "
                  "emission model, so the curve is the arithmetic 1-(1-p)^k and cannot fail for p>0 — the "
                  "genuine empirical content is only (1) the EXECUTION_INDEPENDENT git substrate is wired "
                  "and (2) fee==k is constructible. Nor can it distinguish fee from the cheap baseline: on "
                  "this single-dimension substrate the schema fee EQUALS the schema convention-distance "
                  "(Hamming) exactly (corr=1, by construction). The measured, decoupled calibration — "
                  "breach by real value propagation where fee = sum_d fee_d and scalar Hamming separate — "
                  "is S1b (convention_distance_collapse.md:13-16, :60-68)."),
    }, indent=2) + "\n")
    print(f"\nartifact: {out}")
    return 0 if verdict in ("PROCEED", "DEFLATE") else 2


if __name__ == "__main__":
    raise SystemExit(main())
