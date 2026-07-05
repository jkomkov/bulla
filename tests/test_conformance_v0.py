"""Recourse-conformance v0 — every scenario runs as a test.

The suite IS the category definition's constructive half (Recourse Ladder
paper, pre-registered): what a relying party can verifiably do against a
host-controlling adversary with {signature, log, anchor, omission-proof} —
and what the appeal path must look like when no stateful respondent exists.
"""

from __future__ import annotations

import pytest

from bulla.conformance import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_scenario(scenario):
    assert scenario.check(), f"{scenario.id} [{scenario.group}] {scenario.title}"
