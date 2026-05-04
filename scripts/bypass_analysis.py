"""Bypass analysis on the canonical 13-server tier3 corpus.

Per the Sprint 2 retrospective: stop banking notes; ship working code on
real data. The bypass question is the operationally-meaningful Frontier 4
finding: a 2-server pair (A, B) with direct fee > 0 might be "bypassable"
if there's a third server C such that fee(A, C) = 0 AND fee(C, B) = 0.
In that case, the path-metric d_S(A, B) = 0 — the disagreement is
"hidden risk" because route-around exists.

This script:
  1. Loads all 13 tier3 manifests
  2. Computes pairwise direct fees between server pairs
  3. For each pair with direct fee > 0, scans for bypass servers
  4. Reports which pairs are bypassable, by which bypass tool

Usage:
  /opt/homebrew/bin/python3.11 bulla/scripts/bypass_analysis.py

Output:
  bulla/calibration/data/tier3/report/bypass_analysis.json
  bulla/calibration/data/tier3/report/bypass_analysis.md
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bulla" / "src"))

from bulla.diagnostic import diagnose
from bulla.infer.mcp import infer_from_manifest
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.parser import load_composition

CORPUS_DIR = REPO / "bulla" / "calibration" / "data" / "tier3"
MANIFEST_DIR = CORPUS_DIR / "manifests"
OUT_JSON = CORPUS_DIR / "report" / "bypass_analysis.json"
OUT_MD = CORPUS_DIR / "report" / "bypass_analysis.md"


def load_server_tools(manifest_path: Path) -> list[ToolSpec]:
    """Convert an MCP manifest to a list of Bulla ToolSpecs.

    Uses bulla.infer.mcp.infer_from_manifest which yields a YAML
    composition; we parse that and extract the tool specs.
    """
    yaml_text = infer_from_manifest(manifest_path)
    comp = load_composition(text=yaml_text)
    return list(comp.tools)


def _classify_all(tools: list[ToolSpec]) -> dict:
    from bulla.infer.mcp import classify_tool_rich, extract_field_infos
    tools_dims = {}
    for t in tools:
        tool_dict = {
            "name": t.name,
            "inputSchema": {
                "type": "object",
                "properties": {f: {"type": "string"} for f in t.internal_state},
                "required": t.observable_schema,
            },
        }
        fis = extract_field_infos(tool_dict)
        tools_dims[t.name] = classify_tool_rich(tool_dict, field_infos=fis)
    return tools_dims


def _edges_from_dicts(edge_dicts: list[dict]) -> tuple[Edge, ...]:
    return tuple(
        Edge(
            from_tool=e["from"],
            to_tool=e["to"],
            dimensions=tuple(
                SemanticDimension(
                    name=d["name"],
                    from_field=d.get("from_field"),
                    to_field=d.get("to_field"),
                )
                for d in e["dimensions"]
            ),
        )
        for e in edge_dicts
    )


def compose_servers(name: str, tools_a: list[ToolSpec], tools_b: list[ToolSpec]) -> Composition:
    """Build a 2-server composition by inferring shared-dimension edges."""
    from bulla.infer.mcp import _find_shared_dimensions
    all_tools = tools_a + tools_b
    tools_dims = _classify_all(all_tools)
    edge_dicts = _find_shared_dimensions(tools_dims)
    return Composition(
        name=name,
        tools=tuple(all_tools),
        edges=_edges_from_dicts(edge_dicts),
    )


def compose_three_via_bridge(
    name: str,
    tools_a: list[ToolSpec],
    tools_c: list[ToolSpec],
    tools_b: list[ToolSpec],
) -> Composition:
    """Build a 3-server composition (A, C, B) with edges only A↔C and C↔B
    — no direct A↔B edges. This corresponds to the gluing G_1 ∪_C G_2 of
    G_1 = (A, C) and G_2 = (C, B) at the shared bridge servers C.

    By Theorem 1 of the Stitching Defect paper, fee of this composition
    equals fee(A,C) + fee(C,B) + rank(δ_C^{A,B}), allowing us to extract
    the Mayer-Vietoris connecting-map rank as:

        rank(δ_C^{A,B}) = fee(A_C_B_no_AB_edges) − fee(A,C) − fee(C,B).
    """
    from bulla.infer.mcp import _find_shared_dimensions
    a_names = {t.name for t in tools_a}
    b_names = {t.name for t in tools_b}
    all_tools = tools_a + tools_c + tools_b
    tools_dims = _classify_all(all_tools)
    edge_dicts = _find_shared_dimensions(tools_dims)
    # Filter out direct A-B edges; keep A-C, C-A, C-B, B-C edges.
    # (C-C internal edges to C should be kept too — they are part of the
    # bridge's internal structure and contribute to fee(A,C) and fee(C,B).)
    filtered = []
    for e in edge_dicts:
        f, t = e["from"], e["to"]
        is_ab = (f in a_names and t in b_names) or (f in b_names and t in a_names)
        if is_ab:
            continue
        filtered.append(e)
    return Composition(
        name=name,
        tools=tuple(all_tools),
        edges=_edges_from_dicts(filtered),
    )


def main():
    print("Bulla Bypass Analysis — tier3 corpus")
    print("=" * 70)
    print(f"Manifest directory: {MANIFEST_DIR.relative_to(REPO)}")
    print()

    manifest_files = sorted(MANIFEST_DIR.glob("*.json"))
    if not manifest_files:
        print(f"ERROR: no JSON manifests found in {MANIFEST_DIR}")
        sys.exit(1)
    print(f"Found {len(manifest_files)} server manifests")

    # Load all server tool sets
    server_tools: dict[str, list[ToolSpec]] = {}
    for path in manifest_files:
        name = path.stem
        try:
            tools = load_server_tools(path)
            server_tools[name] = tools
            print(f"  Loaded {name}: {len(tools)} tools")
        except Exception as e:
            print(f"  SKIP {name}: {e}")

    print()
    server_names = sorted(server_tools.keys())
    n_servers = len(server_names)
    print(f"Total: {n_servers} servers, computing {n_servers * (n_servers - 1) // 2} pairwise fees...")
    print()

    # Step 1: compute direct pairwise fees
    direct_fee: dict[tuple[str, str], int] = {}
    for a, b in combinations(server_names, 2):
        try:
            comp = compose_servers(f"{a}+{b}", server_tools[a], server_tools[b])
            diag = diagnose(comp)
            direct_fee[(a, b)] = diag.coherence_fee
        except Exception as e:
            print(f"  FAIL {a}+{b}: {e}")
            direct_fee[(a, b)] = -1  # mark as failed

    # Print fee summary
    fees_succeeded = [f for f in direct_fee.values() if f >= 0]
    print(f"Successful pairs: {len(fees_succeeded)} / {len(direct_fee)}")
    print(f"Fee distribution:")
    for fee in sorted(set(fees_succeeded)):
        count = sum(1 for f in fees_succeeded if f == fee)
        print(f"  fee={fee}: {count} pairs")
    print()

    # Step 2: for each pair with direct fee > 0, scan for bypass servers via 3 criteria.
    # Helper: lookup direct fee for any ordered or unordered pair.
    def lookup_fee(x: str, y: str) -> int:
        return direct_fee.get((x, y), direct_fee.get((y, x), -1))

    # Per-dimension: get blind-spot dimensions for each pair from diagnostic
    pair_blind_spot_dims: dict[tuple[str, str], set[str]] = {}
    for a, b in combinations(server_names, 2):
        try:
            comp = compose_servers(f"{a}+{b}", server_tools[a], server_tools[b])
            diag = diagnose(comp)
            pair_blind_spot_dims[(a, b)] = {bs.dimension for bs in diag.blind_spots}
        except Exception:
            pair_blind_spot_dims[(a, b)] = set()

    # Per-server: which convention dimensions does each server reference?
    server_dimensions: dict[str, set[str]] = {}
    for s, tools in server_tools.items():
        # Run a 1-server diagnose to extract dimensions; build a self-composition
        try:
            single_comp = Composition(name=s, tools=tuple(tools), edges=tuple())
            single_diag = diagnose(single_comp)
            # Dimensions appear in blind spots / boundary obligations.
            # Easier: use the inferred dims directly.
            from bulla.infer.mcp import classify_tool_rich, extract_field_infos
            dims = set()
            for t in tools:
                tool_dict = {
                    "name": t.name,
                    "inputSchema": {
                        "type": "object",
                        "properties": {f: {"type": "string"} for f in t.internal_state},
                        "required": t.observable_schema,
                    },
                }
                fis = extract_field_infos(tool_dict)
                for d in classify_tool_rich(tool_dict, field_infos=fis):
                    dims.add(d.dimension)
            server_dimensions[s] = dims
        except Exception:
            server_dimensions[s] = set()

    # ────────────────────────────────────────────────────────────────────
    # SPRINT 4 — Conjecture R, Candidate R1
    #
    # R1(A, B; S) := fee(A, B) − max_{C ∈ S \ {A,B}} rank(δ_C^{A,B})
    #
    # where rank(δ_C^{A,B}) is the Mayer-Vietoris connecting-map rank from
    # Theorem 1 of the Stitching Defect paper, computed as:
    #
    #   rank(δ_C^{A,B}) = fee(A_C_B with no direct A-B edges)
    #                       − fee(A,C) − fee(C,B)
    #
    # The empirical residual is:
    #   residual(A, B) := min { fee(A,B), min_C [ fee(A,C) + fee(C,B) ] }
    #
    # Conjecture R1: residual(A, B) == R1(A, B; S) for all positive-fee pairs.
    # Falsification: any single mismatch on the 33 positive-fee tier3 pairs.
    # ────────────────────────────────────────────────────────────────────

    print("Computing rank(δ_C) for all (A, B, C) triples via 3-server compositions...")
    rank_delta: dict[tuple[str, str, str], int] = {}
    for (a, b), fee_ab in direct_fee.items():
        if fee_ab <= 0:
            continue
        for c in server_names:
            if c == a or c == b:
                continue
            f_ac = lookup_fee(a, c)
            f_cb = lookup_fee(c, b)
            if f_ac < 0 or f_cb < 0:
                continue
            try:
                triple = compose_three_via_bridge(
                    f"{a}_via_{c}_to_{b}",
                    server_tools[a], server_tools[c], server_tools[b],
                )
                fee_3tool = diagnose(triple).coherence_fee
                rank_delta[(a, b, c)] = fee_3tool - f_ac - f_cb
            except Exception as e:
                print(f"  FAIL triple {a},{c},{b}: {e}")
                rank_delta[(a, b, c)] = -1
    print(f"  Computed {len(rank_delta)} (A, B, C) triple ranks.")
    print()

    bypass_results: list[dict] = []
    r1_match_count = 0
    r1_mismatch_examples: list[dict] = []

    for (a, b), fee_ab in sorted(direct_fee.items()):
        if fee_ab <= 0:
            continue

        # Criterion 1: zero-fee bypass path A → C → B (strictest; both segments fee=0)
        zero_zero: list[str] = []
        # Criterion 2: lower-fee bypass (any C with fee(A,C) + fee(C,B) < fee(A,B))
        lower_fee: list[tuple[str, int]] = []
        # Criterion 3: per-dimension bypass — for each blind-spot dimension d in (A,B),
        # find C that LACKS dimension d entirely (so C's seam with A or B doesn't include d)
        blind_dims = pair_blind_spot_dims.get((a, b), set())
        # Strip the "_match" suffix to get base dimension names:
        blind_dim_bases = {d.replace("_match", "") for d in blind_dims}
        per_dim_bypass: dict[str, list[str]] = {d: [] for d in blind_dims}

        # R1 candidate: fee(A,B) - max_C rank(δ_C^{A,B})
        rank_deltas_for_pair: list[tuple[str, int]] = []

        for c in server_names:
            if c == a or c == b:
                continue
            f_ac = lookup_fee(a, c)
            f_cb = lookup_fee(c, b)
            if f_ac < 0 or f_cb < 0:
                continue
            path_fee = f_ac + f_cb
            if f_ac == 0 and f_cb == 0:
                zero_zero.append(c)
            if path_fee < fee_ab:
                lower_fee.append((c, path_fee))

            rk = rank_delta.get((a, b, c), -1)
            if rk >= 0:
                rank_deltas_for_pair.append((c, rk))

            # Per-dimension: C lacks any of the blind-spot dimensions?
            c_dims = server_dimensions.get(c, set())
            for blind_dim in blind_dims:
                base = blind_dim.replace("_match", "")
                if base not in c_dims:
                    per_dim_bypass[blind_dim].append(c)

        # Compute R1 and empirical residual; compare.
        max_rank_delta = max((r for _, r in rank_deltas_for_pair), default=0)
        r1_value = fee_ab - max_rank_delta
        empirical_residual = min(
            [fee_ab] + [pf for _, pf in lower_fee]  # min(direct, min path-fees)
        )
        r1_matches = (r1_value == empirical_residual)
        if r1_matches:
            r1_match_count += 1
        else:
            if len(r1_mismatch_examples) < 8:  # cap how many we show
                r1_mismatch_examples.append({
                    "pair": (a, b),
                    "direct_fee": fee_ab,
                    "max_rank_delta": max_rank_delta,
                    "best_bridge_C": next((c for c, r in rank_deltas_for_pair if r == max_rank_delta), None),
                    "R1": r1_value,
                    "empirical_residual": empirical_residual,
                    "diff_R1_minus_residual": r1_value - empirical_residual,
                })

        bypass_results.append({
            "pair": (a, b),
            "direct_fee": fee_ab,
            "n_blind_spots": len(blind_dims),
            "blind_dimensions": sorted(blind_dims),
            "zero_zero_bypass_via": zero_zero,
            "lower_fee_bypass_via": sorted(lower_fee, key=lambda x: x[1]),
            "per_dim_bypass": {d: vs for d, vs in per_dim_bypass.items() if vs},
            "rank_deltas_by_C": dict(rank_deltas_for_pair),
            "max_rank_delta": max_rank_delta,
            "R1": r1_value,
            "empirical_residual": empirical_residual,
            "R1_matches_residual": r1_matches,
        })

    # Step 3: report
    pairs_with_zero_zero = [r for r in bypass_results if r["zero_zero_bypass_via"]]
    pairs_with_lower_fee = [r for r in bypass_results if r["lower_fee_bypass_via"]]
    pairs_with_per_dim = [r for r in bypass_results if r["per_dim_bypass"]]

    print("Bypass analysis results")
    print("=" * 70)
    print(f"  {len(bypass_results)} pairs with direct_fee > 0")
    print()
    print(f"  Criterion 1 (strictest — zero-fee path A → C → B):")
    print(f"    {len(pairs_with_zero_zero)} pairs have a bypass server")
    print()
    print(f"  Criterion 2 (lower-fee — fee(A,C) + fee(C,B) < fee(A,B)):")
    print(f"    {len(pairs_with_lower_fee)} pairs have a bypass server")
    print()
    print(f"  Criterion 3 (per-dimension — C lacks the disputed dimension):")
    print(f"    {len(pairs_with_per_dim)} pairs have at least one dimension bypassable")
    print()

    # ── Sprint 4 — Conjecture R1 verdict ──────────────────────────────
    n_pairs_pos = len(bypass_results)
    print(f"Sprint 4 — Conjecture R1 verdict")
    print("=" * 70)
    print(f"  R1(A, B; S) := fee(A, B) − max_C rank(δ_C^{{A,B}})")
    print(f"  empirical residual := min{{ fee(A,B), min_C [ fee(A,C)+fee(C,B) ] }}")
    print()
    print(f"  R1 matches empirical residual on: {r1_match_count} / {n_pairs_pos} pairs")
    if r1_match_count == n_pairs_pos:
        print(f"  ✓ R1 SURVIVES on all {n_pairs_pos} pairs.")
    else:
        print(f"  ✗ R1 FAILS on {n_pairs_pos - r1_match_count} pairs.")
    print()

    # ─── Sprint 4 — Conjecture R2 (refined named-dim formula) ───────────
    #
    # R2(A, B; S) := fee(A, B) − max_{C ∈ S \ {A,B}} |D(A) ∩ D(B) \ D(C)|
    #
    # where D(X) is the set of named convention dimensions referenced in
    # server X (computed by Bulla's _find_shared_dimensions / classify_tool_rich).
    #
    # Intuition: each shared named dimension between A and B contributes
    # exactly 1 to the coherence fee (when A, B disagree). Routing through
    # a server C that LACKS dimension d means d is invisible at A-C and
    # C-B seams, so the path-fee saves the d-contribution. Maximizing over
    # C: pick the C that lacks the MOST shared dims.
    # ────────────────────────────────────────────────────────────────────

    print(f"Sprint 4 — Conjecture R2 verdict (named-dim formula)")
    print("=" * 70)
    print(f"  R2(A, B; S) := fee(A, B) − max_C |D(A) ∩ D(B) ∖ D(C)|")
    print()

    r2_match_count = 0
    r2_mismatch_examples: list[dict] = []
    for r in bypass_results:
        a, b = r["pair"]
        fee_ab = r["direct_fee"]
        d_a = server_dimensions.get(a, set())
        d_b = server_dimensions.get(b, set())
        d_a_base = {x.replace("_match", "") for x in d_a}
        d_b_base = {x.replace("_match", "") for x in d_b}
        shared = d_a_base & d_b_base
        max_lacking = 0
        best_c = None
        for c in server_names:
            if c == a or c == b:
                continue
            d_c = server_dimensions.get(c, set())
            d_c_base = {x.replace("_match", "") for x in d_c}
            lacking = len(shared - d_c_base)
            if lacking > max_lacking:
                max_lacking = lacking
                best_c = c
        r2_value = fee_ab - max_lacking
        empirical_residual = r["empirical_residual"]
        r["R2"] = r2_value
        r["R2_max_lacking"] = max_lacking
        r["R2_best_C"] = best_c
        r["R2_shared_dims"] = sorted(shared)
        if r2_value == empirical_residual:
            r2_match_count += 1
        else:
            if len(r2_mismatch_examples) < 8:
                r2_mismatch_examples.append({
                    "pair": (a, b),
                    "direct_fee": fee_ab,
                    "shared_dims": sorted(shared),
                    "max_lacking": max_lacking,
                    "best_C": best_c,
                    "R2": r2_value,
                    "empirical_residual": empirical_residual,
                    "diff": r2_value - empirical_residual,
                })

    print(f"  R2 matches empirical residual on: {r2_match_count} / {n_pairs_pos} pairs")
    if r2_match_count == n_pairs_pos:
        print(f"  ✓ R2 SURVIVES on all {n_pairs_pos} pairs.")
        print(f"     CLOSED FORM: residual(A, B; S) = fee(A, B) − max_C |D(A) ∩ D(B) ∖ D(C)|")
    else:
        print(f"  ✗ R2 FAILS on {n_pairs_pos - r2_match_count} pairs.")
        print(f"  Mismatches:")
        for ex in r2_mismatch_examples:
            a, b = ex["pair"]
            print(
                f"    {a} ↔ {b}: fee={ex['direct_fee']}, shared={ex['shared_dims']}, "
                f"max_lacking={ex['max_lacking']} (via {ex['best_C']}), "
                f"R2={ex['R2']}, residual={ex['empirical_residual']}, "
                f"diff={ex['diff']}"
            )
    print()

    if pairs_with_lower_fee:
        print("LOWER-FEE BYPASSES (A → C → B has lower path-fee than direct A↔B):")
        print("-" * 70)
        for r in pairs_with_lower_fee[:10]:
            a, b = r["pair"]
            top_c, top_fee = r["lower_fee_bypass_via"][0]
            print(f"  {a} ↔ {b} (direct={r['direct_fee']}, best path via {top_c} = {top_fee})")
        if len(pairs_with_lower_fee) > 10:
            print(f"  ... and {len(pairs_with_lower_fee) - 10} more")
        print()

    if pairs_with_per_dim:
        print("PER-DIMENSION BYPASSES (some blind-spot dimensions invisible to paths through C):")
        print("-" * 70)
        for r in pairs_with_per_dim[:5]:
            a, b = r["pair"]
            print(f"  {a} ↔ {b} (direct fee={r['direct_fee']}, {r['n_blind_spots']} blind dims):")
            for dim, vs in list(r["per_dim_bypass"].items())[:3]:
                print(f"    Dimension '{dim}' bypassable via: {', '.join(vs[:5])}{'...' if len(vs) > 5 else ''}")
        if len(pairs_with_per_dim) > 5:
            print(f"  ... and {len(pairs_with_per_dim) - 5} more")
        print()

    # Step 4: persist
    output = {
        "n_servers": n_servers,
        "server_names": server_names,
        "server_dimensions": {s: sorted(d) for s, d in server_dimensions.items()},
        "n_pairs_total": len(direct_fee),
        "n_pairs_succeeded": len(fees_succeeded),
        "fee_distribution": {
            str(fee): sum(1 for f in fees_succeeded if f == fee)
            for fee in sorted(set(fees_succeeded))
        },
        "n_pairs_with_positive_fee": len(bypass_results),
        "n_zero_zero_bypassable": len(pairs_with_zero_zero),
        "n_lower_fee_bypassable": len(pairs_with_lower_fee),
        "n_per_dim_bypassable": len(pairs_with_per_dim),
        "bypass_results": [
            {
                "pair": list(r["pair"]),
                "direct_fee": r["direct_fee"],
                "n_blind_spots": r["n_blind_spots"],
                "blind_dimensions": r["blind_dimensions"],
                "zero_zero_bypass_via": r["zero_zero_bypass_via"],
                "lower_fee_bypass_via": [list(t) for t in r["lower_fee_bypass_via"]],
                "per_dim_bypass": r["per_dim_bypass"],
            }
            for r in bypass_results
        ],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(output, indent=2))
    print(f"Results JSON: {OUT_JSON.relative_to(REPO)}")

    # Markdown summary
    md_lines = [
        "# Bypass Analysis on tier3 13-server corpus",
        "",
        "**Question:** for each pair of servers (A, B) with direct coherence fee > 0,",
        "does there exist a third server C such that fee(A, C) = 0 AND fee(C, B) = 0?",
        "If yes, the disagreement between A and B is *bypassable* — the path-metric",
        "`d_S(A, B) = 0` because route-around through C exists. The fee says A and B",
        "directly disagree, but in the larger ecosystem the disagreement is",
        "**operationally invisible** because traffic can be routed through C.",
        "",
        "Per Sprint 2 retrospective: this is the operationally-meaningful Frontier 4 finding.",
        "",
        "## Setup",
        f"- {n_servers} MCP server manifests from tier3",
        f"- {len(direct_fee)} pairwise compositions",
        f"- {len(fees_succeeded)} successfully computed",
        "",
        "## Fee distribution",
        "",
        "| Fee | Count |",
        "|---:|---:|",
    ]
    for fee in sorted(set(fees_succeeded)):
        count = sum(1 for f in fees_succeeded if f == fee)
        md_lines.append(f"| {fee} | {count} |")

    md_lines += [
        "",
        "## Bypass results: three criteria",
        "",
        f"- Pairs with positive direct fee: **{len(bypass_results)}**",
        f"- **Zero-zero bypass** (path A → C → B with both segment fees = 0): {len(pairs_with_zero_zero)}",
        f"- **Lower-fee bypass** (path-fee < direct fee for some C): {len(pairs_with_lower_fee)}",
        f"- **Per-dimension bypass** (some blind-spot dimension absent in some C): {len(pairs_with_per_dim)}",
        "",
        "### Server dimension coverage",
        "",
        "Which convention dimensions does each server reference?",
        "",
        "| Server | Dimensions referenced |",
        "|---|---|",
    ]
    for s in server_names:
        md_lines.append(f"| {s} | {', '.join(sorted(server_dimensions.get(s, set()))) or '*(none)*'} |")

    md_lines += [
        "",
        "### Lower-fee bypasses",
        "",
        "Pairs where a third server C provides a path with `fee(A,C) + fee(C,B) < fee(A,B)`.",
        "These are the operationally-meaningful 'soft' bypasses: routing through C reduces",
        "the path-fee below the direct fee.",
        "",
        "| Server A | Server B | Direct fee | Best bypass C | Best path fee |",
        "|---|---|---:|---|---:|",
    ]
    for r in pairs_with_lower_fee:
        a, b = r["pair"]
        top_c, top_fee = r["lower_fee_bypass_via"][0]
        md_lines.append(f"| {a} | {b} | {r['direct_fee']} | {top_c} | {top_fee} |")

    md_lines += [
        "",
        "### Per-dimension bypasses",
        "",
        "For each pair (A, B), each blind-spot dimension d may be bypassable by routing",
        "through a server C that LACKS dimension d entirely (no field of that convention",
        "type). At any seam involving C, dimension d is invisible — the disagreement on",
        "d doesn't contribute to the path-fee.",
        "",
        "| Server A | Server B | Direct fee | Bypassable dimensions |",
        "|---|---|---:|---|",
    ]
    for r in pairs_with_per_dim:
        a, b = r["pair"]
        dims_summary = "; ".join(f"`{d}` via {len(vs)} servers" for d, vs in r["per_dim_bypass"].items())
        md_lines.append(f"| {a} | {b} | {r['direct_fee']} | {dims_summary} |")

    md_lines += [
        "",
        "### Pairs with no bypass possible (any criterion)",
        "",
        "These pairs are **structurally non-bypassable** by all three criteria.",
        "By Theorem C.2 (revised), disagreements here are detectable on every path.",
        "",
        "| Server A | Server B | Direct fee | Blind dimensions |",
        "|---|---|---:|---|",
    ]
    fully_non_bypassable = [
        r for r in bypass_results
        if not r["zero_zero_bypass_via"]
        and not r["lower_fee_bypass_via"]
        and not r["per_dim_bypass"]
    ]
    for r in fully_non_bypassable:
        a, b = r["pair"]
        dims_str = ", ".join(r["blind_dimensions"][:3]) + ("..." if len(r["blind_dimensions"]) > 3 else "")
        md_lines.append(f"| {a} | {b} | {r['direct_fee']} | {dims_str} |")

    md_lines += [
        "",
        "## Operational implication",
        "",
        "**Bypassable pairs are 'hidden risk.'** The fee correctly identifies a disagreement,",
        "but in the larger ecosystem the agent's traffic can be routed through tools that",
        "don't see the disagreement at any seam. Bulla's standard `audit` returns fee N for",
        "these pairs — but the operational impact depends on whether the agent's actual",
        "composition path crosses the disagreement or routes around it.",
        "",
        "**Non-bypassable pairs are 'topological risk.'** The disagreement cannot be avoided",
        "no matter what intermediate tools the agent uses. These are the genuinely structural",
        "obstructions in the ecosystem.",
        "",
        "This is the Frontier 4 result expressed as something an operator can use:",
        "  bulla audit --bypass-analysis my-mcp-config.json",
        "",
        "would output bypassable vs structural disagreements as separate categories.",
    ]
    OUT_MD.write_text("\n".join(md_lines))
    print(f"Results Markdown: {OUT_MD.relative_to(REPO)}")


if __name__ == "__main__":
    main()
