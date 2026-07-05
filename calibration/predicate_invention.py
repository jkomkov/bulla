#!/usr/bin/env python3
"""Invention vs. disclosure — what the value-blind kernel can and cannot see (the honest finding).

North star: in-line predicate INVENTION with enforceability via the witness mechanism, at machine speed.

A first draft of this script "demonstrated invention" by adding a held-but-undeclared `encoding` dimension
and watching fee go 0->1->0 with the slash firing. QA killed it: a convention BOTH tools *hold* is a
LATENT convention, so surfacing it is DISCLOSURE, not invention — the draft dressed disclosure as invention,
the exact slippage under test. This is the corrected, verified finding.

THE TWO ORTHOGONAL AXES:
  * Homotopy axis (what the kernel SEES): does an update change the fee? `dim H^1` is a chain-homotopy
    invariant (refinement-types Thm 7.2, paper.md:746; fee-changing updates "genuinely add or remove
    obstructions", paper.md:779). VERIFIED: fee-changing <=> beyond the chain-homotopy-equivalent slice.
  * Provenance axis (what the kernel is BLIND to): was this meaning ever a tool's behavior / in the
    registry before? disclosure (latent, present-before) vs invention (novel, absent-before).

These axes are ORTHOGONAL: BOTH disclosure and invention are fee-changing (beyond homotopy). So the homotopy
slice does NOT separate invention from disclosure. The separating axis is provenance, which a value-blind
schema does not carry. Hence the kernel cannot distinguish invention from disclosure — they are the SAME
schema operation (extend with a fee-changing dimension). This script ILLUSTRATES that indistinguishability;
it is an architectural fact, not a falsifiable experiment, and is labelled as such.

CONSEQUENCES (the north-star factorization):
  ENFORCE   = the completeness slash (value-blind, machine-speed; built, 29108ac). Necessarily
              invention-agnostic — it conditions on the current schema, which carries no provenance. FREE.
  CLASSIFY  = the provenance REGISTRY (the commons): a convention absent from the registry is novel
              (invention); present is disclosure. The registry is the classifier — the genuine, non-trivial
              role of the commons (NOT the can't-fail "compounding" story, commons_flywheel_prereg.md).
  DETECT    = execution: the trigger that a novel convention is *needed* (the deferred performance layer).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from type_layer_slash import completeness_slash                      # the SAME built slash (29108ac)  # noqa: E402

from bulla.diagnostic import diagnose, minimum_disclosure_set       # noqa: E402
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec  # noqa: E402


def seam(held: tuple[str, ...], declared: tuple[str, ...]) -> Composition:
    """producer->consumer; each tool HOLDS `held` and DECLARES `declared`; one edge per held dim.
    A held-but-undeclared dimension obstructs (fee += 1)."""
    p = ToolSpec("producer", held, declared)
    c = ToolSpec("consumer", held, declared)
    edges = tuple(Edge("producer", "consumer", (SemanticDimension(d, d, d),)) for d in held)
    return Composition("seam", (p, c), edges)


def fingerprint(comp: Composition) -> tuple:
    """Everything the value-blind kernel exposes about a composition's current state."""
    d = diagnose(comp)
    return (d.coherence_fee, tuple(sorted((t, f) for t, f in minimum_disclosure_set(comp))))


def main() -> int:
    # ── Two PROVENANCE histories that CONVERGE to the identical current interface. ──────────────────
    # current state: 'encoding' is held and declared by both -> coherent (fee 0). SAME for both stories.
    current = seam(held=("path", "encoding"), declared=("path", "encoding"))

    # DISCLOSURE history: in the prior interface 'encoding' was a LATENT convention (held, undeclared) ->
    # it was already an obstruction (fee 1); the update merely DECLARED it.
    prior_disclosure = seam(held=("path", "encoding"), declared=("path",))
    # INVENTION history: in the prior interface 'encoding' was ABSENT (not a dimension at all; fee 0);
    # the update CREATED the convention and declared it.
    prior_invention = seam(held=("path",), declared=("path",))

    fp_cur = fingerprint(current)
    encoding_in = lambda comp: any("encoding" in (sd.name for sd in e.dimensions) for e in comp.edges)

    # (A) SCHEMA-INDISTINGUISHABILITY: the current interface is identical regardless of which history
    #     produced it. The kernel, seeing only the current state, cannot recover the provenance.
    cur_again = seam(held=("path", "encoding"), declared=("path", "encoding"))   # rebuilt from "invention"
    indistinguishable = fingerprint(cur_again) == fp_cur

    # (B) PROVENANCE axis — recoverable ONLY with the registry (the prior interface). The classifier is a
    #     fee-DELTA across the registry boundary: disclosure's prior carried 'encoding' as an obstruction
    #     (fee 1), invention's prior did not (fee 0, no such dimension). The current cannot tell you this.
    fee_prior_disc = diagnose(prior_disclosure).coherence_fee
    fee_prior_inv = diagnose(prior_invention).coherence_fee
    provenance_separates = (encoding_in(prior_disclosure) and not encoding_in(prior_invention)
                            and fee_prior_disc != fee_prior_inv)

    # (C) ENFORCEMENT is free and NECESSARILY invention-agnostic: the slash conditions on the current
    #     schema (no provenance), so a receipt that under-declares 'encoding' is slashed identically no
    #     matter which history produced the interface.
    under_declared = seam(held=("path", "encoding"), declared=("path",))    # hides encoding; attests fee 0
    s = completeness_slash(under_declared, attested_fee=0)
    slash_is_provenance_blind = s.slash and s.recomputed_fee == 1

    # (D) HOMOTOPY axis (verified, QA-2) — and why it is NOT the invention axis. Both updates are
    #     fee-changing (disclosure 1->0, invention 0->... the invented dim, if left latent, is 0->1->0):
    #     each crosses the chain-homotopy slice (Thm 7.2). So "beyond homotopy" does not imply "invention".
    disclosure_update_fee_change = (fee_prior_disc, diagnose(current).coherence_fee)        # (1, 0)
    invented_latent = seam(held=("path", "encoding"), declared=("path",))
    invention_update_fee_change = (fee_prior_inv, diagnose(invented_latent).coherence_fee)  # (0, 1)
    both_fee_changing = (disclosure_update_fee_change[0] != disclosure_update_fee_change[1]
                         and invention_update_fee_change[0] != invention_update_fee_change[1])

    ok = indistinguishable and provenance_separates and slash_is_provenance_blind and both_fee_changing
    verdict = ("ARCHITECTURAL FINDING (illustrated; not a falsifiable experiment): the value-blind kernel "
               "CANNOT distinguish invention from disclosure — same schema operation; provenance is the "
               "missing axis." if ok else "INVALID ILLUSTRATION")

    out = {
        "experiment": "invention vs disclosure — the provenance axis (corrected after QA-1)",
        "provenance": "architectural illustration; value-blind kernel; no execution oracle",
        "qa_correction": "draft dressed DISCLOSURE (a held/latent encoding) as INVENTION; the homotopy slice "
                         "is the wrong axis — the invention/disclosure axis is provenance, invisible to the schema.",
        "A_schema_indistinguishable": {"current_fingerprint_fee": fp_cur[0],
                                       "identical_across_histories": indistinguishable},
        "B_provenance_separates": {"encoding_in_prior_disclosure": encoding_in(prior_disclosure),
                                   "encoding_in_prior_invention": encoding_in(prior_invention),
                                   "fee_prior_disclosure": fee_prior_disc, "fee_prior_invention": fee_prior_inv,
                                   "registry_classifies": provenance_separates},
        "C_enforcement_free": {"slash": s.slash, "recomputed_fee": s.recomputed_fee,
                               "necessarily_invention_agnostic": slash_is_provenance_blind},
        "D_homotopy_is_not_the_invention_axis": {
            "thm": "refinement-types Thm 7.2 (paper.md:746); fee-changing => beyond chain-homotopy slice (paper.md:779)",
            "disclosure_update_fee": list(disclosure_update_fee_change),
            "invention_update_fee": list(invention_update_fee_change),
            "both_fee_changing_so_homotopy_does_not_separate_them": both_fee_changing},
        "VERDICT": verdict,
        "north_star_factorization": (
            "ENFORCE = the value-blind slash (free, machine-speed, necessarily invention-agnostic). "
            "CLASSIFY novelty = the provenance REGISTRY/commons (absent=invention, present=disclosure) -- the "
            "genuine non-trivial role of the commons, NOT the can't-fail compounding story. DETECT the need = "
            "execution (the deferred performance layer). So 'in-line predicate invention' is honest as: a "
            "fee-changing schema extension (Thm 7.2 line-779 regime) whose NOVELTY is adjudicated by the "
            "registry and whose ENFORCEMENT is the value-blind slash -- not 'value-blind invention', and not a "
            "retreat to 'mere disclosure'."),
    }
    res = HERE / "results" / "predicate_invention.json"
    res.parent.mkdir(parents=True, exist_ok=True)
    res.write_text(json.dumps(out, indent=2) + "\n")

    print(f"(A) schema-indistinguishable across histories : {indistinguishable}  (current fee={fp_cur[0]})")
    print(f"(B) provenance separates (needs the registry) : {provenance_separates}  "
          f"(prior fee: disclosure={fee_prior_disc}, invention={fee_prior_inv})")
    print(f"(C) slash provenance-blind => enforcement FREE : {slash_is_provenance_blind}  (slash={s.slash}, recomputed={s.recomputed_fee})")
    print(f"(D) both updates fee-changing => homotopy is NOT the invention axis : {both_fee_changing}  "
          f"(disc {disclosure_update_fee_change}, inv {invention_update_fee_change})")
    print(f"\nVERDICT: {verdict}")
    print("north star: ENFORCE=slash(free) | CLASSIFY-novelty=registry(provenance) | DETECT=execution")
    print(f"artifact: {res}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
