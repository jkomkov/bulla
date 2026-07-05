#!/usr/bin/env python3
"""README-as-composition gate — single source of truth.

bulla's thesis is that unexecuted seams drift. The README<->wheel seam is one:
0.40.0 shipped a Session example that TypeError'd on first touch because nobody
ran it. This is the package taking its own medicine — it extracts every fenced
``python`` block from README.md and runs it as a standalone script against the
installed bulla, failing if any raises.

Exemptions are VISIBLE, not counted. A block that genuinely needs external
state (a live MCP server, a framework extra, an object built in prose) opts out
with an HTML comment on the line directly above its fence::

    <!-- bulla-doc-skip: needs a live MCP server -->
    ```python
    ...
    ```

The marker is invisible in rendered Markdown (GitHub/PyPI) but visible in
source and printed in this script's CI output, so exemption growth is
reviewable in a diff. It is deliberately NOT count-pinned — a pinned count is
itself a drift-trap; the manifest is the review surface. The long-term
direction is to convert illustrative fragments to runnable-with-prelude rather
than skip them.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
_SKIP = re.compile(r"<!--\s*bulla-doc-skip:\s*(.*?)\s*-->")


def blocks() -> list[tuple[int, str, str | None]]:
    """(fence_line, source, skip_reason_or_None) for every ```python block."""
    lines = README.read_text(encoding="utf-8").splitlines()
    out: list[tuple[int, str, str | None]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "```python":
            reason = None
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                m = _SKIP.search(lines[j])
                if m:
                    reason = m.group(1)
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != "```":
                body.append(lines[i])
                i += 1
            out.append((i, "\n".join(body), reason))
        i += 1
    return out


def runnable() -> list[tuple[int, str]]:
    return [(n, s) for (n, s, r) in blocks() if r is None]


def main() -> int:
    bs = blocks()
    run = [(n, s) for (n, s, r) in bs if r is None]
    skip = [(n, r) for (n, s, r) in bs if r is not None]

    # The manifest — always printed, so a diff that adds a skip marker is visible.
    print(f"README examples: {len(run)} runnable, {len(skip)} illustrative (skipped)")
    for n, _s, r in bs:
        tag = "RUN " if r is None else "SKIP"
        print(f"  {tag} block ~line {n}" + (f"  — {r}" if r else ""))

    failed: list[tuple[int, str]] = []
    for n, src in run:
        p = subprocess.run(
            [sys.executable, "-c", src], capture_output=True, text=True, timeout=120
        )
        if p.returncode != 0:
            failed.append((n, p.stderr))

    for n, err in failed:
        print(f"\nFAILED README block ~line {n}:\n{err}", file=sys.stderr)
    ok = len(run) - len(failed)
    print(f"\n{'OK' if not failed else 'FAIL'}: {ok}/{len(run)} runnable README blocks pass")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
