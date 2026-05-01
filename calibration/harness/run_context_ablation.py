"""Context ablation: what in the structured prompt gates the vocabulary phenomenon?

The lexical intervention effect (direction→path = +58%) appears under the
structured two-server prompt but disappears under a flat prompt. This
experiment isolates which prompt features enable the effect.

Ablation conditions (progressive stripping):

  FULL        — two named servers, tool names, descriptions, "hidden conventions"
  ANON        — two servers but anonymous (server_A/server_B), tool_1/tool_2,
                descriptions stripped → tests whether names/descriptions matter
  NO_GROUP    — same tools as FULL but presented as flat list, keep all names
                and descriptions → tests whether relational framing matters
  NEUTRAL_TASK — two named servers, full schemas, but ask "potential integration
                 issues" instead of "hidden conventions" → tests task framing

If FULL >> ANON: names and descriptions carry the effect (contextual cues)
If FULL >> NO_GROUP: relational framing carries the effect (two-server mode)
If FULL >> NEUTRAL_TASK: "hidden conventions" language carries the effect
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import time
from pathlib import Path
from typing import Any

import httpx

from .familiarity import parse_identification_response

RESULTS_DIR = Path(__file__).parent.parent / "data" / "agent_confusion"
CORPUS_DIR = Path(__file__).parent.parent / "data" / "registry"


# ── Prompt templates ──────────────────────────────────────────────────

FULL_PROMPT = """\
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

ANON_PROMPT = """\
You are analyzing a composition of two tool servers for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between the two servers without any schema-level \
indication

Your task: identify which fields in each server have hidden conventions that \
could create silent composition failures.

## Server: server_A
Tools:
{left_tools_json}

## Server: server_B
Tools:
{right_tools_json}

## Instructions
For each server, list the field names that have hidden conventions. \
Return JSON with this exact structure:
{{
  "server_A": ["field1", "field2", ...],
  "server_B": ["field3", "field4", ...]
}}

Only include fields whose conventions are genuinely ambiguous or \
under-specified by the schema. Do not include fields with obvious semantics.\
"""

NO_GROUP_PROMPT = """\
You are analyzing a set of MCP tools for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between tools without any schema-level indication

Your task: identify which fields have hidden conventions that could create \
silent failures when these tools are composed together.

## Tools
{all_tools_json}

## Instructions
List all field names that have hidden conventions. Return a JSON array:
["field1", "field2", ...]

Only include fields whose conventions are genuinely ambiguous or \
under-specified by the schema. Do not include fields with obvious semantics.\
"""

NEUTRAL_TASK_PROMPT = """\
You are reviewing a composition of two MCP tool servers for potential \
integration issues.

When tools from different servers are composed, some fields may have \
conventions that are not fully specified in the schema, which could lead \
to subtle bugs.

Your task: identify which fields in each server might cause problems when \
the servers are used together.

## Server: {left_server}
Tools:
{left_tools_json}

## Server: {right_server}
Tools:
{right_tools_json}

## Instructions
For each server, list the field names that could cause integration issues. \
Return JSON with this exact structure:
{{
  "{left_server}": ["field1", "field2", ...],
  "{right_server}": ["field3", "field4", ...]
}}

Only include fields where the convention is genuinely ambiguous.\
"""


# ── Tool schema manipulation ─────────────────────────────────────────

def anonymize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip tool names to neutral placeholders, remove descriptions."""
    tools = copy.deepcopy(tools)
    for i, tool in enumerate(tools):
        tool["name"] = f"tool_{i + 1}"
        tool["description"] = f"Tool {i + 1}"
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        for prop in schema.get("properties", {}).values():
            prop.pop("description", None)
    return tools


# ── Case loading ─────────────────────────────────────────────────────

def load_filesystem_pairs() -> list[dict[str, Any]]:
    """Load filesystem+X compositions where 'path' is hidden."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
    from bulla.guard import BullaGuard

    manifests_dir = CORPUS_DIR / "manifests"
    pairs_jsonl = CORPUS_DIR / "report" / "schema_structure_pairs.jsonl"

    server_tools: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(manifests_dir.glob("*.json")):
        data = json.loads(p.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list):
            server_tools[p.stem] = tools

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
        if "filesystem" not in (left, right):
            continue
        if left not in server_tools or right not in server_tools:
            continue

        prefixed = []
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

        hidden_fields: set[str] = set()
        for bs in diag.blind_spots:
            hidden_fields.add(bs.from_field)
            hidden_fields.add(bs.to_field)

        if "path" not in hidden_fields:
            continue

        cases.append({
            "pair_name": row["pair_name"],
            "left_server": left,
            "right_server": right,
            "fee": diag.coherence_fee,
            "left_tools": server_tools[left],
            "right_tools": server_tools[right],
            "hidden_fields": sorted(hidden_fields),
        })

    return cases


# ── API call ─────────────────────────────────────────────────────────

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


def parse_flat_field_list(text: str) -> set[str]:
    """Parse a JSON array of field names (for NO_GROUP condition)."""
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


# ── Main experiment ──────────────────────────────────────────────────

CONDITIONS = ["full", "anon", "no_group", "neutral_task"]


async def run_context_ablation(
    cases: list[dict[str, Any]],
    api_key: str,
    model: str,
    max_cases: int = 12,
) -> dict[str, list[dict]]:
    cases = cases[:max_cases]
    total = len(cases) * len(CONDITIONS)

    print(f"CONTEXT ABLATION EXPERIMENT")
    print(f"  Cases: {len(cases)} filesystem compositions")
    print(f"  Conditions: {CONDITIONS}")
    print(f"  Total API calls: {total}")
    print(f"  Model: {model}")
    print()

    all_results: dict[str, list[dict]] = {c: [] for c in CONDITIONS}

    async with httpx.AsyncClient() as client:
        done = 0
        for case in cases:
            for cond in CONDITIONS:
                left_tools = case["left_tools"]
                right_tools = case["right_tools"]

                if cond == "full":
                    prompt = FULL_PROMPT.format(
                        left_server=case["left_server"],
                        right_server=case["right_server"],
                        left_tools_json=json.dumps(left_tools, indent=2),
                        right_tools_json=json.dumps(right_tools, indent=2),
                    )
                elif cond == "anon":
                    anon_left = anonymize_tools(left_tools)
                    anon_right = anonymize_tools(right_tools)
                    prompt = ANON_PROMPT.format(
                        left_tools_json=json.dumps(anon_left, indent=2),
                        right_tools_json=json.dumps(anon_right, indent=2),
                    )
                elif cond == "no_group":
                    all_tools = left_tools + right_tools
                    prompt = NO_GROUP_PROMPT.format(
                        all_tools_json=json.dumps(all_tools, indent=2),
                    )
                elif cond == "neutral_task":
                    prompt = NEUTRAL_TASK_PROMPT.format(
                        left_server=case["left_server"],
                        right_server=case["right_server"],
                        left_tools_json=json.dumps(left_tools, indent=2),
                        right_tools_json=json.dumps(right_tools, indent=2),
                    )

                try:
                    response = await call_model(client, api_key, model, prompt)
                except Exception as e:
                    print(f"  ERROR {case['pair_name']}/{cond}: {e}")
                    response = "[]"

                # Parse depending on format
                if cond == "no_group":
                    all_fields = parse_flat_field_list(response)
                    path_identified = "path" in all_fields
                elif cond == "anon":
                    left_id, right_id = parse_identification_response(
                        response, "server_A", "server_B"
                    )
                    all_fields = left_id | right_id
                    path_identified = "path" in all_fields
                else:
                    left_id, right_id = parse_identification_response(
                        response, case["left_server"], case["right_server"]
                    )
                    all_fields = left_id | right_id
                    path_identified = "path" in all_fields

                # Check all hidden fields
                hidden_identified = {
                    f for f in case["hidden_fields"] if f in all_fields
                }

                all_results[cond].append({
                    "pair_name": case["pair_name"],
                    "path_identified": path_identified,
                    "hidden_identified": sorted(hidden_identified),
                    "all_identified": sorted(all_fields),
                    "ground_truth_hidden": case["hidden_fields"],
                })
                done += 1

                mark = "✓" if path_identified else "✗"
                if done % len(CONDITIONS) == 0:
                    print(
                        f"  [{done:2d}/{total}] "
                        f"{case['pair_name'][:35]:35s} "
                        f"{cond:13s} path={mark}"
                    )

    return all_results


def print_ablation_analysis(results: dict[str, list[dict]]) -> None:
    """Print the context ablation results."""
    print(f"\n{'═'*75}")
    print("CONTEXT ABLATION RESULTS")
    print(f"{'═'*75}")
    print(f"\n  Question: What in the structured prompt enables the vocabulary phenomenon?")
    print()

    # Path identification rates
    print(f"  {'Condition':<15s} {'path ID rate':>12s} {'Δ from full':>12s} {'avg fields ID':>15s}")
    print(f"  {'─'*55}")

    full_rate = 0
    for cond in CONDITIONS:
        subset = results[cond]
        n = len(subset)
        path_hits = sum(1 for r in subset if r["path_identified"])
        path_rate = path_hits / n if n > 0 else 0
        avg_fields = sum(len(r["hidden_identified"]) for r in subset) / n if n > 0 else 0

        if cond == "full":
            full_rate = path_rate
            delta = "—"
        else:
            delta = f"{path_rate - full_rate:+.0%}"

        print(f"  {cond:<15s} {path_hits}/{n} = {path_rate:.0%}{delta:>10s}{avg_fields:>12.1f}")

    # Per-field breakdown across conditions
    print(f"\n{'─'*75}")
    print(f"  PER-FIELD IDENTIFICATION RATES BY CONDITION")
    print(f"{'─'*75}")

    # Collect all hidden field names
    all_hidden = set()
    for cond_results in results.values():
        for r in cond_results:
            all_hidden.update(r["ground_truth_hidden"])

    field_rates: dict[str, dict[str, float]] = {}
    for field in sorted(all_hidden):
        field_rates[field] = {}
        for cond in CONDITIONS:
            subset = results[cond]
            appearances = sum(1 for r in subset if field in r["ground_truth_hidden"])
            if appearances == 0:
                continue
            hits = sum(
                1 for r in subset
                if field in r["ground_truth_hidden"] and field in r["hidden_identified"]
            )
            field_rates[field][cond] = hits / appearances

    header = f"  {'Field':<20s}" + "".join(f"{c:>13s}" for c in CONDITIONS)
    print(header)
    print(f"  {'─'*70}")
    for field in sorted(field_rates.keys()):
        rates = field_rates[field]
        parts = [f"  {field:<20s}"]
        for cond in CONDITIONS:
            if cond in rates:
                parts.append(f"{rates[cond]:>12.0%} ")
            else:
                parts.append(f"{'—':>13s}")
        print("".join(parts))

    # Per-case detail
    print(f"\n{'─'*75}")
    print(f"  {'Pair':<35s}", end="")
    for cond in CONDITIONS:
        print(f" {cond[:6]:>7s}", end="")
    print()
    print(f"  {'─'*70}")

    n_cases = len(results["full"])
    for i in range(n_cases):
        pair = results["full"][i]["pair_name"]
        parts = [f"  {pair[:35]:<35s}"]
        for cond in CONDITIONS:
            mark = "✓" if results[cond][i]["path_identified"] else "✗"
            parts.append(f" {mark:>7s}")
        print("".join(parts))

    print(f"\n{'═'*75}")

    # Interpretation
    print(f"\n  INTERPRETATION:")
    rates_by_cond = {}
    for cond in CONDITIONS:
        subset = results[cond]
        n = len(subset)
        rates_by_cond[cond] = sum(1 for r in subset if r["path_identified"]) / n if n else 0

    full_r = rates_by_cond["full"]
    anon_r = rates_by_cond["anon"]
    nogrp_r = rates_by_cond["no_group"]
    neut_r = rates_by_cond["neutral_task"]

    if full_r - anon_r > 0.15:
        print(f"  ★ NAMES/DESCRIPTIONS matter: full ({full_r:.0%}) >> anon ({anon_r:.0%})")
        print(f"    Tool names and descriptions carry significant contextual signal.")
    elif full_r - anon_r < -0.15:
        print(f"  ○ Surprisingly, anonymous schemas do BETTER ({anon_r:.0%} vs {full_r:.0%})")
    else:
        print(f"  ◐ Names/descriptions have limited effect: full ({full_r:.0%}) ≈ anon ({anon_r:.0%})")

    if full_r - nogrp_r > 0.15:
        print(f"  ★ RELATIONAL FRAMING matters: full ({full_r:.0%}) >> no_group ({nogrp_r:.0%})")
        print(f"    The two-server grouping activates a convention-checking mode.")
    elif full_r - nogrp_r < -0.15:
        print(f"  ○ Flat presentation does BETTER ({nogrp_r:.0%} vs {full_r:.0%})")
    else:
        print(f"  ◐ Relational framing has limited effect: full ({full_r:.0%}) ≈ no_group ({nogrp_r:.0%})")

    if full_r - neut_r > 0.15:
        print(f"  ★ TASK LANGUAGE matters: full ({full_r:.0%}) >> neutral ({neut_r:.0%})")
        print(f"    The 'hidden conventions' framing is doing work.")
    elif full_r - neut_r < -0.15:
        print(f"  ○ Neutral task does BETTER ({neut_r:.0%} vs {full_r:.0%})")
    else:
        print(f"  ◐ Task language has limited effect: full ({full_r:.0%}) ≈ neutral ({neut_r:.0%})")

    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="anthropic/claude-sonnet-4")
    parser.add_argument("--max-cases", type=int, default=12)
    args = parser.parse_args()

    print("Loading filesystem pairs...")
    cases = load_filesystem_pairs()
    print(f"  Found {len(cases)} filesystem compositions with 'path' hidden")

    start = time.time()
    results = asyncio.run(
        run_context_ablation(cases, args.api_key, args.model, args.max_cases)
    )
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.1f}s")

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "context_ablation_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved: {out_path}")

    print_ablation_analysis(results)


if __name__ == "__main__":
    main()
