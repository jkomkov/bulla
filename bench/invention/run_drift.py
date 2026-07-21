#!/usr/bin/env python3
"""Run the preregistered M3 calibration and carrier experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bulla.experimental.drift import (
    SeamNull,
    calibrate_null,
    localization_experiment,
)


def default_seams():
    return (
        SeamNull("opaque-acceptance", "opaque", 0.05, betting_fraction=1.0),
        SeamNull("opaque-authority", "opaque", 0.05, betting_fraction=1.0),
        SeamNull("opaque-revocation", "opaque", 0.05, betting_fraction=1.0),
        SeamNull("opaque-evidence", "opaque", 0.05, betting_fraction=1.0),
        SeamNull("regen-value", "regenerated", 0.05, betting_fraction=1.0),
        SeamNull("regen-label", "regenerated", 0.05, betting_fraction=1.0),
        SeamNull("regen-summary", "regenerated", 0.05, betting_fraction=1.0),
        SeamNull("regen-decision", "regenerated", 0.05, betting_fraction=1.0),
        SeamNull("ambient-input", "ambient", 0.05, betting_fraction=1.0),
        SeamNull("ambient-output", "ambient", 0.05, betting_fraction=1.0),
        SeamNull("ambient-cache", "ambient", 0.05, betting_fraction=1.0),
        SeamNull("ambient-control", "ambient", 0.05, betting_fraction=1.0),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-streams", type=int, default=10_000)
    parser.add_argument("--calibration-steps", type=int, default=200)
    parser.add_argument("--experiment-trials", type=int, default=1_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    seams = default_seams()
    report = {
        "schema_version": "0.1-experimental",
        "calibration": calibrate_null(
            seams,
            streams=args.calibration_streams,
            steps=args.calibration_steps,
            alpha=0.05,
            seed=20260717,
        ),
        "localization": localization_experiment(
            seams,
            trials=args.experiment_trials,
            steps=200,
            change_time=50,
            drift_mean=0.40,
            alpha=0.05,
            seed=20260718,
        ),
    }
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(
            f"calibration_upper="
            f"{report['calibration']['one_sided_95_percent_binomial_upper']:.6f}, "
            f"reject_localization="
            f"{report['localization']['reject_operational_localization']}, "
            f"reject_sparse="
            f"{report['localization']['reject_sparse_monitoring_sufficiency']}, "
            f"output={args.output}"
        )
    else:
        print(rendered)


if __name__ == "__main__":
    main()
