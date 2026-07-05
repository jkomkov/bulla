"""The README is a composition — gate it (pytest wrapper).

The extraction + execution logic is the single source of truth in
``scripts/check_readme_examples.py`` (which also prints the RUN/SKIP manifest
in CI so exemption growth is visible in a diff, not silently pinned to a count).
This wrapper reuses that parser so a copy-paste of any non-skipped README block
runs green, per-block, in the local suite too.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_readme_examples.py"
_spec = importlib.util.spec_from_file_location("check_readme_examples", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ALL_BLOCKS = _mod.blocks()
RUNNABLE = _mod.runnable()


def test_readme_has_runnable_blocks():
    assert ALL_BLOCKS, "no ```python blocks found — did the README move?"
    assert RUNNABLE, "every README python block is skip-marked — the gate is toothless"


@pytest.mark.parametrize(
    "src", [pytest.param(s, id=f"readme-block-line-{n}") for (n, s) in RUNNABLE]
)
def test_readme_block_runs(src: str):
    r = subprocess.run(
        [sys.executable, "-c", src], capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, (
        f"README example failed:\n--- source ---\n{src}\n--- stderr ---\n{r.stderr}"
    )
