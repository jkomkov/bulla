#!/usr/bin/env python3
"""Awareness-gap demo — non-LLM reproducer.

Three steps run in sequence:

  1. The failure: simulate a real MCP-style flow where the filesystem
     server's read_file returns an absolute path, the agent passes it
     to a GitHub create_file call, and the GitHub-side validator
     rejects it. Schema validation passes (both fields are strings);
     the request still fails.

  2. The diagnosis: run ``bulla.compose_multi`` on the same two
     server tool lists to surface the path_convention seam in the
     same prose form ``bulla scan`` produces.

  3. The fix: call ``bulla.translate("path_convention", ...)`` to
     normalize the path, then re-run the failing flow. The GitHub-
     side validator accepts the corrected path.

The script depends on no LLM, no live MCP servers, no network. It
loads the canned filesystem.json and github.json manifests from
``./manifests/``, simulates the GitHub validator's repo-relative-
path requirement directly, and uses bulla's own runtime for
diagnosis and translation. Anyone who clones the repo can run this
script and see the same fee, the same wrong output, the same
corrected output.

Usage:
    python repro.py

Exits 0 on the canonical-demo path. Use --no-fix to skip the
translation step and observe the bare failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent / "src")
)

from bulla import compose_multi, translate
from bulla.scan_format import (
    compute_pairwise_fees,
    format_scan_narrative,
)


HERE = Path(__file__).resolve().parent


# ── 1. The simulated GitHub validator ──────────────────────────────


def github_create_file_validator(path: str) -> tuple[bool, str]:
    """Simulate GitHub's create-file path validation.

    Returns (accepted, reason). GitHub's REST API rejects paths that
    look like local filesystem absolute paths because the API treats
    the path as repository-relative.
    """
    if not path:
        return False, "path is empty"
    if path.startswith("/"):
        return False, (
            f"path {path!r} looks like a filesystem absolute path; "
            "GitHub create_file expects a repository-relative path "
            "(e.g. 'src/main.py')"
        )
    if re.match(r"^[A-Z]:[\\/]", path):
        return False, (
            f"path {path!r} looks like a Windows absolute path; "
            "GitHub create_file expects a repository-relative path"
        )
    return True, "accepted"


def filesystem_read_file_returns(filename: str) -> str:
    """Simulate the filesystem MCP server's read_file return value.

    The reference filesystem server runs in a sandboxed root and
    returns absolute paths within that sandbox. We mirror that here
    so the demo is honest about the convention.
    """
    return f"/home/user/projects/myrepo/{filename}"


# ── 2. The demo flow ────────────────────────────────────────────────


def load_manifests() -> dict[str, list[dict]]:
    """Load the canned manifests as the input to compose_multi."""
    out: dict[str, list[dict]] = {}
    for name in ("filesystem", "github"):
        data = json.loads((HERE / "manifests" / f"{name}.json").read_text())
        # The canned manifests are MCP `tools/list` responses — the
        # tools are under the `tools` key.
        if isinstance(data, dict) and "tools" in data:
            out[name] = data["tools"]
        elif isinstance(data, list):
            out[name] = data
        else:
            raise SystemExit(f"unexpected manifest shape in {name}.json")
    return out


def run_demo(*, with_fix: bool = True) -> int:
    print("=" * 60)
    print("Awareness-gap demo — bulla on filesystem + github")
    print("=" * 60)
    print()

    # ── Step 1: the failure ────────────────────────────────────────
    print("Step 1. The agent reads a file, then commits it to GitHub.")
    print()
    fs_path = filesystem_read_file_returns("README.md")
    print(f"  filesystem.read_file(name='README.md')")
    print(f"    -> path = {fs_path!r}")
    print()
    print(f"  github.create_file(path={fs_path!r}, ...)")
    accepted, reason = github_create_file_validator(fs_path)
    if accepted:
        print(f"    -> accepted ({reason})")
        print()
        print("Unexpected: the validator accepted the absolute path. "
              "Demo cannot proceed.")
        return 1
    print(f"    -> REJECTED")
    print(f"       {reason}")
    print()
    print(
        "Schema validation on both sides passes. Both fields are typed "
        "as `str`. The agent silently writes the file to the wrong "
        "place — or, in this case, gets a runtime error after the "
        "request goes out."
    )
    print()

    # ── Step 2: the diagnosis ──────────────────────────────────────
    print("Step 2. bulla diagnoses the seam.")
    print()
    server_tools = load_manifests()
    result = compose_multi(server_tools)
    pairwise = compute_pairwise_fees(server_tools)
    narrative = format_scan_narrative(
        result.diagnostic,
        server_names=sorted(server_tools.keys()),
        config_source=str(HERE / "manifests"),
        pairwise_fees=pairwise,
    )
    for line in narrative.splitlines():
        print(f"  {line}")
    print()

    if not with_fix:
        print("Skipping the fix step (--no-fix). Run without the flag "
              "to see the corrected output.")
        return 0

    # ── Step 3: the fix ────────────────────────────────────────────
    print("Step 3. bulla.translate normalizes the path.")
    print()
    # The bridge runtime ships a registered translator for
    # path_convention. We pin BULLA_REPO_ROOT to the simulated
    # filesystem root so the translation is deterministic — in a
    # real agent loop, the env var is set once at deploy time, or
    # the user registers a project-specific translator.
    import os
    os.environ["BULLA_REPO_ROOT"] = "/home/user/projects/myrepo"

    print(f"  bulla.translate('path_convention', value={fs_path!r},")
    print(f"                  from_convention='filesystem-absolute',")
    print(f"                  to_convention='repo-relative')")
    tr = translate(
        "path_convention",
        value=fs_path,
        from_convention="filesystem-absolute",
        to_convention="repo-relative",
    )
    print(f"    -> value={tr.value!r}")
    print(f"       equivalence={tr.evidence.equivalence!r}")
    print(f"       receipt: {tr.receipt.receipt_hash[:16]}...")
    print()
    print(f"  github.create_file(path={tr.value!r}, ...)")
    accepted, reason = github_create_file_validator(tr.value)
    print(f"    -> {'ACCEPTED' if accepted else 'rejected'} "
          f"({reason})")
    print()
    print("=" * 60)
    print("Done. The seam was detected by bulla at composition time, "
          "before any agent ran. The translator produces a receipt "
          "that chains into the composition's audit trail.")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-fix",
        action="store_true",
        help="Skip the bulla.translate step; only show failure + diagnosis.",
    )
    args = parser.parse_args()
    return run_demo(with_fix=not args.no_fix)


if __name__ == "__main__":
    sys.exit(main())
