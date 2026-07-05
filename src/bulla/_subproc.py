"""Robust process-tree lifecycle for spawned MCP servers.

An MCP server is usually a process *tree* — ``npx -> node -> server`` is the common
case. Terminating only the direct child (the ``npx`` wrapper) leaves the ``node``
grandchildren alive, holding the captured stdout/stderr pipes open. Two failures
follow from that single leak:

* a reader blocked on EOF hangs forever (the in-suite ``subprocess.run`` hang), and
* node processes accumulate across respawns, wedging a long-running proxy (the
  live-demo leak).

The fix is to put each server in its own session (process group) at spawn time and
tear down the whole group on cleanup — terminate the group, reap it, then close the
pipes. Idempotent and safe to call from a ``finally`` even if the child already exited.
"""

from __future__ import annotations

import os
import signal
import subprocess

_POSIX = os.name == "posix"


def session_kwargs() -> dict:
    """Spawn kwargs that isolate the child and its descendants in their own group.

    On POSIX the child becomes a session/group leader; its descendants inherit the
    process-group id, so the whole tree can be signalled with :func:`os.killpg`.
    On Windows we request a new process group so ``terminate()`` reaches the tree.
    """
    if _POSIX:
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        # already gone, or the child was not spawned as a group leader
        pass


def terminate_tree(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """Terminate the child's whole process group, reap it, and close its pipes."""
    if proc.poll() is None:
        if _POSIX:
            _signal_group(proc.pid, signal.SIGTERM)
        else:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            if _POSIX:
                _signal_group(proc.pid, signal.SIGKILL)
            else:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=grace)  # reap — no zombie
            except subprocess.TimeoutExpired:
                pass
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


async def terminate_tree_async(proc, grace: float = 2.0) -> None:
    """Async variant for asyncio subprocesses (the live proxy)."""
    import asyncio

    if proc.returncode is None:
        if _POSIX:
            _signal_group(proc.pid, signal.SIGTERM)
        else:
            try:
                proc.terminate()
            except (ProcessLookupError, OSError):
                pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace)
        except (asyncio.TimeoutError, ProcessLookupError):
            if _POSIX:
                _signal_group(proc.pid, signal.SIGKILL)
            else:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace)  # reap
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
