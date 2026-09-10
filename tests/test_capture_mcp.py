"""Doorstep: transparent stdio capture, privacy, and local integrity."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from bulla.action_receipt import verify_receipt
from bulla.capture_mcp import (
    CAPTURE_LIMIT,
    CaptureDirectoryError,
    CaptureError,
    CaptureRootCheckResult,
    allocate_capture_session,
    check_capture_path,
    check_capture_directory,
    check_capture_root,
    run_mcp_capture,
)


_SERVER = r'''
import json
import os
import sys

log_path = os.environ.get("BULLA_CAPTURE_TEST_LOG")
held = []


def emit(value):
    sys.stdout.buffer.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
    sys.stdout.buffer.flush()


for line in sys.stdin.buffer:
    if log_path:
        with open(log_path, "ab") as log:
            log.write(line)
    try:
        message = json.loads(line)
    except Exception:
        continue
    if message.get("method") == "ping":
        emit({"jsonrpc":"2.0","id":message["id"],"result":{"pong":True}})
        for item in held:
            emit({"jsonrpc":"2.0","id":item["id"],"result":{"content":[{"type":"text","text":"ok"}]}})
        held.clear()
        continue
    if message.get("method") != "tools/call":
        continue
    params = message.get("params") or {}
    arguments = params.get("arguments") or {}
    mode = arguments.get("mode", "ok")
    if mode == "exit":
        raise SystemExit(7)
    if mode == "hold_for_ping":
        held.append(message)
        continue
    if mode == "no_response":
        continue
    if mode == "reverse":
        held.append(message)
        if len(held) < 2:
            continue
        for item in reversed(held):
            emit({"jsonrpc":"2.0","id":item["id"],"result":{"content":[{"type":"text","text":"ok"}]}})
        held.clear()
        continue
    if mode == "messages":
        emit({"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}})
        emit({"jsonrpc":"2.0","id":"server-question","method":"sampling/createMessage","params":{}})
        emit({"jsonrpc":"2.0","id":"unknown-response","result":{"ignored":True}})
    if mode == "json_error":
        emit({"jsonrpc":"2.0","id":message["id"],"error":{"code":-32001,"message":"tool failed"}})
    elif mode == "both_result_and_error":
        emit({"jsonrpc":"2.0","id":message["id"],"result":{},"error":{"code":-32001,"message":"tool failed"}})
    elif mode == "tool_error":
        emit({"jsonrpc":"2.0","id":message["id"],"result":{"isError":True,"content":[{"type":"text","text":"failed"}]}})
    else:
        emit({"jsonrpc":"2.0","id":message["id"],"result":{"content":[{"type":"text","text":"ok"}]}})
'''

_SUBSTITUTING_SERVER = r'''
import json
import os
import pathlib
import sys

output = pathlib.Path(os.environ["BULLA_CAPTURE_TEST_OUTPUT"])
redirected = pathlib.Path(os.environ["BULLA_CAPTURE_TEST_REDIRECTED"])
redirected.mkdir()
line = sys.stdin.buffer.readline()
message = json.loads(line)
for name in ("calls", "receipts", "payloads"):
    source = output / name
    target = redirected / name
    os.rename(source, target)
    os.symlink(target, source, target_is_directory=True)
response = {
    "jsonrpc": "2.0",
    "id": message["id"],
    "result": {"content": [{"type": "text", "text": "ok"}]},
}
sys.stdout.buffer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
sys.stdout.buffer.flush()
'''


def _wire(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def _request(identifier=1, *, mode="ok", secret: str | None = None) -> bytes:
    arguments = {"mode": mode}
    if secret is not None:
        arguments["secret"] = secret
    return _wire({
        "jsonrpc": "2.0",
        "id": identifier,
        "method": "tools/call",
        "params": {"name": "demo.echo", "arguments": arguments},
    })


def _response(identifier=1) -> bytes:
    return _wire({
        "jsonrpc": "2.0",
        "id": identifier,
        "result": {"content": [{"type": "text", "text": "ok"}]},
    })


def _write_server(tmp_path: Path) -> Path:
    path = tmp_path / "server.py"
    path.write_text(_SERVER, encoding="utf-8")
    return path


def _cli_env(log: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if env.get("BULLA_CAPTURE_TEST_INSTALLED") == "1":
        env.pop("PYTHONPATH", None)
    else:
        root = Path(__file__).resolve().parents[1]
        env["PYTHONPATH"] = str(root / "src")
    if log is not None:
        env["BULLA_CAPTURE_TEST_LOG"] = str(log)
    return env


def _run_cli(
    tmp_path: Path,
    frames: bytes,
    *,
    retain: bool = False,
    key: Path | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], Path, Path]:
    server = _write_server(tmp_path)
    output = tmp_path / "capture"
    log = tmp_path / "backend-input.bin"
    command = [
        sys.executable, "-m", "bulla", "capture", "mcp", "--out", str(output),
    ]
    if retain:
        command.append("--retain-payloads")
    if key is not None:
        command.extend(("--key", str(key)))
    command.extend(("--", sys.executable, str(server)))
    result = subprocess.run(
        command,
        input=frames,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(log),
        timeout=20,
    )
    return result, output, log


def _run_root_cli(
    root: Path,
    server: Path,
    frames: bytes,
    *,
    retain: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    command = [
        sys.executable, "-m", "bulla", "capture", "mcp",
        "--session-root", str(root),
    ]
    if retain:
        command.append("--retain-payloads")
    command.extend(("--", sys.executable, str(server)))
    return subprocess.run(
        command,
        input=frames,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(),
        timeout=20,
    )


def _one_receipt(output: Path) -> tuple[bytes, dict]:
    paths = list((output / "receipts").glob("*.json"))
    assert len(paths) == 1
    raw = paths[0].read_bytes()
    return raw, json.loads(raw)


def test_one_call_forwards_bytes_and_leaves_unsigned_v04_receipt(tmp_path: Path):
    request = _request(secret="do-not-retain-this-payload")
    result, output, log = _run_cli(tmp_path, request)

    assert result.returncode == 0, result.stderr.decode()
    assert log.read_bytes() == request
    assert result.stdout == _response()
    assert not (output / "payloads").exists()
    checked = check_capture_directory(output)
    assert checked.ok
    assert (checked.complete, checked.receipts) == (1, 1)

    raw, receipt = _one_receipt(output)
    assert b"do-not-retain-this-payload" not in raw
    assert receipt["schema_version"] == "0.4"
    assert receipt["action"]["type"] == "mcp.tools.call.observed"
    assert receipt["action"]["subject"]["request_frame_sha256"].startswith("sha256:")
    assert receipt["action"]["subject"]["response_frame_sha256"].startswith("sha256:")
    verification = verify_receipt(receipt)
    assert verification.ok and verification.verified_to == "digest"
    assert verification.authority_authentic == "not_applicable"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink boundary")
def test_out_symlink_is_rejected_before_backend_spawn_or_payload_retention(
    tmp_path: Path,
):
    server = _write_server(tmp_path)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    output = tmp_path / "capture"
    output.symlink_to(redirected, target_is_directory=True)
    backend_log = tmp_path / "backend-input.bin"
    secret = "must-not-cross-output-symlink"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bulla",
            "capture",
            "mcp",
            "--out",
            str(output),
            "--retain-payloads",
            "--",
            sys.executable,
            str(server),
        ],
        input=_request(secret=secret),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(backend_log),
        timeout=20,
    )

    assert result.returncode == 2
    assert b"capture output must not be a symlink" in result.stderr
    assert list(redirected.iterdir()) == []
    assert not backend_log.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_windows_junction_parent_is_rejected_before_target_changes(
    tmp_path: Path,
):
    server = _write_server(tmp_path)
    redirected = tmp_path / "junction-target"
    redirected.mkdir()
    sentinel = redirected / "preserve.bin"
    sentinel.write_bytes(b"unchanged-target-bytes\x00\xff")
    junction = tmp_path / "capture-parent-junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(redirected)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert created.returncode == 0, (
        "windows-latest must support the required local NTFS junction gate: "
        f"{created.stderr!r}"
    )

    backend_log = tmp_path / "junction-backend-input.bin"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bulla",
            "capture",
            "mcp",
            "--out",
            str(junction / "capture"),
            "--retain-payloads",
            "--",
            sys.executable,
            str(server),
        ],
        input=_request(secret="must-not-cross-junction-parent"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(backend_log),
        timeout=20,
    )

    assert result.returncode == 2
    assert not backend_log.exists()
    assert {path.name for path in redirected.iterdir()} == {sentinel.name}
    assert sentinel.read_bytes() == b"unchanged-target-bytes\x00\xff"


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic directory handles")
@pytest.mark.parametrize("created_member", ("output", "sessions", "session"))
def test_windows_new_directory_cannot_be_substituted_before_its_anchor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    created_member: str,
):
    import bulla.capture_mcp as module

    root = tmp_path / f"atomic-{created_member}-root"
    output = tmp_path / "atomic-one-shot-output"
    if created_member == "session":
        module._initialize_capture_root_windows(root)
        output = module.allocate_capture_session(root)
    target = {
        "output": output,
        "sessions": root / "sessions",
        "session": output,
    }[created_member]
    attacker = tmp_path / f"{created_member}-attacker"
    attacker.mkdir()
    sentinel = attacker / "sentinel.bin"
    sentinel.write_bytes(b"attacker-directory-unchanged\x00\xff")
    stolen = tmp_path / f"{created_member}-stolen"
    observed: list[Path] = []

    def attempt_substitution(path: Path) -> None:
        if path != target:
            return
        observed.append(path)
        with pytest.raises(OSError):
            path.rename(stolen)

    monkeypatch.setattr(
        module, "_observe_created_windows_directory", attempt_substitution
    )

    if created_member == "sessions":
        module._initialize_capture_root_windows(root)
    else:
        session = module.CaptureSession(output, ["backend-must-not-start"])
        session.finish(0)

    assert observed == [target]
    assert target.is_dir() and not stolen.exists()
    assert {path.name for path in attacker.iterdir()} == {sentinel.name}
    assert sentinel.read_bytes() == b"attacker-directory-unchanged\x00\xff"


@pytest.mark.skipif(os.name != "nt", reason="Windows published-root junction boundary")
def test_windows_published_root_below_junction_parent_is_not_accepted(
    tmp_path: Path,
):
    import bulla.capture_mcp as module

    redirected_parent = tmp_path / "published-root-target"
    redirected_parent.mkdir()
    published_root = redirected_parent / "published"
    module._initialize_capture_root_windows(published_root)
    before = {
        path.relative_to(redirected_parent).as_posix(): (
            "dir" if path.is_dir() else path.read_bytes()
        )
        for path in redirected_parent.rglob("*")
    }
    junction = tmp_path / "published-root-junction"
    created = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(redirected_parent),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    assert created.returncode == 0, created.stderr

    with pytest.raises(CaptureError, match="parent does not exist"):
        module._initialize_capture_root_windows(junction / published_root.name)

    after = {
        path.relative_to(redirected_parent).as_posix(): (
            "dir" if path.is_dir() else path.read_bytes()
        )
        for path in redirected_parent.rglob("*")
    }
    assert after == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory identity boundary")
def test_live_managed_directory_substitution_cannot_redirect_completion_writes(
    tmp_path: Path,
):
    server = tmp_path / "substituting-server.py"
    server.write_text(_SUBSTITUTING_SERVER, encoding="utf-8")
    output = tmp_path / "capture"
    redirected = tmp_path / "redirected"
    env = _cli_env()
    env["BULLA_CAPTURE_TEST_OUTPUT"] = str(output)
    env["BULLA_CAPTURE_TEST_REDIRECTED"] = str(redirected)
    result = subprocess.run(
        [
            sys.executable, "-m", "bulla", "capture", "mcp",
            "--out", str(output), "--retain-payloads",
            "--", sys.executable, str(server),
        ],
        input=_request(secret="must-not-be-persisted-through-substitution"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=20,
    )

    assert result.returncode == 1
    assert result.stdout == _response()
    assert not list((redirected / "receipts").iterdir())
    assert not list((redirected / "payloads").iterdir())
    assert len(list((redirected / "calls").iterdir())) == 1
    with pytest.raises(CaptureDirectoryError, match="required local directory"):
        check_capture_directory(output)


@pytest.mark.parametrize(
    ("mode", "expected_kind"),
    (("tool_error", "result"), ("json_error", "error")),
)
def test_tool_and_jsonrpc_errors_are_complete_observations(
    tmp_path: Path, mode: str, expected_kind: str,
):
    result, output, _ = _run_cli(tmp_path, _request(mode=mode))
    assert result.returncode == 0
    _, receipt = _one_receipt(output)
    assert receipt["action"]["subject"]["response_kind"] == expected_kind
    assert check_capture_directory(output).ok


def test_out_of_order_responses_and_typed_ids_correlate_without_rewrite(tmp_path: Path):
    frames = _request(1, mode="reverse") + _request("1", mode="reverse")
    result, output, log = _run_cli(tmp_path, frames)
    expected = _response("1") + _response(1)
    assert result.returncode == 0, result.stderr.decode()
    assert log.read_bytes() == frames
    assert result.stdout == expected
    checked = check_capture_directory(output)
    assert checked.ok and checked.complete == 2 and checked.receipts == 2
    calls = [json.loads(path.read_bytes()) for path in sorted((output / "calls").glob("*.json"))]
    assert [call["completion_sequence"] for call in calls] == [2, 1]


def test_notifications_and_server_requests_pass_unchanged_without_receipts(tmp_path: Path):
    request = _request(mode="messages")
    result, output, _ = _run_cli(tmp_path, request)
    expected = (
        _wire({"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}})
        + _wire({"jsonrpc":"2.0","id":"server-question","method":"sampling/createMessage","params":{}})
        + _wire({"jsonrpc":"2.0","id":"unknown-response","result":{"ignored":True}})
        + _response()
    )
    assert result.returncode == 0
    assert result.stdout == expected
    assert check_capture_directory(output).receipts == 1


def test_cancellation_without_response_is_incomplete_and_has_no_receipt(tmp_path: Path):
    frames = _request(mode="no_response") + _wire({
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": 1},
    })
    result, output, _ = _run_cli(tmp_path, frames)
    assert result.returncode == 1
    assert result.stdout == b""
    assert not list((output / "receipts").iterdir())
    checked = check_capture_directory(output)
    assert not checked.ok and checked.incomplete == 1


def test_backend_exit_is_preserved_and_pending_call_is_incomplete(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request(mode="exit"))
    assert result.returncode == 7
    checked = check_capture_directory(output)
    assert not checked.ok and checked.incomplete == 1 and checked.receipts == 0
    assert any("backend exited 7" in reason for reason in checked.reasons)


def test_duplicate_inflight_id_never_produces_false_complete(tmp_path: Path):
    frames = _request(1, mode="reverse") + _request(1, mode="reverse")
    result, output, _ = _run_cli(tmp_path, frames)
    assert result.returncode == 1
    assert result.stdout == _response(1) + _response(1)
    checked = check_capture_directory(output)
    assert not checked.ok and checked.uncheckable == 2 and checked.receipts == 0


def test_response_with_result_and_error_is_uncheckable_not_complete(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request(mode="both_result_and_error"))
    assert result.returncode == 1
    assert result.stdout == _wire({
        "jsonrpc": "2.0", "id": 1, "result": {},
        "error": {"code": -32001, "message": "tool failed"},
    })
    checked = check_capture_directory(output)
    assert not checked.ok
    assert checked.complete == 0 and checked.receipts == 0
    assert checked.uncheckable >= 1


def test_non_tool_request_id_collision_cannot_claim_tool_response(tmp_path: Path):
    frames = _request(1, mode="hold_for_ping") + _wire({
        "jsonrpc": "2.0", "id": 1, "method": "ping", "params": {},
    })
    result, output, log = _run_cli(tmp_path, frames)
    assert result.returncode == 1
    assert log.read_bytes() == frames
    assert result.stdout == (
        _wire({"jsonrpc": "2.0", "id": 1, "result": {"pong": True}})
        + _response(1)
    )
    checked = check_capture_directory(output)
    assert not checked.ok
    assert checked.complete == 0 and checked.receipts == 0
    assert checked.uncheckable == 1


def _request_at_size(size: int) -> bytes:
    prefix = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"demo.echo","arguments":{"blob":"'
    suffix = b'"}}}'
    fill = size - len(prefix) - len(suffix)
    assert fill >= 0
    frame = prefix + (b"x" * fill) + suffix + b"\n"
    assert len(frame) - 1 == size
    return frame


@pytest.mark.parametrize(
    ("size", "expected_status", "expected_receipts"),
    (
        (CAPTURE_LIMIT - 1, 0, 1),
        (CAPTURE_LIMIT, 0, 1),
        (CAPTURE_LIMIT + 1, 1, 0),
    ),
)
def test_capture_boundary_never_changes_forwarded_bytes(
    tmp_path: Path, size: int, expected_status: int, expected_receipts: int,
):
    frame = _request_at_size(size)
    result, output, log = _run_cli(tmp_path, frame)
    assert result.returncode == expected_status, result.stderr.decode()
    assert log.read_bytes() == frame
    assert result.stdout == _response()
    assert len(list((output / "receipts").glob("*.json"))) == expected_receipts
    checked = check_capture_directory(output)
    assert checked.ok is (expected_status == 0)
    if expected_status:
        assert checked.uncheckable == 1


@pytest.mark.parametrize("frame", (b"{bad json}\n", b'{"jsonrpc":"2.0"}'))
def test_malformed_or_partial_eof_frame_is_forwarded_but_uncheckable(
    tmp_path: Path, frame: bytes,
):
    result, output, log = _run_cli(tmp_path, frame)
    assert result.returncode == 1
    assert log.read_bytes() == frame
    checked = check_capture_directory(output)
    assert not checked.ok and checked.uncheckable >= 1 and checked.receipts == 0


@pytest.mark.parametrize(
    "frame",
    (
        b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/call","params":{"name":"demo.echo","arguments":{}}}\n',
        b'{"jsonrpc":"2.0","id":9007199254740992,"method":"tools/call","params":{"name":"demo.echo","arguments":{}}}\n',
    ),
)
def test_duplicate_members_and_unsafe_integers_never_create_receipts(
    tmp_path: Path, frame: bytes,
):
    result, output, log = _run_cli(tmp_path, frame)
    assert result.returncode == 1
    assert log.read_bytes() == frame
    assert result.stdout
    checked = check_capture_directory(output)
    assert not checked.ok and checked.uncheckable >= 1 and checked.receipts == 0


def test_retain_payloads_is_explicit_private_and_signed_receipt_authenticates_observer(
    tmp_path: Path,
):
    pytest.importorskip("nacl.signing")
    from bulla.identity import LocalEd25519Signer

    signer = LocalEd25519Signer.generate()
    key = tmp_path / "observer-key.json"
    key.write_text(json.dumps(signer.to_keyfile_dict()), encoding="utf-8")
    key.chmod(0o600)
    request = _request(secret="locally-retained-secret")
    result, output, _ = _run_cli(tmp_path, request, retain=True, key=key)
    assert result.returncode == 0, result.stderr.decode()
    assert b"WARNING: --retain-payloads" in result.stderr
    checked = check_capture_directory(output)
    assert checked.ok and checked.retained_payloads
    assert (output / "payloads" / "000001-request.bin").read_bytes() == request
    assert (output / "payloads" / "000001-response.bin").read_bytes() == _response()
    _, receipt = _one_receipt(output)
    verification = verify_receipt(receipt)
    assert verification.ok and verification.verified_to == "attestation"
    assert verification.authority_authentic == "verified"
    if os.name == "posix":
        for directory in (output, output / "calls", output / "gaps", output / "receipts", output / "payloads"):
            assert directory.stat().st_mode & 0o777 == 0o700
        for path in output.rglob("*"):
            if path.is_file():
                assert path.stat().st_mode & 0o777 == 0o600


def test_response_forwarding_survives_receipt_persistence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    server = _write_server(tmp_path)
    output = tmp_path / "capture"
    original = module._DirectoryAnchor.write

    def fail_receipt(anchor, name: str, data: bytes) -> None:
        if anchor.path.name == "receipts":
            raise OSError("simulated receipt disk failure")
        original(anchor, name, data)

    monkeypatch.setattr(module._DirectoryAnchor, "write", fail_receipt)
    monkeypatch.setenv("BULLA_CAPTURE_TEST_LOG", str(tmp_path / "backend.bin"))
    forwarded = io.BytesIO()
    diagnostic = io.BytesIO()
    status = run_mcp_capture(
        output=output,
        command=[sys.executable, str(server)],
        stdin=io.BytesIO(_request()),
        stdout=forwarded,
        stderr=diagnostic,
    )
    assert status == 1
    assert forwarded.getvalue() == _response()
    checked = check_capture_directory(output)
    assert not checked.ok and checked.incomplete == 1 and checked.receipts == 0


def test_capture_check_rejects_mutation_or_orphaned_receipt(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request())
    assert result.returncode == 0
    call_path = output / "calls" / "000001.json"
    original = call_path.read_bytes()
    call = json.loads(original)
    call["tool_name"] = "mutated"
    call_path.write_text(json.dumps(call), encoding="utf-8")
    with pytest.raises(CaptureDirectoryError, match="digest mismatch"):
        check_capture_directory(output)
    call_path.write_bytes(original)
    receipt = next((output / "receipts").glob("*.json"))
    (output / "receipts" / "orphan.json").write_bytes(receipt.read_bytes())
    with pytest.raises(CaptureDirectoryError, match="orphaned receipt"):
        check_capture_directory(output)


def test_capture_check_detects_retained_payload_mutation(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request(), retain=True)
    assert result.returncode == 0
    response = output / "payloads" / "000001-response.bin"
    response.write_bytes(response.read_bytes() + b"x")
    checked = check_capture_directory(output)
    assert not checked.ok
    assert any("retained response commitment mismatch" in reason for reason in checked.reasons)


def test_commitments_only_capture_rejects_untracked_payload_directory(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request(secret="must-not-appear"))
    assert result.returncode == 0
    payloads = output / "payloads"
    payloads.mkdir(mode=0o700)
    leaked = payloads / "000001-request.bin"
    leaked.write_bytes(b"must-not-appear")
    leaked.chmod(0o600)
    with pytest.raises(CaptureDirectoryError, match="root inventory mismatch"):
        check_capture_directory(output)


def test_capture_check_rejects_disguised_managed_members(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request())
    assert result.returncode == 0
    disguised = output / "calls" / "000001.bin"
    disguised.write_bytes((output / "calls" / "000001.json").read_bytes())
    disguised.chmod(0o600)
    with pytest.raises(CaptureDirectoryError, match="unexpected member in calls"):
        check_capture_directory(output)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink boundary")
def test_capture_check_rejects_session_record_symlink_before_read(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request())
    assert result.returncode == 0
    session = output / "session.json"
    held = tmp_path / "held-session.json"
    session.rename(held)
    session.symlink_to(held)
    with pytest.raises(CaptureDirectoryError, match="not a regular file"):
        check_capture_directory(output)


def test_cli_check_exit_codes(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request())
    assert result.returncode == 0
    command = [sys.executable, "-m", "bulla", "capture", "check", str(output)]
    checked = subprocess.run(command, capture_output=True, env=_cli_env(), timeout=10)
    assert checked.returncode == 0
    assert b"complete=1" in checked.stdout
    unusable = subprocess.run(
        command[:-1] + [str(tmp_path / "absent")],
        capture_output=True,
        env=_cli_env(),
        timeout=10,
    )
    assert unusable.returncode == 2


def test_cli_check_output_is_windows_console_safe(tmp_path: Path):
    result, output, _ = _run_cli(tmp_path, _request())
    assert result.returncode == 0
    env = _cli_env()
    env["PYTHONIOENCODING"] = "cp1252"
    command = [sys.executable, "-m", "bulla", "capture", "check", str(output)]
    checked = subprocess.run(command, capture_output=True, env=env, timeout=10)
    assert checked.returncode == 0
    assert checked.stdout.startswith(b"OK capture")
    unusable = subprocess.run(
        command[:-1] + [str(tmp_path / "absent")],
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert unusable.returncode == 2
    assert unusable.stdout.startswith(b"FAIL unusable capture directory:")


def test_nonempty_output_refuses_before_backend_spawn(tmp_path: Path):
    server = _write_server(tmp_path)
    output = tmp_path / "capture"
    output.mkdir()
    marker = output / "mine.txt"
    marker.write_text("preserve", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, "-m", "bulla", "capture", "mcp", "--out", str(output),
            "--", sys.executable, str(server),
        ],
        input=_request(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(),
        timeout=10,
    )
    assert result.returncode == 2
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_session_root_survives_two_unchanged_server_lifecycles(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "stable-capture-root"

    first = _run_root_cli(root, server, _request(1))
    second = _run_root_cli(root, server, _request(2))

    assert first.returncode == second.returncode == 0
    assert first.stdout == _response(1)
    assert second.stdout == _response(2)
    checked = check_capture_path(root)
    assert isinstance(checked, CaptureRootCheckResult)
    assert checked.ok
    assert (checked.sessions, checked.complete, checked.receipts) == (2, 2, 2)
    assert (checked.incomplete, checked.uncheckable, checked.empty) == (0, 0, 0)
    for receipt_path in checked.receipt_paths:
        receipt = receipt_path.read_bytes()
        assert str(root).encode() not in receipt
        assert receipt_path.parent.parent.name.encode() not in receipt


def test_concurrent_initialization_and_launches_allocate_distinct_sessions(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "concurrent-root"
    root.mkdir()

    def launch(identifier: int) -> subprocess.CompletedProcess[bytes]:
        return _run_root_cli(root, server, _request(identifier))

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(launch, range(1, 9)))

    assert all(result.returncode == 0 for result in results)
    assert {result.stdout for result in results} == {_response(i) for i in range(1, 9)}
    checked = check_capture_root(root)
    assert checked.ok
    assert (checked.sessions, checked.complete, checked.receipts) == (8, 8, 8)
    session_names = [path.name for path in (root / "sessions").iterdir()]
    assert len(session_names) == len(set(session_names)) == 8
    assert set(path.name for path in root.iterdir()) == {"root.json", "sessions"}


def test_concurrent_empty_root_reservations_are_distinct_and_leave_no_orphans(
    tmp_path: Path,
):
    root = tmp_path / "empty-concurrent-root"
    root.mkdir()
    with ThreadPoolExecutor(max_workers=8) as executor:
        reservations = list(executor.map(lambda _: allocate_capture_session(root), range(8)))
    assert len({path.name for path in reservations}) == 8
    assert all(not path.exists() for path in reservations)
    assert set(path.name for path in root.iterdir()) == {"root.json", "sessions"}


def test_windows_root_initialization_uses_one_exclusive_claim(tmp_path: Path):
    import bulla.capture_mcp as module

    root = tmp_path / "windows-concurrent-root"
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda _: module._initialize_capture_root_windows(root), range(8)
            )
        )

    assert module._validate_capture_root_layout(root) == root / "sessions"
    assert not module._windows_root_claim_path(root).exists()
    assert set(path.name for path in tmp_path.iterdir()) == {root.name}


def test_native_windows_access_denied_is_not_claim_ownership(monkeypatch):
    import ctypes
    import bulla.capture_mcp as module

    native = SimpleNamespace(
        c_void_p=ctypes.c_void_p, cast=ctypes.cast, get_last_error=lambda: 5,
    )
    monkeypatch.setattr(module, "_native_windows_claim_api", lambda: (
        native, None, lambda *args: ctypes.c_void_p(-1).value, None, None, None,
    ))
    with pytest.raises(module._WindowsRootClaimDenied) as failure:
        module._acquire_native_windows_root_claim(Path("unused-claim"), b"token\n")
    assert failure.value.errno == 5


@pytest.mark.parametrize("layout", ("absent", "valid-claimed", "malformed", "unreadable-claim"))
def test_windows_denied_claim_never_creates_repairs_or_steals(
    tmp_path, monkeypatch, layout,
):
    import bulla.capture_mcp as module

    root = tmp_path / "denied-root"
    claim = module._windows_root_claim_path(root)
    if layout in ("valid-claimed", "unreadable-claim"):
        module._initialize_capture_root_unclaimed(root)
        claim.write_bytes(b"other-owner\n")
    elif layout == "malformed":
        root.mkdir()
        (root / "unexpected").write_bytes(b"preserve")
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    if layout == "unreadable-claim":
        # The native presence check treats denied/unknown attributes as present.
        monkeypatch.setattr(module, "_windows_root_claim_present", lambda _: True)
    calls = []

    def denied(path, token):
        calls.append(path)
        raise module._WindowsRootClaimDenied(5, "constructed native denial")

    clock = [0.0]
    monkeypatch.setattr(module, "_acquire_windows_root_claim", denied)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(CaptureError, match="access was denied.*5 seconds"):
        module._initialize_capture_root_windows(root)
    assert calls == [claim], "denial must not trigger another ownership attempt"
    assert 5 <= clock[0] < 5.1
    assert before == {p.relative_to(tmp_path).as_posix(): p.read_bytes()
                      for p in tmp_path.rglob("*") if p.is_file()}
    if layout == "absent":
        assert not root.exists() and not claim.exists()


def test_windows_denied_claim_accepts_only_valid_publication_after_claim_removal(
    tmp_path, monkeypatch,
):
    import bulla.capture_mcp as module

    root = tmp_path / "pending-publication"
    module._initialize_capture_root_unclaimed(root)
    claim = module._windows_root_claim_path(root)
    claim.write_bytes(b"constructed-owner\n")
    calls = []

    def denied(path, token):
        calls.append(path)
        raise module._WindowsRootClaimDenied(5, "constructed delete-pending denial")

    def owner_finishes(seconds):
        assert seconds == module._WINDOWS_ROOT_CLAIM_POLL_SECONDS
        assert claim.read_bytes() == b"constructed-owner\n"
        claim.unlink()  # Simulated owner, never the denied initializer.

    monkeypatch.setattr(module, "_acquire_windows_root_claim", denied)
    monkeypatch.setattr(module.time, "sleep", owner_finishes)
    module._initialize_capture_root_windows(root)
    assert calls == [claim]
    assert module._validate_capture_root_layout(root) == root / "sessions"
    assert list((root / "sessions").iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="native Windows delete-pending handle")
def test_native_windows_delete_pending_claim_waits_for_exact_owner_close(
    tmp_path, monkeypatch,
):
    import bulla.capture_mcp as module

    root = tmp_path / "native-delete-pending-root"
    original_api = module._native_windows_claim_api
    pending = threading.Event()
    denied = threading.Event()
    allow_close = threading.Event()

    def observed_api():
        ctypes, wintypes, create_file, write_file, flush_file, set_info = original_api()

        def observed_create(*args):
            handle = create_file(*args)
            error = ctypes.get_last_error()
            if ctypes.cast(handle, ctypes.c_void_p).value == ctypes.c_void_p(-1).value and error == 5:
                denied.set()
            ctypes.set_last_error(error)
            return handle

        def held_disposition(*args):
            ok = set_info(*args)
            if ok:
                pending.set()
                assert allow_close.wait(timeout=10)
            return ok

        return ctypes, wintypes, observed_create, write_file, flush_file, held_disposition

    monkeypatch.setattr(module, "_native_windows_claim_api", observed_api)
    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(module._initialize_capture_root_windows, root)
        try:
            assert pending.wait(timeout=10)
            loser = executor.submit(module._initialize_capture_root_windows, root)
            assert denied.wait(timeout=3), "test must exercise actual Windows error 5"
            assert not loser.done(), "delete-pending is not an unclaimed publication"
        finally:
            allow_close.set()
        winner.result(timeout=10)
        loser.result(timeout=10)
    assert module._validate_capture_root_layout(root) == root / "sessions"
    assert not module._windows_root_claim_present(module._windows_root_claim_path(root))


def test_windows_loser_waits_for_winner_to_release_completed_root_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    root = tmp_path / "windows-paused-winner-root"
    release_entered = threading.Event()
    allow_release = threading.Event()
    release_lock = threading.Lock()
    pause_next_release = [True]
    original_release = module._release_acquired_windows_root_claim
    original_sleep = module.time.sleep
    loser_waiting = threading.Event()

    def paused_release(
        claim: Path, token: bytes, native_handle: int | None,
    ) -> None:
        with release_lock:
            pause = pause_next_release[0]
            pause_next_release[0] = False
        if pause:
            release_entered.set()
            assert allow_release.wait(timeout=5)
        original_release(claim, token, native_handle)

    def observed_sleep(seconds: float) -> None:
        loser_waiting.set()
        original_sleep(seconds)

    monkeypatch.setattr(
        module, "_release_acquired_windows_root_claim", paused_release
    )
    monkeypatch.setattr(module.time, "sleep", observed_sleep)
    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(module._initialize_capture_root_windows, root)
        assert release_entered.wait(timeout=5)
        assert module._validate_capture_root_layout(root) == root / "sessions"
        assert module._windows_root_claim_path(root).exists()
        loser = executor.submit(module._initialize_capture_root_windows, root)
        try:
            assert loser_waiting.wait(timeout=5)
            assert not loser.done()
        finally:
            allow_release.set()
        winner.result(timeout=5)
        loser.result(timeout=5)

    assert not module._windows_root_claim_path(root).exists()
    assert module._validate_capture_root_layout(root) == root / "sessions"


def test_windows_stranded_root_claim_times_out_without_stealing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    root = tmp_path / "windows-stranded-root"
    claim = module._windows_root_claim_path(root)
    claim.write_bytes(b"other-initializer\n")
    clock = [0.0]
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(module.time, "monotonic", monotonic)
    monkeypatch.setattr(module.time, "sleep", sleep)

    with pytest.raises(CaptureError, match="unresolved for 5 seconds"):
        module._initialize_capture_root_windows(root)

    assert sleeps and set(sleeps) == {0.025}
    assert clock[0] >= 5.0
    assert claim.read_bytes() == b"other-initializer\n"
    assert not root.exists()


def test_windows_complete_root_with_stranded_claim_still_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    root = tmp_path / "windows-complete-but-claimed-root"
    module._initialize_capture_root_unclaimed(root)
    claim = module._windows_root_claim_path(root)
    claim.write_bytes(b"stranded-after-publication\n")
    clock = [0.0]

    def monotonic() -> float:
        return clock[0]

    def sleep(seconds: float) -> None:
        assert seconds == 0.025
        clock[0] += seconds

    monkeypatch.setattr(module.time, "monotonic", monotonic)
    monkeypatch.setattr(module.time, "sleep", sleep)

    with pytest.raises(CaptureError, match="unresolved for 5 seconds"):
        module._initialize_capture_root_windows(root)

    assert module._validate_capture_root_layout(root) == root / "sessions"
    assert claim.read_bytes() == b"stranded-after-publication\n"


def test_windows_owned_claim_unlink_failure_never_reports_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    root = tmp_path / "windows-owned-claim-unlink-failure"
    claim = module._windows_root_claim_path(root)

    def denied_release(
        path: Path, token: bytes, native_handle: int | None,
    ) -> None:
        assert path == claim and token
        if native_handle is not None:
            module._close_windows_handle(native_handle)
        raise CaptureError("could not remove owned injected claim")

    monkeypatch.setattr(module, "_release_acquired_windows_root_claim", denied_release)
    monkeypatch.setattr(
        module, "_initialize_capture_root", module._initialize_capture_root_windows
    )

    with pytest.raises(CaptureError, match="could not remove owned"):
        module.allocate_capture_session(root)

    assert claim.is_file()
    assert module._validate_capture_root_layout(root) == root / "sessions"
    assert list((root / "sessions").iterdir()) == []


def test_windows_release_never_steals_changed_claim(
    tmp_path: Path,
):
    import bulla.capture_mcp as module

    claim = tmp_path / ".changed.init.claim"
    claim.write_bytes(b"replacement-owner\n")

    with pytest.raises(CaptureError, match="ownership changed"):
        module._release_windows_root_claim(claim, b"original-owner\n")

    assert claim.read_bytes() == b"replacement-owner\n"


def test_windows_release_accepts_later_owner_after_unlink_aba(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    claim = tmp_path / ".aba.init.claim"
    owned_token = b"owned-initializer\n"
    later_token = b"later-initializer\n"
    claim.write_bytes(owned_token)
    owned_unlinked = threading.Event()
    later_acquired = threading.Event()
    original_unlink = Path.unlink

    def unlink_then_pause(path: Path, *args, **kwargs) -> None:
        original_unlink(path, *args, **kwargs)
        if path == claim:
            owned_unlinked.set()
            assert later_acquired.wait(timeout=5)

    monkeypatch.setattr(Path, "unlink", unlink_then_pause)
    with ThreadPoolExecutor(max_workers=1) as executor:
        released = executor.submit(
            module._release_windows_root_claim, claim, owned_token
        )
        assert owned_unlinked.wait(timeout=5)
        claim.write_bytes(later_token)
        later_acquired.set()
        released.result(timeout=5)

    assert claim.read_bytes() == later_token


def test_windows_reparse_metadata_is_never_a_managed_directory() -> None:
    import bulla.capture_mcp as module

    metadata = SimpleNamespace(
        st_mode=stat.S_IFDIR,
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )

    assert module._is_symlink_or_windows_reparse_point(metadata)


@pytest.mark.parametrize("member", ("root", "sessions", "session"))
def test_simulated_windows_reparse_directories_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str,
):
    import bulla.capture_mcp as module

    root = tmp_path / "windows-reparse-root"
    module._initialize_capture_root_unclaimed(root)
    sessions = root / "sessions"
    session = sessions / f"session-{uuid.uuid4()}"
    session.mkdir()
    target = {"root": root, "sessions": sessions, "session": session}[member]
    original_metadata = module._no_follow_metadata

    def simulated_metadata(path: Path):
        metadata = original_metadata(path)
        if path == target:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_file_attributes=getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                ),
            )
        return metadata

    monkeypatch.setattr(module, "_no_follow_metadata", simulated_metadata)

    if member == "session":
        with pytest.raises(CaptureDirectoryError, match="unexpected member"):
            module._capture_session_members(sessions)
    else:
        with pytest.raises(CaptureDirectoryError):
            module._validate_capture_root_layout(root)


def test_simulated_windows_reparse_parent_refuses_root_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    original_metadata = module._no_follow_metadata

    def simulated_metadata(path: Path):
        metadata = original_metadata(path)
        if path == tmp_path:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_file_attributes=getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
                ),
            )
        return metadata

    monkeypatch.setattr(module, "_no_follow_metadata", simulated_metadata)

    with pytest.raises(CaptureError, match="parent does not exist"):
        module._initialize_capture_root_windows(tmp_path / "refused-root")


def test_stale_initializer_snapshot_revalidates_concurrent_published_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import bulla.capture_mcp as module

    server = _write_server(tmp_path)
    root = tmp_path / "linearizable-root"
    assert _run_root_cli(root, server, _request()).returncode == 0
    sessions = root / "sessions"
    original_iterdir = Path.iterdir
    stale_snapshot_used = False

    def controlled_iterdir(path: Path):
        nonlocal stale_snapshot_used
        if path == root and not stale_snapshot_used:
            stale_snapshot_used = True
            return iter((sessions,))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", controlled_iterdir)
    module._finish_empty_capture_root(root)
    assert stale_snapshot_used
    assert check_capture_root(root).ok


def test_preinit_orphan_is_visible_as_incomplete_and_does_not_block_reentry(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "orphan-root"
    orphan = allocate_capture_session(root)
    orphan.mkdir(mode=0o700)

    checked = check_capture_root(root)
    assert not checked.ok
    assert (checked.sessions, checked.incomplete, checked.receipts) == (1, 1, 0)
    assert any("allocated session was not initialized" in reason for reason in checked.reasons)

    relaunched = _run_root_cli(root, server, _request())
    assert relaunched.returncode == 0
    checked = check_capture_root(root)
    assert not checked.ok
    assert (checked.sessions, checked.incomplete, checked.complete) == (2, 1, 1)


def test_crash_and_incomplete_session_remain_visible_without_blocking_relaunch(
    tmp_path: Path,
):
    server = _write_server(tmp_path)
    root = tmp_path / "reentry-root"

    crashed = _run_root_cli(root, server, _request(1, mode="exit"))
    recovered = _run_root_cli(root, server, _request(2))

    assert crashed.returncode == 7
    assert recovered.returncode == 0 and recovered.stdout == _response(2)
    checked = check_capture_root(root)
    assert not checked.ok
    assert (checked.sessions, checked.complete, checked.incomplete) == (2, 1, 1)
    assert checked.receipts == 1
    assert any("backend exited 7" in reason for reason in checked.reasons)


def test_valid_zero_call_session_is_diagnostic_but_not_useful(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "empty-root"
    empty = _run_root_cli(root, server, b"")
    assert empty.returncode == 0
    checked = check_capture_root(root)
    assert not checked.ok
    assert (checked.sessions, checked.empty, checked.complete) == (1, 1, 0)
    assert checked.reasons == ("capture root contains no complete tools/call receipt",)

    complete = _run_root_cli(root, server, _request(1))
    assert complete.returncode == 0
    checked = check_capture_root(root)
    assert checked.ok
    assert (checked.sessions, checked.empty, checked.complete) == (2, 1, 1)


def test_root_aggregates_commitment_only_and_opt_in_payload_sessions(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "privacy-root"
    secret = "retained-only-in-the-opted-in-session"

    default = _run_root_cli(root, server, _request(1, secret=secret))
    retained = _run_root_cli(root, server, _request(2, secret=secret), retain=True)

    assert default.returncode == retained.returncode == 0
    checked = check_capture_root(root)
    assert checked.ok and checked.retained_payload_sessions == 1
    sessions = sorted((root / "sessions").iterdir())
    payload_sessions = [path for path in sessions if (path / "payloads").exists()]
    assert len(payload_sessions) == 1
    assert secret.encode() in (payload_sessions[0] / "payloads" / "000001-request.bin").read_bytes()
    for receipt_path in checked.receipt_paths:
        assert secret.encode() not in receipt_path.read_bytes()


def _reseal(value: dict, member: str) -> dict:
    body = dict(value)
    body.pop(member, None)
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    body[member] = "sha256:" + __import__("hashlib").sha256(encoded).hexdigest()
    return body


def test_root_marker_inventory_and_coherent_reseal_fail_closed(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "attacked-root"
    assert _run_root_cli(root, server, _request()).returncode == 0

    marker_path = root / "root.json"
    original = marker_path.read_bytes()
    marker = json.loads(original)
    marker["kind"] = "lookalike.capture.root"
    marker_path.write_bytes(
        json.dumps(_reseal(marker, "marker_sha256"), sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    with pytest.raises(CaptureDirectoryError, match="marker is malformed"):
        check_capture_root(root)
    marker_path.write_bytes(original)

    unexpected = root / "latest"
    unexpected.write_text("session", encoding="utf-8")
    with pytest.raises(CaptureDirectoryError, match="root inventory mismatch"):
        check_capture_root(root)


def test_allocation_rejects_unexpected_session_member_before_backend_spawn(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "allocation-attack-root"
    assert _run_root_cli(root, server, _request()).returncode == 0
    existing_sessions = len(list((root / "sessions").iterdir()))
    (root / "sessions" / "unexpected.txt").write_text("hostile", encoding="utf-8")

    with pytest.raises(CaptureError, match="unexpected member in sessions"):
        allocate_capture_session(root)
    refused = _run_root_cli(root, server, _request(2))
    assert refused.returncode == 2
    assert len(list((root / "sessions").iterdir())) == existing_sessions + 1
    assert b"unexpected member in sessions" in refused.stderr


def test_coherently_resealed_session_counts_and_permissions_fail_closed(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "session-attack-root"
    assert _run_root_cli(root, server, _request()).returncode == 0
    session = next((root / "sessions").iterdir())
    session_record = session / "session.json"
    original = session_record.read_bytes()
    value = json.loads(original)
    value["calls_completed"] = 0
    session_record.write_bytes(
        json.dumps(_reseal(value, "session_record_sha256"), sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    with pytest.raises(CaptureDirectoryError, match="completion count"):
        check_capture_root(root)
    session_record.write_bytes(original)

    if os.name == "posix":
        root.chmod(0o755)
        with pytest.raises(CaptureDirectoryError, match="mode is not 0700"):
            check_capture_root(root)
        root.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink boundary")
def test_root_and_session_symlinks_fail_closed(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "real-root"
    assert _run_root_cli(root, server, _request()).returncode == 0

    root_link = tmp_path / "root-link"
    root_link.symlink_to(root, target_is_directory=True)
    with pytest.raises(CaptureDirectoryError, match="does not exist"):
        check_capture_root(root_link)

    sessions = root / "sessions"
    held_sessions = tmp_path / "held-sessions"
    sessions.rename(held_sessions)
    sessions.symlink_to(held_sessions, target_is_directory=True)
    with pytest.raises(CaptureDirectoryError, match="sessions member"):
        check_capture_root(root)
    sessions.unlink()
    held_sessions.rename(sessions)

    target = next(sessions.iterdir())
    session_link = sessions / "session-00000000-0000-4000-8000-000000000001"
    session_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(CaptureDirectoryError, match="unexpected member in sessions"):
        check_capture_root(root)


def test_session_root_refuses_arbitrary_nonempty_root_and_conflicting_cli_flags(
    tmp_path: Path,
):
    server = _write_server(tmp_path)
    root = tmp_path / "mine"
    root.mkdir()
    marker = root / "preserve.txt"
    marker.write_text("mine", encoding="utf-8")
    refused = _run_root_cli(root, server, _request())
    assert refused.returncode == 2
    assert marker.read_text(encoding="utf-8") == "mine"

    conflicting = subprocess.run(
        [
            sys.executable, "-m", "bulla", "capture", "mcp",
            "--out", str(tmp_path / "one-shot"),
            "--session-root", str(tmp_path / "stable"),
            "--", sys.executable, str(server),
        ],
        input=_request(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(),
        timeout=10,
    )
    assert conflicting.returncode == 2
    assert not (tmp_path / "one-shot").exists()
    assert not (tmp_path / "stable").exists()


def test_root_cli_aggregate_and_show_receipts_are_local_and_absolute(tmp_path: Path):
    server = _write_server(tmp_path)
    root = tmp_path / "checked-root"
    assert _run_root_cli(root, server, _request()).returncode == 0
    command = [
        sys.executable, "-m", "bulla", "capture", "check", str(root),
        "--show-receipts",
    ]
    checked = subprocess.run(command, capture_output=True, env=_cli_env(), timeout=10)
    assert checked.returncode == 0
    assert b"sessions=1" in checked.stdout and b"complete=1" in checked.stdout
    receipt_lines = [
        path
        for line in checked.stdout.decode().splitlines()
        if (path := Path(line)).is_absolute()
    ]
    assert len(receipt_lines) == 1
    assert receipt_lines[0].is_absolute() and receipt_lines[0].is_file()
    assert not any(path.name == "index.json" for path in root.rglob("*"))


@pytest.mark.skipif(
    os.name != "posix" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="POSIX unreadable-directory boundary requires an unprivileged user",
)
def test_unreadable_capture_path_is_structurally_unusable_without_traceback(tmp_path: Path):
    path = tmp_path / "unreadable"
    path.mkdir(mode=0o700)
    path.chmod(0)
    command = [sys.executable, "-m", "bulla", "capture", "check", str(path)]
    try:
        checked = subprocess.run(command, capture_output=True, env=_cli_env(), timeout=10)
    finally:
        path.chmod(0o700)
    assert checked.returncode == 2
    assert b"unusable capture directory" in checked.stdout
    assert b"Traceback" not in checked.stderr
