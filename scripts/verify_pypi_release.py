#!/usr/bin/env python3
"""Verify that PyPI accepted the exact local artifacts via the trusted publisher."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import subprocess
import sys
import tempfile
import time
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from pathlib import Path


_GITHUB_WORKFLOW_SHA_OID = "1.3.6.1.4.1.57264.1.3"
_PYPI_PROJECT_URL = "https://pypi.org/pypi/{project}/json"
_PYPI_INTEGRITY_URL = (
    "https://pypi.org/integrity/{project}/{version}/{filename}/provenance"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_url(url: str, *, accept: str = "application/json") -> dict:
    request = Request(
        url,
        headers={"Accept": accept, "User-Agent": "bulla-release-verifier/0.44"},
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed PyPI API
            value = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {url}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"PyPI returned a non-object document for {url}")
    return value


def fetch_pypi_project(project: str) -> dict:
    return _read_json_url(_PYPI_PROJECT_URL.format(project=quote(project)))


def fetch_pypi_provenance(project: str, version: str, filename: str) -> dict:
    return _read_json_url(
        _PYPI_INTEGRITY_URL.format(
            project=quote(project),
            version=quote(version),
            filename=quote(filename),
        ),
        accept="application/vnd.pypi.integrity.v1+json",
    )


def _published_files(project_doc: dict, version: str) -> dict[str, dict]:
    return {
        item["filename"]: item
        for item in (project_doc.get("releases") or {}).get(version, [])
    }


def _certificate_workflow_commit(certificate: str) -> str | None:
    try:
        certificate_bytes = base64.b64decode(certificate, validate=True)
    except (ValueError, TypeError):
        return None
    with tempfile.NamedTemporaryFile() as temporary:
        temporary.write(certificate_bytes)
        temporary.flush()
        result = subprocess.run(
            [
                "openssl",
                "x509",
                "-inform",
                "DER",
                "-in",
                temporary.name,
                "-text",
                "-noout",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode:
        return None
    match = re.search(
        rf"{re.escape(_GITHUB_WORKFLOW_SHA_OID)}:\s*\n\s*.*?([0-9a-f]{{40}})\s*$",
        result.stdout,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="bulla")
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--repository", default="jkomkov/bulla")
    parser.add_argument(
        "--expected-commit",
        help="Require the Fulcio GitHub workflow SHA to equal this immutable commit",
    )
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
        if args.expected_commit:
            if not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit):
                print("expected commit must be a lowercase 40-hex SHA", file=sys.stderr)
                return 2
            commits = {
                commit
                for bundle in bundles
                if (bundle.get("publisher") or {}).get("kind") == "GitHub"
                and (bundle.get("publisher") or {}).get("repository") == args.repository
                for attestation in (bundle.get("attestations") or [])
                if (
                    commit := _certificate_workflow_commit(
                        (attestation.get("verification_material") or {}).get(
                            "certificate"
                        )
                    )
                )
            }
            if commits != {args.expected_commit}:
                print(
                    f"provenance workflow commit mismatch for {path.name}: "
                    f"expected {args.expected_commit}, got {sorted(commits)}",
                    file=sys.stderr,
                )
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
