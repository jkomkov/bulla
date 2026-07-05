"""Stage 0 (robustness): does seam-graph girth grow with composition ARITY?

The pairwise registry is uniformly girth-3 (stage0_girth.py). But the program
targets multi-vendor stacks. If girth stays ~3 as we compose k>2 servers, the
"bounded-local suffices" deflation is robust across scale; if girth grows with
arity, there is a large-stack regime where the cohomological apparatus could
bind. Deterministic (seeded), read-only.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
BULLA = ROOT / "bulla"
sys.path.insert(0, str(BULLA / "src"))
sys.path.insert(0, str(BULLA))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibration.corpus import ManifestStore  # noqa: E402
from calibration.index import MIN_SCHEMA_FIELDS  # noqa: E402
from bulla.guard import BullaGuard  # noqa: E402
from stage0_girth import girth_of, tool_graph, depth3_recovers_full_obstruction  # noqa: E402

CORPUS_DIR = BULLA / "calibration" / "data" / "registry"
OUT = ROOT / "papers" / "coherence-cliff" / "results" / "dissociation_stage0_arity_girth.json"
SEED = 2026
SAMPLES_PER_K = 40
ARITIES = [2, 3, 4, 5, 6, 8]


def load_servers():
    store = ManifestStore(data_dir=CORPUS_DIR)
    out = {}
    for name in store.list_servers():
        tools = store.get_tools(name)
        if not tools:
            continue
        nfields = sum(
            len(((t.get("inputSchema") or t.get("input_schema") or {}) or {}).get("properties", {}))
            for t in tools
            if isinstance(t.get("inputSchema") or t.get("input_schema") or {}, dict)
        )
        if nfields >= MIN_SCHEMA_FIELDS:
            out[name] = tools
    return out


def build(servers, names):
    prefixed = []
    for n in names:
        for t in servers[n]:
            c = dict(t); c["name"] = f"{n}__{t['name']}"; prefixed.append(c)
    return BullaGuard.from_tools_list(prefixed, name="+".join(names)).composition


def main():
    servers = load_servers()
    names = sorted(servers)
    rng = random.Random(SEED)
    by_k = {}
    for k in ARITIES:
        if k > len(names):
            continue
        girths, cyc, d3full, max_g = [], 0, 0, 0
        seen = set()
        tries = 0
        while len(girths) < SAMPLES_PER_K and tries < SAMPLES_PER_K * 20:
            tries += 1
            combo = tuple(sorted(rng.sample(names, k)))
            if combo in seen:
                continue
            seen.add(combo)
            adj = tool_graph(build(servers, combo))
            g = girth_of(adj)
            if g == float("inf"):
                girths.append(None)  # acyclic
            else:
                girths.append(int(g)); cyc += 1; max_g = max(max_g, int(g))
                spans, _, _ = depth3_recovers_full_obstruction(adj)
                d3full += int(spans)
        finite = [g for g in girths if g is not None]
        dist = {}
        for g in finite:
            dist[str(g)] = dist.get(str(g), 0) + 1
        by_k[str(k)] = {
            "samples": len(girths),
            "n_cyclic": cyc,
            "girth_distribution": dict(sorted(dist.items())),
            "max_girth": max_g if finite else None,
            "frac_cyclic_girth_gt_16": round(sum(g > 16 for g in finite) / cyc, 4) if cyc else 0.0,
            "frac_cyclic_depth3_recovers_full": round(d3full / cyc, 4) if cyc else None,
        }

    any_gt16 = any(v["frac_cyclic_girth_gt_16"] > 0 for v in by_k.values())
    all_d3 = all((v["frac_cyclic_depth3_recovers_full"] in (None, 1.0)) for v in by_k.values())
    verdict = ("GIRTH_GROWS_WITH_ARITY (a large-stack regime may bind)"
               if any_gt16 else
               "GIRTH_STAYS_LOW_ACROSS_ARITY (Outcome 4 robust to composition size)")
    result = {
        "stage": "0_arity_girth_robustness",
        "seed": SEED, "samples_per_k": SAMPLES_PER_K, "n_servers": len(names),
        "by_arity": by_k,
        "any_arity_reaches_girth_gt16": any_gt16,
        "depth3_recovers_full_at_all_arities": all_d3,
        "VERDICT": verdict,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
