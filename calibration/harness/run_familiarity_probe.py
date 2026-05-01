"""Run the familiarity-stratified hiddenness identification probe.

Usage:
    python -m bulla.calibration.harness.run_familiarity_probe --pilot
    python -m bulla.calibration.harness.run_familiarity_probe --full

This is the critical experiment for the paper. It shows that hiddenness
identification accuracy correlates with training-data exposure (GitHub stars),
establishing that the capability boundary is memorization, not reasoning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from .familiarity import (
    GITHUB_STARS,
    FamiliarityProbeResult,
    build_identification_prompt,
    compute_server_identification_rates,
    log_familiarity,
    parse_identification_response,
    spearman_correlation,
)

RESULTS_DIR = Path(__file__).parent.parent / "data" / "agent_confusion"
CORPUS_DIR = Path(__file__).parent.parent / "data" / "registry"


def load_pairs_with_ground_truth() -> list[dict[str, Any]]:
    """Load composition pairs and compute ground-truth hidden fields per server."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

    from bulla.guard import BullaGuard

    manifests_dir = CORPUS_DIR / "manifests"
    pairs_jsonl = CORPUS_DIR / "report" / "schema_structure_pairs.jsonl"

    # Load manifests
    server_tools: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(manifests_dir.glob("*.json")):
        data = json.loads(path.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list):
            server_tools[path.stem] = tools

    # Load pairs
    pairs_raw = [
        json.loads(line)
        for line in pairs_jsonl.read_text().splitlines()
        if line.strip()
    ]

    cases = []
    for row in pairs_raw:
        if row["n_edges"] == 0:
            continue

        left, right = row["left_server"], row["right_server"]
        if left not in server_tools or right not in server_tools:
            continue

        # Build composition
        prefixed: list[dict[str, Any]] = []
        for server_name in (left, right):
            for tool in server_tools[server_name]:
                clone = dict(tool)
                clone["name"] = f"{server_name}__{tool['name']}"
                prefixed.append(clone)

        try:
            guard = BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")
            diag = guard.diagnose()
        except Exception:
            continue

        if diag.coherence_fee == 0:
            continue

        # Extract hidden fields per server from blind spots
        left_hidden: set[str] = set()
        right_hidden: set[str] = set()
        for bs in diag.blind_spots:
            # Each blind spot has from_tool, from_field, to_tool, to_field
            from_tool = bs.from_tool
            to_tool = bs.to_tool
            from_field = bs.from_field
            to_field = bs.to_field

            if from_tool.startswith(f"{left}__"):
                left_hidden.add(from_field)
            elif from_tool.startswith(f"{right}__"):
                right_hidden.add(from_field)

            if to_tool.startswith(f"{left}__"):
                left_hidden.add(to_field)
            elif to_tool.startswith(f"{right}__"):
                right_hidden.add(to_field)

        cases.append({
            "pair_name": row["pair_name"],
            "left_server": left,
            "right_server": right,
            "fee": diag.coherence_fee,
            "left_hidden": left_hidden,
            "right_hidden": right_hidden,
            "left_tools": server_tools[left],
            "right_tools": server_tools[right],
        })

    return cases


async def call_model(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    prompt: str,
) -> str:
    """Single API call via OpenRouter."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 2048,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=90.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def run_familiarity_probe(
    cases: list[dict[str, Any]],
    api_key: str,
    model: str = "anthropic/claude-sonnet-4",
    tag: str = "pilot",
) -> list[FamiliarityProbeResult]:
    """Run the identification probe on all cases."""
    total = len(cases)
    print(f"FAMILIARITY-STRATIFIED IDENTIFICATION PROBE")
    print(f"  Cases: {total}")
    print(f"  Model: {model}")
    print()

    results: list[FamiliarityProbeResult] = []
    done = 0

    async with httpx.AsyncClient() as client:
        for case in cases:
            prompt = build_identification_prompt(
                case["left_server"],
                case["right_server"],
                case["left_tools"],
                case["right_tools"],
            )

            try:
                response_text = await call_model(client, api_key, model, prompt)
            except Exception as e:
                print(f"  ERROR {case['pair_name']}: {e}")
                response_text = "{}"

            left_id, right_id = parse_identification_response(
                response_text, case["left_server"], case["right_server"]
            )

            result = FamiliarityProbeResult(
                pair_name=case["pair_name"],
                left_server=case["left_server"],
                right_server=case["right_server"],
                fee=case["fee"],
                left_hidden_fields=frozenset(case["left_hidden"]),
                right_hidden_fields=frozenset(case["right_hidden"]),
                left_identified=frozenset(left_id),
                right_identified=frozenset(right_id),
            )
            results.append(result)
            done += 1

            # Progress
            left_rate = (
                len(left_id & frozenset(case["left_hidden"])) / len(case["left_hidden"])
                if case["left_hidden"] else 0
            )
            right_rate = (
                len(right_id & frozenset(case["right_hidden"])) / len(case["right_hidden"])
                if case["right_hidden"] else 0
            )
            if done % 5 == 0 or done <= 3:
                print(
                    f"  [{done:3d}/{total}] {case['pair_name']:40s} "
                    f"L={left_rate:.0%}({log_familiarity(case['left_server']):.1f}) "
                    f"R={right_rate:.0%}({log_familiarity(case['right_server']):.1f})"
                )

    # Save raw results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"familiarity_probe_{tag}.jsonl"
    with open(out_path, "w") as f:
        for r in results:
            row = {
                "pair_name": r.pair_name,
                "left_server": r.left_server,
                "right_server": r.right_server,
                "fee": r.fee,
                "left_hidden": sorted(r.left_hidden_fields),
                "right_hidden": sorted(r.right_hidden_fields),
                "left_identified": sorted(r.left_identified),
                "right_identified": sorted(r.right_identified),
                "left_stars": GITHUB_STARS.get(r.left_server, 10),
                "right_stars": GITHUB_STARS.get(r.right_server, 10),
            }
            f.write(json.dumps(row) + "\n")
    print(f"\nRaw results: {out_path}")

    return results


def print_familiarity_analysis(results: list[FamiliarityProbeResult]) -> None:
    """Print the correlation analysis."""
    server_results = compute_server_identification_rates(results)

    print(f"\n{'═' * 75}")
    print("FAMILIARITY × IDENTIFICATION RATE")
    print(f"{'═' * 75}")
    print(f"\n{'─'*75}")
    print(f"  {'Server':<30s} {'Stars':>7s} {'log₁₀':>6s} {'ID Rate':>8s} {'Correct':>8s} {'Total':>6s}")
    print(f"{'─'*75}")

    x_log = []
    y_rate = []
    for sr in sorted(server_results, key=lambda s: -s.stars):
        print(
            f"  {sr.server:<30s} {sr.stars:>7d} {sr.log_stars:>6.2f} "
            f"{sr.identification_rate:>7.1%} {sr.correctly_identified:>8d} {sr.total_hidden_fields:>6d}"
        )
        if sr.total_hidden_fields >= 3:  # minimum sample
            x_log.append(sr.log_stars)
            y_rate.append(sr.identification_rate)

    # Correlation test
    if len(x_log) >= 5:
        rho, p = spearman_correlation(x_log, y_rate)
        print(f"\n{'─'*75}")
        print(f"  SPEARMAN CORRELATION (servers with ≥3 hidden fields, N={len(x_log)})")
        print(f"  ρ = {rho:.3f}, p = {p:.4f}")
        print(f"  Significant at α=0.05: {'YES' if p < 0.05 else 'NO'}")
        print(f"{'─'*75}")

        # The claim
        if rho > 0 and p < 0.05:
            print(f"\n  ★ PAPER CLAIM SUPPORTED:")
            print(f"    Hiddenness identification correlates with training exposure.")
            print(f"    The boundary is memorization, not reasoning capacity.")
        elif rho > 0 and p < 0.10:
            print(f"\n  ◐ TREND in expected direction (p < 0.10)")
            print(f"    More data needed for full significance.")
        else:
            print(f"\n  ○ No significant correlation detected.")

    # Quartile analysis
    if len(x_log) >= 8:
        combined = sorted(zip(x_log, y_rate), key=lambda t: t[0])
        mid = len(combined) // 2
        low_half = [y for _, y in combined[:mid]]
        high_half = [y for _, y in combined[mid:]]
        low_mean = sum(low_half) / len(low_half)
        high_mean = sum(high_half) / len(high_half)
        print(f"\n  SPLIT ANALYSIS:")
        print(f"    Low-familiarity  (bottom half): mean ID rate = {low_mean:.1%}")
        print(f"    High-familiarity (top half):    mean ID rate = {high_mean:.1%}")
        print(f"    Gap: {high_mean - low_mean:+.1%}")

    print(f"\n{'═' * 75}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true", help="15 stratified cases")
    parser.add_argument("--full", action="store_true", help="All 240 cases")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--max-cases", type=int, default=None)
    args = parser.parse_args()

    print("Loading corpus with ground-truth hidden fields...")
    cases = load_pairs_with_ground_truth()
    print(f"  Total nonzero-fee cases: {len(cases)}")

    if args.pilot:
        # Stratified sample: pick cases that maximize familiarity diversity
        # Sort by max(left_stars, right_stars) and sample evenly
        import math
        cases.sort(key=lambda c: max(
            GITHUB_STARS.get(c["left_server"], 10),
            GITHUB_STARS.get(c["right_server"], 10),
        ))
        n = args.max_cases or 20
        step = max(1, len(cases) // n)
        cases = cases[::step][:n]
        tag = "pilot"
        print(f"  Pilot sample: {len(cases)} cases (familiarity-stratified)")
    else:
        if args.max_cases:
            cases = cases[:args.max_cases]
        tag = "full"

    api_key = args.api_key
    if not api_key:
        import os
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: provide --api-key or set OPENROUTER_API_KEY")
        return

    start = time.time()
    results = asyncio.run(run_familiarity_probe(cases, api_key, args.model, tag=tag))
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.1f}s")

    print_familiarity_analysis(results)


if __name__ == "__main__":
    main()
