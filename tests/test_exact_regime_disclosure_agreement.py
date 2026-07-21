"""Sprint 12 — empirical regression test for the exact-regime-conservative
disclosure agreement claim.

`bulla/docs/REGIME.md` (Sprint 11) makes a strong claim:

  "Exact-regime (conservative) — Strongest disclosure guarantees:
   `weighted_greedy_repair` and `minimum_disclosure_set` agree on
   cardinality and on bases (paper §3.5)."

The user's Sprint 12 review correctly observed: this is a strong
statement and deserves a regression gate. Without one, the doc could
silently overclaim if the conservative detectors don't actually license
the agreement they imply.

This file empirically verifies the claim across:

  1. The Sprint 6 cycle family (positive control — cycle family is
     entirely exact-regime-conservative).

  2. Curated YAML compositions that pass `is_exact_regime_conservative`.

  3. A negative control: compositions that FAIL exact-conservative
     should not necessarily satisfy the disclosure-agreement claim.
     Documented for context, not asserted.

If the agreement holds across both positive sets — full cardinality
AND set equality — the docs and the implementation are consistent.
If it fails, the regression test surfaces the gap immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BULLA_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BULLA_ROOT / "src"))

from bulla.diagnostic import minimum_disclosure_set
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition
from bulla.regime import classify
from bulla.witness_geometry import weighted_greedy_repair, witness_gram


# ---- Cycle-family helpers (positive controls, exact-conservative by construction) ----

def _build_disjoint_cycles(k: int, m: int) -> Composition:
    n = k * m
    tools = tuple(
        ToolSpec(name=f"t{i}", internal_state=("f",), observable_schema=())
        for i in range(n)
    )
    edges = []
    for c in range(k):
        for i in range(m):
            u = c * m + i
            v = c * m + (i + 1) % m
            edges.append(Edge(
                from_tool=f"t{u}", to_tool=f"t{v}",
                dimensions=(SemanticDimension(name="f_match", from_field="f", to_field="f"),),
            ))
    return Composition(name=f"A_{k}_{m}", tools=tools, edges=tuple(edges))


# ---- 1. Cycle family: cardinality + set equality ----

@pytest.mark.parametrize("k,m", [(2, 4), (2, 5), (3, 6), (4, 8), (5, 10), (6, 7)])
def test_cycle_family_exact_conservative_disclosure_agreement(k, m):
    """For every (k, m), the cycle family `A_{k,m}` satisfies
    `is_exact_regime_conservative`. Under that regime, the doc claim is:
    `|weighted_greedy_repair| == |minimum_disclosure_set|` AND the two
    sets are equal as field sets.

    This test verifies both halves of the claim on the cycle family
    grid (positive control)."""
    comp = _build_disjoint_cycles(k, m)
    report = classify(comp)
    assert report.is_exact_regime_conservative, (
        f"cycle family k={k} m={m} should be exact-conservative; "
        f"if this fails the test premise is invalid."
    )

    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))
    greedy = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)

    # Cardinality agreement (the weaker, easier claim — Sprint 6 already verified this)
    assert len(greedy) == len(min_disc), (
        f"cycle k={k} m={m}: |greedy|={len(greedy)} != |min_disc|={len(min_disc)} "
        f"in exact-conservative regime"
    )
    # Set equality (the stronger doc claim — REGIME.md Sprint 11)
    assert set(greedy) == set(min_disc), (
        f"cycle k={k} m={m}: greedy and min_disc differ as field sets, "
        f"violating the REGIME.md exact-conservative disclosure-agreement claim. "
        f"greedy: {sorted(greedy)}, min_disc: {sorted(min_disc)}"
    )


# ---- 2. Curated YAML compositions that pass exact-conservative ----

def test_exact_conservative_curated_compositions_disclosure_agreement():
    """For every curated YAML composition that passes
    `is_exact_regime_conservative`, the doc claim must hold:
    cardinality + set equality of `weighted_greedy_repair` and
    `minimum_disclosure_set`.

    Compositions that fail `is_exact_regime_conservative` are SKIPPED
    in this test — they live in a regime where the doc makes no
    agreement claim, and a divergence there would be expected, not a
    regression."""
    n_exact = 0
    n_card_agree = 0
    n_set_agree = 0
    failures: list[tuple[str, dict]] = []
    for d in ["compositions", "audit"]:
        for p in sorted((BULLA_ROOT / d).glob("*.yaml")):
            try:
                comp = load_composition(p)
            except Exception:
                continue
            report = classify(comp)
            if not report.is_exact_regime_conservative:
                continue  # outside the doc claim's scope
            n_exact += 1

            K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))
            greedy = weighted_greedy_repair(K, hidden_basis)
            min_disc = minimum_disclosure_set(comp)

            card_ok = len(greedy) == len(min_disc)
            set_ok = set(greedy) == set(min_disc)
            if card_ok:
                n_card_agree += 1
            if set_ok:
                n_set_agree += 1
            if not (card_ok and set_ok):
                failures.append((p.name, {
                    "n_greedy": len(greedy),
                    "n_min_disc": len(min_disc),
                    "card_ok": card_ok,
                    "set_ok": set_ok,
                    "greedy_set": sorted(greedy),
                    "min_disc_set": sorted(min_disc),
                }))

    assert n_exact > 0, (
        "No curated YAML compositions passed `is_exact_regime_conservative`. "
        "Either the corpus has shifted or the predicate has regressed; "
        "investigate before relaxing this test."
    )
    assert n_card_agree == n_exact, (
        f"Cardinality agreement failed in {n_exact - n_card_agree}/{n_exact} "
        f"exact-conservative compositions: {failures}"
    )
    assert n_set_agree == n_exact, (
        f"Set agreement failed in {n_exact - n_set_agree}/{n_exact} "
        f"exact-conservative compositions: {failures}. "
        f"REGIME.md claims set agreement under exact-conservative — if this "
        f"fails, EITHER the doc claim is too strong OR the conservative "
        f"detectors don't license the claim. Investigate before relaxing."
    )


# ---- 3. Reference: behaviour outside exact-conservative (informational) ----

def test_outside_exact_conservative_no_agreement_claim():
    """For compositions that do NOT satisfy `is_exact_regime_conservative`,
    `REGIME.md` makes no agreement claim — divergence in either cardinality
    or set is expected and not a regression. This test exists to document
    that scope: it asserts the negation only weakly (we expect SOME
    divergent example to exist), preventing the doc claim from accidentally
    being treated as universal.
    """
    # Reuse Sprint 7's well-known divergence example (random seed=10).
    sys.path.insert(0, str(BULLA_ROOT / "tests"))
    from test_disclosure_semantics_random import random_composition
    import random
    rng = random.Random(10)
    comp = random_composition(rng, seed_id=10)
    report = classify(comp)
    # The Sprint 7 reproducer is NOT exact-conservative.
    assert not report.is_exact_regime_conservative, (
        "Sprint 7 reproducer suddenly became exact-conservative — "
        "the random generator may have changed; investigate."
    )
    # Outside the regime, divergence is expected (no doc claim applies).
    K, hidden_basis = witness_gram(list(comp.tools), list(comp.edges))
    greedy = weighted_greedy_repair(K, hidden_basis)
    min_disc = minimum_disclosure_set(comp)
    # Document the divergence (not a strict assertion):
    print(f"\nOutside-exact-conservative reference (Sprint 7 seed 10):")
    print(f"  |greedy| = {len(greedy)}, |min_disc| = {len(min_disc)}")
    print(f"  set equality: {set(greedy) == set(min_disc)}")
