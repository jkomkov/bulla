#!/usr/bin/env python3
"""D1 + M1 — the type-layer completeness slash, and its correctness measurement.

The value-blind, machine-speed, execution-INDEPENDENT enforcement primitive of the
bonded-witnessing thesis. A receipt attests a coherence fee; the slash RECOMPUTES it from the
declared composition and fires iff the truth EXCEEDS the attestation — the receipt UNDER-declared
the obstruction (a structurally-required predicate it did not account for; `minimum_disclosure_set`
names exactly which). An HONEST receipt — recomputed == attested, whether fee=0 or fee=k>0 truthfully
declared and refused — is NEVER slashed, regardless of magnitude. Truthfully declaring a genuine
obstruction is not a violation; only *under*-declaration is. Over-declaration (conservative) is spared too.

This is fee-as-TYPE-invariant (λ_∇ / B3, stamp 3621ce14: fee = the obstruction to global TYPE coherence;
disclosure-NF = the minimal coercion set). It is value-blind *by definition* — a Composition carries
field NAMES, never values — which is exactly what makes the slash recomputable in microseconds, by
anyone holding the composition, with no execution oracle. (The performance-layer breach slash — "the
realized values disagreed" — is a separate, secondary track that needs the §3 oracle.)

M1 (MEASUREMENT — can fail): the slash MUST fire on an under-declarer and MUST NOT fire on an honest
receipt (including an honest HIGH fee — the false-positive guard), an over-declarer, or a nuisance
hidden field on no edge. FAIL ⇒ D1 is unsound. Deterministic; verify-before-record (each case's true
fee is measured and asserted before the slash is judged).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from bulla.diagnostic import diagnose, minimum_disclosure_set
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.regime import is_well_formed_for_fee


# ── D1: the primitive ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SlashResult:
    well_formed: bool                       # False => ill-formed; the slash CANNOT judge it (reject, not spare)
    slash: bool                             # meaningful only when well_formed
    attested_fee: int
    recomputed_fee: int
    under_declared_by: int                  # recomputed - attested, clamped at 0
    required_disclosures: tuple[tuple[str, str], ...]   # which predicates the obstruction needs


def completeness_slash(composition: Composition, attested_fee: int) -> SlashResult:
    """Slash iff the recomputed fee EXCEEDS the attested fee (under-declaration only).
    REFUSES to judge an ill-formed composition (`rank_obs > rank_internal` => fee < 0, uninterpretable —
    the declared schema observes more than it holds): an ill-formedness REJECT, not a spare. The fee is
    a type invariant only on the well-formed regime. Execution-independent (deterministic recomputation)."""
    recomputed = diagnose(composition).coherence_fee
    if not is_well_formed_for_fee(composition):              # rank_internal >= rank_obs  <=>  fee >= 0
        return SlashResult(False, False, attested_fee, recomputed, 0, ())
    return SlashResult(
        well_formed=True,
        slash=recomputed > attested_fee,
        attested_fee=attested_fee,
        recomputed_fee=recomputed,
        under_declared_by=max(0, recomputed - attested_fee),
        required_disclosures=tuple(minimum_disclosure_set(composition)),
    )


# ── tiny composition builders (schema only — no values) ──────────────────────

def seam(hidden: bool, dim: str = "path_root") -> Composition:
    """One filesystem->git seam on `dim`. hidden=True -> the convention is held internally but NOT
    declared (fee 1); hidden=False -> it is held internally AND declared observable (fee 0). Both are
    well-formed (internal >= observable); declaring an unheld field would be ill-formed (fee < 0)."""
    if hidden:
        fs, gt = ToolSpec("filesystem", (dim,), ()), ToolSpec("git", (dim,), ())
    else:
        fs, gt = ToolSpec("filesystem", (dim,), (dim,)), ToolSpec("git", (dim,), (dim,))
    return Composition("seam", (fs, gt), (Edge("filesystem", "git", (SemanticDimension(dim, dim, dim),)),))


def seam_with_nuisance(dim: str = "path_root") -> Composition:
    """A hidden seam (fee 1) plus a hidden field referenced by NO edge — a zero column that
    must change neither the fee nor the verdict."""
    fs = ToolSpec("filesystem", (dim, "nuisance_unreferenced"), ())
    gt = ToolSpec("git", (dim,), ())
    return Composition("seam_nuisance", (fs, gt), (Edge("filesystem", "git", (SemanticDimension(dim, dim, dim),)),))


def triangle() -> Composition:
    """A 3-cycle on one hidden dimension — a genuine fee=2 obstruction (per the achievability gate)."""
    d = "money"
    tools = [ToolSpec(f"t{i}", (d,), ()) for i in range(3)]
    edges = [Edge(f"t{i}", f"t{(i + 1) % 3}", (SemanticDimension(d, d, d),)) for i in range(3)]
    return Composition("triangle", tuple(tools), tuple(edges))


def ill_formed_seam(dim: str = "path_root") -> Composition:
    """Observes a field it does NOT hold internally (rank_obs > rank_internal) -> fee < 0, ill-formed.
    The slash must REFUSE to judge this, not silently 'spare' it (the defect the critical QA found)."""
    fs, gt = ToolSpec("filesystem", (), (dim,)), ToolSpec("git", (), (dim,))
    return Composition("ill_formed", (fs, gt), (Edge("filesystem", "git", (SemanticDimension(dim, dim, dim),)),))


# ── M1: slash correctness (the measurement that can fail) ────────────────────

def main() -> int:
    # (name, composition, intended_true_fee, attested_fee, expected_well_formed, expected_slash, rationale)
    cases = [
        ("under_declarer",  seam(hidden=True),    1, 0, True,  True,
         "fee=1 obstruction hidden, attested 0 -> caught"),
        ("honest_zero",     seam(hidden=False),   0, 0, True,  False,
         "convention observable (fee 0), attested 0 -> spared"),
        ("honest_high_fee", triangle(),           2, 2, True,  False,
         "fee=2 truthfully declared and refused -> spared (false-positive guard)"),
        ("over_declarer",   seam(hidden=True),    1, 2, True,  False,
         "conservative over-declaration (attested 2 > true 1) -> spared"),
        ("nuisance_field",  seam_with_nuisance(), 1, 1, True,  False,
         "hidden field on no edge changes nothing -> spared"),
        ("ill_formed",      ill_formed_seam(),   -1, 0, False, False,
         "observes an unheld field (fee<0) -> slash REFUSES to judge (reject, not spare)"),
    ]

    rows, ok = [], True
    for name, comp, intended, attested, exp_wf, exp_slash, why in cases:
        true_fee = diagnose(comp).coherence_fee
        if true_fee != intended:                         # verify-before-record: the case must be built as intended
            print(f"INVALID CONTROL [{name}]: true fee {true_fee} != intended {intended} — case mis-built")
            return 2
        res = completeness_slash(comp, attested)
        wf_ok = res.well_formed == exp_wf
        slash_ok = (res.slash == exp_slash) if res.well_formed else True   # slash meaningful only when well-formed
        passed = wf_ok and slash_ok
        ok = ok and passed
        rows.append({
            "case": name, "true_fee": true_fee, "attested_fee": attested, "well_formed": res.well_formed,
            "expected_well_formed": exp_wf, "slash": res.slash, "expected_slash": exp_slash, "passed": passed,
            "required_disclosures": [list(d) for d in res.required_disclosures], "rationale": why,
        })
        flag = "ok " if passed else "FAIL"
        status = f"slash={str(res.slash):5s}" if res.well_formed else "REFUSED (ill-formed)"
        print(f"[{flag}] {name:16s} true={true_fee:>2} attested={attested} {status:20s} "
              f"exp_wf={exp_wf} exp_slash={exp_slash}  ({why})")

    verdict = "M1_PASS" if ok else "M1_FAIL"
    print(f"\nVERDICT: {verdict} — the type-layer completeness slash "
          f"{'catches under-declaration and spares honesty/nuisance' if ok else 'is UNSOUND (D1 broken)'}")
    print("provenance: EXECUTION_INDEPENDENT (label = deterministic schema recomputation; no oracle)")

    out = Path(__file__).resolve().parents[1] / "calibration" / "results" / "type_layer_slash_M1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "experiment": "type_layer_slash_M1 (D1 build + M1 correctness)",
        "primitive": "completeness_slash: slash iff recompute(declared).fee > attested_fee",
        "provenance": "EXECUTION_INDEPENDENT",
        "layer": "type (value-blind, machine-speed, no oracle)",
        "cases": rows,
        "VERDICT": verdict,
    }, indent=2) + "\n")
    print(f"artifact: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
