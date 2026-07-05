#!/usr/bin/env python3
"""Bulla showcase: the full algebraic repair loop on real MCP servers.

No LLM.  No network.  No randomness.  Deterministic from first line to last.

Two servers (filesystem + GitHub), 40 tools, 114 cross-server edges.
Schema validation sees nothing wrong.  Bulla finds 22 obstructions across
4 semantic dimensions, identifies the exact matroid basis for repair,
simulates the disclosure, re-diagnoses at fee=0, and prints the receipt
with Lean 4 theorem provenance.

Usage:
    python run_showcase.py
    bulla showcase            # after pip install
    bulla showcase --json     # machine-readable output
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly from the repo without pip install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from bulla.showcase import run_showcase  # noqa: E402

if __name__ == "__main__":
    run_showcase()
