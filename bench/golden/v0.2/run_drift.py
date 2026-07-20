#!/usr/bin/env python3
"""Stress the localized e-process under matched and misspecified streams."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from bulla.experimental.drift import ArithmeticEProcessMonitor, SeamNull, clopper_pearson_upper


HERE = Path(__file__).resolve().parent
STREAMS = 10_000
STEPS = 64
ALPHA = 0.05


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def observations(variant: str, rng: random.Random, time: int, state: dict[str, float]) -> dict[str, float]:
    if variant == "iid_boundary_hugging":
        return {"opaque": float(rng.random() < 0.05)}
    if variant.startswith("ar1_"):
        rho = float(variant.split("_")[-1].replace("p", "."))
        prior = state.get("ar", 0.05)
        value = rho * prior + (1.0 - rho) * float(rng.random() < 0.05)
        state["ar"] = value
        return {"opaque": value}
    if variant == "heavy_tailed_innovations":
        return {"opaque": 1.0 if rng.random() < 0.01 else 0.040404040404}
    if variant == "seasonality":
        probability = 0.05 + 0.045 * math.sin(2.0 * math.pi * time / 16.0)
        return {"opaque": float(rng.random() < probability)}
    if variant == "missingness":
        if rng.random() < 0.25:
            return {"opaque": 0.0}
        return {"opaque": float(rng.random() < 0.05)}
    if variant == "asynchronous":
        if time % 2:
            return {"opaque": 0.0}
        return {"opaque": float(rng.random() < 0.05)}
    if variant == "changing_baseline":
        probability = 0.02 if time < STEPS // 2 else 0.08
        return {"opaque": float(rng.random() < probability)}
    if variant == "correlated_seams":
        shared = float(rng.random() < 0.05)
        return {"opaque": shared, "regenerated": shared}
    raise ValueError(variant)


def seams_for(variant: str) -> tuple[SeamNull, ...]:
    seams = [SeamNull("opaque", "opaque", 0.05, betting_fraction=2.0)]
    if variant == "correlated_seams":
        seams.append(SeamNull("regenerated", "regenerated", 0.05, betting_fraction=2.0))
    return tuple(seams)


def stress_variant(variant: str, seed: int, guarantee_applicable: bool) -> dict:
    crossings = 0
    for stream in range(STREAMS):
        rng = random.Random(seed + stream * 104729)
        monitor = ArithmeticEProcessMonitor(seams_for(variant), alpha=ALPHA)
        state: dict[str, float] = {}
        crossed = False
        for time in range(STEPS):
            if monitor.update(observations(variant, rng, time, state)).crossed:
                crossed = True
                break
        crossings += int(crossed)
    upper = clopper_pearson_upper(crossings, STREAMS)
    return {
        "variant": variant,
        "streams": STREAMS,
        "steps": STEPS,
        "crossings": crossings,
        "empirical_rate": crossings / STREAMS,
        "one_sided_95_percent_binomial_upper": upper,
        "guarantee_applicable": guarantee_applicable,
        "calibration_gate": upper <= 0.06 if guarantee_applicable else "NOT_APPLICABLE_MODEL_MISSPECIFIED",
    }


def injected(carrier: str, seed: int) -> dict:
    rng = random.Random(seed)
    seams = (
        SeamNull("opaque", "opaque", 0.05, betting_fraction=2.0),
        SeamNull("regenerated", "regenerated", 0.05, betting_fraction=2.0),
    )
    alarms: list[int] = []
    extraction: list[float] = []
    for _ in range(500):
        monitor = ArithmeticEProcessMonitor(seams, alpha=ALPHA)
        alarm = STEPS + 1
        total = 0.0
        for time in range(1, STEPS + 1):
            opaque_probability = 0.35 if carrier in {"opaque", "simultaneous"} else 0.05
            regenerated_probability = 0.35 if carrier in {"regenerated", "simultaneous"} else 0.05
            snapshot = monitor.update({
                "opaque": float(rng.random() < opaque_probability),
                "regenerated": float(rng.random() < regenerated_probability),
            })
            if time < alarm:
                drift_magnitude = (opaque_probability - 0.05) + (regenerated_probability - 0.05)
                total += 1_000.0 * drift_magnitude
            if snapshot.crossed:
                alarm = time
                break
        alarms.append(alarm)
        extraction.append(total)
    ordered_alarm = sorted(alarms)
    ordered_extraction = sorted(extraction)
    return {
        "carrier": carrier,
        "trials": len(alarms),
        "power_by_step_64": sum(value <= STEPS for value in alarms) / len(alarms),
        "median_alarm_step": ordered_alarm[len(ordered_alarm) // 2],
        "median_undetected_extraction": ordered_extraction[len(ordered_extraction) // 2],
        "extraction_definition": "sum before alarm of exposure_t times drift_magnitude_t; exposure_t=1000 synthetic units",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "drift-stress.json")
    args = parser.parse_args()
    variants = (
        ("iid_boundary_hugging", True),
        ("ar1_0p3", False),
        ("ar1_0p7", False),
        ("ar1_0p95", False),
        ("heavy_tailed_innovations", True),
        ("seasonality", False),
        ("missingness", True),
        ("asynchronous", True),
        ("changing_baseline", False),
        ("correlated_seams", False),
    )
    null_stress = [stress_variant(name, 20260718 + index * 1_000_000, valid) for index, (name, valid) in enumerate(variants)]
    report = {
        "schema_version": "0.2-drift-stress",
        "declared_family_count": 8,
        "variant_count": len(variants),
        "ar1_levels": [0.3, 0.7, 0.95],
        "null_stress": null_stress,
        "injections": [injected(carrier, 9000 + index) for index, carrier in enumerate(("opaque", "regenerated", "simultaneous"))],
        "matching_null_failures": [item["variant"] for item in null_stress if item["guarantee_applicable"] and not item["calibration_gate"]],
        "claim_boundary": "Synthetic streams only; calibration applies only where the fixed common-filtration conditional-mean null is declared valid.",
    }
    write_json(args.output, report)
    print(json.dumps({"variants": len(variants), "matching_null_failures": report["matching_null_failures"]}, sort_keys=True))
    return 1 if report["matching_null_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
