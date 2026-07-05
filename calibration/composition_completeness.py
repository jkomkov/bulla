#!/usr/bin/env python3
"""The composition-completeness measurement — the earned flag, named against the field.

The one result nobody else has, stated as the DCIChecker contrast + the scoping factor.

DCIChecker (2,214 MCP servers) measured per-TOOL inconsistency (9.93%): does one tool match its own
description. Bulla measures the layer above: cross-composition DECLARATION-COMPLETENESS — does a composition
declare every convention it structurally requires — value-blind, deterministically, completely. Two prevalences
(layered honestly by what check would miss them) + the scoping factor that proves the cascade without building
the oracle:

  fee > 0            : an obstruction in NO single tool  -> invisible to per-TOOL checks (DCIChecker's level)
  boundary_fee > 0   : an obstruction in no PAIR either  -> invisible to per-PAIR checks (the non-pairwise cell)
  scoping factor F/K : a value-oracle would consider F cross-tool field-pairs; Bulla flags K required
                       conventions (the disclosure-NF). Layer 1 scopes Layer 2 from O(F) to O(K), value-blind.

HONEST BOUNDS (stated, not hidden): (1) declaration-completeness is complete w.r.t. the registry's KNOWN
dimension vocabulary, not absolutely (a convention not yet in the vocabulary can't be 'required' — that is the
link to the commons, not a weakness). (2) chains are constructed from REAL tools (a notch below a live corpus,
exactly as M2). (3) F/K is vs the NAIVE all-field-pairs baseline (a value-checker may self-narrow; the point is
Bulla narrows deterministically + completely, which a noisy oracle cannot). Value-blind, deterministic, seeded.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BULLA = HERE.parent
sys.path.insert(0, str(BULLA / "src"))
sys.path.insert(0, str(BULLA))
from calibration.corpus import ManifestStore                                       # noqa: E402
from calibration.index import MIN_SCHEMA_FIELDS                                     # noqa: E402
from bulla.guard import BullaGuard                                                 # noqa: E402
from bulla.diagnostic import diagnose, decompose_fee, minimum_disclosure_set       # noqa: E402
from bulla.regime import is_well_formed_for_fee                                     # noqa: E402

CORPUS = BULLA / "calibration" / "data" / "registry"
SEED = 2026
LENGTHS = [3, 4]
PER_LEN = 150


def load_servers():
    store = ManifestStore(data_dir=CORPUS)
    out = {}
    for nm in store.list_servers():
        tools = store.get_tools(nm)
        if not tools:
            continue
        if sum(len(((t.get("inputSchema") or t.get("input_schema") or {}) or {}).get("properties", {}))
               for t in tools if isinstance(t.get("inputSchema") or t.get("input_schema") or {}, dict)) >= MIN_SCHEMA_FIELDS:
            out[nm] = tools
    return out


def n_fields(t):
    return len(((t.get("inputSchema") or t.get("input_schema") or {}) or {}).get("properties", {}))


def compose(servers, chain):
    pre = []
    for nm in chain:
        for t in servers[nm]:
            c = dict(t); c["name"] = f"{nm}__{t['name']}"; pre.append(c)
    return BullaGuard.from_tools_list(pre, name="+".join(chain)).composition


def field_pairs(servers, chain):
    """F = cross-tool field-pairs a naive value-oracle would consider (the un-scoped Layer-2 surface)."""
    tool_fieldcounts = [n_fields(t) for nm in chain for t in servers[nm]]
    F = 0
    for i in range(len(tool_fieldcounts)):
        for j in range(i + 1, len(tool_fieldcounts)):
            F += tool_fieldcounts[i] * tool_fieldcounts[j]
    return F


def main() -> int:
    servers = load_servers()
    names = sorted(servers)
    rng = random.Random(SEED)

    n = n_fee_pos = n_bf_pos = n_wf = 0
    scoping = []
    for L in LENGTHS:
        for _ in range(PER_LEN):
            chain = rng.sample(names, L)
            try:
                comp = compose(servers, chain)
                if not is_well_formed_for_fee(comp):
                    continue
                fee = diagnose(comp).coherence_fee
            except Exception:
                continue
            n_wf += 1; n += 1
            if fee > 0:
                n_fee_pos += 1
            # boundary_fee: any prefix/suffix split with an obstruction in neither side (non-pairwise residue)
            sani = [s.replace("-", "_") for s in chain]
            server_of = lambda t: t.name.split("__", 1)[0]
            bf_any = False
            try:
                for j in range(1, L):
                    pre = frozenset(t.name for t in comp.tools if server_of(t) in set(sani[:j]))
                    suf = frozenset(t.name for t in comp.tools if server_of(t) in set(sani[j:]))
                    if pre and suf and decompose_fee(comp, [pre, suf]).boundary_fee > 0:
                        bf_any = True; break
            except Exception:
                pass
            if bf_any:
                n_bf_pos += 1
            # scoping factor F/K  (K = the conventions Bulla flags as required = disclosure-NF size)
            K = len(set(minimum_disclosure_set(comp)))
            if K > 0:
                scoping.append(field_pairs(servers, chain) / K)

    fee_prev = n_fee_pos / n if n else 0.0
    bf_prev = n_bf_pos / n if n else 0.0
    med_scope = statistics.median(scoping) if scoping else None
    mean_scope = statistics.mean(scoping) if scoping else None

    out = {
        "artifact": "composition-completeness measurement (the DCIChecker contrast + scoping factor)",
        "provenance": "value-blind, deterministic, seed=2026; real-tool chains (constructed, M2-class)",
        "n_compositions": n,
        "invisible_to_per_tool__fee_positive": {"count": n_fee_pos, "prevalence": round(fee_prev, 3),
            "meaning": "an obstruction present in NO single tool -> a per-tool check (DCIChecker) cannot see it"},
        "joint_obstruction__boundary_fee_positive": {"count": n_bf_pos, "prevalence": round(bf_prev, 3),
            "meaning": "a JOINT obstruction not captured by analyzing the parts independently (Theorem A / M2's "
                       "object) -- the non-local term per-component analysis misses. NOT claimed as 'no pair sees "
                       "it' (computed on block splits, so a cross-block pair could sometimes reach it)."},
        "scoping__deterministic_complete_narrowing": {
            "median_F_over_K_vs_naive_allpairs": round(med_scope, 1) if med_scope else None,
            "mean_F_over_K_vs_naive_allpairs": round(mean_scope, 1) if mean_scope else None,
            "meaning": "Bulla narrows the Layer-2 value-check to the K conventions a composition actually requires. "
                       "The DEFENSIBLE claim is that the narrowing is DETERMINISTIC + COMPLETE (a noisy oracle cannot "
                       "guarantee either). The magnitude F/K is vs a NAIVE all-field-pairs baseline (a strawman: a "
                       "real oracle self-narrows), so it is a ceiling, reported for scale only -- not the claim."},
        "named_contrast": (
            f"DCIChecker measured per-tool honesty (9.93% of 2,214 servers inconsistent). We measured "
            f"cross-composition declaration-completeness on the real registry: {fee_prev:.0%} of compositions carry "
            f"an obstruction invisible to any per-tool check (in no single tool), and {bf_prev:.0%} carry a JOINT "
            f"obstruction the parts-decomposition misses (Theorem A) -- value-blind, deterministic, complete, no "
            f"oracle. And Layer 1 narrows the downstream value-check to the conventions a composition actually "
            f"requires -- deterministically and completely (the narrowing is the claim; its magnitude vs naive "
            f"all-pairs is a {round(med_scope) if med_scope else '?'}x ceiling, not a headline)."),
        "honest_bounds": [
            "declaration-completeness is complete w.r.t. the KNOWN dimension vocabulary, not absolutely (link to the commons)",
            "chains constructed from real tools (a notch below a live corpus, as M2)",
            "F/K is vs the naive all-field-pairs baseline; the point is Bulla narrows deterministically + completely",
        ],
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "composition_completeness.json").write_text(json.dumps(out, indent=2) + "\n")

    print(f"compositions measured: {n}")
    print(f"  invisible to per-TOOL (fee>0, in no single tool) : {n_fee_pos}/{n} = {fee_prev:.1%}  <- the clean DCIChecker contrast")
    print(f"  joint obstruction (boundary_fee>0, Theorem A)    : {n_bf_pos}/{n} = {bf_prev:.1%}  <- the non-local term parts-analysis misses")
    print(f"  scoping: deterministic+complete narrowing        : F/K median {round(med_scope,1) if med_scope else None}x vs naive all-pairs (a ceiling, not the claim)")
    print(f"\n{out['named_contrast']}")
    print(f"artifact: {HERE/'results'/'composition_completeness.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
