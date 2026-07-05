#!/usr/bin/env python3
"""Certificate-chain composition (= D3, the composition bond) — the trustless type-layer pool, assembled.

Composes the pieces validated across the arc into a transitive, slashable certificate economy:
the completeness slash (29108ac) + the receipt DAG + boundary_fee (M2, b357e84). It is Vol I's
bill-of-exchange endorsement chain mechanized — value-blind, deterministic, NO execution oracle.

A certificate chain is a composition partitioned into LINKS (one receipt/attestation per link).
`decompose_fee` separates the slash exactly into two kinds:

  - LINK-ATTRIBUTABLE: a link whose recomputed LOCAL fee EXCEEDS its attestation under-declared its OWN
    obstruction -> slash that link's attestor (the per-link completeness slash, D1). Deterministic,
    trustless, no adjudication -> no plutocracy (the type/systemic half of the absent-master problem).
  - THE BOUNDARY (joint) TERM: `boundary_fee` — the obstruction present in the composition but in NO
    single link (Theorem A; M2 showed it is live on ~1/3 of real cuts). By construction NO link is
    locally at fault, so it cannot be allocated to a link. WHO COVERS IT is the design fork (A8).

TRUSTLESS-ORACLE PRECONDITION (I1, load-bearing): the recomputation must run on the composition the
receipt is bound to — verify `hash(provided_composition) == receipt.composition_hash` first — or an
adversary substitutes a coherent decoy and the deterministic "oracle" judges the wrong object. This
module recomputes from the provided composition; the deployable slash MUST gate on the hash bind.

SCOPE (held honest, per the M2 tightenings): `boundary_fee` here is the boundary AT THIS LINK PARTITION
(the chain's own structure), not an irreducible-across-all-partitions minimum; it is a structural
composition term, not validated as real coordination LOSS (that is the foreclosed/deferred value layer).
"""
from __future__ import annotations

import json
from pathlib import Path

from fractions import Fraction

from bulla.diagnostic import decompose_fee
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.witness_geometry import leverage_scores, witness_gram


# ── (d) the boundary allocation: who bonds the joint term — computed by the geometry, not chosen ──

def _leverage_map(comp: Composition) -> dict:
    K, hb = witness_gram(list(comp.tools), list(comp.edges))
    return {hb[i]: l for i, l in enumerate(leverage_scores(K))}


def boundary_leverage_allocation(comp: Composition, links: list[frozenset[str]]) -> dict:
    """Distribute boundary_fee to the bonded hidden-field OWNERS by each field's marginal contribution to
    the JOINT term: `total_leverage - sum(within-group local_leverage)`. Sums to boundary_fee EXACTLY (the
    projection-trace identity: Σ total = total_fee, Σ local = Σ local_fees); is non-negative — PROVEN for the
    all-hidden regime (an internal edge touches only its group's fields, so row(W_group) ⊆ row(W_full) and the
    projection diagonal is monotone: total_leverage ≥ local_leverage ⇒ boundary_leverage ≥ 0), and for the
    observable case validated over ~5000 adversarial constructions targeting the coloop-local/substitutable-
    global failure shape with 0 counterexamples (the observable proof is open — the subset argument breaks
    under the differing (I−P_O) projections; the boundary-Gram leverage, (K⁺K)_jj on the cross-mod-internal
    Gram and in [0,1] by construction, is the provable-everywhere fallback if a deployment audit requires it);
    and is a pure deterministic schema recomputation -> trustless (no adjudication, no plutocracy). Option (d):
    the geometry computes the canonical allocation; coloops (leverage 1) bear their full share, loops
    (leverage 0) bear zero -- the math certifies who could have prevented the obstruction.
    NB: the naive 'restrict witness_gram to cross rows' does NOT sum to boundary_fee (it misses the
    modulo-internal); total-minus-local is the correct rule."""
    total = _leverage_map(comp)
    tmap = {t.name: t for t in comp.tools}
    local: dict = {}
    for g in links:
        sub = Composition("sub", tuple(tmap[n] for n in sorted(g)),
                          tuple(e for e in comp.edges if e.from_tool in g and e.to_tool in g))
        for k, v in _leverage_map(sub).items():
            local[k] = local.get(k, Fraction(0)) + v
    return {f: total[f] - local.get(f, Fraction(0)) for f in total}


# ── the primitive: separate link-attributable slashes from the joint boundary term ──

def allocate_chain_slash(comp: Composition, links: list[frozenset[str]],
                         attested_local_fees: list[int],
                         expected_composition_hash: str | None = None) -> dict:
    """Allocate a chain's slash. `links` partitions the tools (one per chain link); `attested_local_fees`
    is each link's attested LOCAL fee. Returns the link-attributable slashes (recomputed local > attested)
    and the boundary_fee (the joint term, attributable to no single link).

    I1 (trustless precondition, ENFORCED): if `expected_composition_hash` is given, the recomputation is
    REJECTED unless `comp.canonical_hash()` matches it — the defense against substituting a coherent decoy
    for the receipt's bound composition. Without the bind the deterministic oracle could judge the wrong
    object, and the 'no plutocracy' property would be void."""
    if expected_composition_hash is not None and comp.canonical_hash() != expected_composition_hash:
        return {"rejected": True, "reason": "I1: composition_hash mismatch (decoy substitution)",
                "expected": expected_composition_hash, "got": comp.canonical_hash()}
    dec = decompose_fee(comp, links)
    link_slashes = [
        {"link": sorted(g), "recomputed_local": lf, "attested": at, "under_by": lf - at}
        for g, lf, at in zip(links, dec.local_fees, attested_local_fees) if lf > at
    ]
    ba = boundary_leverage_allocation(comp, links)
    return {
        "rejected": False,
        "total_fee": dec.total_fee,
        "local_fees": list(dec.local_fees),
        "link_attributable_slashes": link_slashes,     # trustless, deterministic, plutocracy-free
        "boundary_fee": dec.boundary_fee,               # the joint/systemic term (Theorem A)
        # (d): the joint term is ALLOCATED to bonded field owners by leverage — deterministic, sums to
        # boundary_fee, trustless. NOT a policy fork; the geometry computes it.
        "boundary_allocation": {f"{t}.{fld}": str(v) for (t, fld), v in ba.items() if v != 0},
    }


def triangle() -> Composition:
    """A 3-cycle on one hidden dimension — total fee 2; partitioned into singletons every tool sees fee 0,
    so the whole obstruction is the JOINT boundary term (Theorem A made concrete)."""
    d = "money"
    tools = [ToolSpec(f"t{i}", (d,), ()) for i in range(3)]
    edges = [Edge(f"t{i}", f"t{(i + 1) % 3}", (SemanticDimension(d, d, d),)) for i in range(3)]
    return Composition("triangle", tuple(tools), tuple(edges))


# ── correctness test (can fail): does the allocator attribute link vs boundary correctly? ──

def main() -> int:
    comp = triangle()
    ok = True
    rows = []

    # Case 1 — JOINT boundary (singletons): every link local-fee 0, the entire fee is the boundary.
    links1 = [frozenset({"t0"}), frozenset({"t1"}), frozenset({"t2"})]
    d1 = decompose_fee(comp, links1)
    inv1 = (tuple(d1.local_fees) == (0, 0, 0) and d1.boundary_fee == d1.total_fee == 2)
    a1 = allocate_chain_slash(comp, links1, [0, 0, 0])                 # honest
    pass1 = inv1 and not a1["link_attributable_slashes"] and a1["boundary_fee"] == 2
    ok = ok and pass1
    rows.append({"case": "joint_boundary_singletons", "local_fees": list(d1.local_fees),
                 "boundary_fee": d1.boundary_fee, "link_slashes": a1["link_attributable_slashes"],
                 "passed": pass1, "note": "whole obstruction is joint; no single tool at fault (Theorem A)"})
    print(f"[{'ok ' if pass1 else 'FAIL'}] joint_boundary: locals={list(d1.local_fees)} boundary={d1.boundary_fee} "
          f"link_slashes={len(a1['link_attributable_slashes'])}")

    # Coarser partition {t0,t1},{t2}: some obstruction localizes; measure it (verify-before-record).
    links2 = [frozenset({"t0", "t1"}), frozenset({"t2"})]
    d2 = decompose_fee(comp, links2)
    locals2 = list(d2.local_fees)

    # Case 2 — LINK under-declares its own local obstruction -> only that link is slashed.
    under = [max(0, lf - 1) if lf > 0 else lf for lf in locals2]       # under-declare links with local>0
    a2 = allocate_chain_slash(comp, links2, under)
    expect_slashed = [sorted(g) for g, lf in zip(links2, locals2) if lf > 0]
    got_slashed = [s["link"] for s in a2["link_attributable_slashes"]]
    pass2 = got_slashed == expect_slashed and a2["boundary_fee"] == d2.boundary_fee
    ok = ok and pass2
    rows.append({"case": "link_under_declares", "local_fees": locals2, "attested": under,
                 "boundary_fee": d2.boundary_fee, "link_slashes": a2["link_attributable_slashes"],
                 "passed": pass2, "note": "only the under-declaring link is slashed; boundary unaffected"})
    print(f"[{'ok ' if pass2 else 'FAIL'}] link_under: locals={locals2} attested={under} "
          f"slashed={got_slashed} expect={expect_slashed} boundary={a2['boundary_fee']}")

    # Case 3 — HONEST chain (attested == true local fees) -> no link slashed; boundary is the joint term.
    a3 = allocate_chain_slash(comp, links2, locals2)
    pass3 = not a3["link_attributable_slashes"] and a3["boundary_fee"] == d2.boundary_fee
    ok = ok and pass3
    rows.append({"case": "honest_chain", "local_fees": locals2, "attested": locals2,
                 "boundary_fee": d2.boundary_fee, "link_slashes": a3["link_attributable_slashes"],
                 "passed": pass3, "note": "honest links spared; boundary is the joint term -> coverage fork"})
    print(f"[{'ok ' if pass3 else 'FAIL'}] honest_chain: locals={locals2} slashed={len(a3['link_attributable_slashes'])} "
          f"boundary={a3['boundary_fee']}")

    # Case 4 — I1 hash-bind: a decoy composition whose hash != the receipt's bound hash is REJECTED.
    bound_hash = comp.canonical_hash()
    r_match = allocate_chain_slash(comp, links2, locals2, expected_composition_hash=bound_hash)
    r_decoy = allocate_chain_slash(comp, links2, locals2, expected_composition_hash="deadbeef_not_the_bound_hash")
    pass4 = (not r_match["rejected"]) and r_decoy["rejected"]
    ok = ok and pass4
    rows.append({"case": "I1_hash_bind", "matched_hash_rejected": r_match["rejected"],
                 "decoy_hash_rejected": r_decoy["rejected"], "passed": pass4,
                 "note": "the deterministic oracle refuses a substituted composition (trustless precondition)"})
    print(f"[{'ok ' if pass4 else 'FAIL'}] I1_hash_bind: matched->rejected={r_match['rejected']} "
          f"decoy->rejected={r_decoy['rejected']}")

    # Case 5 — (d) boundary allocation: leverage allocation sums to boundary_fee and is non-negative.
    ba = boundary_leverage_allocation(comp, links2)
    s_ba = sum(ba.values())
    pass5 = s_ba == d2.boundary_fee and all(v >= 0 for v in ba.values())
    ok = ok and pass5
    alloc_str = {f"{t}.{f}": str(v) for (t, f), v in ba.items() if v != 0}
    rows.append({"case": "boundary_allocation_by_leverage", "boundary_fee": d2.boundary_fee,
                 "sum_allocation": str(s_ba), "allocation": alloc_str, "passed": pass5,
                 "note": "(d): boundary allocated to bonded field owners by leverage; sums to boundary_fee, deterministic"})
    print(f"[{'ok ' if pass5 else 'FAIL'}] boundary_alloc(d): sum={s_ba} == boundary_fee={d2.boundary_fee}; alloc={alloc_str}")

    verdict = "PASS" if ok else "FAIL"
    print(f"\nVERDICT: {verdict} — link-attributable slashes + the (d) leverage-allocated joint boundary, all trustless")
    print("provenance: EXECUTION_INDEPENDENT (deterministic schema recomputation; trustless given the I1 hash-bind)")
    print("BOUNDARY ALLOCATION (d, RESOLVED — not a policy fork): the joint boundary_fee is allocated to the bonded")
    print("  field owners by leverage (total - local), summing to boundary_fee. (b)/(c) 'charge the composer' would")
    print("  need to identify the composer off-chain -> adjudication -> plutocracy; (d) stays deterministic/trustless.")

    out = Path(__file__).resolve().parent / "results" / "certificate_chain.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "artifact": "certificate_chain (D3 / composition bond) — link vs boundary slash separation",
        "provenance": "EXECUTION_INDEPENDENT (deterministic recomputation; trustless given I1 hash-bind)",
        "depends_on": {"slash": "29108ac", "boundary_fee_live": "b357e84 (M2)"},
        "cases": rows,
        "VERDICT": verdict,
        "boundary_allocation_rule": {
            "decided": "(d) leverage allocation = total_leverage - local_leverage",
            "why_trustless": ("deterministic schema recomputation charged to the schema-named bonded field owners; "
                              "sums to boundary_fee exactly. (b)/(c) 'charge the composer' would require off-chain "
                              "composer identification -> adjudication -> the value/plutocracy regime the type layer escaped"),
            "coloops_bear_full": "leverage 1 = must-disclose, no substitute",
            "loops_bear_zero": "leverage 0 = the geometry certifies the field could not have prevented the obstruction",
            "correction": ("the naive 'restrict witness_gram to cross rows' sums to the total cross-rank, NOT boundary_fee "
                           "(it misses the modulo-internal); total-minus-local is the correct, verified rule"),
        },
        "I1_precondition": "the slash is trustless only if recomputation is bound to receipt.composition_hash",
    }, indent=2) + "\n")
    print(f"artifact: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
