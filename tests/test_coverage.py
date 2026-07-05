"""Receipt coverage — omission detection against a declared anchor.

The unit tests are deterministic (a synthetic anchored set), never depending on
live git tags: coverage is a plain set difference and must be provable as one.
"""

from __future__ import annotations

from pathlib import Path

from bulla.coverage import (
    coverage_headline,
    coverage_report,
    receipted_release_versions,
)

_RELEASES = Path(__file__).resolve().parents[1] / "releases"


def test_coverage_is_a_set_difference():
    rep = coverage_report("git", ["v0.30.0", "v0.36.0", "v0.40.0"], {"0.40.0": "r/0.40.0.json"})
    assert rep["coverage"] == round(1 / 3, 4)
    assert rep["unreceipted_delta"] == ["v0.30.0", "v0.36.0"]  # anchored, no receipt
    assert rep["covered"] == ["v0.40.0"]


def test_normalization_joins_tag_to_version():
    # git tag 'v0.40.0' must match a receipt keyed by version '0.40.0'
    rep = coverage_report("git", ["v0.40.0"], {"0.40.0": "x"})
    assert rep["coverage"] == 1.0 and not rep["unreceipted_delta"]


def test_empty_anchor_is_full_coverage_not_zero_division():
    rep = coverage_report("git", [], {})
    assert rep["coverage"] == 1.0 and rep["total_anchored"] == 0


def test_headline_is_min_over_anchors_and_names_the_weakest():
    strong = coverage_report("cloud", ["a"], {"a": "x"})           # 100%
    weak = coverage_report("git", ["a", "b"], {"a": "x"})          # 50%
    assert coverage_headline([strong, weak]) == "Coverage: 50% (weakest anchor: git)"


def test_reads_release_versions_from_corpus():
    if not _RELEASES.is_dir():
        return
    got = receipted_release_versions(_RELEASES)
    # the committed corpus is the two real published releases
    assert "0.40.0" in got and "0.37.0" in got
