"""Localized drift monitoring with explicit e-process guarantees.

For a bounded observation X_t in [0, 1] with
E[X_t | F_{t-1}] <= mu under the declared null, Hoeffding's lemma makes

    exp(lambda * (X_t - mu) - lambda^2 / 8)

a valid e-factor.  Products over a common filtration are nonnegative
supermartingales.  A fixed weighted arithmetic mean across seam processes is
again a nonnegative supermartingale without any independence assumption.

Products across seams are intentionally absent from the default API.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class SeamNull:
    seam_id: str
    carrier: str
    null_mean: float
    betting_fraction: float = 1.0
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.seam_id:
            raise ValueError("seam_id must be non-empty")
        if self.carrier not in ("opaque", "regenerated", "ambient"):
            raise ValueError("carrier must be 'opaque', 'regenerated', or 'ambient'")
        if not (0.0 <= self.null_mean <= 1.0):
            raise ValueError("null_mean must lie in [0, 1]")
        if not math.isfinite(self.betting_fraction) or self.betting_fraction <= 0:
            raise ValueError("betting_fraction must be finite and positive")
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError("weight must be finite and positive")


@dataclass(frozen=True)
class DriftSnapshot:
    time: int
    aggregate_e_value: float
    per_seam_e_values: Mapping[str, float]
    threshold: float
    crossed: bool
    first_crossing_time: int | None
    guarantee: str

    def __bool__(self) -> bool:
        raise TypeError("DriftSnapshot has no truth value; inspect .crossed")

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "aggregate_e_value": self.aggregate_e_value,
            "per_seam_e_values": dict(self.per_seam_e_values),
            "threshold": self.threshold,
            "crossed": self.crossed,
            "first_crossing_time": self.first_crossing_time,
            "guarantee": self.guarantee,
        }


class ArithmeticEProcessMonitor:
    """Common-filtration weighted arithmetic merger for seam e-processes."""

    def __init__(self, seams: Sequence[SeamNull], *, alpha: float = 0.05):
        if not seams:
            raise ValueError("at least one seam null is required")
        if not (0.0 < alpha < 1.0):
            raise ValueError("alpha must lie in (0, 1)")
        if len({x.seam_id for x in seams}) != len(seams):
            raise ValueError("seam ids must be unique")
        self._seams = tuple(seams)
        total_weight = sum(x.weight for x in seams)
        self._weights = {
            x.seam_id: x.weight / total_weight for x in self._seams
        }
        self._log_e = {x.seam_id: 0.0 for x in self._seams}
        self._time = 0
        self._alpha = alpha
        self._threshold = 1.0 / alpha
        self._first_crossing_time = None

    @property
    def seam_ids(self) -> tuple[str, ...]:
        return tuple(x.seam_id for x in self._seams)

    def update(self, observations: Mapping[str, float]) -> DriftSnapshot:
        if set(observations) != set(self.seam_ids):
            missing = set(self.seam_ids) - set(observations)
            extra = set(observations) - set(self.seam_ids)
            raise ValueError(
                f"one common-filtration observation per seam is required; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        self._time += 1
        for seam in self._seams:
            value = observations[seam.seam_id]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or not (0.0 <= value <= 1.0)
            ):
                raise ValueError(
                    f"observation for {seam.seam_id!r} must be finite in [0, 1]"
                )
            lam = seam.betting_fraction
            self._log_e[seam.seam_id] += (
                lam * (float(value) - seam.null_mean) - (lam * lam) / 8.0
            )
        aggregate = self._weighted_logsumexp()
        crossed = aggregate >= self._threshold
        if crossed and self._first_crossing_time is None:
            self._first_crossing_time = self._time
        return self.snapshot()

    def _weighted_logsumexp(self) -> float:
        terms = [
            math.log(self._weights[seam_id]) + log_e
            for seam_id, log_e in self._log_e.items()
        ]
        maximum = max(terms)
        if maximum > 700:
            return math.inf
        return math.exp(maximum) * sum(math.exp(x - maximum) for x in terms)

    def snapshot(self) -> DriftSnapshot:
        values = {
            seam_id: math.exp(log_e) if log_e < 700 else math.inf
            for seam_id, log_e in self._log_e.items()
        }
        aggregate = self._weighted_logsumexp()
        return DriftSnapshot(
            time=self._time,
            aggregate_e_value=aggregate,
            per_seam_e_values=values,
            threshold=self._threshold,
            crossed=aggregate >= self._threshold,
            first_crossing_time=self._first_crossing_time,
            guarantee=(
                f"Under the declared common-filtration nulls, the probability "
                f"of ever crossing {self._threshold:g} is at most "
                f"alpha={self._alpha:g}. Model misspecification is outside this guarantee."
            ),
        )


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    maximum_iterations = 300
    epsilon = 3e-14
    floor = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < floor:
        d = floor
    d = 1.0 / d
    h = d
    for m in range(1, maximum_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + aa / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < floor:
            d = floor
        c = 1.0 + aa / c
        if abs(c) < floor:
            c = floor
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) <= epsilon:
            return h
    raise ArithmeticError("incomplete beta continued fraction did not converge")


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def clopper_pearson_upper(
    successes: int,
    trials: int,
    *,
    confidence: float = 0.95,
) -> float:
    """Exact one-sided binomial upper confidence bound."""
    if not (0 <= successes <= trials) or trials <= 0:
        raise ValueError("require 0 <= successes <= trials and trials > 0")
    if not (0.0 < confidence < 1.0):
        raise ValueError("confidence must lie in (0, 1)")
    if successes == trials:
        return 1.0
    a = successes + 1.0
    b = trials - successes
    low, high = 0.0, 1.0
    for _ in range(100):
        mid = (low + high) / 2.0
        if _regularized_beta(mid, a, b) < confidence:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def calibrate_null(
    seams: Sequence[SeamNull],
    *,
    streams: int = 10_000,
    steps: int = 200,
    alpha: float = 0.05,
    seed: int = 20260717,
) -> dict:
    """Monte Carlo calibration under independent Bernoulli null streams.

    Independence is used only to generate this calibration corpus, not to merge
    seam e-values or state the runtime guarantee.
    """
    if streams <= 0 or steps <= 0:
        raise ValueError("streams and steps must be positive")
    rng = random.Random(seed)
    crossings = 0
    for _ in range(streams):
        monitor = ArithmeticEProcessMonitor(seams, alpha=alpha)
        crossed = False
        for _time in range(steps):
            observations = {
                seam.seam_id: float(rng.random() < seam.null_mean)
                for seam in seams
            }
            if monitor.update(observations).crossed:
                crossed = True
                break
        crossings += int(crossed)
    upper = clopper_pearson_upper(crossings, streams, confidence=0.95)
    return {
        "schema_version": "0.1-experimental",
        "streams": streams,
        "steps": steps,
        "alpha": alpha,
        "seed": seed,
        "crossings": crossings,
        "empirical_rate": crossings / streams,
        "one_sided_95_percent_binomial_upper": upper,
        "promotion_gate_at_most_0_06": upper <= 0.06,
        "null": {
            seam.seam_id: {
                "conditional_mean_at_most": seam.null_mean,
                "carrier": seam.carrier,
            }
            for seam in seams
        },
    }


def _paired_detection_run(
    *,
    rng: random.Random,
    seams: Sequence[SeamNull],
    affected_carrier: str,
    change_time: int,
    steps: int,
    drift_mean: float,
    alpha: float,
) -> dict[str, int | None]:
    opaque = [x for x in seams if x.carrier == "opaque"]
    regenerated = [x for x in seams if x.carrier == "regenerated"]
    sparse = [x for x in seams if x.carrier in ("opaque", "regenerated")]
    monitors = {
        "opaque_only": ArithmeticEProcessMonitor(opaque, alpha=alpha),
        "regenerated_only": ArithmeticEProcessMonitor(regenerated, alpha=alpha),
        "sparse_combined": ArithmeticEProcessMonitor(sparse, alpha=alpha),
        "full_graph": ArithmeticEProcessMonitor(seams, alpha=alpha),
    }
    monitor_seams = {
        "opaque_only": opaque,
        "regenerated_only": regenerated,
        "sparse_combined": sparse,
        "full_graph": list(seams),
    }
    crossings = {name: None for name in monitors}
    for time_index in range(1, steps + 1):
        observations = {}
        for seam in seams:
            mean = seam.null_mean
            if time_index >= change_time and seam.carrier == affected_carrier:
                mean = drift_mean
            observations[seam.seam_id] = float(rng.random() < mean)
        for name, monitor in monitors.items():
            snapshot = monitor.update(
                {x.seam_id: observations[x.seam_id] for x in monitor_seams[name]}
            )
            if crossings[name] is None and snapshot.crossed:
                crossings[name] = time_index
    return crossings


def localization_experiment(
    seams: Sequence[SeamNull],
    *,
    trials: int = 1000,
    steps: int = 200,
    change_time: int = 50,
    drift_mean: float = 0.40,
    alpha: float = 0.05,
    seed: int = 20260718,
) -> dict:
    """Paired opaque-vs-full monitoring experiment for both drift carriers."""
    if not any(x.carrier == "opaque" for x in seams):
        raise ValueError("localization experiment needs at least one opaque seam")
    if not any(x.carrier == "regenerated" for x in seams):
        raise ValueError("localization experiment needs at least one regenerated seam")
    rng = random.Random(seed)
    results = {}
    for carrier in ("opaque", "regenerated"):
        arm_times = {
            "opaque_only": [],
            "regenerated_only": [],
            "sparse_combined": [],
            "full_graph": [],
        }
        for _ in range(trials):
            crossings = _paired_detection_run(
                rng=rng,
                seams=seams,
                affected_carrier=carrier,
                change_time=change_time,
                steps=steps,
                drift_mean=drift_mean,
                alpha=alpha,
            )
            for name, crossing in crossings.items():
                arm_times[name].append(crossing)
        carrier_result = {}
        for name, times in arm_times.items():
            detected = [x for x in times if x is not None]
            carrier_result[f"{name}_power"] = len(detected) / trials
            carrier_result[f"{name}_median_delay"] = (
                statistics.median(x - change_time for x in detected)
                if detected
                else None
            )
        results[carrier] = carrier_result
    opaque = results["opaque"]
    power_loss = opaque["full_graph_power"] - opaque["opaque_only_power"]
    opaque_delay = opaque["opaque_only_median_delay"]
    full_delay = opaque["full_graph_median_delay"]
    delay_loss_fraction = (
        (opaque_delay - full_delay) / max(abs(full_delay), 1.0)
        if opaque_delay is not None and full_delay is not None
        else math.inf
    )
    reject_localization = power_loss > 0.05 or delay_loss_fraction > 0.10
    combined_losses = {}
    reject_sparse_sufficiency = False
    for carrier, record in results.items():
        loss = record["full_graph_power"] - record["sparse_combined_power"]
        sparse_delay = record["sparse_combined_median_delay"]
        full_graph_delay = record["full_graph_median_delay"]
        delay_fraction = (
            (sparse_delay - full_graph_delay) / max(abs(full_graph_delay), 1.0)
            if sparse_delay is not None and full_graph_delay is not None
            else math.inf
        )
        combined_losses[carrier] = {
            "power_loss": loss,
            "delay_loss_fraction": delay_fraction,
        }
        reject_sparse_sufficiency = reject_sparse_sufficiency or loss > 0.05 or delay_fraction > 0.10
    return {
        "schema_version": "0.1-experimental",
        "trials": trials,
        "steps": steps,
        "change_time": change_time,
        "drift_mean": drift_mean,
        "alpha": alpha,
        "seed": seed,
        "results": results,
        "opaque_injection_power_loss": power_loss,
        "opaque_injection_delay_loss_fraction": delay_loss_fraction,
        "reject_operational_localization": reject_localization,
        "sparse_combined_losses": combined_losses,
        "reject_sparse_monitoring_sufficiency": reject_sparse_sufficiency,
        "regenerated_value_boundary": (
            "Opaque-only monitoring has no theorem-level claim for regenerated "
            "drift; any power loss is reported as a boundary result."
        ),
        "conditional_monitoring_statement": (
            "The sparse combined arm covers opaque transported tension plus "
            "regenerative-node innovation residuals. Agreement with full graph is "
            "empirical under this declared generator, not a global theorem."
        ),
    }
