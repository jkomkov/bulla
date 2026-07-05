from __future__ import annotations

from bulla.testing.active_repair_tasks import build_hidden_seam_tasks, evaluate_arms


def test_build_hidden_seam_tasks_count():
    tasks = build_hidden_seam_tasks(12)
    assert len(tasks) == 12
    assert all(t.true_rank in {1, 2, 3, 5, 10} for t in tasks)


def test_witness_guided_arm_reaches_full_success_on_fixture():
    tasks = build_hidden_seam_tasks(20)
    metrics = {m.arm: m for m in evaluate_arms(tasks)}
    assert metrics["witness_guided"].success_rate == 1.0
    assert metrics["human_engineer"].success_rate <= 1.0
    assert metrics["llm_only"].success_rate <= metrics["human_engineer"].success_rate

