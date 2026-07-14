#!/usr/bin/env python3
"""Mint the release receipt AT release time — closing the retroactive seam.

``releases/reconstruct.py`` records the honest gap: every receipt before
0.43.0 was reconstructed after the fact, unsigned by construction. This
script is the other half — run by ``publish.yml`` at tag time (and runnable
locally against a built ``dist/``), it mints the ``package.release``
ActionReceipt while the wheel that ships is on disk:

  * evidence  — sha256 of the actual ``dist/`` wheel + sdist
                (``third_party_anchored`` once PyPI serves the same bytes);
  * verdict   — a real, recomputable release-gate ``WitnessReceipt`` minted
                here over the flagship example composition and written as a
                ``.witness.json`` sidecar (``diagnostic_ref`` points at it);
  * signature — ed25519 over the content hash when a key is supplied
                (``--key`` file or ``BULLA_RELEASE_KEY`` env holding the
                keyfile JSON); unsigned minting is refused unless
                ``--allow-unsigned`` states the gap explicitly;
  * anchor    — optional OpenTimestamps stamp of the attestation hash
                (``--ots``, best-effort: calendars answer with a pending
                attestation that upgrades to a Bitcoin block later).

    python scripts/mint_release_receipt.py --dist dist --out releases/0.43.0.json \
        --key ~/.bulla/release-key.json --ots
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from bulla import __version__
from bulla.action_receipt import build_release_receipt, verify_receipt
from bulla.diagnostic import diagnose
from bulla.envelope import (
    Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy,
)
from bulla.parser import load_composition
from bulla.witness import witness

_REPO = Path(__file__).resolve().parents[1]
_GATE_COMPOSITION = _REPO / "examples" / "two-manifest-quickstart" / "example_fetch_memory_joint.yaml"


def _sha256_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=_REPO, timeout=30
    ).stdout.strip()


def _release_envelope(version: str) -> RecourseEnvelope:
    return RecourseEnvelope(
        authority=Authority(
            principal="github:jkomkov",
            policy="policy://bulla/release",
            delegation=("pypi:project:bulla",),
        ),
        bounds=Bounds(scope=f"pypi:bulla version:{version}"),
        recourse=Recourse(
            challenge_window="P90D",
            forum=Forum(
                log_endpoint="https://pypi.org/project/bulla/",
                # Trusted Publishing anchors the release in the public Rekor
                # log — the independently-pinnable root the retroactive corpus
                # lacked (its forums were host-asserted, honestly marked).
                trusted_root_ref="rekor:sigstore-pep740",
            ),
            remedies=(
                Remedy(rung="recompute", verifier="pip download + sha256 vs PyPI", anchor=f"pypi:bulla=={version}"),
                Remedy(rung="revert", verifier="pypi yank", anchor=f"pypi:bulla=={version}"),
                Remedy(rung="escalate", verifier="maintainer review", anchor="github:jkomkov"),
            ),
        ),
        retention_class="authority-permanent",
        disclosure_class="public",
    )


def _release_gate_witness(out_sidecar: Path) -> dict:
    """Mint the real release-gate verdict: the flagship example composition,
    measured by the code being released, written next to the receipt so the
    reference is resolvable offline."""
    comp = load_composition(_GATE_COMPOSITION)
    receipt = witness(diagnose(comp), comp)
    out_sidecar.write_text(json.dumps(receipt.to_dict(), indent=2) + "\n", encoding="utf-8")
    return {"status": "reference", "ref": "sha256:" + receipt.to_dict()["receipt_hash"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dist", type=Path, default=_REPO / "dist", help="Directory holding the built wheel + sdist.")
    ap.add_argument("--out", type=Path, default=None, help=f"Output path (default releases/{__version__}.json).")
    ap.add_argument("--key", type=Path, default=None, help="ed25519 keyfile (bulla key gen). Falls back to $BULLA_RELEASE_KEY (keyfile JSON).")
    ap.add_argument("--allow-unsigned", action="store_true", help="Mint without a signature, stating the gap in producer.")
    ap.add_argument("--ots", action="store_true", help="OpenTimestamps-anchor the attestation hash (writes .ots sidecar, base64).")
    ap.add_argument("--test-result", default=None, help="e.g. '12549 passed' — the suite result on this exact commit.")
    args = ap.parse_args()

    wheels = sorted(args.dist.glob(f"bulla-{__version__}-*.whl"))
    sdists = sorted(args.dist.glob(f"bulla-{__version__}.tar.gz"))
    if not wheels or not sdists:
        print(f"Error: dist/ lacks bulla-{__version__} wheel+sdist (build first; version must match __version__).",
              file=sys.stderr)
        return 2

    commit = _git("rev-parse", "HEAD")
    tag = os.environ.get("GITHUB_REF_NAME") or _git("describe", "--exact-match", "--tags") or ""
    tree = _git("rev-parse", "HEAD^{tree}")

    out = args.out or (_REPO / "releases" / f"{__version__}.json")
    sidecar = out.with_suffix(".witness.json")
    diagnostic_ref = _release_gate_witness(sidecar)

    signer = None
    keyfile_json = None
    if args.key:
        keyfile_json = json.loads(args.key.read_text())
    elif os.environ.get("BULLA_RELEASE_KEY"):
        keyfile_json = json.loads(os.environ["BULLA_RELEASE_KEY"])
    if keyfile_json is not None:
        from bulla.identity import LocalEd25519Signer
        signer = LocalEd25519Signer.from_keyfile_dict(keyfile_json)
    elif not args.allow_unsigned:
        print("Error: no signing key (--key / $BULLA_RELEASE_KEY). An unsigned release receipt "
              "re-opens the seam this script closes; pass --allow-unsigned to state the gap explicitly.",
              file=sys.stderr)
        return 2

    # Honest provenance: only a CI tag-time mint may claim "at-release"; a
    # local run is a release-candidate build (the wheel PyPI serves is the
    # one CI builds, so a local receipt must not pose as the shipping one).
    producer: dict = {
        "bulla_version": __version__,
        "minted": "at-release" if os.environ.get("GITHUB_ACTIONS") else "release-candidate-build",
        "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
    }
    if signer is None:
        producer["note"] = "UNSIGNED — no release key configured at mint time (stated, not hidden)"

    kwargs = dict(
        package="bulla",
        version=__version__,
        git_commit=commit,
        git_tag=tag,
        wheel_sha256=_sha256_file(wheels[0]),
        sdist_sha256=_sha256_file(sdists[0]),
        tree_hash="sha256:" + tree if tree else None,
        test_result=args.test_result,
        diagnostic_ref=diagnostic_ref,
        envelope=_release_envelope(__version__),
        root_of_trust={"scheme": "sigstore-pep740"},  # rekor_log_index known only post-publish
        timestamp=datetime.now(timezone.utc).isoformat(),
        producer=producer,
    )
    receipt = build_release_receipt(**kwargs)
    if signer is not None:
        receipt = build_release_receipt(**kwargs, signature=signer.sign(receipt.content_hash))

    out.write_text(receipt.to_json() + "\n", encoding="utf-8")
    v = verify_receipt(receipt.to_dict())
    print(f"wrote {out}  verified_to={v.verified_to}  content={receipt.content_hash[:23]}…")
    if not v.ok:
        print("Error: freshly minted receipt does not verify — refusing to continue.", file=sys.stderr)
        return 1

    if args.ots:
        try:
            from bulla.ots import stamp_hash
            import base64
            att_hex = receipt.attestation_hash.split(":", 1)[1]
            proof = base64.b64encode(stamp_hash(att_hex)).decode("ascii")
            out.with_suffix(".json.ots").write_text(proof + "\n")
            print(f"anchored attestation hash via OpenTimestamps -> {out.name}.ots (pending until a Bitcoin block)")
        except Exception as e:
            # Best-effort by design: the anchor upgrades non-repudiation across
            # time; its absence is reported, never silently skipped.
            print(f"WARNING: OTS anchoring failed ({e}) — receipt is signed but not timestamped.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
