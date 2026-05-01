"""Reverse intervention: rename canonical 'path' to neutral synonym.

Tests whether identification drops when the canonical name is replaced by
a semantically plausible but less lexically famous alternative.

Conditions:
  BASELINE   — original schemas (path is 'path')
  NEUTRAL    — 'path' renamed to 'location' (semantically similar, not famous)
  NEUTRAL2   — 'path' renamed to 'target' (another neutral option)

If identification drops under both neutral renames, the causal case is
complete: it's the specific TOKEN 'path', not the semantic concept of
"file location", that drives identification.
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

NEUTRAL_RENAMES = {
    "location": "path",      # semantically similar, less canonical
    "target": "path",        # semantically plausible, not a famous token
    "entry": "path",         # generic, not file-specific
}


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
        # Only filesystem pairs
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

        # Check that 'path' is hidden
        left_hidden = set()
        right_hidden = set()
        for bs in diag.blind_spots:
            if bs.from_tool.startswith(f"{left}__"):
                left_hidden.add(bs.from_field)
            elif bs.from_tool.startswith(f"{right}__"):
                right_hidden.add(bs.from_field)
            if bs.to_tool.startswith(f"{left}__"):
                left_hidden.add(bs.to_field)
            elif bs.to_tool.startswith(f"{right}__"):
                right_hidden.add(bs.to_field)

        all_hidden = left_hidden | right_hidden
        if "path" not in all_hidden:
            continue

        cases.append({
            "pair_name": row["pair_name"],
            "left_server": left,
            "right_server": right,
            "fee": diag.coherence_fee,
            "left_tools": server_tools[left],
            "right_tools": server_tools[right],
            "path_hidden_in": "left" if "path" in left_hidden else "right",
        })

    return cases


def rename_field_in_tools(
    tools: list[dict[str, Any]], old: str, new: str
) -> list[dict[str, Any]]:
    """Rename a field across all tool schemas."""
    tools = copy.deepcopy(tools)
    for tool in tools:
        schema = tool.get("inputSchema", tool.get("input_schema", {}))
        props = schema.get("properties", {})
        if old in props:
            prop_def = props.pop(old)
            # Update description to use new name
            if "description" in prop_def:
                prop_def["description"] = prop_def["description"].replace(old, new)
            props[new] = prop_def
        required = schema.get("required", [])
        if old in required:
            required[required.index(old)] = new
        # Also rename in tool description
        if "description" in tool:
            tool["description"] = tool["description"].replace(f" {old} ", f" {new} ")
            tool["description"] = tool["description"].replace(f"'{old}'", f"'{new}'")
    return tools


PROBE_PROMPT = """\
You are analyzing a composition of two MCP tool servers for hidden fields.

A field is "hidden" if:
- It appears as a parameter in at least one tool
- Its semantic convention (format, expected values, meaning) is not observable \
from the schema alone
- Its convention could differ between the two servers without any schema-level \
indication

Your task: identify which fields have hidden conventions that could create \
silent composition failures.

## Server: {left_server}
Tools:
{left_tools_json}

## Server: {right_server}
Tools:
{right_tools_json}

## Instructions
List the field names that have hidden conventions. Return a JSON array of \
field names:
["field1", "field2", ...]

Only include fields whose conventions are genuinely ambiguous or under-specified.\
"""


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


def parse_field_list(text: str) -> set[str]:
    """Parse a JSON array of field names."""
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


async def run_reverse_intervention(
    cases: list[dict[str, Any]],
    api_key: str,
    model: str,
    max_cases: int = 10,
) -> dict[str, list[dict]]:
    """Run baseline + two neutral renames per case."""
    cases = cases[:max_cases]
    conditions = ["baseline", "location", "target"]
    total = len(cases) * len(conditions)

    print(f"REVERSE INTERVENTION: canonical → neutral")
    print(f"  Cases: {len(cases)} (filesystem pairs where 'path' is hidden)")
    print(f"  Conditions: {conditions}")
    print(f"  Total API calls: {total}")
    print(f"  Model: {model}")
    print()

    all_results: dict[str, list[dict]] = {c: [] for c in conditions}

    async with httpx.AsyncClient() as client:
        done = 0
        for case in cases:
            for cond in conditions:
                # Build tools for this condition
                left_tools = case["left_tools"]
                right_tools = case["right_tools"]

                if cond != "baseline":
                    # Rename 'path' to the neutral word in BOTH servers
                    left_tools = rename_field_in_tools(left_tools, "path", cond)
                    right_tools = rename_field_in_tools(right_tools, "path", cond)

                prompt = PROBE_PROMPT.format(
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

                fields = parse_field_list(response)

                # Check if the target field (path or its rename) is identified
                target_name = "path" if cond == "baseline" else cond
                identified = target_name in fields

                all_results[cond].append({
                    "pair_name": case["pair_name"],
                    "target_field": target_name,
                    "identified": identified,
                    "all_fields": sorted(fields),
                })
                done += 1

                if done % 3 == 0:
                    mark = "✓" if identified else "✗"
                    print(f"  [{done:2d}/{total}] {case['pair_name'][:35]:35s} {cond:10s} {mark}")

    return all_results


def print_reverse_analysis(results: dict[str, list[dict]]) -> None:
    """Print the reverse intervention results."""
    print(f"\n{'═'*70}")
    print("REVERSE INTERVENTION RESULTS")
    print(f"{'═'*70}")
    print(f"\n  Question: Does renaming 'path' to a neutral synonym reduce identification?")
    print()

    for cond in ["baseline", "location", "target"]:
        subset = results[cond]
        n = len(subset)
        hits = sum(1 for r in subset if r["identified"])
        rate = hits / n if n > 0 else 0
        label = f"'path'" if cond == "baseline" else f"'{cond}'"
        print(f"  {cond:10s} (field called {label:12s}): {hits}/{n} = {rate:.0%}")

    baseline_hits = [r["identified"] for r in results["baseline"]]
    location_hits = [r["identified"] for r in results["location"]]
    target_hits = [r["identified"] for r in results["target"]]

    # Paired comparison
    n = len(baseline_hits)
    b_rate = sum(baseline_hits) / n
    l_rate = sum(location_hits) / n
    t_rate = sum(target_hits) / n

    print(f"\n{'─'*70}")
    print(f"  DROPS:")
    print(f"    baseline → 'location': {b_rate:.0%} → {l_rate:.0%} (Δ = {l_rate - b_rate:+.0%})")
    print(f"    baseline → 'target':   {b_rate:.0%} → {t_rate:.0%} (Δ = {t_rate - b_rate:+.0%})")

    # McNemar for each
    from math import comb
    for rename, hits in [("location", location_hits), ("target", target_hits)]:
        lost = sum(1 for b, r in zip(baseline_hits, hits) if b and not r)
        gained = sum(1 for b, r in zip(baseline_hits, hits) if not b and r)
        total_disc = lost + gained
        if total_disc > 0:
            p = sum(comb(total_disc, k) * 0.5**total_disc for k in range(lost, total_disc + 1))
            print(f"    '{rename}': {lost} lost, {gained} gained, McNemar p = {p:.4f}")

    # Per-case detail
    print(f"\n{'─'*70}")
    print(f"  {'Pair':<35s} {'baseline':>8s} {'location':>9s} {'target':>7s}")
    print(f"{'─'*70}")
    for i in range(len(results["baseline"])):
        pair = results["baseline"][i]["pair_name"]
        b = "✓" if results["baseline"][i]["identified"] else "✗"
        l = "✓" if results["location"][i]["identified"] else "✗"
        t = "✓" if results["target"][i]["identified"] else "✗"
        print(f"  {pair:<35s} {b:>8s} {l:>9s} {t:>7s}")

    print(f"\n{'═'*70}")

    # Interpretation
    if l_rate < b_rate - 0.15 or t_rate < b_rate - 0.15:
        print(f"\n  ★ IDENTIFICATION DROPS WITH NEUTRAL RENAME")
        print(f"    It is the specific token 'path', not the semantic concept,")
        print(f"    that drives identification.")
    else:
        print(f"\n  ○ No significant drop detected — may need larger N.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="anthropic/claude-sonnet-4")
    parser.add_argument("--max-cases", type=int, default=10)
    args = parser.parse_args()

    print("Loading filesystem pairs...")
    cases = load_filesystem_pairs()
    print(f"  Found {len(cases)} filesystem compositions with 'path' hidden")

    start = time.time()
    results = asyncio.run(
        run_reverse_intervention(cases, args.api_key, args.model, args.max_cases)
    )
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.1f}s")

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "reverse_intervention_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved: {out_path}")

    print_reverse_analysis(results)


if __name__ == "__main__":
    main()
