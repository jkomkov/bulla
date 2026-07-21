"""Receipt coverage — omission detection against a declared anchor.

The unit tests are deterministic (a synthetic anchored set), never depending on
live git tags: coverage is a plain set difference and must be provable as one.
"""

from __future__ import annotations

from pathlib import Path

from bulla.coverage import (
    coverage_headline,
    coverage_report,
    inspect_release_receipts,
    is_package_release_tag,
    is_strict_semver,
    pypi_coverage,
    pypi_release_versions,
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


def _pypi_file(filename: str, digest: str) -> dict:
    return {"filename": filename, "digests": {"sha256": digest.removeprefix("sha256:")}}


def test_pypi_is_primary_release_record_and_candidates_stay_separate():
    release_040 = __import__("json").loads((_RELEASES / "0.40.0.json").read_text())
    evidence = {item["name"]: item["hash"] for item in release_040["evidence_refs"]}
    project_doc = {
        "releases": {
            "0.40.0": [
                _pypi_file("bulla-0.40.0-py3-none-any.whl", evidence["wheel"]),
                _pypi_file("bulla-0.40.0.tar.gz", evidence["sdist"]),
            ],
            "0.41.0": [
                _pypi_file("bulla-0.41.0-py3-none-any.whl", "sha256:" + "1" * 64),
                _pypi_file("bulla-0.41.0.tar.gz", "sha256:" + "2" * 64),
            ],
            # An experiment label is not a package release.
            "0.41.0-replication!": [_pypi_file("bad", "sha256:" + "3" * 64)],
        }
    }
    report = pypi_coverage(
        _RELEASES,
        project_doc=project_doc,
        verify_integrity=False,
    )
    assert report["status_counts"] == {
        "contemporaneous": 0,
        "reconstructed": 1,
        "missing": 1,
        "invalid": 0,
    }
    assert report["releases"][0]["status"] == "reconstructed"
    assert report["releases"][1]["status"] == "missing"
    assert [item["version"] for item in report["candidates"]] == ["0.43.0"]


def test_receipt_inventory_verifies_before_counting():
    inventory = inspect_release_receipts(_RELEASES)
    assert {item["version"] for item in inventory["receipts"]} >= {"0.37.0", "0.40.0", "0.43.0"}
    candidate = next(item for item in inventory["receipts"] if item["version"] == "0.43.0")
    assert candidate["provenance"] == "candidate"


def test_strict_semver_rejects_tag_pollution():
    assert is_strict_semver("v0.44.0")
    assert is_strict_semver("v0.44.0-rc.1")
    assert not is_strict_semver("v0.1.0-replication!")
    assert not is_strict_semver("release-v0.44.0")


def test_package_release_tags_exclude_candidates_and_experiment_labels():
    assert is_package_release_tag("v0.44.0")
    assert not is_package_release_tag("0.44.0")
    assert not is_package_release_tag("v0.44.0-rc.1")
    assert not is_package_release_tag("v0.1.0-replication")


def test_pypi_release_versions_excludes_empty_and_non_semver_releases():
    doc = {
        "releases": {
            "0.44.0": [{"filename": "x"}],
            "0.43.0": [{"filename": "y"}],
            "0.42.0": [],
            "nightly": [{"filename": "z"}],
        }
    }
    assert pypi_release_versions(doc) == ["0.43.0", "0.44.0"]
