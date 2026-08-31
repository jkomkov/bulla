"""Transparent stdio MCP observation for the Doorstep experiment.

This module is deliberately smaller than :mod:`bulla.live_proxy`.  It does not
initialize a backend, rewrite identifiers, inject tools, or apply policy.  It
copies the byte stream in both directions and observes complete newline-delimited
JSON-RPC messages without changing the bytes.  Client requests are registered
before the backend can answer them; server responses are persisted only after
the response bytes have been forwarded to the client.

The capture directory is an implementation-local recovery aid, not a protocol
object.  The only portable object emitted here is the existing ActionReceipt
v0.4 stored below ``receipts/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, Callable

from bulla import __version__
from bulla._subproc import session_kwargs, terminate_tree
from bulla.action_receipt import (
    ActionReceipt,
    build_action_receipt_v04,
    sign_action_receipt_v04,
    verify_receipt,
)
from bulla.envelope import RecourseEnvelope
from bulla.receipt_parser import ReceiptParseError, parse_action_receipt_json


CAPTURE_LIMIT = 1_048_576
_CHUNK_SIZE = 65_536
_SAFE_INTEGER_MAX = (1 << 53) - 1
_COVERAGE = {"COMPLETE", "INCOMPLETE", "UNCHECKABLE"}
_CAPTURE_ROOT_MARKER = "root.json"
_CAPTURE_ROOT_KIND = "bulla.mcp-capture-session-root.local"
_CAPTURE_ROOT_LAYOUT = 1
_SESSION_NAME = re.compile(
    r"session-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


class CaptureError(RuntimeError):
    """The requested capture could not be started or persisted safely."""


class CaptureDirectoryError(ValueError):
    """The local capture directory is unreadable or structurally unusable."""


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _addressed(value: dict[str, Any], member: str) -> dict[str, Any]:
    body = dict(value)
    body.pop(member, None)
    body[member] = _sha256(_json_bytes(body))
    return body


def _check_address(value: dict[str, Any], member: str) -> bool:
    carried = value.get(member)
    body = dict(value)
    body.pop(member, None)
    return isinstance(carried, str) and carried == _sha256(_json_bytes(body))


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=False)
    if os.name == "posix":
        path.chmod(0o700)


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write one private file and make its rename durable on POSIX."""
    temp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        if os.name == "posix":
            temp.chmod(0o600)
        os.replace(temp, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def _atomic_write_at(directory_fd: int, name: str, data: bytes) -> None:
    """Atomically write one filename relative to an already-open directory."""
    if not name or Path(name).name != name or name in {".", ".."}:
        raise OSError(f"invalid managed capture filename: {name!r}")
    temp = f".{name}.tmp-{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temp,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except BaseException:
        try:
            os.unlink(temp, dir_fd=directory_fd)
        except OSError:
            pass
        raise


@dataclass
class _DirectoryAnchor:
    """Stable identity and write handle for one private managed directory."""

    path: Path
    device: int
    inode: int
    fd: int | None

    @classmethod
    def open(cls, path: Path) -> "_DirectoryAnchor":
        if path.is_symlink() or not path.is_dir():
            raise OSError(f"managed capture directory is not a directory: {path}")
        before = os.stat(path, follow_symlinks=False)
        fd: int | None = None
        if os.name == "posix":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                os.close(fd)
                raise OSError(f"managed capture directory changed while opening: {path}")
        return cls(path=path, device=before.st_dev, inode=before.st_ino, fd=fd)

    def verify(self) -> None:
        try:
            current = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise OSError(f"managed capture directory is unavailable: {self.path}") from exc
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (self.device, self.inode)
        ):
            raise OSError(f"managed capture directory identity changed: {self.path}")
        if self.fd is not None:
            opened = os.fstat(self.fd)
            if (opened.st_dev, opened.st_ino) != (self.device, self.inode):
                raise OSError(f"managed capture directory handle changed: {self.path}")

    def write(self, name: str, data: bytes) -> None:
        self.verify()
        if self.fd is None:
            _atomic_write(self.path / name, data)
        else:
            _atomic_write_at(self.fd, name, data)
        self.verify()

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _capture_root_marker() -> dict[str, Any]:
    return _addressed(
        {
            "kind": _CAPTURE_ROOT_KIND,
            "layout_version": _CAPTURE_ROOT_LAYOUT,
        },
        "marker_sha256",
    )


def _validate_capture_root_layout(root: Path) -> Path:
    """Validate the fixed local root shell without inspecting its sessions."""
    if root.is_symlink() or not root.is_dir():
        raise CaptureDirectoryError(f"capture session root does not exist: {root}")
    marker_path = root / _CAPTURE_ROOT_MARKER
    sessions_path = root / "sessions"
    try:
        members = {member.name for member in root.iterdir()}
    except OSError as exc:
        raise CaptureDirectoryError(f"cannot inventory capture session root: {exc}") from exc
    expected = {_CAPTURE_ROOT_MARKER, "sessions"}
    if members != expected:
        raise CaptureDirectoryError(
            "capture session root inventory mismatch; "
            f"unexpected={sorted(members - expected)}, missing={sorted(expected - members)}"
        )
    if marker_path.is_symlink() or not marker_path.is_file():
        raise CaptureDirectoryError("capture session root marker is not a regular file")
    if sessions_path.is_symlink() or not sessions_path.is_dir():
        raise CaptureDirectoryError("capture session root sessions member is not a directory")
    marker = _load_local_json(marker_path)
    if marker != _capture_root_marker():
        raise CaptureDirectoryError("capture session root marker is malformed")
    if os.name == "posix":
        if _mode(root) != 0o700 or _mode(sessions_path) != 0o700:
            raise CaptureDirectoryError("capture session root directory mode is not 0700")
        if _mode(marker_path) != 0o600:
            raise CaptureDirectoryError("capture session root marker mode is not 0600")
    return sessions_path


def _discard_unpublished_root(temp: Path) -> None:
    """Remove only the exact unpublished root skeleton created by this module."""
    try:
        (temp / _CAPTURE_ROOT_MARKER).unlink(missing_ok=True)
        (temp / "sessions").rmdir()
        temp.rmdir()
    except OSError:
        pass


def _commit_existing_root_marker(root: Path) -> None:
    """Rename a complete marker from outside a concurrently visible empty root."""
    temp = root.parent / f".{root.name}.marker-{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(_json_bytes(_capture_root_marker()) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            temp.chmod(0o600)
        os.replace(temp, root / _CAPTURE_ROOT_MARKER)
        _fsync_dir(root)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def _finish_empty_capture_root(root: Path) -> None:
    """Commit an existing empty root by writing its deterministic marker last."""
    try:
        members = {member.name for member in root.iterdir()}
    except OSError as exc:
        raise CaptureDirectoryError(f"cannot inventory capture session root: {exc}") from exc
    if members == {_CAPTURE_ROOT_MARKER, "sessions"}:
        _validate_capture_root_layout(root)
        return
    if members not in (set(), {"sessions"}):
        raise CaptureDirectoryError("capture session root is not empty or recognized")
    sessions = root / "sessions"
    if sessions.exists() or sessions.is_symlink():
        if sessions.is_symlink() or not sessions.is_dir():
            raise CaptureDirectoryError("capture session root initialization is malformed")
        if any(sessions.iterdir()):
            _validate_capture_root_layout(root)
            return
    else:
        try:
            _mkdir_private(sessions)
        except FileExistsError:
            if sessions.is_symlink() or not sessions.is_dir():
                raise CaptureDirectoryError(
                    "capture session root initialization is malformed"
                )
            if any(sessions.iterdir()):
                _validate_capture_root_layout(root)
                return
    if os.name == "posix":
        root.chmod(0o700)
        sessions.chmod(0o700)
    _commit_existing_root_marker(root)


def _initialize_capture_root(root: Path) -> None:
    if root.exists() or root.is_symlink():
        if root.is_symlink() or not root.is_dir():
            _validate_capture_root_layout(root)
            return
        try:
            _validate_capture_root_layout(root)
        except CaptureDirectoryError:
            _finish_empty_capture_root(root)
            _validate_capture_root_layout(root)
        return
    parent = root.parent
    if parent.is_symlink() or not parent.is_dir():
        raise CaptureError(f"capture session root parent does not exist: {parent}")
    temp = parent / f".{root.name}.init-{uuid.uuid4().hex}"
    try:
        _mkdir_private(temp)
        _mkdir_private(temp / "sessions")
        _atomic_write(
            temp / _CAPTURE_ROOT_MARKER,
            _json_bytes(_capture_root_marker()) + b"\n",
        )
        _fsync_dir(temp)
        try:
            os.rename(temp, root)
            _fsync_dir(parent)
        except OSError:
            _discard_unpublished_root(temp)
            _validate_capture_root_layout(root)
    except BaseException:
        _discard_unpublished_root(temp)
        raise


def allocate_capture_session(root: Path) -> Path:
    """Reserve an unpublished session path below one reusable local root."""
    try:
        _initialize_capture_root(root)
        sessions = _validate_capture_root_layout(root)
        _capture_session_members(sessions)
    except CaptureDirectoryError as exc:
        raise CaptureError(str(exc)) from exc
    for _ in range(16):
        identifier = uuid.uuid4()
        session = sessions / f"session-{identifier}"
        if not session.exists() and not session.is_symlink():
            return session
    raise CaptureError("could not allocate a distinct capture session")


def _decode_strict_json(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate JSON member {key!r}")
            out[key] = value
        return out

    def integer(raw: str) -> int:
        value = int(raw)
        if abs(value) > _SAFE_INTEGER_MAX:
            raise ValueError("unsafe JSON integer")
        return value

    def constant(raw: str) -> Any:
        raise ValueError(f"non-finite JSON number {raw}")

    def decimal(raw: str) -> Decimal:
        value = Decimal(raw)
        if not value.is_finite():
            raise ValueError("non-finite JSON number")
        return value

    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_int=integer,
        parse_float=decimal,
        parse_constant=constant,
    )


def _strict_json(frame: bytes) -> dict[str, Any]:
    if not frame.endswith(b"\n"):
        raise ValueError("MCP stdio message is not newline terminated")
    value = _decode_strict_json(frame[:-1])
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise ValueError("MCP frame is not a JSON-RPC 2.0 object")
    return value


def _id_key(value: Any) -> tuple[str, str] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return ("integer", str(value))
    if isinstance(value, str):
        return ("string", value)
    return None


@dataclass
class _ObservedCall:
    sequence: int
    id_key: tuple[str, str]
    tool_name: str
    request_frame: bytes
    request_sha256: str
    path: Path
    persisted: bool = False
    coverage: str = "INCOMPLETE"
    diagnostic_reason: str = "NO_RESPONSE"
    response_frame: bytes | None = None
    response_sha256: str | None = None
    response_kind: str | None = None
    receipt_name: str | None = None
    completion_sequence: int | None = None

    def record(self) -> dict[str, Any]:
        return _addressed(
            {
                "sequence": self.sequence,
                "completion_sequence": self.completion_sequence,
                "coverage": self.coverage,
                "diagnostic_reason": self.diagnostic_reason,
                "tool_name": self.tool_name,
                "request_frame_sha256": self.request_sha256,
                "response_frame_sha256": self.response_sha256,
                "response_kind": self.response_kind,
                "receipt": self.receipt_name,
            },
            "call_record_sha256",
        )


@dataclass(frozen=True)
class CaptureCheckResult:
    ok: bool
    complete: int
    incomplete: int
    uncheckable: int
    receipts: int
    retained_payloads: bool
    receipt_paths: tuple[Path, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptureRootCheckResult:
    ok: bool
    sessions: int
    empty: int
    complete: int
    incomplete: int
    uncheckable: int
    receipts: int
    retained_payload_sessions: int
    receipt_paths: tuple[Path, ...] = ()
    reasons: tuple[str, ...] = ()


class _LineTap:
    """Bound one observation copy while hashing every forwarded frame."""

    def __init__(
        self,
        *,
        direction: str,
        on_frame: Callable[[bytes], None],
        on_gap: Callable[[str, str, str, int], None],
        limit: int = CAPTURE_LIMIT,
    ) -> None:
        self.direction = direction
        self.on_frame = on_frame
        self.on_gap = on_gap
        self.limit = limit
        self.buffer = bytearray()
        self.payload_length = 0
        self.oversized = False
        self.hasher = hashlib.sha256()

    def feed(self, chunk: bytes) -> None:
        rest = chunk
        while rest:
            newline = rest.find(b"\n")
            if newline < 0:
                piece, rest = rest, b""
                terminated = False
            else:
                piece, rest = rest[: newline + 1], rest[newline + 1 :]
                terminated = True
            payload_part = len(piece) - (1 if terminated else 0)
            self.payload_length += payload_part
            self.hasher.update(piece)
            if not self.oversized:
                if self.payload_length <= self.limit:
                    self.buffer.extend(piece)
                else:
                    self.buffer.clear()
                    self.oversized = True
            if terminated:
                digest = "sha256:" + self.hasher.hexdigest()
                if self.oversized:
                    self.on_gap(
                        self.direction,
                        "OVERSIZE_NOT_RETAINED",
                        digest,
                        self.payload_length,
                    )
                else:
                    self.on_frame(bytes(self.buffer))
                self._reset()

    def eof(self) -> None:
        if self.payload_length or self.buffer or self.oversized:
            self.on_gap(
                self.direction,
                "MALFORMED_FRAME",
                "sha256:" + self.hasher.hexdigest(),
                self.payload_length,
            )
        self._reset()

    def _reset(self) -> None:
        self.buffer.clear()
        self.payload_length = 0
        self.oversized = False
        self.hasher = hashlib.sha256()


class CaptureSession:
    """Receiver-local coverage state and ActionReceipt persistence."""

    def __init__(
        self,
        output: Path,
        command: list[str],
        *,
        retain_payloads: bool = False,
        signer: Any = None,
        clock: Callable[[], str] | None = None,
        uuid_factory: Callable[[], uuid.UUID] | None = None,
    ) -> None:
        self.output = output
        self.command = list(command)
        self.retain_payloads = retain_payloads
        self.signer = signer
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self.uuid_factory = uuid_factory or uuid.uuid4
        self.lock = threading.RLock()
        self.pending: dict[tuple[str, str], _ObservedCall] = {}
        self.inflight_requests: dict[tuple[str, str], str] = {}
        self.ambiguous_ids: set[tuple[str, str]] = set()
        self.calls: list[_ObservedCall] = []
        self.call_sequence = 0
        self.completion_sequence = 0
        self.gap_sequence = 0
        self.capture_failed = False
        self.finished = False
        self.backend_exit_code: int | None = None
        self._prepare_directory()
        self._anchors: dict[str, _DirectoryAnchor] = {}
        try:
            self._anchors = {
                "root": _DirectoryAnchor.open(self.output),
                "calls": _DirectoryAnchor.open(self.calls_dir),
                "gaps": _DirectoryAnchor.open(self.gaps_dir),
                "receipts": _DirectoryAnchor.open(self.receipts_dir),
            }
            if self.retain_payloads:
                self._anchors["payloads"] = _DirectoryAnchor.open(self.payloads_dir)
            self._write_session()
        except BaseException:
            self._close_anchors()
            raise

    @property
    def receipts_dir(self) -> Path:
        return self.output / "receipts"

    @property
    def calls_dir(self) -> Path:
        return self.output / "calls"

    @property
    def gaps_dir(self) -> Path:
        return self.output / "gaps"

    @property
    def payloads_dir(self) -> Path:
        return self.output / "payloads"

    def _prepare_directory(self) -> None:
        if self.output.is_symlink():
            raise CaptureError(f"capture output must not be a symlink: {self.output}")
        if self.output.exists():
            if not self.output.is_dir():
                raise CaptureError(f"capture output is not a directory: {self.output}")
            if any(self.output.iterdir()):
                raise CaptureError(f"capture output must be absent or empty: {self.output}")
            if os.name == "posix":
                self.output.chmod(0o700)
        else:
            try:
                self.output.mkdir(mode=0o700, parents=False)
            except FileNotFoundError as exc:
                raise CaptureError(
                    f"capture output parent does not exist: {self.output.parent}"
                ) from exc
            if os.name == "posix":
                self.output.chmod(0o700)
            _fsync_dir(self.output.parent)
        for path in (self.receipts_dir, self.calls_dir, self.gaps_dir):
            _mkdir_private(path)
        if self.retain_payloads:
            _mkdir_private(self.payloads_dir)

    def _session_record(self) -> dict[str, Any]:
        return _addressed(
            {
                "bulla_version": __version__,
                "capture_limit": CAPTURE_LIMIT,
                "retained_payloads": self.retain_payloads,
                "backend_command_sha256": _sha256(_json_bytes(self.command)),
                "finished": self.finished,
                "backend_exit_code": self.backend_exit_code,
                "capture_failed": self.capture_failed,
                "calls_observed": len(self.calls),
                "calls_completed": sum(c.coverage == "COMPLETE" for c in self.calls),
                "gaps_observed": self.gap_sequence,
            },
            "session_record_sha256",
        )

    def _write_session(self) -> None:
        self._anchors["root"].write(
            "session.json", _json_bytes(self._session_record()) + b"\n"
        )

    def _close_anchors(self) -> None:
        for anchor in self._anchors.values():
            try:
                anchor.close()
            except OSError:
                pass

    def _capture_failure(self, message: str) -> None:
        self.capture_failed = True
        print(f"bulla capture: {message}", file=sys.stderr, flush=True)

    def _persist_call(self, call: _ObservedCall) -> bool:
        try:
            self._anchors["calls"].write(
                call.path.name, _json_bytes(call.record()) + b"\n"
            )
            call.persisted = True
            return True
        except OSError as exc:
            self._capture_failure(f"could not persist call {call.sequence}: {exc}")
            return False

    def record_gap(
        self, direction: str, reason: str, frame_sha256: str, payload_length: int,
    ) -> None:
        with self.lock:
            self.gap_sequence += 1
            record = _addressed(
                {
                    "gap_sequence": self.gap_sequence,
                    "coverage": "UNCHECKABLE",
                    "direction": direction,
                    "diagnostic_reason": reason,
                    "frame_sha256": frame_sha256,
                    "payload_length": payload_length,
                },
                "gap_record_sha256",
            )
            try:
                self._anchors["gaps"].write(
                    f"{self.gap_sequence:06d}.json",
                    _json_bytes(record) + b"\n",
                )
            except OSError as exc:
                self._capture_failure(f"could not persist capture gap: {exc}")

    def observe_client(self, frame: bytes) -> None:
        try:
            message = _strict_json(frame)
        except Exception:
            self.record_gap("client_to_server", "MALFORMED_FRAME", _sha256(frame), len(frame) - 1)
            return
        method = message.get("method")
        if not isinstance(method, str):
            return
        key = _id_key(message.get("id"))
        if method != "tools/call":
            if key is None:
                return
            with self.lock:
                if key in self.inflight_requests or key in self.ambiguous_ids:
                    previous = self.pending.pop(key, None)
                    if previous is not None:
                        previous.coverage = "UNCHECKABLE"
                        previous.diagnostic_reason = "DUPLICATE_IN_FLIGHT_ID"
                        self._persist_call(previous)
                    self.inflight_requests.pop(key, None)
                    self.ambiguous_ids.add(key)
                else:
                    self.inflight_requests[key] = method
            return
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if key is None or not isinstance(name, str) or not name:
            self.record_gap("client_to_server", "MALFORMED_FRAME", _sha256(frame), len(frame) - 1)
            return
        with self.lock:
            self.call_sequence += 1
            call = _ObservedCall(
                sequence=self.call_sequence,
                id_key=key,
                tool_name=name,
                request_frame=frame,
                request_sha256=_sha256(frame),
                path=self.calls_dir / f"{self.call_sequence:06d}.json",
            )
            self.calls.append(call)
            if not self._persist_call(call):
                call.diagnostic_reason = "PERSISTENCE_FAILED"
                return
            if key in self.inflight_requests or key in self.ambiguous_ids:
                previous = self.pending.pop(key, None)
                if previous is not None:
                    previous.coverage = "UNCHECKABLE"
                    previous.diagnostic_reason = "DUPLICATE_IN_FLIGHT_ID"
                    self._persist_call(previous)
                call.coverage = "UNCHECKABLE"
                call.diagnostic_reason = "DUPLICATE_IN_FLIGHT_ID"
                self._persist_call(call)
                self.inflight_requests.pop(key, None)
                self.ambiguous_ids.add(key)
                return
            self.pending[key] = call
            self.inflight_requests[key] = method

    def observe_server(self, frame: bytes) -> None:
        try:
            message = _strict_json(frame)
        except Exception:
            self.record_gap("server_to_client", "MALFORMED_FRAME", _sha256(frame), len(frame) - 1)
            return
        if "method" in message:
            return
        key = _id_key(message.get("id"))
        result_members = {member for member in ("result", "error") if member in message}
        valid_members = (
            {"jsonrpc", "id", next(iter(result_members))}
            if len(result_members) == 1
            else set()
        )
        if key is None or len(result_members) != 1 or set(message) != valid_members:
            self.record_gap(
                "server_to_client", "MALFORMED_FRAME", _sha256(frame), len(frame) - 1,
            )
            if key is not None:
                with self.lock:
                    call = self.pending.pop(key, None)
                    self.inflight_requests.pop(key, None)
                    self.ambiguous_ids.add(key)
                    if call is not None:
                        call.coverage = "UNCHECKABLE"
                        call.diagnostic_reason = "MALFORMED_FRAME"
                        self._persist_call(call)
            return
        with self.lock:
            if key in self.ambiguous_ids:
                return
            method = self.inflight_requests.pop(key, None)
            call = self.pending.pop(key, None)
            if call is None:
                return  # a response to a non-tools/call request is ordinary traffic
            if method != "tools/call":
                call.coverage = "UNCHECKABLE"
                call.diagnostic_reason = "DUPLICATE_IN_FLIGHT_ID"
                self._persist_call(call)
                self.ambiguous_ids.add(key)
                return
            response_kind = "error" if "error" in message else "result"
            self._complete_call(call, frame, response_kind)

    def _complete_call(
        self, call: _ObservedCall, response_frame: bytes, response_kind: str,
    ) -> None:
        event_id = str(self.uuid_factory())
        try:
            receipt = build_action_receipt_v04(
                action={
                    "type": "mcp.tools.call.observed",
                    "subject": {
                        "transport": "mcp-stdio-jsonrpc",
                        "tool_name": call.tool_name,
                        "request_frame_sha256": call.request_sha256,
                        "response_frame_sha256": _sha256(response_frame),
                        "response_kind": response_kind,
                    },
                },
                diagnostic_ref={"status": "not_applicable"},
                envelope=RecourseEnvelope(
                    retention_class="operational",
                    disclosure_class="party",
                ),
                event_id=event_id,
                claimed_at=self.clock(),
                evidence_refs=(
                    {
                        "name": "mcp.request.frame",
                        "hash": call.request_sha256,
                        "grounding": "self_asserted",
                    },
                    {
                        "name": "mcp.response.frame",
                        "hash": _sha256(response_frame),
                        "grounding": "self_asserted",
                    },
                ),
                producer={"bulla_version": __version__, "capture": "mcp-stdio"},
            )
            if self.signer is not None:
                receipt = sign_action_receipt_v04(receipt, self.signer)
        except Exception as exc:
            call.diagnostic_reason = "PERSISTENCE_FAILED"
            self._persist_call(call)
            self._capture_failure(f"could not construct the observed-call receipt: {exc}")
            return
        receipt_name = f"{call.sequence:06d}-{event_id}.json"
        try:
            if self.retain_payloads:
                self._anchors["payloads"].write(
                    f"{call.sequence:06d}-request.bin",
                    call.request_frame,
                )
                self._anchors["payloads"].write(
                    f"{call.sequence:06d}-response.bin",
                    response_frame,
                )
            self._anchors["receipts"].write(
                receipt_name,
                receipt.to_json().encode("utf-8") + b"\n",
            )
        except OSError as exc:
            call.diagnostic_reason = "PERSISTENCE_FAILED"
            self._persist_call(call)
            self._capture_failure(f"response was forwarded but receipt persistence failed: {exc}")
            return
        self.completion_sequence += 1
        call.completion_sequence = self.completion_sequence
        call.coverage = "COMPLETE"
        call.diagnostic_reason = "NONE"
        call.response_frame = response_frame
        call.response_sha256 = _sha256(response_frame)
        call.response_kind = response_kind
        call.receipt_name = receipt_name
        if not self._persist_call(call):
            call.coverage = "INCOMPLETE"
            call.diagnostic_reason = "PERSISTENCE_FAILED"

    def finish(self, backend_exit_code: int) -> None:
        with self.lock:
            self.backend_exit_code = backend_exit_code
            reason = "BACKEND_TERMINATED" if backend_exit_code else "NO_RESPONSE"
            for call in self.pending.values():
                if call.coverage == "INCOMPLETE":
                    call.diagnostic_reason = reason
                    self._persist_call(call)
            self.pending.clear()
            self.inflight_requests.clear()
            self.finished = True
            try:
                self._write_session()
            except OSError as exc:
                self._capture_failure(f"could not finalize capture session: {exc}")
            finally:
                self._close_anchors()

    @property
    def has_coverage_gap(self) -> bool:
        return self.gap_sequence > 0 or any(
            call.coverage != "COMPLETE" for call in self.calls
        )


def _read_chunk(stream: BinaryIO) -> bytes:
    read1 = getattr(stream, "read1", None)
    if read1 is not None:
        return read1(_CHUNK_SIZE)
    return stream.read(_CHUNK_SIZE)


def _write_all(stream: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if written is None:
            written = len(view)
        if written <= 0:
            raise BrokenPipeError("zero-byte write")
        view = view[written:]
    stream.flush()


def _pump(
    source: BinaryIO,
    destination: BinaryIO,
    tap: _LineTap | None,
    failure: threading.Event,
    errors: list[str],
    *,
    close_destination: bool,
    tap_before_write: bool = False,
) -> None:
    try:
        while True:
            chunk = _read_chunk(source)
            if not chunk:
                if tap is not None:
                    tap.eof()
                return
            if tap is not None and tap_before_write:
                tap.feed(chunk)
            _write_all(destination, chunk)
            if tap is not None and not tap_before_write:
                tap.feed(chunk)
    except Exception as exc:
        errors.append(f"{threading.current_thread().name}: {type(exc).__name__}: {exc}")
        failure.set()
    finally:
        if close_destination:
            try:
                destination.close()
            except OSError:
                pass


def run_mcp_capture(
    *,
    output: Path,
    command: list[str],
    retain_payloads: bool = False,
    signer: Any = None,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> int:
    """Run one transparent stdio wrapper and return its process-style status."""
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise CaptureError("capture mcp requires a server command after --")
    session = CaptureSession(
        output, command, retain_payloads=retain_payloads, signer=signer,
    )
    child = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        **session_kwargs(),
    )
    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    source_in = stdin or sys.stdin.buffer
    destination_out = stdout or sys.stdout.buffer
    destination_err = stderr or sys.stderr.buffer
    transport_failure = threading.Event()
    transport_errors: list[str] = []
    input_tap = _LineTap(
        direction="client_to_server",
        on_frame=session.observe_client,
        on_gap=session.record_gap,
    )
    output_tap = _LineTap(
        direction="server_to_client",
        on_frame=session.observe_server,
        on_gap=session.record_gap,
    )
    threads = [
        threading.Thread(
            target=_pump,
            args=(source_in, child.stdin, input_tap, transport_failure, transport_errors),
            kwargs={"close_destination": True, "tap_before_write": True},
            daemon=True,
            name="bulla-capture-client",
        ),
        threading.Thread(
            target=_pump,
            args=(child.stdout, destination_out, output_tap, transport_failure, transport_errors),
            kwargs={"close_destination": False},
            daemon=True,
            name="bulla-capture-server",
        ),
        threading.Thread(
            target=_pump,
            args=(child.stderr, destination_err, None, transport_failure, transport_errors),
            kwargs={"close_destination": False},
            daemon=True,
            name="bulla-capture-stderr",
        ),
    ]
    for thread in threads:
        thread.start()
    try:
        while child.poll() is None:
            if transport_failure.is_set():
                terminate_tree(child)
                break
            time.sleep(0.02)
        return_code = child.wait()
    except KeyboardInterrupt:
        terminate_tree(child)
        return_code = child.returncode if child.returncode is not None else 130
    for thread in threads[1:]:
        thread.join(timeout=2.0)
    if transport_errors:
        message = "bulla capture: transparent transport failed: " + "; ".join(transport_errors) + "\n"
        try:
            _write_all(destination_err, message.encode("utf-8", errors="replace"))
        except Exception:
            print(message, end="", file=sys.stderr)
    session.finish(return_code)
    if return_code != 0:
        return return_code
    if transport_failure.is_set() or session.capture_failed or session.has_coverage_gap:
        return 1
    return 0


def _load_local_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CaptureDirectoryError(f"local record is not a regular file: {path}")
    try:
        value = _decode_strict_json(path.read_bytes())
    except Exception as exc:
        raise CaptureDirectoryError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CaptureDirectoryError(f"local record is not an object: {path}")
    return value


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _managed_members(directory: Path, suffix: str) -> list[Path]:
    try:
        members = sorted(directory.iterdir())
    except OSError as exc:
        raise CaptureDirectoryError(f"cannot inventory {directory}: {exc}") from exc
    for member in members:
        if member.is_symlink() or not member.is_file() or member.suffix != suffix:
            raise CaptureDirectoryError(
                f"unexpected member in {directory.name}: {member.name}"
            )
    return members


def _capture_session_members(sessions_dir: Path) -> list[Path]:
    try:
        session_paths = sorted(sessions_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise CaptureDirectoryError(f"cannot inventory capture sessions: {exc}") from exc
    for session_path in session_paths:
        if (
            session_path.is_symlink()
            or not session_path.is_dir()
            or _SESSION_NAME.fullmatch(session_path.name) is None
        ):
            raise CaptureDirectoryError(
                f"unexpected member in sessions: {session_path.name}"
            )
        identifier = session_path.name.removeprefix("session-")
        try:
            parsed = uuid.UUID(identifier)
        except ValueError as exc:
            raise CaptureDirectoryError(
                f"invalid capture session name: {session_path.name}"
            ) from exc
        if parsed.version != 4 or str(parsed) != identifier:
            raise CaptureDirectoryError(
                f"invalid capture session name: {session_path.name}"
            )
    return session_paths


def check_capture_directory(output: Path) -> CaptureCheckResult:
    """Check the current implementation-local capture layout fail-closed."""
    if output.is_symlink() or not output.is_dir():
        raise CaptureDirectoryError(f"capture directory does not exist: {output}")
    session_path = output / "session.json"
    session = _load_local_json(session_path)
    if not _check_address(session, "session_record_sha256"):
        raise CaptureDirectoryError("session record digest mismatch")
    for key in (
        "bulla_version", "capture_limit", "retained_payloads",
        "backend_command_sha256", "finished", "backend_exit_code",
        "capture_failed", "calls_observed", "calls_completed", "gaps_observed",
    ):
        if key not in session:
            raise CaptureDirectoryError(f"session record is missing {key}")
    retained = session["retained_payloads"]
    if not isinstance(retained, bool) or session["capture_limit"] != CAPTURE_LIMIT:
        raise CaptureDirectoryError("session capture configuration is invalid")
    required_dirs = [output / "calls", output / "gaps", output / "receipts"]
    if retained:
        required_dirs.append(output / "payloads")
    if any(path.is_symlink() or not path.is_dir() for path in required_dirs):
        raise CaptureDirectoryError("capture directory is missing a required local directory")
    expected_root = {"session.json", "calls", "gaps", "receipts"}
    if retained:
        expected_root.add("payloads")
    try:
        root_members = {member.name for member in output.iterdir()}
    except OSError as exc:
        raise CaptureDirectoryError(f"cannot inventory capture directory: {exc}") from exc
    if root_members != expected_root:
        unexpected = sorted(root_members - expected_root)
        missing = sorted(expected_root - root_members)
        raise CaptureDirectoryError(
            f"capture root inventory mismatch; unexpected={unexpected}, missing={missing}"
        )
    reasons: list[str] = []
    if not session["finished"]:
        reasons.append("capture session was not finalized")
    if session["capture_failed"]:
        reasons.append("capture session reported a persistence failure")
    if session["backend_exit_code"] != 0:
        reasons.append(f"backend exited {session['backend_exit_code']}")

    call_paths = _managed_members(output / "calls", ".json")
    gap_paths = _managed_members(output / "gaps", ".json")
    receipt_paths = _managed_members(output / "receipts", ".json")
    payload_paths = _managed_members(output / "payloads", ".bin") if retained else []
    if len(call_paths) != session["calls_observed"]:
        raise CaptureDirectoryError("session call count does not match local call records")
    if len(gap_paths) != session["gaps_observed"]:
        raise CaptureDirectoryError("session gap count does not match local gap records")
    if not call_paths:
        reasons.append("no tools/call was observed")

    expected_receipts: set[str] = set()
    expected_payloads: set[str] = set()
    completion_sequences: list[int] = []
    complete = incomplete = uncheckable = 0
    for index, path in enumerate(call_paths, 1):
        call = _load_local_json(path)
        if not _check_address(call, "call_record_sha256"):
            raise CaptureDirectoryError(f"call record digest mismatch: {path.name}")
        if path.name != f"{index:06d}.json" or call.get("sequence") != index:
            raise CaptureDirectoryError("call records are missing, duplicated, or reordered")
        coverage = call.get("coverage")
        if coverage not in _COVERAGE:
            raise CaptureDirectoryError(f"unknown coverage state in {path.name}")
        if coverage == "COMPLETE":
            complete += 1
            receipt_name = call.get("receipt")
            completion_sequence = call.get("completion_sequence")
            if not isinstance(receipt_name, str) or not isinstance(completion_sequence, int):
                raise CaptureDirectoryError(f"complete call lacks receipt coordinates: {path.name}")
            completion_sequences.append(completion_sequence)
            if receipt_name in expected_receipts:
                raise CaptureDirectoryError("duplicate receipt reference")
            expected_receipts.add(receipt_name)
            receipt_path = output / "receipts" / receipt_name
            try:
                raw_receipt = receipt_path.read_bytes()
                parsed = parse_action_receipt_json(raw_receipt).to_dict()
            except (OSError, ReceiptParseError) as exc:
                raise CaptureDirectoryError(f"invalid receipt {receipt_name}: {exc}") from exc
            verification = verify_receipt(parsed)
            if not verification.ok:
                reasons.append(f"receipt verification failed: {receipt_name}")
                continue
            receipt = ActionReceipt.from_dict(parsed)
            subject = receipt.action.get("subject")
            expected_subject = {
                "transport": "mcp-stdio-jsonrpc",
                "tool_name": call.get("tool_name"),
                "request_frame_sha256": call.get("request_frame_sha256"),
                "response_frame_sha256": call.get("response_frame_sha256"),
                "response_kind": call.get("response_kind"),
            }
            if receipt.schema_version != "0.4" or receipt.action.get("type") != "mcp.tools.call.observed":
                reasons.append(f"receipt uses the wrong existing ActionReceipt profile: {receipt_name}")
            if subject != expected_subject:
                reasons.append(f"receipt/call commitment mismatch: {receipt_name}")
            expected_evidence = (
                {
                    "name": "mcp.request.frame",
                    "hash": call.get("request_frame_sha256"),
                    "grounding": "self_asserted",
                },
                {
                    "name": "mcp.response.frame",
                    "hash": call.get("response_frame_sha256"),
                    "grounding": "self_asserted",
                },
            )
            if receipt.evidence_refs != expected_evidence:
                reasons.append(f"receipt evidence mismatch: {receipt_name}")
            if retained:
                request_name = f"{index:06d}-request.bin"
                response_name = f"{index:06d}-response.bin"
                expected_payloads.update((request_name, response_name))
                request_path = output / "payloads" / request_name
                response_path = output / "payloads" / response_name
                try:
                    request_bytes = request_path.read_bytes()
                    response_bytes = response_path.read_bytes()
                except OSError as exc:
                    raise CaptureDirectoryError(f"missing retained payload for call {index}: {exc}") from exc
                if _sha256(request_bytes) != call.get("request_frame_sha256"):
                    reasons.append(f"retained request commitment mismatch: call {index}")
                if _sha256(response_bytes) != call.get("response_frame_sha256"):
                    reasons.append(f"retained response commitment mismatch: call {index}")
        elif coverage == "INCOMPLETE":
            incomplete += 1
            reasons.append(f"call {index} is incomplete: {call.get('diagnostic_reason')}")
        else:
            uncheckable += 1
            reasons.append(f"call {index} is uncheckable: {call.get('diagnostic_reason')}")

    if sorted(completion_sequences) != list(range(1, len(completion_sequences) + 1)):
        raise CaptureDirectoryError("completion records are missing, duplicated, or reordered")
    for index, path in enumerate(gap_paths, 1):
        gap = _load_local_json(path)
        if not _check_address(gap, "gap_record_sha256"):
            raise CaptureDirectoryError(f"gap record digest mismatch: {path.name}")
        if path.name != f"{index:06d}.json" or gap.get("gap_sequence") != index:
            raise CaptureDirectoryError("gap records are missing, duplicated, or reordered")
        if gap.get("coverage") != "UNCHECKABLE":
            raise CaptureDirectoryError("gap record falsely claims completeness")
        uncheckable += 1
        reasons.append(f"capture gap {index}: {gap.get('diagnostic_reason')}")

    actual_receipts = {path.name for path in receipt_paths}
    if actual_receipts != expected_receipts:
        raise CaptureDirectoryError("missing or orphaned receipt file")
    actual_payloads = {path.name for path in payload_paths}
    if actual_payloads != expected_payloads:
        raise CaptureDirectoryError("missing or orphaned retained payload file")
    if session["calls_completed"] != complete:
        raise CaptureDirectoryError("session completion count does not match call records")

    if os.name == "posix":
        for directory in required_dirs + [output]:
            if _mode(directory) != 0o700:
                reasons.append(f"directory mode is not 0700: {directory.name}")
        files = [session_path, *call_paths, *gap_paths, *receipt_paths, *payload_paths]
        for path in files:
            if _mode(path) != 0o600:
                reasons.append(f"file mode is not 0600: {path.name}")

    return CaptureCheckResult(
        ok=not reasons,
        complete=complete,
        incomplete=incomplete,
        uncheckable=uncheckable,
        receipts=len(receipt_paths),
        retained_payloads=retained,
        receipt_paths=tuple(path.resolve(strict=True) for path in receipt_paths),
        reasons=tuple(reasons),
    )


def check_capture_root(root: Path) -> CaptureRootCheckResult:
    """Aggregate independently checkable sessions without creating a root index."""
    sessions_dir = _validate_capture_root_layout(root)
    session_paths = _capture_session_members(sessions_dir)

    complete = incomplete = uncheckable = receipts = empty = retained = 0
    receipt_paths: list[Path] = []
    reasons: list[str] = []
    for session_path in session_paths:
        try:
            session_members = list(session_path.iterdir())
        except OSError as exc:
            raise CaptureDirectoryError(
                f"cannot inventory capture session {session_path.name}: {exc}"
            ) from exc
        if not session_members:
            incomplete += 1
            reasons.append(f"{session_path.name}: allocated session was not initialized")
            continue
        result = check_capture_directory(session_path)
        complete += result.complete
        incomplete += result.incomplete
        uncheckable += result.uncheckable
        receipts += result.receipts
        retained += int(result.retained_payloads)
        session_empty = (
            result.complete == 0
            and result.incomplete == 0
            and result.uncheckable == 0
            and result.receipts == 0
        )
        if session_empty:
            empty += 1
        ignorable = session_empty and result.reasons == ("no tools/call was observed",)
        if not result.ok and not ignorable:
            reasons.extend(f"{session_path.name}: {reason}" for reason in result.reasons)
        receipt_paths.extend(
            path.resolve(strict=True)
            for path in sorted((session_path / "receipts").iterdir())
        )
    if complete == 0:
        reasons.append("capture root contains no complete tools/call receipt")
    return CaptureRootCheckResult(
        ok=not reasons,
        sessions=len(session_paths),
        empty=empty,
        complete=complete,
        incomplete=incomplete,
        uncheckable=uncheckable,
        receipts=receipts,
        retained_payload_sessions=retained,
        receipt_paths=tuple(receipt_paths),
        reasons=tuple(reasons),
    )


def check_capture_path(path: Path) -> CaptureCheckResult | CaptureRootCheckResult:
    """Dispatch local checking without turning either layout into a wire profile."""
    if path.is_symlink() or not path.is_dir():
        raise CaptureDirectoryError(f"capture directory does not exist: {path}")
    try:
        names = {member.name for member in path.iterdir()}
    except OSError as exc:
        raise CaptureDirectoryError(f"cannot inventory capture directory: {exc}") from exc
    if _CAPTURE_ROOT_MARKER in names or "sessions" in names:
        return check_capture_root(path)
    return check_capture_directory(path)
