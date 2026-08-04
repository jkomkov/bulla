"""Third-party reproduction profile: the selective-publication detector.

Profile: bulla/spec/reproduction-profile-v0.1-draft.md

The load-bearing property is not that a reproduction receipt verifies. It is
that a reproducing party which attempts four reproductions and publishes three
is detectable by reconciling the published reports against the attempt register
it fixed beforehand. That reconciliation is the existing `event_coverage`; this
profile adds no machinery to it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bulla.action_receipt import verify_receipt
from bulla.coverage import event_coverage
from bulla.wrap import receipt_for

EXAMPLES = Path(__file__).resolve().parents[1] / "spec" / "reproduction-examples"
RESULT_STATUSES = {"reproduced", "diverged", "inconclusive", "blocked"}


def _report(event_id: str) -> dict:
    """One published finding, carrying the id of the attempt it reports."""
    return receipt_for("reproduction.report", {"event_id": event_id})


# ── the detector ──────────────────────────────────────────────────────────


def test_selective_publication_is_detected() -> None:
    """Four attempts, three published reports: the fourth is named."""
    register = [
        {"id": "rep-1"},
        {"id": "rep-2"},
        {"id": "rep-3"},
        {"id": "rep-4"},
    ]
    published = [_report("rep-1"), _report("rep-2"), _report("rep-3")]

    report = event_coverage(register, published, anchor="reproduction-register")

    assert report["unreceipted_delta"] == ["rep-4"]
    assert report["total_anchored"] == 4
    assert report["receipted"] == 3
    assert report["coverage"] == 0.75


def test_a_diverging_result_still_has_to_be_published() -> None:
    """Publishing only agreeing findings is the same defect as publishing none."""
    register = [{"id": "rep-1"}, {"id": "rep-2"}]
    # rep-2 diverged and was quietly dropped.
    report = event_coverage(register, [_report("rep-1")], anchor="reproduction-register")
    assert report["unreceipted_delta"] == ["rep-2"]


def test_complete_publication_reconciles() -> None:
    register = [{"id": "rep-1"}, {"id": "rep-2"}]
    published = [_report("rep-1"), _report("rep-2")]
    report = event_coverage(register, published, anchor="reproduction-register")
    assert report["unreceipted_delta"] == []
    assert report["coverage"] == 1.0


def test_a_report_for_an_unregistered_attempt_is_phantom() -> None:
    """A report naming an attempt the register never held does not cover anything."""
    register = [{"id": "rep-1"}]
    published = [_report("rep-1"), _report("rep-99")]
    report = event_coverage(register, published, anchor="reproduction-register")
    assert report["phantom_receipt_ids"] == ["rep-99"]
    assert report["unreceipted_delta"] == []


def test_register_fails_closed_on_a_duplicate_id() -> None:
    """A register the reporter can silently collapse reconciles against nothing."""
    with pytest.raises(ValueError):
        event_coverage([{"id": "rep-1"}, {"id": "rep-1"}], [], anchor="reproduction-register")


# ── the example receipts ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["attempt-reproduced.json", "attempt-diverged.json", "attempt-blocked.json"]
)
def test_example_receipt_verifies(name: str) -> None:
    receipt = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    verification = verify_receipt(receipt)
    assert verification.ok
    assert verification.verified_to == "digest"
    assert receipt["action"]["type"] == "reproduction.attempt"
    result = receipt["action"]["subject"]["result"]
    assert result["status"] in RESULT_STATUSES
    # The matching key the reconciliation depends on is content-bound.
    assert receipt["action"]["subject"]["event_id"]


def test_blocked_attempt_records_the_refusal() -> None:
    """An attempt that never ran still names what was asked, of whom, and the answer."""
    receipt = json.loads((EXAMPLES / "attempt-blocked.json").read_text(encoding="utf-8"))
    block = receipt["action"]["subject"]["result"]["block"]
    for field in ("requested", "requested_from", "requested_at", "response"):
        assert block[field], f"blocked attempt must record {field}"
    assert block["response"] in {"denied", "unanswered", "conditioned", "withdrawn"}


def test_diverged_attempt_carries_its_divergences() -> None:
    receipt = json.loads((EXAMPLES / "attempt-diverged.json").read_text(encoding="utf-8"))
    result = receipt["action"]["subject"]["result"]
    assert result["status"] == "diverged"
    assert result["divergences"], "a divergence claim must name the divergences"


def test_tampering_with_a_result_fails_closed() -> None:
    """A published finding cannot be edited after the fact and still verify."""
    receipt = json.loads((EXAMPLES / "attempt-diverged.json").read_text(encoding="utf-8"))
    assert verify_receipt(receipt).ok
    receipt["action"]["subject"]["result"]["status"] = "reproduced"
    assert not verify_receipt(receipt).ok


def test_examples_are_reproducible_from_the_generator() -> None:
    """The committed examples match what the generator emits."""
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "generate.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
