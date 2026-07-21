#!/usr/bin/env python3
"""Recover the exact immutable wheel and sdist already accepted by PyPI."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from verify_pypi_release import fetch_pypi_project


def _download(url: str, target: Path) -> str:
    digest = hashlib.sha256()
    request = Request(url, headers={"User-Agent": "bulla-release-finalizer/0.44"})
    with urlopen(request, timeout=60) as response, target.open("wb") as handle:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            digest.update(chunk)
            handle.write(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()

    payload = fetch_pypi_project()
    files = list(payload.get("releases", {}).get(args.version, []))
    wheels = [item for item in files if item.get("packagetype") == "bdist_wheel"]
    sdists = [item for item in files if item.get("packagetype") == "sdist"]
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            f"expected exactly one wheel and one sdist for {args.version}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    args.dist.mkdir(parents=True, exist_ok=True)
    for item in (*wheels, *sdists):
        filename = str(item["filename"])
        if Path(filename).name != filename or urlparse(str(item["url"])).scheme != "https":
            raise SystemExit(f"unsafe PyPI artifact reference: {filename!r}")
        target = args.dist / filename
        observed = _download(str(item["url"]), target)
        expected = str(item["digests"]["sha256"])
        if observed != expected:
            target.unlink(missing_ok=True)
            raise SystemExit(
                f"digest mismatch for {filename}: expected {expected}, observed {observed}"
            )
        print(f"verified {filename} sha256:{observed}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
