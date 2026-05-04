"""Sprint 15 Phase 0 — search for the cleanest pairwise-clean / global-positive
fee composition under the witness-ready regime.

Trigger condition (all must hold):
  * all induced pairwise sub-compositions: coherence_fee == 0
  * global composition: coherence_fee > 0
  * projective observables (all sub-compositions and global)
  * well-formed for fee (all sub-compositions and global)

Secondary preferences (ranked):
  1. smallest tool count
  2. highest global fee (for visual drama in the demo)
  3. all pairwise exact_regime_conservative (DFD + CHP both hold per pair)
     — bonus, not required

Search strategy:

  PHASE 0A — synthetic hub-and-spoke variants (small, fast):
    - hub with k spokes, k ∈ {2..5}
    - field names: "DFD-violating" (different from/to names) vs
                   "DFD-respecting" (same name `p` on hub and spokes)
    - field counts per tool: minimal

  PHASE 0B — registry-triple scan:
    - for each triple of registry servers, build the global 3-server
      composition and the 3 induced pairwise sub-compositions, check
      the trigger.
    - bounded search (top-N triples by computational cost).

Output:
  papers/composition-doctrine/sprint15_demo/search_results.json
  papers/composition-doctrine/sprint15_demo/search_summary.md
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from bulla.diagnostic import diagnose
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.regime import classify

OUT_DIR = REPO.parent / "papers" / "composition-doctrine" / "sprint15_demo"


# ---- Helpers ----

def induced_pairs(comp: Composition) -> dict[tuple[str, str], Composition]:
    """For each unordered pair of tools, build the induced sub-composition
    containing only those two tools and the edges between them."""
    out: dict[tuple[str, str], Composition] = {}
    tool_map = {t.name: t for t in comp.tools}
    names = sorted(tool_map.keys())
    for a, b in combinations(names, 2):
        members = {a, b}
        sub_tools = (tool_map[a], tool_map[b])
        sub_edges = tuple(
            e for e in comp.edges
            if e.from_tool in members and e.to_tool in members
        )
        sub = Composition(
            name=f"pair_{a}__{b}",
            tools=sub_tools,
            edges=sub_edges,
        )
        out[(a, b)] = sub
    return out


def evaluate_trigger(comp: Composition) -> dict:
    """Return a structured evaluation of whether `comp` satisfies the
    Phase 0 trigger condition. Includes pairwise + global metrics."""
    pairs = induced_pairs(comp)
    pair_results: dict[str, dict] = {}
    pair_fees: list[int] = []
    pair_projective_all = True
    pair_well_formed_all = True
    pair_exact_conservative_all = True
    for label, pair in pairs.items():
        d = diagnose(pair)
        r = classify(pair)
        pair_results[f"{label[0]}-{label[1]}"] = {
            "fee": d.coherence_fee,
            "projective": r.has_projective_observables,
            "well_formed": r.is_well_formed_for_fee,
            "exact_conservative": r.is_exact_regime_conservative,
            "n_edges": len(pair.edges),
        }
        pair_fees.append(d.coherence_fee)
        if not r.has_projective_observables:
            pair_projective_all = False
        if not r.is_well_formed_for_fee:
            pair_well_formed_all = False
        if not r.is_exact_regime_conservative:
            pair_exact_conservative_all = False

    g_diag = diagnose(comp)
    g_r = classify(comp)
    global_result = {
        "fee": g_diag.coherence_fee,
        "projective": g_r.has_projective_observables,
        "well_formed": g_r.is_well_formed_for_fee,
        "exact_conservative": g_r.is_exact_regime_conservative,
        "rank_obs": g_r.rank_obs,
        "rank_internal": g_r.rank_internal,
    }

    trigger_fires = (
        max(pair_fees) == 0
        and g_diag.coherence_fee > 0
        and pair_projective_all
        and pair_well_formed_all
        and g_r.has_projective_observables
        and g_r.is_well_formed_for_fee
    )

    return {
        "name": comp.name,
        "n_tools": len(comp.tools),
        "n_edges": len(comp.edges),
        "trigger_fires": trigger_fires,
        "max_pairwise_fee": max(pair_fees) if pair_fees else 0,
        "global_fee": g_diag.coherence_fee,
        "all_pairs_projective": pair_projective_all,
        "all_pairs_well_formed": pair_well_formed_all,
        "all_pairs_exact_conservative": pair_exact_conservative_all,
        "pair_results": pair_results,
        "global": global_result,
    }


# ---- PHASE 0A: synthetic hub-and-spoke variants ----

def hub_spoke_dfd_violating(n_spokes: int) -> Composition:
    """Hub A with observable field 'x'; each spoke B_i has hidden field 'h_i'.
    Edges: A→B_i with dim from_field='x', to_field='h_i' (DFD-violating).

    This is the original hub-and-spoke from the proof-of-existence."""
    A = ToolSpec(name="A", internal_state=("x",), observable_schema=("x",))
    spokes = tuple(
        ToolSpec(
            name=f"B{i}",
            internal_state=(f"h{i}",),
            observable_schema=(),
        )
        for i in range(1, n_spokes + 1)
    )
    edges = tuple(
        Edge(
            from_tool="A",
            to_tool=f"B{i}",
            dimensions=(SemanticDimension(name=f"ab{i}", from_field="x", to_field=f"h{i}"),),
        )
        for i in range(1, n_spokes + 1)
    )
    return Composition(
        name=f"hub_dfd_violating_{n_spokes}_spokes",
        tools=(A,) + spokes,
        edges=edges,
    )


def hub_spoke_dfd_respecting(n_spokes: int) -> Composition:
    """Hub A with observable field 'p'; each spoke B_i has the SAME field
    name 'p' but hidden. Edges: A→B_i with dim from_field='p', to_field='p'.
    DFD-conservative is preserved per pair AND globally; CHP-conservative
    is preserved per pair but fails globally (A.p as from-side referenced
    n_spokes times).

    Cleaner narrative: 'A has an observable value `p`; each spoke expects a
    hidden version of `p` matched from A. Pairwise the contract is one
    match. Globally, A's single `p` is being claimed equal to n_spokes
    different things at once.'"""
    A = ToolSpec(name="A", internal_state=("p",), observable_schema=("p",))
    spokes = tuple(
        ToolSpec(
            name=f"B{i}",
            internal_state=("p",),
            observable_schema=(),
        )
        for i in range(1, n_spokes + 1)
    )
    edges = tuple(
        Edge(
            from_tool="A",
            to_tool=f"B{i}",
            dimensions=(SemanticDimension(name=f"match_{i}", from_field="p", to_field="p"),),
        )
        for i in range(1, n_spokes + 1)
    )
    return Composition(
        name=f"hub_dfd_respecting_{n_spokes}_spokes",
        tools=(A,) + spokes,
        edges=edges,
    )


def synthetic_candidates() -> list[Composition]:
    out = []
    for k in range(2, 6):  # k=2,3,4,5 spokes
        out.append(hub_spoke_dfd_violating(k))
        out.append(hub_spoke_dfd_respecting(k))
    return out


# ---- PHASE 0B: registry-triple scan ----

def _load_registry_manifests(manifests_dir: Path) -> dict[str, list[dict]]:
    """Inlined manifest loader (matches Sprint 13 cli helper)."""
    manifests: dict[str, list[dict]] = {}
    if not manifests_dir.is_dir():
        return manifests
    for f in sorted(manifests_dir.glob("*.json")):
        if f.name == "coherence.db" or f.stem.startswith("."):
            continue
        try:
            data = json.loads(f.read_text())
            tools = data.get("tools", [])
            if tools:
                manifests[f.stem] = tools
        except (json.JSONDecodeError, KeyError):
            continue
    return manifests


def _build_triple(servers: tuple[str, str, str], manifests: dict) -> Composition:
    """Build a 3-server composition via BullaGuard.from_tools_list."""
    from bulla.guard import BullaGuard
    prefixed: list[dict] = []
    for s in servers:
        for t in manifests[s]:
            p = dict(t)
            p["name"] = f"{s}__{t['name']}"
            prefixed.append(p)
    return BullaGuard.from_tools_list(
        prefixed, name="+".join(servers)
    ).composition


def registry_triple_scan(
    manifests_dir: Path,
    max_triples: int = 60,
) -> list[dict]:
    """For up to `max_triples` server triples, build the 3-server composition
    and check trigger. Servers are sampled deterministically.

    Many real-MCP triples will have nonzero pairwise fees (since most
    real registry pairs already have fee>0). Trigger fires only when all
    three pairwise sub-comps have fee=0 AND the global has fee>0."""
    manifests = _load_registry_manifests(manifests_dir)
    if len(manifests) < 3:
        return []
    servers = sorted(manifests.keys())

    # Prioritize servers with small tool counts for cheaper trigger checks
    server_sizes = sorted(servers, key=lambda s: len(manifests[s]))
    # Take smallest 12 servers; that's C(12,3) = 220 triples — bound
    # by max_triples.
    short_list = server_sizes[:12]

    out: list[dict] = []
    seen = 0
    for triple in combinations(short_list, 3):
        if seen >= max_triples:
            break
        seen += 1
        try:
            comp = _build_triple(triple, manifests)
            ev = evaluate_trigger(comp)
            ev["servers"] = list(triple)
            ev["source"] = "registry_triple"
            out.append(ev)
        except Exception as e:
            print(f"  skip {triple}: {e}", file=sys.stderr)
    return out


# ---- Ranking ----

def rank_candidates(evals: list[dict]) -> list[dict]:
    """Rank evals that pass the trigger. Sort by:
      1. smallest n_tools (ascending)
      2. all pairs exact-conservative (descending — True first)
      3. highest global_fee (descending — more drama)
      4. fewest edges (ascending — simpler structure)
    """
    triggered = [e for e in evals if e["trigger_fires"]]
    triggered.sort(key=lambda e: (
        e["n_tools"],
        not e["all_pairs_exact_conservative"],
        -e["global_fee"],
        e["n_edges"],
    ))
    return triggered


# ---- Main ----

def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Sprint 15 Phase 0 — Search for pairwise-clean / global-positive composition")
    print("=" * 80)

    # PHASE 0A: synthetic
    print()
    print("PHASE 0A — synthetic hub-and-spoke variants")
    syn_evals: list[dict] = []
    for c in synthetic_candidates():
        ev = evaluate_trigger(c)
        ev["source"] = "synthetic"
        syn_evals.append(ev)
        triggered = "✓" if ev["trigger_fires"] else "✗"
        exact_pair = "✓" if ev["all_pairs_exact_conservative"] else " "
        print(f"  {triggered} {c.name:50s} "
              f"n_tools={ev['n_tools']} "
              f"max_pair={ev['max_pairwise_fee']} "
              f"global={ev['global_fee']} "
              f"pair_exact={exact_pair}")

    # PHASE 0B: registry triples
    print()
    print("PHASE 0B — registry triple scan")
    manifests_dir = REPO / "calibration" / "data" / "registry" / "manifests"
    reg_evals = registry_triple_scan(manifests_dir, max_triples=80)
    n_triggered = sum(1 for e in reg_evals if e["trigger_fires"])
    print(f"  scanned {len(reg_evals)} triples, {n_triggered} triggered")
    for e in reg_evals:
        if e["trigger_fires"]:
            print(f"  ✓ {'+'.join(e['servers']):40s} "
                  f"global={e['global_fee']} "
                  f"max_pair={e['max_pairwise_fee']}")

    # Rank
    all_evals = syn_evals + reg_evals
    ranked = rank_candidates(all_evals)
    print()
    print(f"=== Top {min(5, len(ranked))} candidates (trigger=fired) ===")
    for i, c in enumerate(ranked[:5]):
        print(f"  #{i+1}: {c['name']} "
              f"(tools={c['n_tools']}, fee={c['global_fee']}, "
              f"pair_exact={c['all_pairs_exact_conservative']}, "
              f"source={c['source']})")

    # Persist
    out_json = OUT_DIR / "search_results.json"
    out_json.write_text(json.dumps({
        "synthetic": syn_evals,
        "registry": reg_evals,
        "ranked": ranked,
        "top_pick": ranked[0] if ranked else None,
    }, indent=2, default=str))

    out_md_lines = [
        "# Sprint 15 Phase 0 — Search Results",
        "",
        f"## Trigger condition",
        f"- `max(pairwise_fees) == 0`",
        f"- `global_fee > 0`",
        f"- all pairwise + global: projective observables",
        f"- all pairwise + global: well-formed for fee",
        f"",
        f"## Synthetic candidates (n = {len(syn_evals)})",
        "",
        "| Name | tools | max pair fee | global fee | pair exact-c | trigger? |",
        "|---|---|---|---|---|---|",
    ]
    for e in syn_evals:
        out_md_lines.append(
            f"| `{e['name']}` | {e['n_tools']} | {e['max_pairwise_fee']} | "
            f"{e['global_fee']} | "
            f"{'✓' if e['all_pairs_exact_conservative'] else '✗'} | "
            f"{'✓ TRIGGER' if e['trigger_fires'] else ' '} |"
        )
    out_md_lines.append("")
    out_md_lines.append(f"## Registry triple scan (top {len(reg_evals)} sampled)")
    out_md_lines.append("")
    out_md_lines.append(f"Triggered: {n_triggered} / {len(reg_evals)}")
    out_md_lines.append("")
    if any(e["trigger_fires"] for e in reg_evals):
        out_md_lines.append("| Servers | global fee | trigger |")
        out_md_lines.append("|---|---|---|")
        for e in reg_evals:
            if e["trigger_fires"]:
                out_md_lines.append(
                    f"| {'+'.join(e['servers'])} | {e['global_fee']} | ✓ |"
                )
    out_md_lines.append("")
    out_md_lines.append("## Top picks (ranked)")
    out_md_lines.append("")
    out_md_lines.append("Ranking key: (1) smallest n_tools, (2) all pairs exact-conservative, (3) highest global fee, (4) fewest edges.")
    out_md_lines.append("")
    out_md_lines.append("| Rank | Name | tools | fee | pair exact-c | source |")
    out_md_lines.append("|---|---|---|---|---|---|")
    for i, c in enumerate(ranked[:10]):
        out_md_lines.append(
            f"| {i+1} | `{c['name']}` | {c['n_tools']} | "
            f"{c['global_fee']} | "
            f"{'✓' if c['all_pairs_exact_conservative'] else '✗'} | "
            f"{c['source']} |"
        )

    out_md = OUT_DIR / "search_summary.md"
    out_md.write_text("\n".join(out_md_lines) + "\n")

    print()
    print(f"Detailed JSON: {out_json.relative_to(REPO.parent)}")
    print(f"Markdown:      {out_md.relative_to(REPO.parent)}")
    return 0 if ranked else 1


if __name__ == "__main__":
    sys.exit(main())
