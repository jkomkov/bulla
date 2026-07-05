"""Adversarial seam-probing harness — systematic failure manufacturing.

Generates a grid of single-field probes across dimension × visibility ×
mismatch, executes each against seam_backend in permissive mode, classifies
consequences by correctness oracle, and outputs JSONL for downstream
analysis (WS8 sign test).

Consequence taxonomy (correctness-shaped, not exception-shaped):
  GUARDED_REJECT    — consumer raised an error (strict-mode only, real servers)
  SILENT_CORRUPTION — no error, but wrong result (the dangerous case)
  CORRECT_PASS      — mismatch present but result correct (visible, normalized)
  EXPECTED_CLEAN    — no mismatch was present (control probe)

The ``is_failure`` property covers both GUARDED_REJECT and SILENT_CORRUPTION,
making downstream analysis uniform regardless of whether the consumer crashed
or silently corrupted.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from calibration.harness.live_validation import (
    Dim,
    Seam,
    SeamResult,
    run_seam,
    schema_metrics,
)

# The three dimensions supported by the seam_backend positive control.
_DIMENSIONS = ("encoding", "index", "unit")


# ── Consequence enum ──────────────────────────────────────────────────────


class Consequence(Enum):
    """Typed consequence of an adversarial probe, derived from (raised, correct)."""

    GUARDED_REJECT = "guarded_reject"
    SILENT_CORRUPTION = "silent_corruption"
    CORRECT_PASS = "correct_pass"
    EXPECTED_CLEAN = "expected_clean"

    @property
    def is_failure(self) -> bool:
        return self in (Consequence.GUARDED_REJECT, Consequence.SILENT_CORRUPTION)


# ── Ground truth oracle ──────────────────────────────────────────────────
#
# What the permissive consumer SHOULD return for each dimension when
# the producer's intended meaning is correctly decoded.
#
# encoding: the producer always encodes "café-déjà" — correct decode is that string
# index:    the producer points at the last element of range(5) — element 4
# unit:     the consumer convention is always celsius (22.0°C room temperature)

_GROUND_TRUTH: dict[str, dict[str, Any]] = {
    "encoding": {"decoded": "café-déjà"},
    "index": {"element": 4},
    "unit": {"value": 20.0},  # 20°C = 68°F (exact inverse)
}


def _check_correct(dim_name: str, result: dict[str, Any] | None) -> bool:
    """Compare a single dimension's computed result against ground truth."""
    if result is None:
        return False
    expected = _GROUND_TRUTH.get(dim_name)
    if expected is None:
        return False
    return result == expected


# ── Consequence classification ────────────────────────────────────────────


def classify_consequence(
    mismatch: bool,
    raised: bool,
    correct: bool | None,
) -> Consequence:
    """Assign consequence from (mismatch, raised, correct).

    Args:
        mismatch: whether a convention mismatch was present in the probe
        raised: whether the consumer raised an exception
        correct: whether the computed result matched ground truth (None if unknown)
    """
    if not mismatch:
        return Consequence.EXPECTED_CLEAN
    if raised:
        return Consequence.GUARDED_REJECT
    if correct:
        return Consequence.CORRECT_PASS
    return Consequence.SILENT_CORRUPTION


# ── Label provenance ──────────────────────────────────────────────────────
#
# The single most important antifragility property of this pipeline: a
# downstream analyzer must never silently treat a circular label as a
# falsification test. On the constructed seam_backend, the failure label and
# fee_d are both functions of (mismatch, hidden) by construction, so a "green"
# sign test is plumbing validation, not prediction evidence. We make that
# guarantee *structural* rather than a prose caveat by stamping every row with
# where its label came from. ``sign_test`` refuses to emit a citable verdict
# unless provenance is EXECUTION_INDEPENDENT.


class LabelProvenance(Enum):
    """Where a probe's failure label came from, relative to the fee.

    CONSTRUCTED: the harness authored both the failure mechanism and the fee
        inputs, so the label is not independent of the fee. Suitable only for
        construct-validity (plumbing) checks.
    EXECUTION_INDEPENDENT: the label came from executing a backend the fee did
        not help construct (a real MCP server, a third-party trace). A sign
        test on these labels is a genuine non-circular falsification test.
    """

    CONSTRUCTED = "constructed"
    EXECUTION_INDEPENDENT = "execution_independent"


@dataclass(frozen=True)
class ProbeResult:
    """Classified consequence of one adversarial probe."""

    label: str
    dimension: str
    visible: bool
    mismatch: bool
    fee: int
    fee_d: int
    observable_distance: int
    consequence: Consequence
    error_message: str | None
    dropped: bool
    # Probes generated against the constructed seam_backend are, by definition,
    # not fee-independent. Real-backend ingestion sets EXECUTION_INDEPENDENT.
    provenance: LabelProvenance = LabelProvenance.CONSTRUCTED


def default_probes() -> list[Seam]:
    """Systematic grid: 3 dimensions × 2 visibility × 2 mismatch = 12 probes."""
    probes: list[Seam] = []
    for dim in _DIMENSIONS:
        for visible in (False, True):
            for mismatch in (False, True):
                vis = "vis" if visible else "hid"
                mis = "mis" if mismatch else "ok"
                label = f"probe_{dim}_{vis}_{mis}"
                probes.append(
                    Seam(label, (Dim(dim, visible=visible, mismatch=mismatch),))
                )
    return probes


def multi_dimension_probes() -> list[Seam]:
    """Two-dimension probes that create discriminating pairs for the sign test.

    Each pair shares the same observable Hamming distance but differs in
    fee_d, so the sign test can ask: does fee_d predict failure rate
    beyond what Hamming already explains?
    """
    probes: list[Seam] = []
    for i, d1 in enumerate(_DIMENSIONS):
        for d2 in _DIMENSIONS[i + 1 :]:
            # Same Hamming (1): one visible mismatch. Different fee:
            # (a) only the visible mismatch — fee = 0
            probes.append(
                Seam(
                    f"multi_{d1}vis_{d2}clean",
                    (
                        Dim(d1, visible=True, mismatch=True),
                        Dim(d2, visible=True, mismatch=False),
                    ),
                )
            )
            # (b) visible mismatch + hidden mismatch — fee > 0
            probes.append(
                Seam(
                    f"multi_{d1}vis_{d2}hid",
                    (
                        Dim(d1, visible=True, mismatch=True),
                        Dim(d2, visible=False, mismatch=True),
                    ),
                )
            )
    return probes


def _to_probe_result(seam: Seam, sr: SeamResult) -> ProbeResult:
    """Convert SeamResult to ProbeResult with correctness-based consequence."""
    # Use the first dimension for single-dim probes; for multi-dim,
    # use the first hidden-mismatched dimension if any.
    primary = seam.dims[0]
    for d in seam.dims:
        if not d.visible and d.mismatch:
            primary = d
            break
    fee_d = sr.fee_by_dim.get(primary.name, 0)
    has_mismatch = any(d.mismatch for d in seam.dims)

    if sr.dropped:
        consequence = Consequence.EXPECTED_CLEAN  # placeholder for dropped
    elif sr.failed:
        consequence = classify_consequence(has_mismatch, raised=True, correct=None)
    else:
        # Check correctness from permissive consumer results.
        correct = True
        if sr.consumer_results is not None:
            for d in seam.dims:
                if d.load_bearing:
                    dim_result = sr.consumer_results.get(d.name)
                    if not _check_correct(d.name, dim_result):
                        correct = False
                        break
        consequence = classify_consequence(has_mismatch, raised=False, correct=correct)

    return ProbeResult(
        label=sr.label,
        dimension=primary.name,
        visible=primary.visible,
        mismatch=primary.mismatch,
        fee=sr.fee,
        fee_d=fee_d,
        observable_distance=sr.observable_distance,
        consequence=consequence,
        error_message=sr.error,
        dropped=sr.dropped,
        # Probes from the constructed seam_backend are never fee-independent.
        provenance=LabelProvenance.CONSTRUCTED,
    )


async def run_probes(
    probes: list[Seam] | None = None,
) -> list[ProbeResult]:
    """Execute all probes in permissive mode and return classified results."""
    probes = probes or (default_probes() + multi_dimension_probes())
    results: list[ProbeResult] = []
    for seam in probes:
        sr = await run_seam(seam, permissive=True)
        results.append(_to_probe_result(seam, sr))
    return results


def write_jsonl(results: list[ProbeResult], path: Path) -> None:
    """Write results as newline-delimited JSON for downstream analysis."""
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(
                json.dumps(
                    {
                        "label": r.label,
                        "dimension": r.dimension,
                        "visible": r.visible,
                        "mismatch": r.mismatch,
                        "fee": r.fee,
                        "fee_d": r.fee_d,
                        "observable_distance": r.observable_distance,
                        "consequence": r.consequence.value,
                        "error_message": r.error_message,
                        "dropped": r.dropped,
                        "provenance": r.provenance.value,
                    }
                )
                + "\n"
            )


def main() -> None:
    results = asyncio.run(run_probes())
    runnable = [r for r in results if not r.dropped]
    dropped = [r for r in results if r.dropped]
    print(f"# Adversarial probe: {len(runnable)} runnable, {len(dropped)} dropped\n")
    hdr = (
        f"{'label':35s} {'fee':>3s} {'fee_d':>5s} "
        f"{'obsDist':>7s} {'consequence':>18s}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        if r.dropped:
            print(f"{r.label:35s}  DROPPED: {r.error_message}")
            continue
        print(
            f"{r.label:35s} {r.fee:3d} {r.fee_d:5d} "
            f"{r.observable_distance:7d} {r.consequence.value:>18s}"
        )


if __name__ == "__main__":
    main()
