"""G28 three-arm hidden-seam benchmark utilities.

Current arm evaluation is simulated scaffolding and does not execute live LLM
or human-in-the-loop collection.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.repair import build_witness_guided_plan


@dataclass(frozen=True)
class HiddenSeamTask:
    task_id: str
    composition: Composition
    true_rank: int


@dataclass(frozen=True)
class ArmMetrics:
    arm: str
    n_tasks: int
    success_rate: float
    avg_questions: float
    avg_disclosure: float
    avg_cost: float

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "n_tasks": self.n_tasks,
            "success_rate": self.success_rate,
            "avg_questions": self.avg_questions,
            "avg_disclosure": self.avg_disclosure,
            "avg_cost": self.avg_cost,
        }


def _build_hidden_cycle(rank: int, prefix: str) -> Composition:
    tools = tuple(
        ToolSpec(name=f"{prefix}_t{i}", internal_state=("f",), observable_schema=())
        for i in range(rank * 2)
    )
    edges = []
    for i in range(rank):
        a = 2 * i
        b = 2 * i + 1
        edges.append(Edge(f"{prefix}_t{a}", f"{prefix}_t{b}", (SemanticDimension(f"d{i}", "f", "f"),)))
        edges.append(Edge(f"{prefix}_t{b}", f"{prefix}_t{a}", (SemanticDimension(f"d{i}_b", "f", "f"),)))
    return Composition(name=f"{prefix}_rank_{rank}", tools=tools, edges=tuple(edges))


def build_hidden_seam_tasks(n_tasks: int = 50) -> list[HiddenSeamTask]:
    if n_tasks < 1:
        raise ValueError("n_tasks must be >= 1")
    ranks = [1, 2, 3, 5, 10]
    tasks: list[HiddenSeamTask] = []
    for i in range(n_tasks):
        rank = ranks[i % len(ranks)]
        task_id = f"g28_task_{i:03d}"
        comp = _build_hidden_cycle(rank, prefix=task_id)
        tasks.append(HiddenSeamTask(task_id=task_id, composition=comp, true_rank=rank))
    return tasks


def _det_score(task_id: str, arm: str) -> float:
    raw = hashlib.sha256(f"{task_id}:{arm}".encode()).hexdigest()[:8]
    return int(raw, 16) / 0xFFFFFFFF


def evaluate_arms(tasks: list[HiddenSeamTask]) -> list[ArmMetrics]:
    metrics: list[ArmMetrics] = []

    for arm in ("llm_only", "human_engineer", "witness_guided"):
        successes = 0
        q_total = 0.0
        d_total = 0.0
        c_total = 0.0

        for task in tasks:
            rank = task.true_rank
            if arm == "witness_guided":
                plan = build_witness_guided_plan(task.composition)
                questions = len(plan.questions)
                disclosure = questions
                cost = plan.total_cost
                success = disclosure >= rank
            elif arm == "human_engineer":
                score = _det_score(task.task_id, arm)
                questions = max(1, int(rank * (1.0 + 0.4 * score)))
                disclosure = max(1, int(questions * 0.8))
                cost = float(questions) * 1.7
                success = disclosure >= rank and score > 0.15
            else:
                score = _det_score(task.task_id, arm)
                questions = max(1, int(rank * (0.6 + 0.6 * score)))
                disclosure = max(1, int(questions * 0.6))
                cost = float(questions) * 1.1
                success = disclosure >= rank and score > 0.35

            successes += 1 if success else 0
            q_total += questions
            d_total += disclosure
            c_total += cost

        n = max(1, len(tasks))
        metrics.append(
            ArmMetrics(
                arm=arm,
                n_tasks=len(tasks),
                success_rate=successes / n,
                avg_questions=q_total / n,
                avg_disclosure=d_total / n,
                avg_cost=c_total / n,
            )
        )

    return metrics


def write_benchmark_report(
    tasks: list[HiddenSeamTask],
    metrics: list[ArmMetrics],
    *,
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_label": "SIMULATED_PLACEHOLDER",
        "simulated": True,
        "warning": (
            "Arm results are deterministic simulations for infrastructure testing; "
            "not measured benchmark performance."
        ),
        "n_tasks": len(tasks),
        "rank_histogram": {
            str(rank): sum(1 for t in tasks if t.true_rank == rank)
            for rank in sorted({t.true_rank for t in tasks})
        },
        "arms": [m.to_dict() for m in metrics],
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")

