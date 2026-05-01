"""Run the specification-gap experiment.

Usage:
    python -m bulla.calibration.harness.run_specification_gap --pilot
    python -m bulla.calibration.harness.run_specification_gap --full
    python -m bulla.calibration.harness.run_specification_gap --load-corpus-only

The experiment is simple:
    For each (case, condition) pair, ask the model to produce the disclosure set.
    Score against Bulla ground truth. Aggregate by fee level and condition.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

from .specification_gap import (
    CompositionCase,
    PROMPT_FNS,
    Score,
    load_corpus,
    score_response,
)

RESULTS_DIR = Path(__file__).parent.parent / "data" / "agent_confusion"
CORPUS_DIR = Path(__file__).parent.parent / "data" / "registry"

CONDITIONS = ("schema", "natural", "assisted")


async def call_model(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    prompt: str,
) -> str:
    """Single API call. Returns raw text response."""
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
        json=payload,
        headers=headers,
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def run_experiment(
    cases: list[CompositionCase],
    api_key: str,
    model: str = "anthropic/claude-sonnet-4",
    conditions: tuple[str, ...] = CONDITIONS,
    tag: str = "pilot",
) -> list[Score]:
    """Run the full experiment."""
    total = len(cases) * len(conditions)
    print(f"SPECIFICATION GAP EXPERIMENT")
    print(f"  Cases: {len(cases)}")
    print(f"  Conditions: {list(conditions)}")
    print(f"  Model: {model}")
    print(f"  Total API calls: {total}")
    print()

    scores: list[Score] = []
    done = 0

    async with httpx.AsyncClient() as client:
        for case in cases:
            for condition in conditions:
                prompt_fn = PROMPT_FNS[condition]
                prompt = prompt_fn(case)

                try:
                    response_text = await call_model(client, api_key, model, prompt)
                except Exception as e:
                    print(f"  ERROR {case.pair_name}/{condition}: {e}")
                    response_text = "[]"

                result = score_response(case, condition, response_text)
                scores.append(result)
                done += 1

                marker = "✓" if result.exact_match else f"J={result.jaccard:.2f}"
                if done % 5 == 0 or not result.exact_match:
                    print(
                        f"  [{done:3d}/{total}] fee={case.fee:2d} "
                        f"{condition:9s} {marker} "
                        f"({result.produced_set.__len__()}/{case.fee} fields) "
                        f"{case.pair_name}"
                    )

    # Save
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"spec_gap_{tag}.jsonl"
    with open(out_path, "w") as f:
        for s in scores:
            f.write(json.dumps(s.to_dict()) + "\n")
    print(f"\nResults: {out_path}")

    return scores


def print_summary(scores: list[Score]) -> None:
    """Print the essential summary table."""
    print(f"\n{'═' * 70}")
    print("SPECIFICATION GAP — RESULTS")
    print(f"{'═' * 70}")

    # By condition
    print(f"\n{'─'*70}")
    print(f"  {'Condition':<12} {'Exact%':>8} {'Jaccard':>8} {'Recall':>8} {'Precision':>8} {'N':>5}")
    print(f"{'─'*70}")
    for cond in CONDITIONS:
        subset = [s for s in scores if s.condition == cond]
        if not subset:
            continue
        n = len(subset)
        exact = sum(1 for s in subset if s.exact_match) / n
        jacc = sum(s.jaccard for s in subset) / n
        rec = sum(s.recall for s in subset) / n
        prec = sum(s.precision for s in subset) / n
        print(f"  {cond:<12} {exact:>7.1%} {jacc:>8.3f} {rec:>8.3f} {prec:>8.3f} {n:>5}")

    # By fee level × condition
    fee_levels = sorted(set(s.fee for s in scores))
    print(f"\n{'─'*70}")
    print(f"  {'Fee':<5} {'Condition':<12} {'Exact%':>8} {'Recall':>8} {'N':>5}")
    print(f"{'─'*70}")
    for fee in fee_levels:
        for cond in CONDITIONS:
            subset = [s for s in scores if s.fee == fee and s.condition == cond]
            if not subset:
                continue
            n = len(subset)
            exact = sum(1 for s in subset if s.exact_match) / n
            rec = sum(s.recall for s in subset) / n
            print(f"  {fee:<5} {cond:<12} {exact:>7.1%} {rec:>8.3f} {n:>5}")
        print()

    # The key test: does recall drop with fee under natural condition?
    natural_scores = [s for s in scores if s.condition == "natural"]
    if natural_scores:
        low_fee = [s for s in natural_scores if s.fee <= 3]
        high_fee = [s for s in natural_scores if s.fee > 3]
        if low_fee and high_fee:
            low_recall = sum(s.recall for s in low_fee) / len(low_fee)
            high_recall = sum(s.recall for s in high_fee) / len(high_fee)
            print(f"  KEY TEST: recall(fee≤3) = {low_recall:.3f}, recall(fee>3) = {high_recall:.3f}")
            print(f"  Gap: {low_recall - high_recall:.3f}")

    # The rescue test: does assisted condition improve over natural?
    natural_mean = sum(s.recall for s in scores if s.condition == "natural") / max(1, len([s for s in scores if s.condition == "natural"]))
    assisted_mean = sum(s.recall for s in scores if s.condition == "assisted") / max(1, len([s for s in scores if s.condition == "assisted"]))
    if natural_mean > 0 or assisted_mean > 0:
        print(f"\n  RESCUE TEST: natural recall = {natural_mean:.3f}, assisted recall = {assisted_mean:.3f}")
        print(f"  Lift: {assisted_mean - natural_mean:+.3f}")

    print(f"\n{'═' * 70}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true", help="5 cases per fee stratum")
    parser.add_argument("--full", action="store_true", help="All 240 cases")
    parser.add_argument("--load-corpus-only", action="store_true", help="Just load and report corpus stats")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--max-fee", type=int, default=None)
    args = parser.parse_args()

    manifests_dir = CORPUS_DIR / "manifests"
    pairs_jsonl = CORPUS_DIR / "report" / "schema_structure_pairs.jsonl"

    print("Loading corpus...")
    cases = load_corpus(manifests_dir, pairs_jsonl)
    print(f"  Total nonzero-fee cases: {len(cases)}")

    fee_dist = {}
    for c in cases:
        fee_dist[c.fee] = fee_dist.get(c.fee, 0) + 1
    print(f"  Fee distribution: {dict(sorted(fee_dist.items()))}")

    if args.load_corpus_only:
        return

    # Stratified sampling for pilot
    if args.pilot:
        # Take up to 3 cases per fee level, prioritizing diversity
        sampled: list[CompositionCase] = []
        for fee in sorted(fee_dist.keys()):
            if args.max_fee and fee > args.max_fee:
                break
            fee_cases = [c for c in cases if c.fee == fee]
            sampled.extend(fee_cases[:3])
        cases = sampled
        print(f"  Pilot sample: {len(cases)} cases")
        tag = "pilot"
    else:
        if args.max_fee:
            cases = [c for c in cases if c.fee <= args.max_fee]
        tag = "full"

    api_key = args.api_key
    if not api_key:
        import os
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: provide --api-key or set OPENROUTER_API_KEY")
        return

    start = time.time()
    scores = asyncio.run(run_experiment(cases, api_key, args.model, tag=tag))
    elapsed = time.time() - start
    print(f"\nElapsed: {elapsed:.1f}s")

    print_summary(scores)


if __name__ == "__main__":
    main()
