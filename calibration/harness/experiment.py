"""Experiment orchestrator for the agent convention-confusion harness.

Runs the three-arm experiment:
  - Presents tool cluster + task to an LLM via the Anthropic API
  - Forces a tool_call response (tool_choice: any)
  - Scores each response with the judge
  - Writes results to JSONL for analysis

Usage:
    python -m bulla.calibration.harness.experiment --pilot
    python -m bulla.calibration.harness.experiment --full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .arms import ACYCLIC_TOOLS, CYCLIC_TOOLS, DISAMBIGUATED_TOOLS, OVERLAP_CONTROL_TOOLS
from .judge import JudgeResult, judge_response
from .tasks import TASKS_BY_ARM, ProbeTask

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


RESULTS_DIR = Path(__file__).parent.parent / "data" / "agent_confusion"

AGENT_SYSTEM_PROMPT = """\
You are a helpful assistant with access to tools. When the user asks you to \
perform a task, select the most appropriate tool and call it with the correct \
parameters. Always use exactly one tool call per request. Do not explain your \
reasoning — just call the tool."""


@dataclass
class ExperimentConfig:
    model: str = "claude-sonnet-4-20250514"
    repetitions: int = 10
    temperature: float = 0.0
    max_tasks_per_arm: int = 30  # set lower for pilot
    arms: list[str] = field(default_factory=lambda: ["cyclic", "acyclic", "disambiguated", "overlap_control"])
    output_tag: str = "full"

    @property
    def total_calls(self) -> int:
        return len(self.arms) * self.max_tasks_per_arm * self.repetitions


def _get_tools_for_arm(arm: str) -> list[dict[str, Any]]:
    """Return tool definitions formatted for Anthropic API."""
    raw = {
        "cyclic": CYCLIC_TOOLS,
        "acyclic": ACYCLIC_TOOLS,
        "disambiguated": DISAMBIGUATED_TOOLS,
        "overlap_control": OVERLAP_CONTROL_TOOLS,
    }[arm]
    # Convert to Anthropic tool format
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in raw
    ]


def _extract_tool_call(response: Any) -> dict[str, Any] | None:
    """Extract tool name and params from Anthropic API response."""
    for block in response.content:
        if block.type == "tool_use":
            return {"tool": block.name, "params": block.input}
    return None


async def run_single_probe(
    client: Any,
    config: ExperimentConfig,
    tools: list[dict[str, Any]],
    task: ProbeTask,
) -> dict[str, Any] | None:
    """Single API call: system + tools + task → tool_call."""
    response = await client.messages.create(
        model=config.model,
        max_tokens=256,
        temperature=config.temperature,
        system=AGENT_SYSTEM_PROMPT,
        tools=tools,
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": task.description}],
    )
    return _extract_tool_call(response)


async def run_experiment(config: ExperimentConfig) -> list[JudgeResult]:
    """Run the full experiment across all arms, tasks, and repetitions."""
    if anthropic is None:
        raise ImportError("anthropic package required: pip install anthropic")

    client = anthropic.AsyncAnthropic()
    results: list[JudgeResult] = []
    total = config.total_calls
    done = 0

    print(f"Running experiment: {total} total API calls")
    print(f"  Model: {config.model}")
    print(f"  Arms: {config.arms}")
    print(f"  Tasks/arm: {config.max_tasks_per_arm}")
    print(f"  Repetitions: {config.repetitions}")
    print()

    for arm in config.arms:
        tools = _get_tools_for_arm(arm)
        tasks = TASKS_BY_ARM[arm][: config.max_tasks_per_arm]

        for task in tasks:
            for rep in range(config.repetitions):
                try:
                    response = await run_single_probe(client, config, tools, task)
                except Exception as e:
                    print(f"  ERROR on {task.task_id} rep {rep}: {e}")
                    response = None

                result = judge_response(
                    task_id=task.task_id,
                    arm=arm,
                    repetition=rep,
                    ground_truth_tool=task.ground_truth_tool,
                    ground_truth_params=task.ground_truth_params,
                    hidden_dimension=task.hidden_dimension.value,
                    agent_response=response,
                )
                results.append(result)
                done += 1

                if done % 10 == 0:
                    print(f"  [{done}/{total}] {arm}/{task.task_id} rep={rep} → {result.error_type.value}")

    return results


def save_results(results: list[JudgeResult], tag: str) -> Path:
    """Write results to JSONL."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"results_{tag}.jsonl"
    with open(out_path, "w") as f:
        for r in results:
            f.write(json.dumps(r.to_dict()) + "\n")
    print(f"\nResults written to {out_path} ({len(results)} records)")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run agent confusion experiment")
    parser.add_argument("--pilot", action="store_true", help="Pilot run: 5 tasks × 3 reps")
    parser.add_argument("--full", action="store_true", help="Full run: 30 tasks × 10 reps")
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--reps", type=int, default=None)
    parser.add_argument("--tasks", type=int, default=None)
    args = parser.parse_args()

    if args.pilot:
        config = ExperimentConfig(
            model=args.model,
            repetitions=args.reps or 3,
            max_tasks_per_arm=args.tasks or 5,
            output_tag="pilot",
        )
    elif args.full:
        config = ExperimentConfig(
            model=args.model,
            repetitions=args.reps or 10,
            max_tasks_per_arm=args.tasks or 30,
            output_tag="full",
        )
    else:
        config = ExperimentConfig(
            model=args.model,
            repetitions=args.reps or 3,
            max_tasks_per_arm=args.tasks or 5,
            output_tag="custom",
        )

    start = time.time()
    results = asyncio.run(run_experiment(config))
    elapsed = time.time() - start

    save_results(results, config.output_tag)

    # Quick summary
    by_arm: dict[str, list[JudgeResult]] = {}
    for r in results:
        by_arm.setdefault(r.arm, []).append(r)

    print(f"\n{'='*60}")
    print(f"SUMMARY (elapsed: {elapsed:.1f}s)")
    print(f"{'='*60}")
    for arm, arm_results in by_arm.items():
        correct = sum(1 for r in arm_results if r.tool_correct)
        total = len(arm_results)
        rate = correct / total if total > 0 else 0
        print(f"  {arm:15s}: {correct}/{total} correct ({rate:.1%})")
    print()


if __name__ == "__main__":
    main()
