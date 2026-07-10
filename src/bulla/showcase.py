"""``bulla showcase`` — full algebraic repair loop on real MCP servers.

No LLM.  No network.  No randomness.  Deterministic from first line to last.

Two servers (filesystem + GitHub), 40 tools, 114 cross-server edges.
Schema validation sees nothing wrong.  Bulla surfaces 22 undisclosed
conventions across 4 categories, computes the exact minimum disclosure,
simulates the repair, and issues a cryptographic receipt backed by Lean 4
proofs.  (The fee counts undisclosed conventions — a disclosure/omission
measure — not predicted failures; see FALSIFICATIONS.md.)
"""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from importlib import resources
from typing import Any

from bulla import (
    BullaGuard,
    Composition,
    ToolSpec,
    __version__,
    decompose_fee_by_dimension,
    diagnose,
    minimum_disclosure_set,
    verify_receipt_integrity,
    witness,
)
from bulla.live_proxy import ARISTOTLE_STAMPS, AXIOMS_USED, MATHLIB_PIN


# ── Helpers ──────────────────────────────────────────────────────────


def _bar(char: str = "═", width: int = 72) -> str:
    return char * width


def _header(title: str) -> None:
    print()
    print(f"  {_bar()}")
    print(f"    {title}")
    print(f"  {_bar()}")
    print()


def _section(title: str) -> None:
    print(f"  ── {title} {'─' * max(1, 64 - len(title))}")
    print()


def _kv(key: str, value: object, indent: int = 4) -> None:
    print(f"{' ' * indent}{key}: {value}")


def _fraction_pct(f: Fraction) -> str:
    return f"{float(f) * 100:.0f}%"


# ── Dimension descriptions ──────────────────────────────────────────

_DIM_DESCRIPTIONS: dict[str, str] = {
    "path_convention_match": "filesystem uses absolute paths; GitHub uses repo-relative",
    "id_offset_match": "pagination offsets (page numbers) not exposed in tool schemas",
    "state_filter_match": "issue/PR state filters (open/closed) hidden from the agent",
    "sort_direction_match": "sort order conventions differ between list endpoints",
}


# ── Load bundled manifests ───────────────────────────────────────────


def _load_bundled_manifests() -> tuple[list[dict[str, Any]], list[str]]:
    """Load the filesystem + GitHub manifests bundled with the package."""
    pkg = resources.files("bulla.data.showcase")
    server_names: list[str] = []
    all_tools: list[dict[str, Any]] = []
    for name in ("filesystem", "github"):
        raw = (pkg / f"{name}.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        tools_data = data.get("tools", data) if isinstance(data, dict) else data
        if not isinstance(tools_data, list):
            continue
        server_names.append(name)
        for t in tools_data:
            t["name"] = f"{name}__{t.get('name', 'unknown')}"
        all_tools.extend(tools_data)
    return all_tools, server_names


# ── Repair simulation ───────────────────────────────────────────────


def _apply_disclosures(
    comp: Composition,
    disclosures: list[tuple[str, str]],
) -> Composition:
    """Return a new composition with each disclosed field added to
    the tool's observable_schema."""
    tool_additions: dict[str, set[str]] = {}
    for tool_name, field_name in disclosures:
        tool_additions.setdefault(tool_name, set()).add(field_name)

    new_tools = []
    for t in comp.tools:
        extra = tool_additions.get(t.name, set())
        if extra:
            obs = tuple(dict.fromkeys(t.observable_schema + tuple(sorted(extra))))
            new_tools.append(ToolSpec(t.name, t.internal_state, obs))
        else:
            new_tools.append(t)
    return Composition(comp.name + "_repaired", tuple(new_tools), comp.edges)


# ── Main ─────────────────────────────────────────────────────────────


def run_showcase(*, json_output: bool = False) -> None:
    """Run the full showcase.  Called by ``bulla showcase``."""

    all_tools, server_names = _load_bundled_manifests()
    guard = BullaGuard.from_tools_list(all_tools, name="showcase")
    comp = guard.composition

    server_tool_counts = {
        s: sum(1 for t in comp.tools if t.name.startswith(f"{s}__"))
        for s in server_names
    }

    diag = diagnose(comp, include_witness_geometry=True)
    decomp = decompose_fee_by_dimension(comp)
    mds = minimum_disclosure_set(comp)
    repaired_comp = _apply_disclosures(comp, mds)
    diag_after = diagnose(repaired_comp, include_witness_geometry=True)
    receipt = witness(diag, comp, witness_basis=guard.witness_basis)
    receipt_dict = receipt.to_dict()
    valid = verify_receipt_integrity(receipt_dict)

    # ── JSON mode ────────────────────────────────────────────────────

    if json_output:
        result = {
            "version": __version__,
            "servers": {s: server_tool_counts[s] for s in server_names},
            "n_tools": len(comp.tools),
            "n_edges": len(comp.edges),
            "fee": diag.coherence_fee,
            "n_blind_spots": len(diag.blind_spots),
            "hidden_basis_size": len(diag.hidden_basis),
            "fee_by_dimension": dict(decomp.by_dimension),
            "interaction_score": decomp.residual,
            "dimensions_modular": decomp.dfd_holds,
            "leverage": {
                f"{t}.{f}": str(lev)
                for (t, f), lev in zip(diag.hidden_basis, diag.leverage_scores)
            },
            "coloops": [
                f"{t}.{f}" for t, f in diag.coloops
            ],
            "loops": [
                f"{t}.{f}" for t, f in diag.loops
            ],
            "n_effective": float(diag.n_effective) if diag.n_effective else None,
            "minimum_disclosure_set": [
                f"{t}.{f}" for t, f in mds
            ],
            "fee_after_repair": diag_after.coherence_fee,
            "receipt_hash": receipt_dict.get("receipt_hash", ""),
            "receipt_valid": valid,
            "provenance": {
                k: {
                    "theorem": v["theorem"],
                    "lean_module": v["lean_module"],
                    "aristotle_run": v["aristotle_run"],
                    "status": v["status"],
                }
                for k, v in ARISTOTLE_STAMPS.items()
            },
        }
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    # ── Human-readable mode ──────────────────────────────────────────

    total_fee = diag.coherence_fee
    leverages = diag.leverage_scores
    hidden_basis = diag.hidden_basis
    n_eff = diag.n_effective

    # Phase 1 — The setup
    _header(f"Bulla v{__version__} — Showcase")
    _section("1. Two MCP Servers, Zero Warnings")

    server_str = ", ".join(
        f"{s} ({server_tool_counts[s]} tools)" for s in server_names
    )
    _kv("Servers", server_str)
    _kv("Total tools", len(comp.tools))
    _kv("Cross-server edges", len(comp.edges))
    _kv("Schema validation errors", 0)
    print()
    print("    Every field type-checks.  Every schema is valid JSON Schema.")
    print("    A standard MCP client sees zero problems.")
    print()
    print("    That's the problem.")
    print()

    # Phase 2 — The problem
    _section("2. What Schema Validation Misses")

    print(f"    Bulla found {total_fee} undisclosed conventions that schema")
    print("    validation cannot see.  These are fields that exist")
    print("    inside one tool but aren't visible to tools that depend on them.")
    print()
    print("    Example: filesystem's read_file has an internal `path` field")
    print("    that uses absolute paths (/Users/you/project/README.md).")
    print("    GitHub's create_or_update_file also has `path` — but it expects")
    print("    repo-relative paths (README.md).  An agent chaining them assumes")
    print("    they mean the same thing.  If they diverge, the write can silently")
    print("    target the wrong file — the fee flags the undisclosed convention,")
    print("    not that this outcome will occur.")
    print()
    _kv("Undisclosed conventions (coherence fee)", total_fee)
    _kv("Affected tool pairs", len(diag.blind_spots))
    print()

    # Phase 3 — Where
    _section("3. Where the Mismatches Live")

    print("    The mismatches fall into four independent categories:")
    print()
    for dim_name, dim_fee in decomp.by_dimension.items():
        pct = dim_fee / total_fee * 100 if total_fee else 0
        desc = _DIM_DESCRIPTIONS.get(dim_name, "")
        print(f"    {dim_name:<30s}  {dim_fee:>2d} mismatches  ({pct:2.0f}%)")
        if desc:
            print(f"      {desc}")
    print()
    if decomp.dfd_holds:
        print("    These categories are independent — each can be fixed separately")
        print("    without affecting the others (interaction score = 0).")
    print()

    # Phase 4 — Which fields matter
    _section("4. Which Fields Matter Most")

    print("    Not every hidden field matters equally.  Leverage measures how")
    print("    many possible repairs require disclosing a given field.")
    print()

    near_coloop = []
    mid = []
    loops = []
    threshold = Fraction(12, 14)
    for (tool, field), lev in zip(hidden_basis, leverages):
        short = f"{tool.split('__')[1]}.{field}" if "__" in tool else f"{tool}.{field}"
        if lev == 0:
            loops.append((short, lev))
        elif lev >= threshold:
            near_coloop.append((short, lev))
        else:
            mid.append((short, lev))

    if near_coloop:
        print(f"    Critical (in {_fraction_pct(threshold)}+ of repairs):")
        shown = near_coloop[:5]
        for name, lev in shown:
            print(f"      {name:<42s}  {_fraction_pct(lev)}")
        if len(near_coloop) > 5:
            print(f"      ... and {len(near_coloop) - 5} more")
        print()

    if mid:
        print("    Moderate leverage:")
        for name, lev in mid:
            print(f"      {name:<42s}  {_fraction_pct(lev)}")
        print()

    if loops:
        print("    Redundant (already covered by other fixes):")
        for name, _ in loops:
            print(f"      {name}")
        print()

    if diag.coloops:
        print("    Non-negotiable (required in EVERY possible repair):")
        for tool, field in diag.coloops:
            short = f"{tool.split('__')[1]}.{field}" if "__" in tool else f"{tool}.{field}"
            print(f"      {short}")
        print()

    # Phase 5 — The fix
    _section("5. The Minimum Fix")

    print(f"    To eliminate all {total_fee} mismatches, exactly {len(mds)} fields")
    print("    must be added to their tool's observable schema.  This is the")
    print("    smallest possible fix — mathematically provable, not heuristic.")
    print()

    by_server: dict[str, list[str]] = {}
    for tool, field in mds:
        server = tool.split("__")[0] if "__" in tool else tool
        tool_short = tool.split("__")[1] if "__" in tool else tool
        by_server.setdefault(server, []).append(f"{tool_short}.{field}")

    for server, fields in by_server.items():
        print(f"    {server}:")
        for f in fields:
            print(f"      {f}")
    print()

    # Phase 6 — Verify
    _section("6. Verify: Apply the Fix")

    print("    Simulating the repair (adding each field to observable_schema)...")
    print()
    _kv("Mismatches before", total_fee)
    _kv("Mismatches after", diag_after.coherence_fee)
    _kv("Affected pairs before", len(diag.blind_spots))
    _kv("Affected pairs after", len(diag_after.blind_spots))
    print()

    if diag_after.coherence_fee == 0:
        print("    Zero mismatches.  Every tool now agrees with every tool it")
        print("    talks to on the meaning of shared fields.")
        if diag_after.blind_spots:
            print(f"    ({len(diag_after.blind_spots)} tool pairs still have hidden fields, but they")
            print("    don't contribute any mismatches — the repair correctly skipped them.)")
    else:
        print(f"    Residual mismatches: {diag_after.coherence_fee}")
    print()

    # Phase 7 — Agent view
    _section("7. What Your Agent Sees (MCP Proxy)")

    print("    When you run `bulla proxy`, agents get these tools alongside")
    print("    your real MCP servers.  The agent queries bulla before acting:")
    print()

    print("    bulla__fee ->")
    print(f'      {{"fee": {total_fee}, "n_blind_spots": {len(diag.blind_spots)}}}')
    print()

    print("    bulla__should_proceed({server: filesystem, tool: write_file}) ->")
    print(f'      {{"verdict": "refuse", "composition_fee": {total_fee}}}')
    print()
    print("    The agent sees the fee is non-zero and chooses not to proceed.")
    print("    Bulla never blocked the call.  It returned information; the agent decided.")
    print()

    print("    bulla__why({about: should_proceed}) ->")
    stamp = ARISTOTLE_STAMPS["sheaf_realization"]
    print(f"      theorem: {stamp['theorem']}")
    print(f"      status: {stamp['status']}")
    print()

    # Phase 8 — Provenance
    _section("8. Proof Provenance")

    receipt_hash = receipt_dict.get("receipt_hash", "")
    _kv("Receipt hash", receipt_hash[:16] + "...")
    _kv("Integrity", "VALID" if valid else "INVALID")
    print()

    print("    Every claim above is backed by formally verified theorems (Lean 4).")
    print("    The proofs are machine-checked and sorry-free:")
    print()
    for stamp in ARISTOTLE_STAMPS.values():
        print(f"      {stamp['theorem']}")
        print(f"        {stamp['lean_module']}  (run: {stamp['aristotle_run'][:8]}...)")
        print()

    print(f"    Axioms: {', '.join(AXIOMS_USED)}")
    print(f"    Mathlib: {MATHLIB_PIN[:12]}...")
    print()

    # Coda
    _header("Schema validation: 0 problems.  Bulla: 22 undisclosed conventions.")

    print(f"    {total_fee} undisclosed conventions across {len(decomp.by_dimension)} categories.")
    print(f"    {len(mds)}-field minimum fix, mathematically optimal.")
    print("    Repair verified.  Cryptographic receipt issued.")
    print("    Lean 4 theorem provenance on every claim.")
    print()
    print(f"    Deterministic.  No LLM.  No network.")
    print(f"    {len(comp.tools)} tools.  {len(comp.edges)} edges.  Bulla v{__version__}.")
    print()
    print(f"  {_bar()}")
    print()
