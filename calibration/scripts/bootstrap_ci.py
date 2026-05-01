#!/usr/bin/env python3
"""Bootstrap 95% confidence intervals for synthetic ecology benchmark.

Reads results for both Claude Sonnet 4 and GPT-4o, computes 10,000-resample
bootstrap CIs for exact_match rate, macro-precision, and macro-recall
per model x condition.
"""

import json
import random
from pathlib import Path

random.seed(42)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "agent_confusion"
INPUTS = {
    "claude": DATA_DIR / "synthetic_ecology_results_claude.json",
    "gpt4o": DATA_DIR / "synthetic_ecology_results_gpt4o.json",
}
OUTPUT = DATA_DIR / "bootstrap_ci.json"

N_BOOT = 10_000
CONDITIONS = ("structured", "bare", "flat")
METRICS = ("exact_match", "precision", "recall")


def trial_metrics(trial: dict) -> dict:
    """Compute per-trial precision, recall, exact_match.

    Edge cases:
      gt=[] id=[]   -> exact_match=1, precision=1.0 (vacuous), recall=1.0 (vacuous)
      gt=[] id=[x]  -> exact_match=0, precision=0.0, recall=1.0 (no positives to miss)
      gt=[x] id=[x] -> exact_match=1, precision=1.0, recall=1.0
      gt=[x] id=[]  -> exact_match=0, precision=0.0, recall=0.0
    """
    exact_match = 1 if trial["correct"] else 0

    n_tp = len(trial["true_positives"])
    n_id = len(trial["identified"])
    n_gt = len(trial["ground_truth"])

    # precision: tp / identified  (0/0 -> 1.0 by convention)
    if n_id == 0:
        precision = 1.0 if n_gt == 0 else 0.0
    else:
        precision = n_tp / n_id

    # recall: tp / ground_truth  (0/0 -> 1.0 by convention)
    if n_gt == 0:
        recall = 1.0
    else:
        recall = n_tp / n_gt

    return {"exact_match": exact_match, "precision": precision, "recall": recall}


def macro_mean(metric_list: list[dict], key: str) -> float:
    """Mean of a metric across trials."""
    return sum(t[key] for t in metric_list) / len(metric_list)


def bootstrap_ci(trials: list[dict], n_boot: int = N_BOOT) -> dict:
    """Bootstrap 95% CIs for exact_match, precision, recall."""
    metrics = [trial_metrics(t) for t in trials]
    n = len(metrics)

    point = {k: macro_mean(metrics, k) for k in METRICS}

    boot_samples: dict[str, list[float]] = {k: [] for k in METRICS}

    for _ in range(n_boot):
        sample = random.choices(metrics, k=n)
        for key in METRICS:
            boot_samples[key].append(macro_mean(sample, key))

    ci = {}
    for key in METRICS:
        vals = sorted(boot_samples[key])
        lo = vals[int(n_boot * 0.025)]
        hi = vals[int(n_boot * 0.975)]
        ci[key] = {
            "point": round(point[key], 4),
            "ci_low": round(lo, 4),
            "ci_high": round(hi, 4),
        }

    return ci


def main() -> None:
    results: dict[str, dict] = {}

    for model, path in INPUTS.items():
        with open(path) as f:
            data = json.load(f)

        results[model] = {}
        for condition in CONDITIONS:
            trials = data[condition]
            assert len(trials) == 24, (
                f"{model}/{condition} has {len(trials)} trials, expected 24"
            )
            results[model][condition] = bootstrap_ci(trials)

    # Write JSON output
    with open(OUTPUT, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {OUTPUT}\n")

    # Print comparison table
    col = f"{'Model':<8} {'Condition':<12} {'Metric':<14} {'Point':>7} {'95% CI':>20}"
    print(col)
    print("=" * len(col))
    for model in INPUTS:
        for condition in CONDITIONS:
            for metric in METRICS:
                r = results[model][condition][metric]
                ci_str = f"[{r['ci_low']:.4f}, {r['ci_high']:.4f}]"
                print(
                    f"{model:<8} {condition:<12} {metric:<14} "
                    f"{r['point']:>7.4f} {ci_str:>20}"
                )
            print()


if __name__ == "__main__":
    main()
