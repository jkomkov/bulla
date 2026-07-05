"""Construct-validity assertions for the live-execution positive control.

These run real producer/consumer subprocesses (``seam_backend.py``) and check
that the SCHEMA channel (fee) and the EXECUTION channel (real failure) line up
the way the witness theory claims. This is a positive control, not a
generalization experiment — see ``calibration/harness/live_validation.py``.

The supported, honest claims (and nothing more):

  R. Recall / soundness as a filter:  fee == 0  =>  the seam does not fail.
     Equivalently, every real failure sits on a dimension with fee_d >= 1.

  B. Observable blind spot:  there exist seams that fail at runtime while their
     observable convention-distance is 0 — i.e. the failures are invisible to a
     pairwise schema checker but caught by the (cohomological) fee.

  P. Honest imprecision:  fee_d >= 1 does NOT imply failure (a hidden coupling
     whose conventions happen to agree has fee but does not fail). The fee marks
     an at-risk *coupling*, not a confirmed value mismatch.
"""

from __future__ import annotations

import asyncio

import pytest

from calibration.harness.live_validation import default_seams, run_all


@pytest.fixture(scope="module")
def results():
    res = asyncio.run(run_all(default_seams()))
    # The control is meaningless if backends could not be spawned.
    dropped = [r.label for r in res if r.dropped]
    assert not dropped, f"backends failed to spawn for: {dropped}"
    return res


def test_mechanism_is_faithful(results):
    """A seam fails at runtime iff it has a hidden, mismatched, load-bearing
    dimension — confirming the constructed failures are genuine, not stipulated."""
    for r in results:
        expected_fail = len(r.hidden_mismatch_dims) > 0
        assert r.failed == expected_fail, (
            f"{r.label}: failed={r.failed} but hidden_mismatch={r.hidden_mismatch_dims}; "
            f"error={r.error}"
        )


def test_claim_R_fee_zero_implies_no_failure(results):
    """Perfect recall: fee == 0 => no execution failure."""
    for r in results:
        if r.fee == 0:
            assert not r.failed, f"{r.label}: fee==0 yet failed (recall violation)"


def test_claim_R_every_failure_localizes_to_positive_fee(results):
    """Every failure occurs on a dimension the fee flagged (fee_d >= 1)."""
    failures = [r for r in results if r.failed]
    assert failures, "expected at least one real failure in the control"
    for r in failures:
        for dim in r.hidden_mismatch_dims:
            assert r.fee_by_dim.get(dim, 0) >= 1, (
                f"{r.label}: failing dim {dim!r} has fee_d={r.fee_by_dim.get(dim, 0)}"
            )


def test_claim_B_failures_invisible_to_observable_distance(results):
    """The core thesis: failures exist whose observable convention-distance is 0,
    so a pairwise schema checker would pass them while the fee does not."""
    invisible_failures = [r for r in results if r.failed and r.observable_distance == 0]
    assert invisible_failures, (
        "expected failures invisible to observable distance (obs_dist==0)"
    )
    # And observable distance is not a sound failure detector here: at least one
    # seam has positive observable distance but does not fail (handled mismatch).
    handled = [r for r in results if r.observable_distance > 0 and not r.failed]
    assert handled, "expected a visible-but-handled mismatch (obs_dist>0, no fail)"


def test_claim_P_fee_is_imprecise_on_value_match(results):
    """A hidden coupling whose conventions agree has fee_d>=1 but does not fail —
    the fee marks an at-risk coupling, not a confirmed mismatch."""
    by_label = {r.label: r for r in results}
    hm = by_label["hidden_match_encoding"]
    assert hm.fee >= 1 and not hm.failed, (
        f"hidden_match_encoding: fee={hm.fee} failed={hm.failed} "
        "(expected fee>=1 and no failure)"
    )
