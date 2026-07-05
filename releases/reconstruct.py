#!/usr/bin/env python3
"""Reconstruct the retroactive ReleaseReceipt corpus — bulla dogfooding its own
schema against its OWN release history.

These receipts were NOT minted at release time (the ActionReceipt did not exist
yet); they are honest reconstructions, marked ``reconstructed`` in ``producer``
and left UNSIGNED (no one signed them at the time). They therefore verify to the
``digest`` rung — which is the truth. The first *signed*, attestation-rung,
externally-rooted ReleaseReceipt is the one minted live when 0.41 actually
publishes through Trusted Publishing.

The embedded data is real and immutable: the wheel/sdist SHA-256 come from PyPI
(``https://pypi.org/pypi/bulla/<v>/json``); the git commits are the version-bump
commits on ``origin/main``. Two honest findings this corpus records about bulla's
own provenance — the exact thing ``bulla coverage`` exists to surface:

  * 0.37.0 shipped to PyPI with **no git tag** (``git_tag: null``) — a release the
    git anchor is blind to.
  * the ``v0.40.0`` tag points at a *different* commit than the version-bump
    commit, so "the tag" and "the release" are not the same object.

Run ``python bulla/releases/reconstruct.py`` from the repo root to regenerate.
"""

from __future__ import annotations

import json
from pathlib import Path

from bulla.action_receipt import build_release_receipt
from bulla.envelope import Authority, Bounds, Forum, Recourse, Remedy, RecourseEnvelope

# Real, immutable release data. wheel/sdist digests: PyPI. commit: the
# version-bump commit on origin/main. tag: the git tag, or None (the gap).
RELEASES = [
    {
        "version": "0.37.0",
        "git_commit": "c3c3bd36f5db5c7921c5b704ffea8a30ece02445",
        "git_tag": None,  # <-- shipped to PyPI, never git-tagged
        "wheel_sha256": "sha256:5870fbf176f3ed043f9ade8ac6659b48ec316889425344177b69441a1cd28550",
        "sdist_sha256": "sha256:fd0ab7c836e15064e4447f4620c734856a793819aea8975d6c9db893a167f997",
        "released": "2026-05-03T00:00:00+00:00",
    },
    {
        "version": "0.40.0",
        "git_commit": "54c80401964dc4456b71c8b5dd6a39b34dde4e36",
        "git_tag": "v0.40.0",  # tag commit d9a973f6 differs from the bump commit
        "wheel_sha256": "sha256:46ee5a36bd6275e01025e3e6abbffc658ad678b0017f76c896cdf9c93d658da0",
        "sdist_sha256": "sha256:5db2376db4904b9664134ac992c6926226f2139f67e30f4502a2150612664ee0",
        "released": "2026-07-03T00:00:00+00:00",
    },
]


def _release_envelope(version: str) -> RecourseEnvelope:
    """The accountability structure of a package release. Note the honest
    weakness recorded for pre-PEP-740 releases: the forum's trusted root is only
    the PyPI project page (a host-asserted root) — precisely the gap Trusted
    Publishing closes by anchoring to the public Rekor log."""
    return RecourseEnvelope(
        authority=Authority(
            principal="github:jkomkov",  # the surviving principal (maintainer)
            policy="policy://bulla/release",
            delegation=("pypi:project:bulla",),
        ),
        bounds=Bounds(scope=f"pypi:bulla version:{version}"),
        recourse=Recourse(
            challenge_window="P90D",
            forum=Forum(
                log_endpoint="https://pypi.org/project/bulla/",
                trusted_root_ref="pypi:project:bulla",  # weak (host-asserted) — TP upgrades this
            ),
            remedies=(
                Remedy(rung="recompute", verifier="pip download + sha256 vs PyPI", anchor=f"pypi:bulla=={version}"),
                Remedy(rung="revert", verifier="pypi yank", anchor=f"pypi:bulla=={version}"),
                Remedy(rung="escalate", verifier="maintainer review", anchor="github:jkomkov"),
            ),
        ),
        retention_class="authority-permanent",  # a release is a record of power — it persists
        disclosure_class="public",
    )


def main() -> int:
    out_dir = Path(__file__).resolve().parent
    for rel in RELEASES:
        receipt = build_release_receipt(
            package="bulla",
            version=rel["version"],
            git_commit=rel["git_commit"],
            git_tag=rel["git_tag"] or "",
            wheel_sha256=rel["wheel_sha256"],
            sdist_sha256=rel["sdist_sha256"],
            # no release-gate verdict was recorded at the time — say so, don't fake a ref
            diagnostic_ref={
                "status": "deferred",
                "note": "retroactive reconstruction — no release-gate WitnessReceipt was minted at release time",
            },
            envelope=_release_envelope(rel["version"]),
            # root_of_trust omitted: these predate PEP 740 attestation (the honest gap)
            timestamp=rel["released"],
            producer={
                "bulla_version": "0.41.0",
                "reconstructed": "2026-07-04",
                "note": "retroactive — not minted at release time; unsigned by construction",
            },
        )
        path = out_dir / f"{rel['version']}.json"
        path.write_text(receipt.to_json() + "\n", encoding="utf-8")
        print(f"wrote {path.name}  content={receipt.content_hash[:23]}…  (unsigned → verifies to digest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
