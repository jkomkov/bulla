#!/usr/bin/env python3
"""Neutral-token ablation for lexical intervention experiment (section 4.5).

Motivation:
    The lexical intervention shows renaming "direction" -> "path" increases
    identification from 0% to 58%. But is this because "path" is specifically
    convention-relevant, or merely because it is a more common/salient token?

    This ablation renames the obscure field to a NEUTRAL common word ("value")
    that is frequent in programming but NOT convention-associated in the
    composition context.  If the neutral rename does NOT boost identification,
    the original result is specifically about convention vocabulary, not generic
    token frequency.

Design:
    For each composition pair from the original experiment, run TWO conditions:

    BASELINE  — original field names (same as main experiment baseline)
    NEUTRAL   — rename the obscure field (e.g. "direction") to "value"

    Compare the neutral-condition identification rate against:
    - baseline obscure-field rate (should be similar if "value" is inert)
    - swap-condition rate from the original experiment (58%, the convention word)

    If neutral << swap, the effect is convention-specific, not frequency-driven.

Usage:
    python bulla/calibration/scripts/run_neutral_ablation.py --api-key $OPENROUTER_API_KEY
    python bulla/calibration/scripts/run_neutral_ablation.py --api-key $OPENROUTER_API_KEY --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BULLA_ROOT = Path(__file__).resolve().parent.parent.parent
CALIBRATION_ROOT = BULLA_ROOT / "calibration"
REGISTRY_DIR = CALIBRATION_ROOT / "data" / "registry"
RESULTS_DIR = CALIBRATION_ROOT / "data" / "agent_confusion"
EXISTING_RESULTS = RESULTS_DIR / "lexical_intervention_results.jsonl"

# The neutral token: common in programming, but not convention-associated
# for file paths, sort directions, pagination cursors, etc.
NEUTRAL_TOKEN = "value"

# ---------------------------------------------------------------------------
# Prompt template — identical to lexical_intervention.py
# ---------------------------------------------------------------------------

INTERVENTION_PROMPT = """\
You are analyzing a composition of two MCP tool servers for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between the two servers without any schema-level \
indication

Your task: identify which fields in each server have hidden conventions that \
could create silent composition failures.

## Server: {left_server}
Tools:
{left_tools_json}

## Server: {right_server}
Tools:
{right_tools_json}

## Instructions
For each server, list the field names that have hidden conventions. \
Return JSON with this exact structure:
{{
  "{left_server}": ["field1", "field2", ...],
  "{right_server}": ["field3", "field4", ...]
}}

Only include fields whose conventions are genuinely ambiguous or \
under-specified by the schema. Do not include fields with obvious semantics.\
"""


# ---------------------------------------------------------------------------
# Load existing intervention results to know which pairs and fields to test
# ---------------------------------------------------------------------------

def load_existing_results() -> list[dict[str, Any]]:
    """Load the original lexical_intervention_results.jsonl."""
    if not EXISTING_RESULTS.exists():
        print(f"ERROR: Cannot find {EXISTING_RESULTS}")
        sys.exit(1)
    rows = []
    for line in EXISTING_RESULTS.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def get_unique_pairs(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract unique (pair_name, canonical_field, obscure_field) from baseline rows."""
    seen = set()
    pairs = []
    for row in results:
        if row["condition"] != "baseline":
            continue
        key = row["pair_name"]
        if key in seen:
            continue
        seen.add(key)
        pairs.append({
            "pair_name": row["pair_name"],
            "canonical_field": row["canonical_field"],
            "obscure_field": row["obscure_field"],
        })
    return pairs


# ---------------------------------------------------------------------------
# Load server tool schemas from manifests
# ---------------------------------------------------------------------------

def load_server_tools() -> dict[str, list[dict[str, Any]]]:
    """Load tool schemas from the registry manifests directory."""
    server_tools: dict[str, list[dict[str, Any]]] = {}
    manifests_dir = REGISTRY_DIR / "manifests"
    for path in sorted(manifests_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list) and tools:
            server_tools[path.stem] = tools
    return server_tools


# ---------------------------------------------------------------------------
# Field renaming (same logic as lexical_intervention.py)
# ---------------------------------------------------------------------------

def rename_field_in_schema(
    tools: list[dict[str, Any]],
    old_name: str,
    new_name: str,
) -> list[dict[str, Any]]:
    """Deep-rename a field across all tool schemas."""
    tools = copy.deepcopy(tools)
    for tool in tools:
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        props = schema.get("properties", {})
        if old_name in props:
            props[new_name] = props.pop(old_name)
            if "description" in props[new_name]:
                props[new_name]["description"] = props[new_name]["description"].replace(
                    old_name, new_name
                )
        required = schema.get("required", [])
        if old_name in required:
            idx = required.index(old_name)
            required[idx] = new_name
    return tools


# ---------------------------------------------------------------------------
# Response parsing (same logic as familiarity.py)
# ---------------------------------------------------------------------------

def parse_identification_response(
    text: str,
    left_server: str,
    right_server: str,
) -> tuple[set[str], set[str]]:
    """Parse model's identification response into per-server field sets."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return set(), set()

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return set(), set()

    left_fields = set(data.get(left_server, []))
    right_fields = set(data.get(right_server, []))
    return left_fields, right_fields


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

async def call_model(
    client: "httpx.AsyncClient",
    api_key: str,
    model: str,
    prompt: str,
) -> str:
    """Single API call via OpenRouter."""
    import httpx  # noqa: F811

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


# ---------------------------------------------------------------------------
# Core experiment
# ---------------------------------------------------------------------------

async def run_neutral_ablation(
    pairs: list[dict[str, Any]],
    server_tools: dict[str, list[dict[str, Any]]],
    api_key: str,
    model: str = "anthropic/claude-sonnet-4",
    dry_run: bool = False,
    neutral_token: str = NEUTRAL_TOKEN,
) -> list[dict[str, Any]]:
    """Run baseline + neutral conditions for each pair."""
    import httpx

    conditions = ["baseline", "neutral"]
    total = len(pairs) * len(conditions)

    print(f"NEUTRAL-TOKEN ABLATION EXPERIMENT")
    print(f"  Neutral token: '{neutral_token}'")
    print(f"  Pairs: {len(pairs)}")
    print(f"  Conditions: {conditions}")
    print(f"  Total API calls: {total}")
    print(f"  Model: {model}")
    print(f"  Dry run: {dry_run}")
    print()

    results: list[dict[str, Any]] = []
    done = 0

    async with httpx.AsyncClient() as client:
        for pair in pairs:
            pair_name = pair["pair_name"]
            obscure_field = pair["obscure_field"]
            canonical_field = pair["canonical_field"]

            # Parse server names from pair_name
            parts = pair_name.split("+")
            if len(parts) != 2:
                print(f"  SKIP {pair_name}: cannot parse server names")
                continue
            left_server, right_server = parts[0], parts[1]

            if left_server not in server_tools or right_server not in server_tools:
                print(f"  SKIP {pair_name}: missing manifest for {left_server} or {right_server}")
                continue

            left_tools = server_tools[left_server]
            right_tools = server_tools[right_server]

            for condition in conditions:
                if condition == "baseline":
                    prompt_left = left_tools
                    prompt_right = right_tools
                elif condition == "neutral":
                    # Rename obscure field -> neutral token in whichever server has it
                    prompt_left = rename_field_in_schema(left_tools, obscure_field, neutral_token)
                    prompt_right = rename_field_in_schema(right_tools, obscure_field, neutral_token)
                else:
                    raise ValueError(f"Unknown condition: {condition}")

                prompt = INTERVENTION_PROMPT.format(
                    left_server=left_server,
                    right_server=right_server,
                    left_tools_json=json.dumps(prompt_left, indent=2),
                    right_tools_json=json.dumps(prompt_right, indent=2),
                )

                if dry_run:
                    response_text = "{}"
                    print(f"  [DRY] {pair_name[:30]:30s} {condition:10s} prompt_len={len(prompt)}")
                else:
                    try:
                        response_text = await call_model(client, api_key, model, prompt)
                    except Exception as e:
                        print(f"  ERROR {pair_name}/{condition}: {e}")
                        response_text = "{}"

                # Parse response
                left_id, right_id = parse_identification_response(
                    response_text, left_server, right_server
                )
                all_identified = left_id | right_id

                # Score: did model identify the obscure field (under whatever name)?
                if condition == "baseline":
                    obscure_identified = obscure_field in all_identified
                    canonical_identified = canonical_field in all_identified
                elif condition == "neutral":
                    obscure_identified = neutral_token in all_identified
                    canonical_identified = canonical_field in all_identified
                else:
                    obscure_identified = False
                    canonical_identified = False

                row = {
                    "pair_name": pair_name,
                    "condition": condition,
                    "canonical_field": canonical_field,
                    "obscure_field": obscure_field,
                    "neutral_token": neutral_token if condition == "neutral" else None,
                    "canonical_identified": canonical_identified,
                    "obscure_identified": obscure_identified,
                    "identified_names": sorted(all_identified),
                }
                results.append(row)
                done += 1

                status = "Y" if obscure_identified else "N"
                c_status = "Y" if canonical_identified else "N"
                print(
                    f"  [{done:3d}/{total}] {pair_name[:30]:30s} "
                    f"{condition:10s} obscure={status} canonical={c_status}"
                )

    return results


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_results(
    ablation_results: list[dict[str, Any]],
    original_results: list[dict[str, Any]],
    neutral_token: str = NEUTRAL_TOKEN,
) -> None:
    """Print comparative analysis."""
    print(f"\n{'=' * 75}")
    print("NEUTRAL-TOKEN ABLATION — ANALYSIS")
    print(f"{'=' * 75}")

    # Ablation rates
    baseline = [r for r in ablation_results if r["condition"] == "baseline"]
    neutral = [r for r in ablation_results if r["condition"] == "neutral"]

    if baseline:
        n = len(baseline)
        obs_rate = sum(1 for r in baseline if r["obscure_identified"]) / n
        can_rate = sum(1 for r in baseline if r["canonical_identified"]) / n
        print(f"\n  BASELINE (N={n})")
        print(f"    Obscure field identified:   {obs_rate:.0%}")
        print(f"    Canonical field identified:  {can_rate:.0%}")

    if neutral:
        n = len(neutral)
        obs_rate_neutral = sum(1 for r in neutral if r["obscure_identified"]) / n
        can_rate_neutral = sum(1 for r in neutral if r["canonical_identified"]) / n
        print(f"\n  NEUTRAL (renamed to '{neutral_token}') (N={n})")
        print(f"    '{neutral_token}' identified:         {obs_rate_neutral:.0%}")
        print(f"    Canonical field identified:  {can_rate_neutral:.0%}")

    # Pull swap-condition rate from original experiment
    original_swap = [r for r in original_results if r["condition"] == "swap"]
    if original_swap:
        n_swap = len(original_swap)
        # In swap, "obscure_role_identified" means the obscure field (now called "path")
        # was identified — this is the convention-word rate
        swap_rate = sum(1 for r in original_swap if r["obscure_role_identified"]) / n_swap
        print(f"\n  ORIGINAL SWAP (renamed to 'path') (N={n_swap})")
        print(f"    'path' (convention word) identified: {swap_rate:.0%}")

    # Comparison
    if neutral and original_swap:
        print(f"\n{'─' * 75}")
        print("  CRITICAL COMPARISON")
        print(f"{'─' * 75}")
        print(f"    Baseline (original name):    {obs_rate:.0%}")
        print(f"    Neutral  ('{neutral_token}'):          {obs_rate_neutral:.0%}")
        print(f"    Swap     ('path'):            {swap_rate:.0%}")
        print()

        # Interpret
        neutral_boost = obs_rate_neutral - obs_rate
        convention_boost = swap_rate - obs_rate

        print(f"    Neutral boost over baseline:    {neutral_boost:+.0%}")
        print(f"    Convention boost over baseline:  {convention_boost:+.0%}")

        if convention_boost > 0.15 and neutral_boost < 0.15:
            print(f"\n    RESULT: Convention-specific effect confirmed.")
            print(f"    The 'path' boost ({convention_boost:+.0%}) is NOT replicated by generic")
            print(f"    common word '{neutral_token}' ({neutral_boost:+.0%}).")
            print(f"    Lexical retrieval is vocabulary-specific, not frequency-driven.")
        elif neutral_boost >= 0.15:
            print(f"\n    RESULT: Generic frequency effect detected.")
            print(f"    The neutral token '{neutral_token}' also boosts identification")
            print(f"    ({neutral_boost:+.0%}), suggesting token frequency contributes.")
            if convention_boost > neutral_boost + 0.15:
                print(f"    However, convention word still shows additional boost")
                print(f"    ({convention_boost:+.0%} vs {neutral_boost:+.0%}).")
        else:
            print(f"\n    RESULT: Inconclusive — need more data.")

    # Per-case breakdown
    print(f"\n{'─' * 75}")
    print("  PER-CASE BREAKDOWN")
    print(f"{'─' * 75}")
    print(f"  {'Pair':<30s} {'Cond':<10s} {'Obscure':>8s} {'Canon':>7s}  {'Identified fields (first 5)'}")
    print(f"  {'─' * 72}")

    for r in ablation_results:
        obs = "Y" if r["obscure_identified"] else "N"
        can = "Y" if r["canonical_identified"] else "N"
        names = r["identified_names"][:5]
        print(f"  {r['pair_name'][:30]:<30s} {r['condition']:<10s} {obs:>8s} {can:>7s}  {names}")

    print(f"\n{'=' * 75}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Neutral-token ablation for lexical intervention experiment"
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="OpenRouter API key (or set OPENROUTER_API_KEY env var)",
    )
    parser.add_argument("--model", default="anthropic/claude-sonnet-4")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip API calls, just validate setup")
    parser.add_argument("--neutral-token", default=NEUTRAL_TOKEN,
                        help=f"Neutral token to test (default: '{NEUTRAL_TOKEN}')")
    args = parser.parse_args()

    neutral_token = args.neutral_token

    if not args.api_key and not args.dry_run:
        print("ERROR: provide --api-key or set OPENROUTER_API_KEY env var")
        sys.exit(1)

    # Load existing results
    print("Loading existing lexical intervention results...")
    original_results = load_existing_results()
    print(f"  {len(original_results)} rows loaded")

    # Extract unique pairs
    pairs = get_unique_pairs(original_results)
    print(f"  {len(pairs)} unique pairs")

    # Load server tool schemas
    print("Loading server manifests...")
    server_tools = load_server_tools()
    print(f"  {len(server_tools)} servers loaded")

    # Validate all pairs have manifests
    missing = set()
    for p in pairs:
        parts = p["pair_name"].split("+")
        for s in parts:
            if s not in server_tools:
                missing.add(s)
    if missing:
        print(f"  WARNING: missing manifests for: {missing}")

    # Run
    start = time.time()
    results = asyncio.run(
        run_neutral_ablation(
            pairs, server_tools, args.api_key, args.model, args.dry_run,
            neutral_token=neutral_token,
        )
    )
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.1f}s")

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "neutral_ablation_results.jsonl"
    with open(out_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Results saved: {out_path}")

    # Analyze
    analyze_results(results, original_results, neutral_token=neutral_token)


if __name__ == "__main__":
    main()
