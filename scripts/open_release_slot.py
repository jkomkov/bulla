#!/usr/bin/env python3
"""Repository-local compatibility wrapper for opening a v0.2 release slot.

The default-branch release workflow uses ``trusted_release_signer.py``. This
wrapper remains for maintainers running the same slot contract manually and
requires the external versioned issuer registry.

    python scripts/open_release_slot.py --version 0.45.0 \
        --out release-slot/0.45.0.slot.json --key ~/.bulla/release-key.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_slot import build_slot, verify_slot  # noqa: E402
from trusted_release_signer import release_issuer  # noqa: E402

from bulla.identity import LocalEd25519Signer  # noqa: E402

_REPO = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=_REPO, timeout=30
    ).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--context",
        type=Path,
        default=_REPO / "releases/release-trust-context.json",
    )
    ap.add_argument("--key", type=Path, default=None,
                    help="ed25519 keyfile (bulla key gen). Falls back to $BULLA_RELEASE_KEY (keyfile JSON).")
    args = ap.parse_args()

    keyfile_json = None
    if args.key:
        keyfile_json = json.loads(args.key.read_text())
    elif os.environ.get("BULLA_RELEASE_KEY"):
        keyfile_json = json.loads(os.environ["BULLA_RELEASE_KEY"])
    if keyfile_json is None:
        print("a release key is required to open a slot (--key or $BULLA_RELEASE_KEY)", file=sys.stderr)
        return 2
    signer = LocalEd25519Signer.from_keyfile_dict(keyfile_json)
    try:
        issuer_record = release_issuer(args.context, args.version)
    except (OSError, ValueError) as exc:
        print(f"release trust context rejected: {exc}", file=sys.stderr)
        return 2
    if signer.issuer != issuer_record["issuer"]:
        print("release key is not selected by the external context", file=sys.stderr)
        return 2

    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    tree_payload = subprocess.run(
        ["git", "cat-file", "tree", tree], capture_output=True, cwd=_REPO, timeout=30
    ).stdout
    import hashlib

    slot = build_slot(
        version=args.version,
        source_commit=commit,
        source_tree_sha256="sha256:" + hashlib.sha256(tree_payload).hexdigest(),
        signer=signer,
        issuer_record=issuer_record,
    )
    ok, reason = verify_slot(slot, issuer_record=issuer_record)
    if not ok:
        print(f"freshly built slot failed verification: {reason}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(slot, indent=2, sort_keys=True) + "\n")
    print(f"opened release slot {slot['version']} -> {args.out}")
    print(f"slot_hash {slot['slot_hash']}")
    print(f"close_deadline {slot['close_deadline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
