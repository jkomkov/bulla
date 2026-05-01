"""Run the lexical intervention experiment.

Usage:
    python -m calibration.harness.run_lexical_intervention --api-key $KEY

This is THE causal experiment. It proves that lexical form governs access
to hiddenness by intervening on field names while holding structure fixed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from .familiarity import parse_identification_response
from .lexical_intervention import (
    InterventionCase,
    InterventionResult,
    build_intervention_cases,
    build_prompt_for_condition,
    score_intervention,
)

RESULTS_DIR = Path(__file__).parent.parent / "data" / "agent_confusion"
CORPUS_DIR = Path(__file__).parent.parent / "data" / "registry"


def load_cases() -> list[dict[str, Any]]:
    """Load composition cases with ground-truth hidden fields."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
    from bulla.guard import BullaGuard

    manifests_dir = CORPUS_DIR / "manifests"
    pairs_jsonl = CORPUS_DIR / "report" / "schema_structure_pairs.jsonl"

    server_tools: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(manifests_dir.glob("*.json")):
        data = json.loads(path.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list):
            server_tools[path.stem] = tools

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

        left_hidden: set[str] = set()
        right_hidden: set[str] = set()
        for bs in diag.blind_spots:
            if bs.from_tool.startswith(f"{left}__"):
                left_hidden.add(bs.from_field)
            elif bs.from_tool.startswith(f"{right}__"):
                right_hidden.add(bs.from_field)
            if bs.to_tool.startswith(f"{left}__"):
                left_hidden.add(bs.to_field)
            elif bs.to_tool.startswith(f"{right}__"):
                right_hidden.add(bs.to_field)

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


async def run_intervention(
    intervention_cases: list[InterventionCase],
    api_key: str,
    model: str = "anthropic/claude-sonnet-4",
    max_cases: int = 15,
) -> list[InterventionResult]:
    """Run all three conditions on each intervention case."""
    cases = intervention_cases[:max_cases]
    conditions = ["baseline", "swap", "mask"]
    total = len(cases) * len(conditions)

    print(f"LEXICAL INTERVENTION EXPERIMENT")
    print(f"  Cases: {len(cases)}")
    print(f"  Conditions: {conditions}")
    print(f"  Total API calls: {total}")
    print(f"  Model: {model}")
    print()

    results: list[InterventionResult] = []
    done = 0

    async with httpx.AsyncClient() as client:
        for case in cases:
            for condition in conditions:
                # Determine server names for prompt
                left_server = case.canonical_field_server
                right_server = case.obscure_field_server
                if left_server == right_server:
                    # Both in same server — use pair_name parts
                    parts = case.pair_name.split("+")
                    left_server, right_server = parts[0], parts[1]

                prompt = build_prompt_for_condition(
                    case, condition, left_server, right_server
                )

                try:
                    response_text = await call_model(client, api_key, model, prompt)
                except Exception as e:
                    print(f"  ERROR {case.pair_name}/{condition}: {e}")
                    response_text = "{}"

                # Parse response
                if condition == "mask":
                    left_id, right_id = parse_identification_response(
                        response_text, "server_A", "server_B"
                    )
                else:
                    left_id, right_id = parse_identification_response(
                        response_text, left_server, right_server
                    )

                result = score_intervention(
                    case, condition, set(left_id), set(right_id)
                )
                results.append(result)
                done += 1

                if done % 3 == 0:
                    c_mark = "✓" if result.canonical_role_identified else "✗"
                    o_mark = "✓" if result.obscure_role_identified else "✗"
                    print(
                        f"  [{done:3d}/{total}] {case.pair_name[:30]:30s} "
                        f"{condition:8s} canonical={c_mark} obscure={o_mark}"
                    )

    return results


def print_intervention_analysis(results: list[InterventionResult]) -> None:
    """Print the causal analysis."""
    print(f"\n{'═' * 75}")
    print("LEXICAL INTERVENTION — CAUSAL ANALYSIS")
    print(f"{'═' * 75}")

    conditions = ["baseline", "swap", "mask"]
    for cond in conditions:
        subset = [r for r in results if r.condition == cond]
        if not subset:
            continue
        n = len(subset)
        canonical_rate = sum(1 for r in subset if r.canonical_role_identified) / n
        obscure_rate = sum(1 for r in subset if r.obscure_role_identified) / n
        print(f"\n  {cond.upper()} (N={n})")
        print(f"    Canonical-ROLE field identified: {canonical_rate:.0%}")
        print(f"    Obscure-ROLE field identified:   {obscure_rate:.0%}")

    # The critical comparison
    baseline = [r for r in results if r.condition == "baseline"]
    swap = [r for r in results if r.condition == "swap"]
    mask = [r for r in results if r.condition == "mask"]

    if baseline and swap:
        print(f"\n{'─' * 75}")
        print("  CAUSAL TEST: Does identification follow NAME or ROLE?")
        print(f"{'─' * 75}")

        b_canonical = sum(1 for r in baseline if r.canonical_role_identified) / len(baseline)
        b_obscure = sum(1 for r in baseline if r.obscure_role_identified) / len(baseline)
        s_canonical = sum(1 for r in swap if r.canonical_role_identified) / len(swap)
        s_obscure = sum(1 for r in swap if r.obscure_role_identified) / len(swap)

        print(f"\n  If identification follows ROLE (structural reasoning):")
        print(f"    Baseline canonical rate ≈ Swap canonical rate")
        print(f"    Actual: {b_canonical:.0%} vs {s_canonical:.0%}")

        print(f"\n  If identification follows NAME (lexical retrieval):")
        print(f"    Baseline canonical rate >> Swap canonical rate")
        print(f"    Swap obscure rate >> Baseline obscure rate")
        print(f"    (because the obscure field now HAS the canonical name)")
        print(f"    Actual: baseline_obscure={b_obscure:.0%} → swap_obscure={s_obscure:.0%}")

        # Verdict
        follows_name = (b_canonical > s_canonical + 0.15) or (s_obscure > b_obscure + 0.15)
        follows_role = abs(b_canonical - s_canonical) < 0.15 and abs(b_obscure - s_obscure) < 0.15

        if follows_name:
            print(f"\n  ★ IDENTIFICATION FOLLOWS NAME, NOT ROLE")
            print(f"    Lexical form causally governs access to hiddenness.")
        elif follows_role:
            print(f"\n  ○ Identification follows role (structural reasoning).")
        else:
            print(f"\n  ◐ Mixed signal — need more data.")

    if mask:
        m_canonical = sum(1 for r in mask if r.canonical_role_identified) / len(mask)
        m_obscure = sum(1 for r in mask if r.obscure_role_identified) / len(mask)
        print(f"\n  MASK CONDITION (neutral placeholders):")
        print(f"    Canonical-role identified: {m_canonical:.0%}")
        print(f"    Obscure-role identified:   {m_obscure:.0%}")
        if m_canonical < 0.1 and m_obscure < 0.1:
            print(f"    → Total collapse. Lexical cues were doing ALL the work.")

    # Per-case breakdown
    print(f"\n{'─' * 75}")
    print("  PER-CASE BREAKDOWN")
    print(f"{'─' * 75}")
    print(f"  {'Pair':<30s} {'Cond':<9s} {'Canon':>6s} {'Obscure':>8s} {'Names identified'}")
    print(f"{'─' * 75}")

    cases_seen = set()
    for r in results:
        c_mark = "✓" if r.canonical_role_identified else "✗"
        o_mark = "✓" if r.obscure_role_identified else "✗"
        names = sorted(r.identified_names)[:5]
        pair_label = r.pair_name[:30] if r.pair_name not in cases_seen or r.condition == "baseline" else ""
        if r.condition == "baseline":
            cases_seen.add(r.pair_name)
        print(f"  {pair_label:<30s} {r.condition:<9s} {c_mark:>6s} {o_mark:>8s}  {names}")

    print(f"\n{'═' * 75}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="anthropic/claude-sonnet-4")
    parser.add_argument("--max-cases", type=int, default=15)
    args = parser.parse_args()

    print("Loading corpus...")
    raw_cases = load_cases()
    print(f"  Total nonzero-fee cases: {len(raw_cases)}")

    print("Building intervention cases (need both path + non-path hidden)...")
    intervention_cases = build_intervention_cases(raw_cases)
    print(f"  Valid intervention cases: {len(intervention_cases)}")

    if not intervention_cases:
        print("ERROR: No cases with both path-family and non-path hidden fields.")
        return

    start = time.time()
    results = asyncio.run(
        run_intervention(intervention_cases, args.api_key, args.model, args.max_cases)
    )
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.1f}s")

    # Save raw results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "lexical_intervention_results.jsonl"
    with open(out_path, "w") as f:
        for r in results:
            row = {
                "pair_name": r.pair_name,
                "condition": r.condition,
                "canonical_field": r.canonical_field,
                "obscure_field": r.obscure_field,
                "canonical_role_identified": r.canonical_role_identified,
                "obscure_role_identified": r.obscure_role_identified,
                "identified_names": sorted(r.identified_names),
            }
            f.write(json.dumps(row) + "\n")
    print(f"Raw results: {out_path}")

    print_intervention_analysis(results)


if __name__ == "__main__":
    main()
