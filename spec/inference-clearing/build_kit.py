#!/usr/bin/env python3
"""Build the canonical inference-clearing reproduction kit."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
DIGEST = HERE / "inference-clearing-reproduction-kit.tar.sha256"
STATIC = (
    "PROFILE.md",
    "THREAT-MODEL.md",
    "REPRODUCE.md",
    "clearing-core.schema.json",
    "verification-context.schema.json",
    "hostile-cases.json",
    "expected-verdict.json",
    "site-projection.json",
    "check.py",
    "check.mjs",
    "compare_reports.py",
)
PYTHON_MODULES = (
    "_canonical.py",
    "action_receipt.py",
    "certificate.py",
    "coboundary.py",
    "delegation.py",
    "diagnostic.py",
    "envelope.py",
    "executable_form.py",
    "experimental/checkpoint.py",
    "experimental/inference_clearing.py",
    "identity.py",
    "model.py",
    "ots.py",
    "receipt_parser.py",
    "regime.py",
    "registry.py",
    "witness_geometry.py",
)


def _members() -> dict[str, bytes]:
    result = {name: (HERE / name).read_bytes() for name in STATIC}
    source = HERE.parents[1] / "src" / "bulla"
    result["python-src/bulla/__init__.py"] = b'"""Minimal inference-clearing verifier package."""\n'
    result["python-src/bulla/experimental/__init__.py"] = b'"""Source-only inference-clearing dependencies."""\n'
    for name in PYTHON_MODULES:
        result[f"python-src/bulla/{name}"] = (source / name).read_bytes()
    for directory in ("vectors", "contexts"):
        for path in sorted((HERE / directory).rglob("*")):
            if path.is_file():
                result[path.relative_to(HERE).as_posix()] = path.read_bytes()
    return result


def build() -> bytes:
    members = _members()
    manifest = {
        "profile": "bulla.inference-clearing-reproduction-kit/0.1-experimental",
        "members": [
            {
                "path": name,
                "byte_length": len(raw),
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
            for name, raw in sorted(members.items())
        ],
        "external_context_boundary": "contexts are supplied separately from each transaction bundle",
        "external_reproduction": "BLOCKED",
    }
    members["kit-manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(members.items()):
            info = tarfile.TarInfo(f"inference-clearing-kit/{name}")
            info.size = len(raw)
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    raw = build()
    digest = hashlib.sha256(raw).hexdigest()
    archive_name = "inference-clearing-reproduction-kit.tar"
    sidecar = f"{digest}  {archive_name}\n".encode()
    if args.check:
        if not DIGEST.exists() or DIGEST.read_bytes() != sidecar:
            raise SystemExit("inference-clearing reproduction kit drifted")
        print(f"inference-clearing reproduction kit matches sha256:{digest}")
    else:
        if args.out is None:
            raise SystemExit("--out is required unless --check is used")
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / archive_name).write_bytes(raw)
        DIGEST.write_bytes(sidecar)
        print(f"built inference-clearing reproduction kit sha256:{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
