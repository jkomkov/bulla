#!/usr/bin/env python3
"""Epistemic Receipt Demo — three cases in one script.

Demo A: Exact regime    — real replay, geometry dividend is provably exact
Demo B: Surrogate regime — synthetic, shows what Bulla reports when it cannot
                           claim exactness (coloops or non-uniform matroid)
Demo C: Comparison      — same composition, with and without epistemic receipt

Run:
    cd bulla/examples/epistemic-demo
    python run_demo.py

All output is deterministic. No network calls, no LLM, no randomness.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from bulla.proxy import BullaProxySession, EpistemicReceipt, RepairGeometry

BASE_DIR = Path(__file__).resolve().parent
MANIFESTS_DIR = BASE_DIR / "manifests"
OUTPUT_DIR = BASE_DIR / "output"


def _load_manifests() -> dict[str, list]:
    result = {}
    for p in sorted(MANIFESTS_DIR.glob("*.json")):
        data = json.loads(p.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list):
            result[p.stem] = tools
    return result


def _separator(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


# ── Demo A: Exact Regime ──────���───────────────────────────────────────


def demo_a_exact() -> dict:
    """Real proxy replay producing regime=exact."""
    _separator("Demo A: Exact Regime")

    server_tools = _load_manifests()
    trace = json.loads((BASE_DIR / "trace_exact.json").read_text())

    session = BullaProxySession(server_tools)
    records = session.replay_trace(trace["calls"])

    call_2 = records[-1]
    rg = call_2.local_diagnostic.repair_geometry
    er = rg.epistemic_view()

    print(f"Composition:  analytics → storage")
    print(f"Hidden seams: path_convention, date_format")
    print()
    print(f"Coherence fee:     {er.fee}")
    print(f"Geometry dividend: {er.geometry_dividend}")
    print(f"Optimal cost:      {er.sigma_star}")
    print(f"Regime:            {er.regime}")
    print()
    print("Recommended repair:")
    for tool, field in er.recommended_repair:
        server, name = tool.split("__", 1)
        print(f"  expose {field} on {name} ({server})")
    print()
    print("What this means:")
    print("  The geometry dividend is EXACT — not an approximation.")
    print("  Bulla can prove that no cheaper repair exists.")

    result = {
        "demo": "A",
        "title": "Exact Regime",
        "composition": "analytics → storage",
        "local_diagnostic": call_2.local_diagnostic.to_dict(),
        "epistemic_receipt": er.to_dict(),
        "disposition": call_2.receipt.disposition.value,
    }
    print(f"\nJSON output:\n{json.dumps(er.to_dict(), indent=2)}")
    return result


# ── Demo B: Surrogate Regime ───────────���──────────────────────────────


def demo_b_surrogate() -> dict:
    """Synthetic construction showing regime=surrogate with coloop_burden.

    Coloops are rare in real MCP compositions (0/373 in the calibration
    corpus), but they arise in compositions with single-path dependencies
    — e.g., a credential field that every downstream tool must receive
    but that has no alternative disclosure path.

    This demo constructs the RepairGeometry directly to show what the
    product output looks like when exactness cannot be claimed.
    """
    _separator("Demo B: Surrogate Regime (coloop burden)")

    rg = RepairGeometry(
        fee=3,
        beta=3,
        repair_entropy=1.0986,
        component_sizes=(3,),
        reachable_basis_count=3,
        stability_ratio=1.0,
        robustness_margin=2.0,
        repair_mode="rigid",
        recommended_basis=(
            ("auth__get_credentials", "token"),
            ("api__fetch_data", "path"),
        ),
        greedy_basis=(
            ("auth__get_credentials", "token"),
            ("api__fetch_data", "path"),
        ),
        field_costs={
            ("auth__get_credentials", "token"): 10.0,
            ("api__fetch_data", "path"): 6.0,
            ("api__fetch_data", "user_id"): 2.0,
        },
        forced_cost=10.0,
        geometry_dividend=6.0,
        sigma_star=16.0,
        residual_regime="uniform_product",
    )
    er = rg.epistemic_view()

    print(f"Composition:  auth → api → database (synthetic)")
    print(f"Hidden seams: token (coloop), path, user_id")
    print()
    print(f"Coherence fee:     {er.fee}")
    print(f"Geometry dividend: {er.geometry_dividend}")
    print(f"Optimal cost:      {er.sigma_star}")
    print(f"Regime:            {er.regime}")
    print(f"Forced cost:       {er.forced_cost}")
    print(f"Downgrade reason:  {er.downgrade}")
    print()
    print("What this means:")
    print("  'token' is a coloop — it must be disclosed in EVERY repair.")
    print("  Its cost (10.0) is unavoidable. The geometry dividend (6.0)")
    print("  is a useful approximation but NOT a provable lower bound.")
    print("  Bulla tells you this honestly: regime=surrogate.")

    result = {
        "demo": "B",
        "title": "Surrogate Regime (coloop burden)",
        "composition": "auth → api → database (synthetic)",
        "note": "Coloops are rare (0/373 in calibration corpus) but real.",
        "epistemic_receipt": er.to_dict(),
    }
    print(f"\nJSON output:\n{json.dumps(er.to_dict(), indent=2)}")
    return result


# ── Demo C: Comparison ──────────────────���─────────────────────────────


def demo_c_comparison() -> dict:
    """Same composition through two lenses: naive vs Bulla."""
    _separator("Demo C: Comparison — What Others See vs What Bulla Sees")

    server_tools = _load_manifests()
    trace = json.loads((BASE_DIR / "trace_exact.json").read_text())

    session = BullaProxySession(server_tools)
    records = session.replay_trace(trace["calls"])

    call_2 = records[-1]
    rg = call_2.local_diagnostic.repair_geometry
    er = rg.epistemic_view()

    print("── Without Bulla ──")
    print()
    print("  analytics.query_events → storage.write_report")
    print("  Schema validation:  PASS  (both accept path: string)")
    print("  Runtime result:     analytics returns /data/events/april-errors.csv")
    print("                      storage writes to /data/events/april-errors.csv")
    print("  Visible problem:    None")
    print("  Actual risk:        path convention mismatch (absolute vs relative)")
    print("                      date format mismatch (ISO-8601 vs epoch)")
    print()
    print("── With Bulla ──")
    print()
    print(f"  Coherence fee:     {er.fee} hidden convention dimensions")
    print(f"  Geometry dividend: {er.geometry_dividend} (cost saved by smart repair)")
    print(f"  Optimal cost:      {er.sigma_star}")
    print(f"  Regime:            {er.regime}")
    print(f"  Disposition:       {call_2.receipt.disposition.value}")
    print()
    print("  Recommended repair:")
    for tool, field in er.recommended_repair:
        server, name = tool.split("__", 1)
        print(f"    expose {field} on {name} ({server})")
    print()
    print("The difference:")
    print("  Others give you a recommendation.")
    print("  Bulla gives you the epistemic status of the recommendation.")

    naive = {
        "schema_validation": "PASS",
        "visible_problems": 0,
        "actual_hidden_risks": 2,
    }
    bulla = {
        "coherence_fee": er.fee,
        "epistemic_receipt": er.to_dict(),
        "disposition": call_2.receipt.disposition.value,
    }
    result = {
        "demo": "C",
        "title": "Comparison",
        "without_bulla": naive,
        "with_bulla": bulla,
    }
    return result


# ── Main ──────────────────────────────────────────────────��───────────


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    results = {
        "exact": demo_a_exact(),
        "surrogate": demo_b_surrogate(),
        "comparison": demo_c_comparison(),
    }

    out_path = OUTPUT_DIR / "demo_output.json"
    out_path.write_text(json.dumps(results, indent=2))

    _separator("Done")
    print(f"Full JSON output written to: {out_path.relative_to(BASE_DIR)}")
    print()
    print("Three demos, one contract:")
    print("  A. Exact    — Bulla proves the repair is optimal")
    print("  B. Surrogate — Bulla admits when it cannot prove optimality")
    print("  C. Comparison — what others miss, what Bulla catches")


if __name__ == "__main__":
    main()
