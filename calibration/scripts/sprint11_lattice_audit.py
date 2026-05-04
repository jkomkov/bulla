"""Sprint 11 Phase 7 (stretch) — cross-corpus regime lattice audit.

For every corpus the program has access to, compute the rate at which
each regime predicate holds:

  Predicates (in order of strength):
    1. has_projective_observables       (Sprint 9)
    2. is_well_formed_for_fee           (Sprint 8)
    3. has_dfd_conservative             (Sprint 11)
    4. has_chp_conservative             (Sprint 11)
    5. is_exact_regime_conservative     (Sprint 11; = DFD ∧ CHP)
    6. is_all_hidden                    (Sprint 9)

  Plus the negative-fee rate (`coherence_fee < 0`) as a sanity counter.

Corpora swept:
  C1+C2. Curated YAML (bulla/compositions + bulla/audit)
  C3.    Cycle family (Sprint 6 grid)
  C4.    Registry pair compositions (250 sampled from 57 servers)
  C5.    Sprint 7 random stress (1000 from disjoint-partition generator)
  C6.    Sprint 9 well-formed random (1000 from projective generator)

Output:
  papers/composition-doctrine/sprint11_lattice_audit.{json,md}

This is the "beautiful bridge between code quality and research claims"
the user's stretch goal asks for: every corpus gets a regime profile,
revealing how much of the empirical program lives in each theorem regime.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO.parent / "bulla" / "tests"))

from bulla.parser import load_composition
from bulla.regime import classify

PREDICATES = [
    "has_projective_observables",
    "is_well_formed_for_fee",
    "has_dfd_conservative",
    "has_chp_conservative",
    "is_exact_regime_conservative",
    "is_all_hidden",
]


def measure_corpus(name: str, comps: list) -> dict:
    """Return per-predicate rate for the corpus."""
    n = len(comps)
    if n == 0:
        return {"name": name, "n": 0}
    rates: dict[str, int] = {p: 0 for p in PREDICATES}
    n_neg_fee = 0
    for comp in comps:
        try:
            r = classify(comp)
        except Exception:
            continue
        for p in PREDICATES:
            if getattr(r, p):
                rates[p] += 1
        if r.fee_formula < 0:
            n_neg_fee += 1
    return {
        "name": name,
        "n": n,
        "n_neg_fee": n_neg_fee,
        **{p: rates[p] for p in PREDICATES},
    }


def load_curated() -> list:
    out = []
    for d in [REPO / "compositions", REPO / "audit"]:
        if d.exists():
            for p in sorted(d.glob("*.yaml")):
                try:
                    out.append(load_composition(p))
                except Exception:
                    pass
    return out


def load_cycle_family() -> list:
    sys.path.insert(0, str(REPO.parent / "papers" / "locality-cycle-family" / "script"))
    from build_grid import build_disjoint_cycles, build_single_cycle
    out = []
    for k in (2, 3, 4, 5, 6):
        for m in (4, 5, 6, 7, 8, 9, 10):
            out.append(build_disjoint_cycles(k, m))
            out.append(build_single_cycle(k, m))
    return out


def load_registry_pairs(n_pairs: int = 250) -> list:
    sys.path.insert(0, str(REPO / "calibration" / "scripts"))
    from sprint4_canonical_pair import build_composition_from_manifests, load_manifests
    manifests = load_manifests()
    servers = sorted(manifests.keys())
    rng = random.Random(20260502)
    pairs = [(a, b) for i, a in enumerate(servers) for b in servers[i+1:]]
    rng.shuffle(pairs)
    out = []
    for a, b in pairs[:n_pairs]:
        try:
            out.append(build_composition_from_manifests(a, manifests[a], b, manifests[b]))
        except Exception:
            pass
    return out


def load_random_stress(n: int = 1000) -> list:
    from test_disclosure_semantics_random import random_composition
    rng = random.Random(20260502)
    return [random_composition(rng, seed_id=i) for i in range(n)]


def load_well_formed_random(n: int = 1000) -> list:
    from test_schema_shape_invariant import well_formed_random_composition
    rng = random.Random(20260502)
    return [well_formed_random_composition(rng, seed_id=i) for i in range(n)]


def main():
    print("Sprint 11 Phase 7 — Cross-Corpus Regime Lattice Audit")
    print("=" * 100)

    corpora = [
        ("C1+C2 — curated YAML", load_curated()),
        ("C3 — cycle family", load_cycle_family()),
        ("C4 — registry pairs (250 sampled)", load_registry_pairs(n_pairs=250)),
        ("C5 — random stress (Sprint 7 disjoint)", load_random_stress(n=1000)),
        ("C6 — well-formed random (Sprint 9 projective)", load_well_formed_random(n=1000)),
    ]

    rows = []
    for name, comps in corpora:
        row = measure_corpus(name, comps)
        rows.append(row)

    # Print compact text table
    print()
    print(f"{'Corpus':50s} {'n':>6s} {'proj %':>8s} {'wf% ':>8s} {'dfd%':>6s} {'chp%':>6s} {'exact%':>8s} {'hid%':>6s} {'neg%':>6s}")
    print("-" * 100)
    for row in rows:
        n = row["n"]
        if n == 0:
            print(f"{row['name']:50s} {n:>6d}  (no compositions)")
            continue
        def pct(field: str) -> str:
            return f"{100 * row[field] / n:>5.1f}%"
        print(f"{row['name']:50s} {n:>6d} "
              f"{pct('has_projective_observables'):>8s} "
              f"{pct('is_well_formed_for_fee'):>8s} "
              f"{pct('has_dfd_conservative'):>6s} "
              f"{pct('has_chp_conservative'):>6s} "
              f"{pct('is_exact_regime_conservative'):>8s} "
              f"{pct('is_all_hidden'):>6s} "
              f"{pct('n_neg_fee'):>6s}")

    # Persist
    out_dir = REPO.parent / "papers" / "composition-doctrine"
    out_json = out_dir / "sprint11_lattice_audit.json"
    out_md = out_dir / "sprint11_lattice_audit.md"
    out_json.write_text(json.dumps(rows, indent=2))

    md_lines = [
        "# Sprint 11 Phase 7 — Cross-Corpus Regime Lattice Audit",
        "",
        "Per-corpus rate at which each regime predicate holds. "
        "Higher = stronger regime guarantees.",
        "",
        "| Corpus | n | projective | well-formed | DFD-conservative | CHP-conservative | exact-conservative | all-hidden | neg-fee |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        n = row["n"]
        if n == 0:
            continue
        md_lines.append(
            f"| {row['name']} | {n} | "
            f"{100*row['has_projective_observables']/n:.1f}% | "
            f"{100*row['is_well_formed_for_fee']/n:.1f}% | "
            f"{100*row['has_dfd_conservative']/n:.1f}% | "
            f"{100*row['has_chp_conservative']/n:.1f}% | "
            f"{100*row['is_exact_regime_conservative']/n:.1f}% | "
            f"{100*row['is_all_hidden']/n:.1f}% | "
            f"{100*row['n_neg_fee']/n:.1f}% |"
        )
    md_lines.append("")
    md_lines.append("## Reading the table")
    md_lines.append("")
    md_lines.append(
        "- **projective**: structural schema-shape invariant (Sprint 9). "
        "Implies well-formed-for-fee."
    )
    md_lines.append(
        "- **well-formed**: rank_internal ≥ rank_obs; coherence_fee is non-negative."
    )
    md_lines.append(
        "- **DFD-conservative / CHP-conservative / exact-conservative**: "
        "paper §3.5 sufficient conditions, conservatively detected on `bulla.model`. "
        "These are sufficient (not necessary) for the abstract paper notions; "
        "always preserve the `-conservative` qualifier in user-facing prose."
    )
    md_lines.append(
        "- **all-hidden**: special case where every observable_schema is empty "
        "(cycle family lives here)."
    )
    md_lines.append(
        "- **neg-fee**: percentage of compositions where coherence_fee < 0 "
        "(should be 0% for any well-formed corpus)."
    )
    out_md.write_text("\n".join(md_lines) + "\n")

    print()
    print(f"  Detailed JSON: {out_json.relative_to(REPO.parent)}")
    print(f"  Markdown:      {out_md.relative_to(REPO.parent)}")


if __name__ == "__main__":
    main()
