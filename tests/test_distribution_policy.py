"""Release-boundary tests for the curated Bulla distribution."""

from __future__ import annotations

import json
import importlib.util
import io
from pathlib import Path
import stat
import tarfile
import zipfile

import bulla
import bulla.experimental
import pytest


ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "distribution-policy.json").read_text(encoding="utf-8"))
SPEC = importlib.util.spec_from_file_location(
    "verify_distribution_policy",
    ROOT / "scripts/verify_distribution_policy.py",
)
assert SPEC is not None and SPEC.loader is not None
DISTRIBUTION_GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISTRIBUTION_GATE)

EXPECTED_STANDALONE_TEST_EXCLUSIONS = {
    "tests/test_control_plane_alpha_protocol.py": (
        "requires the monorepo Git index and control-plane fixture tree"
    ),
    "tests/test_control_plane_deployment_receipt.py": (
        "requires Glyph control-plane evidence outside the Bulla subtree"
    ),
    "tests/test_control_plane_deployment_workflow.py": (
        "requires root workflows and the Worker package outside the Bulla subtree"
    ),
    "tests/test_query_answerability.py": (
        "requires monorepo paper fixtures and source-only CLI commands"
    ),
}


def test_distribution_policy_matches_version_and_public_exports() -> None:
    assert POLICY["release"] == bulla.__version__ == "0.45.1"
    assert POLICY["normative_action_receipt"] == "0.2"
    assert set(POLICY["required_root_exports"]) <= set(bulla.__all__)


def test_source_only_modules_are_not_aggregate_experimental_exports() -> None:
    exported = set(bulla.experimental.__all__)
    for module in POLICY["source_only_modules"]:
        leaf = module.rsplit(".", 1)[-1]
        assert leaf not in exported
    for forbidden in (
        "ActionBoundary",
        "ChallengeCase",
        "GeneralizationFrontier",
    ):
        assert forbidden not in exported


def test_hatch_excludes_every_declared_source_only_member() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for member in POLICY["forbidden_sdist_prefixes"]:
        if member.endswith("/"):
            member = member[:-1]
        assert f'"/{member}"' in pyproject


def test_packaged_cli_does_not_register_source_only_commands() -> None:
    cli = (ROOT / "src/bulla/cli.py").read_text(encoding="utf-8")
    for command in POLICY["forbidden_cli_subcommands"]:
        assert f'experimental_sub.add_parser(\n        "{command}"' not in cli


def test_standalone_test_selection_follows_the_distribution_policy() -> None:
    assert (
        POLICY["standalone_test_exclusions"]
        == EXPECTED_STANDALONE_TEST_EXCLUSIONS
    )
    selected = set(
        DISTRIBUTION_GATE.standalone_test_paths(
            ROOT / "distribution-policy.json", ROOT
        )
    )
    excluded = set(EXPECTED_STANDALONE_TEST_EXCLUSIONS)
    discovered = set(DISTRIBUTION_GATE._discover_pytest_paths(ROOT))

    assert selected == discovered - excluded
    assert excluded.isdisjoint(selected)
    assert "tests/test_action_receipt.py" in selected
    assert "tests/test_action_boundary.py" in selected
    assert "tests/test_agent_incident_packet.py" in selected
    assert "tests/test_distribution_policy.py" in selected
    assert "tests/test_query_answerability.py" not in selected
    assert "tests/test_control_plane_deployment_workflow.py" not in selected


def test_standalone_selection_uses_both_default_pytest_name_patterns(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_prefix.py").write_text("", encoding="utf-8")
    (tests / "suffix_test.py").write_text("", encoding="utf-8")
    (tests / "helper.py").write_text("", encoding="utf-8")

    assert DISTRIBUTION_GATE._discover_pytest_paths(tmp_path) == [
        "tests/suffix_test.py",
        "tests/test_prefix.py",
    ]


def test_standalone_selection_rejects_an_extra_exclusion(tmp_path: Path) -> None:
    policy = dict(POLICY)
    exclusions = dict(EXPECTED_STANDALONE_TEST_EXCLUSIONS)
    exclusions["tests/test_distribution_policy.py"] = "disable the policy gate"
    policy["standalone_test_exclusions"] = exclusions
    policy_path = tmp_path / "distribution-policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(
        DISTRIBUTION_GATE.DistributionError,
        match="must retain the exact standalone test exclusions",
    ):
        DISTRIBUTION_GATE.standalone_test_paths(policy_path, ROOT)


def test_exact_member_commitment_rejects_an_undeclared_archive_member() -> None:
    members = {"bulla/__init__.py", "bulla/receipt_parser.py"}
    commitment = {
        "algorithm": "sha256-sorted-posix-lines-v1",
        "count": 2,
        "sha256": (
            "c1697917312112b105af5a41d5c606a15a6c5443e06f09d61e41a2e79fe258d0"
        ),
    }
    DISTRIBUTION_GATE._require_exact_member_set(members, commitment, "test")
    with pytest.raises(
        DISTRIBUTION_GATE.DistributionError, match="member set is undeclared"
    ):
        DISTRIBUTION_GATE._require_exact_member_set(
            members | {"bulla/undeclared.py"}, commitment, "test"
        )


def test_sdist_rejects_symlinks_and_duplicate_members(tmp_path: Path) -> None:
    symlink_archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(symlink_archive, "w:gz") as archive:
        member = tarfile.TarInfo("bulla-0.44.4/src/bulla/undeclared.py")
        member.type = tarfile.SYMTYPE
        member.linkname = "/tmp/undeclared.py"
        archive.addfile(member)
    with pytest.raises(
        DISTRIBUTION_GATE.DistributionError, match="non-regular member"
    ):
        DISTRIBUTION_GATE._sdist_members(symlink_archive, "0.44.4")

    duplicate_archive = tmp_path / "duplicate.tar.gz"
    with tarfile.open(duplicate_archive, "w:gz") as archive:
        for payload in (b"first", b"second"):
            member = tarfile.TarInfo("bulla-0.44.4/src/bulla/repeated.py")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    with pytest.raises(
        DISTRIBUTION_GATE.DistributionError, match="duplicate member"
    ):
        DISTRIBUTION_GATE._sdist_members(duplicate_archive, "0.44.4")


def test_wheel_rejects_duplicate_members(tmp_path: Path) -> None:
    wheel = tmp_path / "duplicate.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for payload in ("first", "second"):
            member = zipfile.ZipInfo("bulla/repeated.py")
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(member, payload)
    with pytest.raises(
        DISTRIBUTION_GATE.DistributionError, match="duplicate member"
    ):
        DISTRIBUTION_GATE._wheel_members(wheel)


@pytest.mark.parametrize(
    "member",
    (
        "bulla/./action_receipt.py",
        "./bulla/action_receipt.py",
        "bulla//action_receipt.py",
    ),
)
def test_wheel_rejects_noncanonical_member_aliases(
    tmp_path: Path, member: str
) -> None:
    wheel = tmp_path / "noncanonical.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        info = zipfile.ZipInfo(member)
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, "payload")
    with pytest.raises(
        DISTRIBUTION_GATE.DistributionError, match="unsafe archive member"
    ):
        DISTRIBUTION_GATE._wheel_members(wheel)


@pytest.mark.parametrize("member", ("./README.md", "docs//guide.md"))
def test_sdist_rejects_noncanonical_member_aliases(
    tmp_path: Path, member: str
) -> None:
    archive_path = tmp_path / "noncanonical.tar.gz"
    payload = b"payload"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo(f"bulla-0.44.4/{member}")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(
        DISTRIBUTION_GATE.DistributionError, match="unsafe archive member"
    ):
        DISTRIBUTION_GATE._sdist_members(archive_path, "0.44.4")
