#!/usr/bin/env python3
"""The seam-declaration receipt — containment (b) the transports omit.

Deep-research (wpczgk0ie, 2026-06-22) named the program's niche: MCP/A2A/ACP/MPP are TRANSPORT + discovery,
not convention agreement — they let never-co-designed agents connect and transact while leaving units /
encodings / path-rooting to the agents (seam GENERATORS). The missing, rate-limiting, unbuilt containment is
an agreed convention layer. This is the today-useful, POSITIVE form of it: given two REAL cross-owner tools,
infer the conventions latent at their seam, flag the ones neither declares, and emit the declaration the seam
needs. No baseline contest, no detection claim — the Declare primitive, which every seam-failure post-mortem
(Mars Climate Orbiter, Ariane 5) recommends and which the shipping transports do not provide.

Reuses the built machinery: ManifestStore (real registry) + diagnose + decompose_fee_by_dimension +
minimum_disclosure_set. Value-blind, deterministic, schema-only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BULLA = HERE.parent                       # .../bulla  (this file lives in .../bulla/calibration)
sys.path.insert(0, str(BULLA / "src"))    # resolves `bulla.*`
sys.path.insert(0, str(BULLA))            # resolves `calibration.*`
from calibration.corpus import ManifestStore                                       # noqa: E402
from calibration.index import MIN_SCHEMA_FIELDS                                     # noqa: E402
from bulla.guard import BullaGuard                                                 # noqa: E402
from bulla.diagnostic import diagnose, decompose_fee_by_dimension, minimum_disclosure_set  # noqa: E402

CORPUS = BULLA / "calibration" / "data" / "registry"


def load_servers():
    store = ManifestStore(data_dir=CORPUS)
    out = {}
    for nm in store.list_servers():
        tools = store.get_tools(nm)
        if not tools:
            continue
        nfields = sum(len(((t.get("inputSchema") or t.get("input_schema") or {}) or {}).get("properties", {}))
                      for t in tools if isinstance(t.get("inputSchema") or t.get("input_schema") or {}, dict))
        if nfields >= MIN_SCHEMA_FIELDS:
            out[nm] = tools
    return out


def compose(servers, a, b):
    pre = []
    for nm in (a, b):
        for t in servers[nm]:
            c = dict(t); c["name"] = f"{nm}__{t['name']}"; pre.append(c)
    return BullaGuard.from_tools_list(pre, name=f"{a}+{b}").composition


def receipt_for(servers, a, b):
    """Return the seam-declaration receipt for the cross-owner pair (a, b), or None if no obstruction/ill-formed."""
    try:
        comp = compose(servers, a, b)
        d = diagnose(comp)
        if d.coherence_fee <= 0:
            return None
        by_dim = decompose_fee_by_dimension(comp).by_dimension
        undeclared = sorted([dim for dim, f in by_dim.items() if f > 0])
        disclosures = sorted({(t, f) for t, f in minimum_disclosure_set(comp)})
        return {"seam": f"{a} <-> {b}", "owners": [a, b], "coherence_fee": d.coherence_fee,
                "undeclared_conventions": undeclared,
                "declare_at": [{"tool": t, "field": f} for t, f in disclosures]}
    except Exception:
        return None


def main() -> int:
    servers = load_servers()
    names = sorted(servers)
    receipts = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r = receipt_for(servers, a, b)
            if r:
                receipts.append(r)
    receipts.sort(key=lambda r: (-r["coherence_fee"], r["seam"]))

    # the substrate is present: how many real cross-owner pairs already carry an undeclared-convention seam
    n_pairs = len(names) * (len(names) - 1) // 2
    n_obstructed = len(receipts)

    out = {
        "artifact": "seam-declaration receipt (containment b — the convention layer the transports omit)",
        "provenance": "value-blind schema computation over the real registry; deterministic",
        "registry_servers": len(names),
        "cross_owner_pairs": n_pairs,
        "pairs_with_undeclared_convention_seam": n_obstructed,
        "fraction": round(n_obstructed / n_pairs, 3) if n_pairs else 0.0,
        "example_receipts": receipts[:8],
        "reading": (
            "Each receipt names a REAL cross-owner seam where a convention is latent in both tools and declared "
            "by neither — exactly the assumption-conflict Garlan calls architectural mismatch and the SIS-not-"
            "followed seam that lost Mars Climate Orbiter. The transports (MCP/A2A/ACP) would let these two agents "
            "compose and act; none of them would surface the undeclared convention. The receipt IS the missing "
            "containment: it forces the meaning explicit at the seam BEFORE a consequential action. Positive and "
            "today-useful; needs no bond and no absent-master world to be worth running."),
    }
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "seam_declaration.json").write_text(json.dumps(out, indent=2) + "\n")

    print(f"real registry: {len(names)} independently-owned servers, {n_pairs} cross-owner pairs")
    print(f"pairs already carrying an UNDECLARED-convention seam: {n_obstructed} ({out['fraction']:.0%})")
    print("\nSAMPLE SEAM-DECLARATION RECEIPTS (the declaration each seam needs, that no transport provides):\n")
    for r in receipts[:6]:
        print(f"  ┌ seam: {r['seam']}   (fee={r['coherence_fee']}: {r['coherence_fee']} undeclared convention(s))")
        print(f"  │ undeclared conventions: {', '.join(r['undeclared_conventions']) or '(structural)'}")
        decl = '; '.join(f"{d['tool']}::{d['field']}" for d in r['declare_at'][:4])
        print(f"  └ DECLARE at: {decl}{' …' if len(r['declare_at']) > 4 else ''}\n")
    print(f"artifact: {HERE/'results'/'seam_declaration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
