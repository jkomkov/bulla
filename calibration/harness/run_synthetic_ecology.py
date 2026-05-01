"""Run the synthetic ecology benchmark: can models detect non-local hiddenness?

Tests whether frontier LLMs can determine which fields are blind spots
in a composition when the answer depends on the composition partner.

Three prompt conditions:
    STRUCTURED  — two named servers, full descriptions, "hidden conventions"
    BARE        — anonymous servers, neutral tool names, stripped descriptions
    FLAT        — all tools in one flat list, no server grouping

The decisive test: for the SAME tool (e.g., file_reader), does the model
give DIFFERENT answers when the composition partner changes?

If it always says "path is hidden" regardless of partner → lexical proxy
If it correctly says "path matters only with data_loader" → structural reasoning
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
from .synthetic_ecology import (
    SERVERS,
    TEST_PAIRS,
    SyntheticGroundTruth,
    compute_ground_truth,
)

RESULTS_DIR = Path(__file__).parent.parent / "data" / "agent_confusion"


# ── Prompt templates ─────────��───────────────────────────────────────

STRUCTURED_PROMPT = """\
You are analyzing a composition of two MCP tool servers for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between the two servers without any schema-level \
indication

Your task: identify which fields in each server have hidden conventions that \
could create silent composition failures when these two servers are used together.

## Server: {left_server}
Tools:
{left_tools_json}

## Server: {right_server}
Tools:
{right_tools_json}

## Instructions
For each server, list the field names that have hidden conventions IN THE \
CONTEXT OF THIS SPECIFIC COMPOSITION. A field is only problematic if its \
convention could differ between these two servers.

Return JSON with this exact structure:
{{
  "{left_server}": ["field1", "field2", ...],
  "{right_server}": ["field3", "field4", ...]
}}

Only include fields whose conventions are genuinely ambiguous or \
under-specified and that appear in BOTH servers (directly or via \
semantic equivalence).\
"""

BARE_PROMPT = """\
You are analyzing a composition of two tool servers for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between the two servers without any schema-level \
indication

Your task: identify which fields have hidden conventions that could create \
silent composition failures when these two servers are used together.

## Server: server_A
Tools:
{left_tools_json}

## Server: server_B
Tools:
{right_tools_json}

## Instructions
For each server, list the field names with hidden conventions IN THE \
CONTEXT OF THIS COMPOSITION.

Return JSON:
{{
  "server_A": ["field1", ...],
  "server_B": ["field3", ...]
}}

Only include fields whose conventions are genuinely ambiguous and that \
could cause issues when the servers are composed.\
"""

FLAT_PROMPT = """\
You are analyzing a set of tools for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention is not observable from the schema alone
- Its convention could differ between tools without any schema-level indication

Your task: identify which fields have hidden conventions that could create \
silent failures when these tools are composed together.

## Tools
{all_tools_json}

## Instructions
List all field names that have hidden conventions. Return a JSON array:
["field1", "field2", ...]

Only include fields whose conventions are genuinely ambiguous.\
"""

CONDITIONS = ["structured", "bare", "flat"]


def anonymize_tool(tool: dict[str, Any], idx: int) -> dict[str, Any]:
    """Strip a tool to bare schema: neutral name, no descriptions."""
    import copy
    tool = copy.deepcopy(tool)
    tool["name"] = f"tool_{idx}"
    tool["description"] = f"Tool {idx}"
    schema = tool.get("inputSchema", tool.get("input_schema", {}))
    for prop in schema.get("properties", {}).values():
        prop.pop("description", None)
    return tool


# ── API ───────────────���──────────────────────────────────────────────

async def call_model(
    client: httpx.AsyncClient, api_key: str, model: str, prompt: str
) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 1024,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = await client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        json=payload, headers=headers, timeout=90.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_flat_list(text: str) -> set[str]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return set()
    try:
        arr = json.loads(text[start:end + 1])
        return set(str(x) for x in arr)
    except json.JSONDecodeError:
        return set()


# ── Main experiment ────────────────────────────────────���─────────────

async def run_synthetic_ecology(
    ground_truths: list[SyntheticGroundTruth],
    api_key: str,
    model: str,
    n_repeats: int = 1,
) -> dict[str, list[dict]]:
    total = len(ground_truths) * len(CONDITIONS) * n_repeats
    print(f"SYNTHETIC ECOLOGY BENCHMARK")
    print(f"  Compositions: {len(ground_truths)}")
    print(f"  Conditions: {CONDITIONS}")
    print(f"  Repeats: {n_repeats}")
    print(f"  Total API calls: {total}")
    print(f"  Model: {model}")
    print()

    all_results: dict[str, list[dict]] = {c: [] for c in CONDITIONS}

    async with httpx.AsyncClient() as client:
        done = 0
        for gt in ground_truths:
            left_tools = SERVERS[gt.left_server]
            right_tools = SERVERS[gt.right_server]

            for cond in CONDITIONS:
                for rep in range(n_repeats):
                    if cond == "structured":
                        prompt = STRUCTURED_PROMPT.format(
                            left_server=gt.left_server,
                            right_server=gt.right_server,
                            left_tools_json=json.dumps(left_tools, indent=2),
                            right_tools_json=json.dumps(right_tools, indent=2),
                        )
                    elif cond == "bare":
                        anon_left = [anonymize_tool(t, i + 1) for i, t in enumerate(left_tools)]
                        anon_right = [anonymize_tool(t, len(left_tools) + i + 1) for i, t in enumerate(right_tools)]
                        prompt = BARE_PROMPT.format(
                            left_tools_json=json.dumps(anon_left, indent=2),
                            right_tools_json=json.dumps(anon_right, indent=2),
                        )
                    elif cond == "flat":
                        all_tools = left_tools + right_tools
                        prompt = FLAT_PROMPT.format(
                            all_tools_json=json.dumps(all_tools, indent=2),
                        )

                    try:
                        response = await call_model(client, api_key, model, prompt)
                    except Exception as e:
                        print(f"  ERROR {gt.left_server}+{gt.right_server}/{cond}: {e}")
                        response = "[]"

                    # Parse
                    if cond == "flat":
                        all_fields = parse_flat_list(response)
                    elif cond == "bare":
                        left_id, right_id = parse_identification_response(
                            response, "server_A", "server_B"
                        )
                        all_fields = left_id | right_id
                    else:
                        left_id, right_id = parse_identification_response(
                            response, gt.left_server, gt.right_server
                        )
                        all_fields = left_id | right_id

                    # Score against ground truth
                    true_positives = all_fields & gt.blind_spot_fields
                    false_positives = all_fields - gt.blind_spot_fields
                    false_negatives = gt.blind_spot_fields - all_fields

                    all_results[cond].append({
                        "pair": f"{gt.left_server}+{gt.right_server}",
                        "fee": gt.fee,
                        "ground_truth": sorted(gt.blind_spot_fields),
                        "identified": sorted(all_fields),
                        "true_positives": sorted(true_positives),
                        "false_positives": sorted(false_positives),
                        "false_negatives": sorted(false_negatives),
                        "correct": len(false_positives) == 0 and len(false_negatives) == 0,
                        "repeat": rep,
                    })
                    done += 1

                    tp_mark = "✓" if true_positives else "—"
                    fp_mark = f"FP:{len(false_positives)}" if false_positives else ""
                    fn_mark = f"FN:{len(false_negatives)}" if false_negatives else ""
                    if done % len(CONDITIONS) == 0:
                        print(
                            f"  [{done:2d}/{total}] "
                            f"{gt.left_server[:12]:12s}+{gt.right_server[:12]:12s} "
                            f"{cond:11s} "
                            f"truth={sorted(gt.blind_spot_fields)} "
                            f"got={sorted(all_fields)[:4]} "
                            f"{tp_mark} {fp_mark} {fn_mark}"
                        )

    return all_results


def print_ecology_analysis(
    results: dict[str, list[dict]],
    ground_truths: list[SyntheticGroundTruth],
) -> None:
    """Analyze the synthetic ecology results."""
    print(f"\n{'═'*80}")
    print("SYNTHETIC ECOLOGY — NON-LOCALITY BENCHMARK")
    print(f"{'═'*80}")

    # Overall accuracy per condition
    print(f"\n  OVERALL ACCURACY (exact match: identified == ground truth)")
    print(f"  {'Condition':<12s} {'Correct':>8s} {'Precision':>10s} {'Recall':>8s}")
    print(f"  {'─'*45}")

    for cond in CONDITIONS:
        subset = results[cond]
        n = len(subset)
        correct = sum(1 for r in subset if r["correct"])
        total_tp = sum(len(r["true_positives"]) for r in subset)
        total_fp = sum(len(r["false_positives"]) for r in subset)
        total_fn = sum(len(r["false_negatives"]) for r in subset)
        precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
        recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
        print(f"  {cond:<12s} {correct}/{n} = {correct/n:.0%}  {precision:>8.0%}  {recall:>8.0%}")

    # The critical test: non-locality
    print(f"\n{'─'*80}")
    print("  NON-LOCALITY TEST")
    print(f"  Does the model give DIFFERENT answers for the SAME tool in different compositions?")
    print(f"{'─'*80}")

    # Group results by left server
    for cond in CONDITIONS:
        print(f"\n  [{cond.upper()}]")
        by_left: dict[str, list[dict]] = {}
        for r in results[cond]:
            left = r["pair"].split("+")[0]
            by_left.setdefault(left, []).append(r)

        for server in sorted(by_left.keys()):
            compositions = by_left[server]
            if len(compositions) < 2:
                continue
            identified_sets = [frozenset(r["identified"]) for r in compositions]
            all_same = len(set(identified_sets)) == 1
            truth_sets = [frozenset(r["ground_truth"]) for r in compositions]
            truth_all_same = len(set(truth_sets)) == 1

            if truth_all_same:
                continue  # Not informative for non-locality

            if all_same:
                marker = "✗ STATIC"
            else:
                # Check if the variation matches ground truth
                matches_truth = all(
                    frozenset(r["identified"]) == frozenset(r["ground_truth"])
                    for r in compositions
                )
                marker = "★ CORRECT VARIATION" if matches_truth else "◐ VARIES (imprecise)"

            print(f"    {server:15s}: {marker}")
            for r in compositions:
                right = r["pair"].split("+")[1]
                truth = r["ground_truth"] or "(none)"
                got = sorted(r["identified"])[:5] or "(none)"
                match = "✓" if r["correct"] else "✗"
                print(f"      + {right:15s} truth={str(truth):20s} got={str(got):20s} {match}")

    # False positive analysis
    print(f"\n{'─'*80}")
    print("  FALSE POSITIVE ANALYSIS")
    print(f"  Which fields does the model flag as hidden when they're NOT blind spots?")
    print(f"{'─'*80}")

    for cond in CONDITIONS:
        fp_counts: dict[str, int] = {}
        for r in results[cond]:
            for fp in r["false_positives"]:
                fp_counts[fp] = fp_counts.get(fp, 0) + 1
        if fp_counts:
            sorted_fps = sorted(fp_counts.items(), key=lambda x: -x[1])
            print(f"  [{cond}]: ", end="")
            print(", ".join(f"{f}({c})" for f, c in sorted_fps[:8]))

    # Per-case detail
    print(f"\n{'─'*80}")
    header = f"  {'Pair':<30s}"
    for cond in CONDITIONS:
        header += f" {cond[:6]:>8s}"
    header += f" {'truth':>15s}"
    print(header)
    print(f"  {'─'*75}")

    for i, gt in enumerate(ground_truths):
        pair = f"{gt.left_server}+{gt.right_server}"
        truth = sorted(gt.blind_spot_fields) if gt.blind_spot_fields else "(none)"
        parts = [f"  {pair[:30]:<30s}"]
        for cond in CONDITIONS:
            r = results[cond][i]
            mark = "✓" if r["correct"] else "✗"
            parts.append(f" {mark:>8s}")
        parts.append(f" {str(truth):>15s}")
        print("".join(parts))

    print(f"\n{'═'*80}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="anthropic/claude-sonnet-4")
    parser.add_argument("--repeats", type=int, default=3,
                        help="Number of repeats per condition (for stability)")
    args = parser.parse_args()

    print("Computing ground truth via BullaGuard...")
    ground_truths = compute_ground_truth()
    for gt in ground_truths:
        bs = sorted(gt.blind_spot_fields) if gt.blind_spot_fields else "(none)"
        print(f"  {gt.left_server:15s} + {gt.right_server:15s} → fee={gt.fee} blind_spots={bs}")
    print()

    start = time.time()
    results = asyncio.run(
        run_synthetic_ecology(ground_truths, args.api_key, args.model, args.repeats)
    )
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.1f}s")

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "synthetic_ecology_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved: {out_path}")

    print_ecology_analysis(results, ground_truths)


if __name__ == "__main__":
    main()
