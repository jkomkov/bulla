"""Stable first-action demonstration: automatic receipt, tamper, omission, drill."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from bulla.action_receipt import verify_receipt
from bulla.first_action_demo import FirstActionDemoError, run_first_action_demo


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bulla", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_complete_demo_emits_receipt_and_finds_bypass(tmp_path: Path) -> None:
    root, report = run_first_action_demo(tmp_path / "demo")

    assert report["demo_version"] == "1"
    assert report["receipt"]["schema_version"] == "0.2"
    assert report["receipt"]["automatically_emitted"] is True
    assert report["verification"]["record_integrity"] == "VERIFIED"
    assert report["verification"]["declared_bounds"] == "CONFORMS"
    assert report["verification"]["evidence_grounding"] == "SELF_ASSERTED"
    assert report["verification"]["authority"] == "UNAUTHENTICATED"
    assert report["verification"]["underlying_event_occurrence"] == "NOT_ESTABLISHED"
    assert report["verification"]["reliance"] == "NOT_COMPUTED"
    assert report["tamper_control"]["record_integrity"] == "FAILED"
    assert report["tamper_control"]["original_receipt_unchanged"] is True
    assert report["coverage"]["before"]["receipted"] == 1
    assert report["coverage"]["before"]["total_anchored"] == 1
    assert report["coverage"]["after"]["receipted"] == 1
    assert report["coverage"]["after"]["total_anchored"] == 2
    assert report["coverage"]["after"]["unreceipted_delta"] == ["pay_demo_043"]
    assert report["drill"]["verifier"]["checker_agreement"] == "MATCH"
    assert report["drill"]["verifier"]["tamper_control"] == "REJECTED"

    receipt_path = root / report["receipt"]["path"]
    raw = receipt_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == report["receipt"]["sha256"]
    assert verify_receipt(json.loads(raw)).ok is True
    assert set(report["artifacts"].values()) == {
        "receiver-actions.json",
        "receipts/pay_demo_042.json",
        "controls/pay_demo_042.amount-changed.json",
        "reports/verification.json",
        "reports/coverage-before.json",
        "reports/coverage-after.json",
        "reports/drill.json",
        "report.json",
    }
    for relative in report["artifacts"].values():
        assert (root / relative).is_file()


def test_demo_artifacts_are_byte_deterministic(tmp_path: Path) -> None:
    first_root, first = run_first_action_demo(tmp_path / "first")
    second_root, second = run_first_action_demo(tmp_path / "second")

    assert first == second
    for relative in first["artifacts"].values():
        assert (first_root / relative).read_bytes() == (second_root / relative).read_bytes()


def test_demo_refuses_existing_or_symlink_output(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FirstActionDemoError, match="nonexistent"):
        run_first_action_demo(existing)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(FirstActionDemoError, match="nonexistent"):
        run_first_action_demo(link)

    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(FirstActionDemoError, match="must not traverse a symlink"):
        run_first_action_demo(parent_link / "new-demo")


def test_demo_main_process_does_not_use_network(monkeypatch, tmp_path: Path) -> None:
    def denied(*_args, **_kwargs):
        raise AssertionError("network entry point used")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    _, report = run_first_action_demo(tmp_path / "offline")
    assert report["drill"]["verifier"]["network_guard"] == "PYTHON_AUDIT_HOOK"


def test_cli_json_and_text_contract(tmp_path: Path) -> None:
    json_root = tmp_path / "json"
    result = _run_cli("demo", "--out", str(json_root), "--format", "json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["demo_version"] == "1"
    assert payload["output_directory"] == str(json_root.resolve())
    assert payload["coverage"]["after"]["unreceipted_delta"] == ["pay_demo_043"]

    text_root = tmp_path / "text"
    result = _run_cli("demo", "--out", str(text_root))
    assert result.returncode == 0, result.stderr
    assert "FIRST ACTION DEMO · CONSTRUCTED LOCAL SCENARIO" in result.stdout
    assert "coverage before       1/1" in result.stdout
    assert "coverage after        1/2" in result.stdout
    assert "unreceipted action    pay_demo_043" in result.stdout
    assert "Neither record establishes that funds moved." in result.stdout


def test_cli_refuses_existing_output_with_exit_2(tmp_path: Path) -> None:
    root = tmp_path / "already-there"
    root.mkdir()
    result = _run_cli("demo", "--out", str(root))
    assert result.returncode == 2
    assert "must name a nonexistent path" in result.stderr


def test_bare_cli_leads_with_receipts() -> None:
    result = _run_cli()
    assert result.returncode == 0
    # Windows runners use a cp1252 console unless UTF-8 is explicitly enabled.
    # Bare help is a first-contact path and must not crash before rendering.
    result.stdout.encode("cp1252")
    lines = result.stdout.splitlines()
    assert "receipts for consequential agent actions" in lines[0]
    assert lines.index("  bulla demo                     # action -> receipt -> alteration -> omission") < lines.index("Composition diagnostics:")
