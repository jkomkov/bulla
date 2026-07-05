"""The leak IS the property: a spawned process *tree* must be fully reaped.

An MCP server is `npx -> node -> server`; terminating only the direct child leaks the
grandchild, which holds the captured pipe open (the hang) and accumulates across
respawns (the live leak). These tests spawn a real grandchild-leaking tree and assert
the whole group is gone after teardown.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from bulla._subproc import session_kwargs, terminate_tree

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process-group teardown is POSIX")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _spawn_tree() -> tuple[subprocess.Popen, int]:
    """A parent that backgrounds a long-lived grandchild and prints its pid."""
    proc = subprocess.Popen(
        ["sh", "-c", "sleep 60 & echo $!; wait"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **session_kwargs(),
    )
    grandchild_pid = int(proc.stdout.readline().strip())
    return proc, grandchild_pid


def test_terminate_tree_reaps_the_whole_group():
    proc, gpid = _spawn_tree()
    assert _alive(gpid), "grandchild should be alive before teardown"

    terminate_tree(proc, grace=2.0)

    deadline = time.time() + 3.0
    while time.time() < deadline and _alive(gpid):
        time.sleep(0.05)
    assert not _alive(gpid), "grandchild leaked — the process tree was not reaped"
    # pipes closed (fds released)
    assert proc.stdin is None or proc.stdin.closed
    assert proc.stdout is None or proc.stdout.closed


def test_terminate_tree_is_idempotent_on_a_dead_child():
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **session_kwargs(),
    )
    proc.wait(timeout=5)
    # safe to call in a finally even though the child already exited
    terminate_tree(proc, grace=1.0)
    terminate_tree(proc, grace=1.0)
