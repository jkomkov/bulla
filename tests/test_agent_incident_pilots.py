from __future__ import annotations

import json
from pathlib import Path

import pytest

from bulla.action_receipt import verify_receipt
from bulla.experimental.incident_pilots import (
    DENOMINATOR_PROVENANCE,
    _http_post,
    _mcp_tcp_call,
    run_http_pilot,
    run_mcp_pilot,
    summarize_coverage,
)
from bulla.experimental.incident_packet import (
    IncidentVerificationContext,
    verify_incident_packet,
)


@pytest.mark.parametrize(
    "runner,protocol",
    [(run_http_pilot, "http"), (run_mcp_pilot, "mcp")],
)
def test_live_pilot_exposes_direct_bypass(
    tmp_path: Path,
    runner,
    protocol: str,
) -> None:
    output = tmp_path / protocol
    run = runner(output)
    result = summarize_coverage(run)

    assert result["decision"]["coverage"] == "2/2"
    assert result["decision"]["uncovered"] == []
    assert result["effect"]["coverage"] == "1/2"
    assert len(result["effect"]["uncovered"]) == 1
    assert result["denominator_provenance"] == DENOMINATOR_PROVENANCE
    assert all(row["protocol"] == protocol for row in run.effect_observations)

    receipt_files = sorted((output / "receipts").glob("*.json"))
    assert len(receipt_files) == 4
    for receipt_file in receipt_files:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        assert receipt["schema_version"] == "0.4"
        verdict = verify_receipt(receipt)
        assert verdict.ok
        assert verdict.verified_to == "attestation"

    context = IncidentVerificationContext.from_dict(
        json.loads((output / "context.json").read_text(encoding="utf-8"))
    )
    verification = verify_incident_packet(output / "packet", context)
    assert verification.exit_code == 0
    assert verification.coverage_status == "COMPUTED"
    assert {
        (row.phase, row.receipted, row.total, len(row.uncovered_ids))
        for row in verification.coverage
    } == {("decision", 2, 2, 0), ("effect", 1, 2, 1)}
    assert verification.reliance == "NOT_COMPUTED"


def test_mcp_pilot_writes_only_fixed_runtime_value(tmp_path: Path) -> None:
    output = tmp_path / "mcp"
    run_mcp_pilot(output)
    assert (output / "runtime" / "fixed-append.txt").read_text(
        encoding="utf-8"
    ) == "incident-pilot-fixed-value\nincident-pilot-fixed-value\n"


def test_pilots_reject_implicit_output_location() -> None:
    scripts = Path(__file__).parents[1] / "examples" / "agent-incident-packet"
    for name in ("run_http_pilot.py", "run_mcp_pilot.py"):
        source = (scripts / name).read_text(encoding="utf-8")
        assert 'required=True' in source
        assert 'default=' not in source


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:4444/act",
        "http://localhost:4444/act",
        "http://[::1]:4444/act",
        "http://user:secret@127.0.0.1:4444/act",
        "http://127.0.0.1:4444/act?next=external",
        "http://127.0.0.1:4444/act#fragment",
        "http://127.0.0.1:4444/CONNECT",
        "http://127.0.0.1/act",
        "http://127.0.0.1:80/act",
    ],
)
def test_http_helper_rejects_non_closed_destination_before_connect(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    connected = False

    def unexpected_connect(*_args, **_kwargs):
        nonlocal connected
        connected = True
        raise AssertionError("network call must not occur")

    monkeypatch.setattr(
        "bulla.experimental.incident_pilots._LOCAL_HTTP_OPENER.open",
        unexpected_connect,
    )
    with pytest.raises(ValueError):
        _http_post(url, {}, allowed_path="/act")
    assert connected is False


@pytest.mark.parametrize("port", [False, 0, 80, 65536, "4444"])
def test_mcp_helper_rejects_invalid_port_before_connect(
    monkeypatch: pytest.MonkeyPatch,
    port,
) -> None:
    connected = False

    def unexpected_connect(*_args, **_kwargs):
        nonlocal connected
        connected = True
        raise AssertionError("network call must not occur")

    monkeypatch.setattr(
        "bulla.experimental.incident_pilots.socket.create_connection",
        unexpected_connect,
    )
    with pytest.raises(ValueError):
        _mcp_tcp_call(port, {})
    assert connected is False
