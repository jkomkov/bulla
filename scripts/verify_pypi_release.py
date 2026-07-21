#!/usr/bin/env python3
"""Verify that PyPI accepted the exact local artifacts via the trusted publisher."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

from bulla.coverage import fetch_pypi_project, fetch_pypi_provenance


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _published_files(project_doc: dict, version: str) -> dict[str, dict]:
    return {
        item["filename"]: item
        for item in (project_doc.get("releases") or {}).get(version, [])
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="bulla")
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--repository", default="jkomkov/bulla")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval", type=int, default=6)
    parser.add_argument(
        "--structural-only",
        action="store_true",
        help="Check Integrity API structure without invoking pypi-attestations",
    )
    args = parser.parse_args()

    local_files = sorted(args.dist.glob(f"bulla-{args.version}*"))
    local_files = [path for path in local_files if path.suffix == ".whl" or path.name.endswith(".tar.gz")]
    if len(local_files) < 2:
        print("expected a wheel and sdist in dist/", file=sys.stderr)
        return 2

    published: dict[str, dict] = {}
    for attempt in range(1, args.attempts + 1):
        try:
            published = _published_files(fetch_pypi_project(args.project), args.version)
        except RuntimeError as exc:
            print(f"attempt {attempt}: {exc}", file=sys.stderr)
        if all(path.name in published for path in local_files):
            break
        if attempt < args.attempts:
            time.sleep(args.interval)
    else:
        print(f"PyPI did not expose every {args.project} {args.version} artifact", file=sys.stderr)
        return 1

    for path in local_files:
        record = published[path.name]
        expected = (record.get("digests") or {}).get("sha256")
        actual = _sha256(path)
        if actual != expected:
            print(f"digest mismatch for {path.name}: local={actual} pypi={expected}", file=sys.stderr)
            return 1
        provenance = fetch_pypi_provenance(args.project, args.version, path.name)
        bundles = provenance.get("attestation_bundles") or []
        publishers = [bundle.get("publisher") or {} for bundle in bundles]
        if not any(
            publisher.get("kind") == "GitHub"
            and publisher.get("repository") == args.repository
            and bundle.get("attestations")
            for publisher, bundle in zip(publishers, bundles)
        ):
            print(f"no Integrity API attestation from {args.repository} for {path.name}", file=sys.stderr)
            return 1
        if not args.structural_only:
            result = subprocess.run(
                [
                    "pypi-attestations",
                    "verify",
                    "pypi",
                    "--repository",
                    f"https://github.com/{args.repository}",
                    record["url"],
                ],
                check=False,
            )
            if result.returncode:
                return result.returncode
        print(f"verified {path.name}  sha256:{actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
