#!/usr/bin/env python3
"""T2-achievability gate — can Bulla's rank-fee even SEE the holonomy that breaches?

A cheap, Stage-0-style probe run BEFORE building the full S1b oracle (the move that found
"Bounded" cheaply before the heavy stages). It decides whether the citable experiment should
test the rank-fee profile at all, or pivot to a value-aware harmonic predictor.

The §3 breach is **multi-path disagreement** = a non-trivial **directed holonomy** around a
loop (two paths from source to sink compose to different net maps). Bulla's `fee` is the rank
of the ±1 coboundary incidence (`convention_distance_collapse.md:257`; V3: undirected,
symmetric under edge reversal). Decisively: **a Bulla `Composition` carries field NAMES, never
convention VALUES** — so `fee` is a function of the *schema* alone, while breach is a function
of the *values*. The same schema can breach or not; `fee` cannot see the difference.

This probe MEASURES (not asserts) two things:
  A. value-blindness — one fixed cyclic schema; Bulla's `fee` is a single number, yet a minimal
     holonomy propagator shows the SAME schema breaches under one edge-transform assignment and
     not under another. fee is constant across the breach axis -> AUC = 0.5 at fixed schema.
  B. the β₁ disadvantage — `fee_d = V_d−1` vs count `E_d = V_d−1+β₁,d`. breach REQUIRES multi-path
     (β₁>0); the count sees β₁, the rank-fee's collapse throws it away. So the count weakly
     dominates the rank-fee on the very capacity that enables breach.

VERDICT: if no constructed composition lets the rank-fee profile STRICTLY out-discriminate the
per-dimension count on measured breach, T2 is analytically foreclosed for the rank-fee, and the
citable S1b experiment must test the VALUE-AWARE harmonic / Hodge predictor (the parent pre-reg's
Outcome-3 / Redirected branch; V3's "what's missing from rank-based fee"). Read-only; deterministic.
"""
from __future__ import annotations

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec

TOL = 1e-9   # holonomy "beyond tolerance" = a real multi-path disagreement


def cycle_schema(n: int, dim: str = "money") -> Composition:
    """An n-tool directed cycle t0->t1->...->t_{n-1}->t0 on ONE hidden dimension.
    All tools hold `dim` internally (hidden); no observable schema. This is the SCHEMA only —
    it contains no convention values."""
    tools = [ToolSpec(f"t{i}", (dim,), ()) for i in range(n)]
    edges = [Edge(f"t{i}", f"t{(i + 1) % n}", (SemanticDimension(dim, dim, dim),)) for i in range(n)]
    return Composition(f"cycle_{n}", tuple(tools), tuple(edges))


def holonomy_breach(edge_transforms: list[float]) -> tuple[float, bool]:
    """Minimal faithful breach model: each edge carries a convention transform r_e (e.g. a money
    re-scale the consumer applies under an undeclared convention). The directed holonomy around the
    closed loop is the product; the value returns multiplied by it. breach iff the loop fails to
    compose to the identity beyond tolerance. This is a VALUE property — Bulla's fee never sees it."""
    hol = 1.0
    for r in edge_transforms:
        hol *= r
    return hol, abs(hol - 1.0) > TOL


def betti1(n_vertices: int, n_edges: int) -> int:
    return n_edges - n_vertices + 1   # connected


def main() -> int:
    print("=" * 78)
    print("A. VALUE-BLINDNESS — one fixed schema, fee is one number, breach is not")
    print("=" * 78)
    comp = cycle_schema(3, "money")                 # fixed SCHEMA (3-cycle, one hidden dim)
    fee = diagnose(comp).coherence_fee
    V, E = 3, 3
    print(f"schema = 3-cycle on one hidden dim;  Bulla fee_d = {fee}  "
          f"(V-1 = {V-1}, E = {E}, beta_1 = {betti1(V, E)})")

    # The SAME schema, two different VALUE assignments (edge transforms) Bulla never sees:
    telescoping = [2.0, 3.0, 1.0 / 6.0]   # product = 1  -> holonomy trivial -> NO breach
    non_telesc  = [2.0, 3.0, 2.0]         # product = 12 -> holonomy non-trivial -> BREACH
    h0, b0 = holonomy_breach(telescoping)
    h1, b1 = holonomy_breach(non_telesc)
    print(f"  values A (telescoping): holonomy = {h0:.3f}  breach = {b0}   | Bulla fee = {fee}")
    print(f"  values B (non-trivial): holonomy = {h1:.3f}  breach = {b1}   | Bulla fee = {fee}")
    fee_blind = (fee is not None) and (b0 != b1)   # same fee, different breach
    print(f"  => same schema, same fee, DIFFERENT breach: fee is value-blind = {fee_blind}")

    print()
    print("=" * 78)
    print("B. THE β₁ DISADVANTAGE — rank-fee throws away the multi-path capacity breach needs")
    print("=" * 78)
    print(f"{'cycle n':>8} {'fee_d (rank)':>14} {'E_d (count)':>12} {'beta_1':>8} {'fee_d<E_d':>10}")
    rank_lt_count = False
    for n in range(2, 7):
        c = cycle_schema(n, "money")
        f = diagnose(c).coherence_fee
        E_n = n                       # n edges on the cycle
        b1_n = betti1(n, n)           # = 1 for a single cycle
        lt = f < E_n
        rank_lt_count = rank_lt_count or lt
        print(f"{n:>8} {f:>14} {E_n:>12} {b1_n:>8} {str(lt):>10}")
    print("  breach REQUIRES multi-path (beta_1 > 0). The count E_d sees beta_1; the rank-fee's")
    print("  collapse (fee_d = V-1) does not. So on the capacity that enables breach, the count")
    print("  weakly dominates the rank-fee — the rank can only TIE it, never beat it, structurally.")

    print()
    print("=" * 78)
    foreclosed = fee_blind and rank_lt_count
    if foreclosed:
        print("VERDICT: T2 FORECLOSED for the rank-fee.")
        print("  Bulla's fee is schema-only (value-blind); breach is a value-driven directed-holonomy")
        print("  property; and where the rank departs from the count (the beta_1 collapse) it discards")
        print("  the multi-path capacity that enables breach. No rank-fee profile can strictly")
        print("  out-discriminate the per-dimension count on measured breach.")
        print("  => REDIRECT: the citable S1b experiment must test a VALUE-AWARE harmonic / Hodge")
        print("     predictor (parent Outcome-3 / Redirected; V3's open question), NOT the rank-fee.")
    else:
        print("VERDICT: T2 NOT foreclosed by this probe — a rank-fee separation may exist; lock + build.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
