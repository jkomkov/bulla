#!/usr/bin/env python3
"""Mint the release receipt only AFTER PyPI accepts the release.

``releases/reconstruct.py`` records the honest gap: every receipt before
0.43.0 was reconstructed after the fact, unsigned by construction. This
script is the other half — run by ``publish.yml`` after Trusted Publishing —
and mints the ``package.release`` ActionReceipt only after the wheel and sdist
on disk match PyPI's accepted digests and expose Integrity API provenance:

  * evidence  — sha256 of the exact wheel + sdist PyPI serves;
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

    python scripts/mint_release_receipt.py --dist dist --out releases/0.44.0.json \
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
from bulla.coverage import fetch_pypi_project, fetch_pypi_provenance, integrity_url
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


def _git_tree_sha256() -> str | None:
    """Hash the exact Git tree object payload without mislabeling its SHA-1 id."""
    tree = _git("rev-parse", "HEAD^{tree}")
    if not tree:
        return None
    result = subprocess.run(
        ["git", "cat-file", "tree", tree],
        capture_output=True,
        cwd=_REPO,
        timeout=30,
        check=False,
    )
    if result.returncode:
        return None
    return "sha256:" + hashlib.sha256(result.stdout).hexdigest()


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
    ap.add_argument("--project", default="bulla", help="PyPI project name.")
    ap.add_argument("--repository", default="jkomkov/bulla", help="Expected GitHub Trusted Publisher owner/repo.")
    args = ap.parse_args()

    wheels = sorted(args.dist.glob(f"bulla-{__version__}-*.whl"))
    sdists = sorted(args.dist.glob(f"bulla-{__version__}.tar.gz"))
    if not wheels or not sdists:
        print(f"Error: dist/ lacks bulla-{__version__} wheel+sdist (build first; version must match __version__).",
              file=sys.stderr)
        return 2

    try:
        project_doc = fetch_pypi_project(args.project)
        published = {
            item["filename"]: item
            for item in (project_doc.get("releases") or {}).get(__version__, [])
        }
        accepted = []
        for artifact in (wheels[0], sdists[0]):
            record = published.get(artifact.name)
            if record is None:
                raise RuntimeError(f"PyPI has not accepted {artifact.name}")
            local_digest = _sha256_file(artifact).removeprefix("sha256:")
            remote_digest = (record.get("digests") or {}).get("sha256")
            if local_digest != remote_digest:
                raise RuntimeError(
                    f"PyPI digest mismatch for {artifact.name}: local={local_digest} remote={remote_digest}"
                )
            provenance = fetch_pypi_provenance(args.project, __version__, artifact.name)
            bundles = provenance.get("attestation_bundles") or []
            if not any(
                (bundle.get("publisher") or {}).get("kind") == "GitHub"
                and (bundle.get("publisher") or {}).get("repository") == args.repository
                and bundle.get("attestations")
                for bundle in bundles
            ):
                raise RuntimeError(
                    f"Integrity API has no {args.repository} Trusted Publisher attestation for {artifact.name}"
                )
            accepted.append(record)
    except RuntimeError as exc:
        print(f"Error: post-publication verification failed: {exc}", file=sys.stderr)
        return 1

    commit = _git("rev-parse", "HEAD")
    tag = os.environ.get("GITHUB_REF_NAME") or _git("describe", "--exact-match", "--tags") or ""
    tree_hash = _git_tree_sha256()

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

    producer: dict = {
        "bulla_version": __version__,
        "minted": "post-publication",
        "workflow": os.environ.get("GITHUB_WORKFLOW", "local"),
        "pypi_project": args.project,
    }
    if signer is None:
        producer["note"] = "UNSIGNED — no release key configured at mint time (stated, not hidden)"

    kwargs = dict(
        package="bulla",
        version=__version__,
        git_commit=commit,
        git_tag=tag,
        wheel_sha256="sha256:" + accepted[0]["digests"]["sha256"],
        sdist_sha256="sha256:" + accepted[1]["digests"]["sha256"],
        tree_hash=tree_hash,
        test_result=args.test_result,
        diagnostic_ref=diagnostic_ref,
        envelope=_release_envelope(__version__),
        root_of_trust={
            "scheme": "sigstore-pep740",
            "publisher": f"github:{args.repository}",
            "integrity_api": [
                integrity_url(args.project, __version__, item["filename"])
                for item in accepted
            ],
        },
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
