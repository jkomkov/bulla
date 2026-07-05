"""Auto-discovery spike: how far does ``BullaGuard.from_tools_list``
get on real MCP server manifests, without any manual composition
construction?

For each pair of captured ``tools/list`` responses, this script:

  1. Loads the raw JSON (no `_emits/_consumes_dimensions` hints — the
     real format coming back from any MCP server in the wild).
  2. Concatenates the tools with namespaced names (``server__tool``),
     mirroring what ``BullaLiveProxy.start_backends`` does.
  3. Runs ``BullaGuard.from_tools_list`` + ``diagnose`` and prints the
     discovered composition shape (tool count, edge count, blind-spot
     count, coherence fee).
  4. Where ground-truth receipts exist (canonical-demo), compares the
     discovered fee against the expected fee.

The question this answers: can the live proxy detect real obstructions
*automatically* on real MCP manifests, or does it always require the
manual ``session.add_tools_and_edges`` step the end-to-end test had to
fall back on?

Run from the repo root:

    python bulla/spikes/auto_discovery/run.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bulla.diagnostic import diagnose
from bulla.guard import BullaGuard


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MANIFESTS = REPO_ROOT / "bulla" / "examples"


def _load_tools(manifest_path: Path, server_name: str) -> list[dict[str, Any]]:
    """Load a captured tools/list response and prefix each tool name."""
    raw = json.loads(manifest_path.read_text())
    if isinstance(raw, dict) and "tools" in raw:
        tools = raw["tools"]
    elif isinstance(raw, list):
        tools = raw
    else:
        raise ValueError(f"unexpected manifest shape: {manifest_path}")
    prefixed: list[dict[str, Any]] = []
    for t in tools:
        copy = dict(t)
        copy["name"] = f"{server_name}__{t.get('name', 'unknown')}"
        prefixed.append(copy)
    return prefixed


def _diagnose_pair(
    name: str,
    manifests: dict[str, Path],
    expected_fee: int | None = None,
    expected_blind_spots: int | None = None,
) -> dict[str, Any]:
    print(f"\n── {name} ──")
    all_tools: list[dict[str, Any]] = []
    for server, path in manifests.items():
        tools = _load_tools(path, server)
        print(f"  {server}: {len(tools)} tools from {path.name}")
        all_tools.extend(tools)
    guard = BullaGuard.from_tools_list(all_tools, name=name)
    diag = diagnose(guard.composition)
    n_edges = len(guard.composition.edges)
    n_blind = len(diag.blind_spots)
    fee = diag.coherence_fee
    print(f"  → composition: {len(guard.composition.tools)} tools, "
          f"{n_edges} edges")
    print(f"  → diagnostic: fee={fee}, blind_spots={n_blind}, "
          f"witness_basis.declared={guard._witness_basis.declared}, "
          f"inferred={guard._witness_basis.inferred}, "
          f"unknown={guard._witness_basis.unknown}, "
          f"discovered={guard._witness_basis.discovered}")
    if expected_fee is not None:
        gap = fee - expected_fee
        coverage = (
            (fee / expected_fee * 100.0) if expected_fee > 0 else 100.0
        )
        print(f"  → vs ground truth (fee={expected_fee}): "
              f"gap={gap:+d}, coverage={coverage:.0f}%")
    if expected_blind_spots is not None:
        gap = n_blind - expected_blind_spots
        coverage = (
            (n_blind / expected_blind_spots * 100.0)
            if expected_blind_spots > 0 else 100.0
        )
        print(f"  → vs ground truth (blind_spots={expected_blind_spots}): "
              f"gap={gap:+d}, coverage={coverage:.0f}%")
    return {
        "name": name,
        "n_tools": len(guard.composition.tools),
        "n_edges": n_edges,
        "fee": fee,
        "n_blind_spots": n_blind,
        "expected_fee": expected_fee,
        "expected_blind_spots": expected_blind_spots,
    }


def main() -> None:
    pairs = [
        (
            "canonical-demo: filesystem + github",
            {
                "filesystem": (
                    MANIFESTS / "canonical-demo" / "manifests" / "filesystem.json"
                ),
                "github": (
                    MANIFESTS / "canonical-demo" / "manifests" / "github.json"
                ),
            },
            25, 234,  # from receipts/audit_receipt.json
        ),
        (
            "real_world_audit: filesystem + github",
            {
                "filesystem": (
                    MANIFESTS / "real_world_audit" / "manifests" / "filesystem.json"
                ),
                "github": (
                    MANIFESTS / "real_world_audit" / "manifests" / "github.json"
                ),
            },
            None, None,
        ),
        (
            "real_world_audit: filesystem + puppeteer",
            {
                "filesystem": (
                    MANIFESTS / "real_world_audit" / "manifests" / "filesystem.json"
                ),
                "puppeteer": (
                    MANIFESTS / "real_world_audit" / "manifests" / "puppeteer.json"
                ),
            },
            None, None,
        ),
        (
            "real_world_audit: filesystem + memory",
            {
                "filesystem": (
                    MANIFESTS / "real_world_audit" / "manifests" / "filesystem.json"
                ),
                "memory": (
                    MANIFESTS / "real_world_audit" / "manifests" / "memory.json"
                ),
            },
            None, None,
        ),
        (
            "real_world_audit: github + puppeteer",
            {
                "github": (
                    MANIFESTS / "real_world_audit" / "manifests" / "github.json"
                ),
                "puppeteer": (
                    MANIFESTS / "real_world_audit" / "manifests" / "puppeteer.json"
                ),
            },
            None, None,
        ),
        (
            "epistemic-demo: analytics + storage",
            {
                "analytics": (
                    MANIFESTS / "epistemic-demo" / "manifests" / "analytics.json"
                ),
                "storage": (
                    MANIFESTS / "epistemic-demo" / "manifests" / "storage.json"
                ),
            },
            None, None,
        ),
        (
            "awareness-gap-demo: filesystem + github",
            {
                "filesystem": (
                    MANIFESTS / "awareness-gap-demo" / "manifests" / "filesystem.json"
                ),
                "github": (
                    MANIFESTS / "awareness-gap-demo" / "manifests" / "github.json"
                ),
            },
            None, None,
        ),
    ]
    results = []
    for name, manifests, exp_fee, exp_blind in pairs:
        try:
            r = _diagnose_pair(
                name, manifests,
                expected_fee=exp_fee,
                expected_blind_spots=exp_blind,
            )
            results.append(r)
        except FileNotFoundError as exc:
            print(f"\n── {name} ──")
            print(f"  skipped: {exc}")
        except Exception as exc:
            print(f"\n── {name} ──")
            print(f"  ERROR: {exc!r}")

    print("\n── Summary ──")
    print(f"{'Pair':<55} {'fee':>5} {'blind':>6} {'edges':>6} {'tools':>6}")
    for r in results:
        print(
            f"{r['name']:<55} {r['fee']:>5} "
            f"{r['n_blind_spots']:>6} {r['n_edges']:>6} {r['n_tools']:>6}"
        )
    nontrivial = [r for r in results if r["fee"] > 0 or r["n_blind_spots"] > 0]
    print(
        f"\nNon-trivial obstructions detected: {len(nontrivial)}/{len(results)} pairs"
    )
    if results and results[0]["expected_fee"] is not None:
        coverage = (
            results[0]["fee"] / results[0]["expected_fee"] * 100.0
            if results[0]["expected_fee"] > 0 else 100.0
        )
        print(
            f"Canonical-demo coverage: {coverage:.0f}% "
            f"(observed fee={results[0]['fee']}, expected={results[0]['expected_fee']})"
        )


if __name__ == "__main__":
    main()
