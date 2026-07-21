"""M3 e-process monitor and localization boundary tests."""

import math

import pytest

from bulla.experimental.drift import (
    ArithmeticEProcessMonitor,
    SeamNull,
    calibrate_null,
    clopper_pearson_upper,
    localization_experiment,
)


def _seams():
    return (
        SeamNull("opaque-1", "opaque", 0.05, betting_fraction=1.0),
        SeamNull("opaque-2", "opaque", 0.05, betting_fraction=1.0),
        SeamNull("regen-1", "regenerated", 0.05, betting_fraction=1.0),
        SeamNull("regen-2", "regenerated", 0.05, betting_fraction=1.0),
        SeamNull("ambient-1", "ambient", 0.05, betting_fraction=1.0),
    )


def test_weighted_arithmetic_merger_matches_declared_e_factors():
    seams = (
        SeamNull("a", "opaque", 0.5, betting_fraction=1.0, weight=1.0),
        SeamNull("b", "regenerated", 0.5, betting_fraction=1.0, weight=1.0),
    )
    monitor = ArithmeticEProcessMonitor(seams, alpha=0.05)

    snapshot = monitor.update({"a": 1.0, "b": 0.0})

    expected = (math.exp(0.375) + math.exp(-0.625)) / 2.0
    assert snapshot.aggregate_e_value == pytest.approx(expected)
    assert "Model misspecification is outside" in snapshot.guarantee
    with pytest.raises(TypeError, match="no truth value"):
        bool(snapshot)


def test_common_filtration_requires_every_seam_at_each_update():
    monitor = ArithmeticEProcessMonitor(_seams())

    with pytest.raises(ValueError, match="one common-filtration observation"):
        monitor.update({"opaque-1": 0.0})


def test_exact_one_sided_binomial_upper_bound():
    upper = clopper_pearson_upper(0, 100, confidence=0.95)

    assert upper == pytest.approx(1.0 - 0.05 ** (1.0 / 100.0), rel=1e-10)
    assert clopper_pearson_upper(100, 100) == 1.0


def test_null_calibration_reports_promotion_gate_without_claiming_independence_runtime():
    report = calibrate_null(_seams(), streams=200, steps=50, seed=7)

    assert report["streams"] == 200
    assert report["one_sided_95_percent_binomial_upper"] >= report["empirical_rate"]
    assert set(report["null"]) == {x.seam_id for x in _seams()}


def test_localization_experiment_has_double_edged_boundary():
    report = localization_experiment(
        _seams(),
        trials=200,
        steps=160,
        change_time=40,
        drift_mean=0.45,
        seed=11,
    )

    opaque = report["results"]["opaque"]
    regenerated = report["results"]["regenerated"]
    assert opaque["opaque_only_power"] > 0.8
    assert regenerated["opaque_only_power"] < regenerated["full_graph_power"]
    assert regenerated["regenerated_only_power"] > 0.8
    assert regenerated["sparse_combined_power"] > 0.8
    assert "reject_sparse_monitoring_sufficiency" in report
    assert "no theorem-level claim" in report["regenerated_value_boundary"]
