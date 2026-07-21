#!/usr/bin/env python3
"""Build the deterministic routed-inference v0.1-draft reproduction bundle."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


HERE = Path(__file__).resolve().parent
ROOT_NAME = "routed-inference-profile-v0.1-draft"
OUT = HERE / "dist" / f"{ROOT_NAME}.zip"
GLYPH_OUT = HERE.parents[1] / "glyph" / "public" / "downloads" / OUT.name
FIXTURES = HERE / "routed-inference-vectors"


def _inputs() -> list[tuple[str, Path]]:
    fixed = [
        ("PROFILE.md", HERE / "routed-inference-profile-v0.1-draft.md"),
        ("STATUS.json", HERE / "routed-inference-profile-status.json"),
        ("REQUIREMENT-EVIDENCE.md", HERE / "routed-inference-requirement-evidence.md"),
        ("IMPLEMENTER.md", HERE / "routed-inference-IMPLEMENTER.md"),
        (
            "CONFORMANCE-REPORT-TEMPLATE.json",
            HERE / "routed-inference-conformance-report-template.json",
        ),
        ("check.py", FIXTURES / "check.py"),
        ("expected.json", FIXTURES / "expected.json"),
        ("violation-taxonomy.json", FIXTURES / "violation-taxonomy.json"),
        ("size-report.json", FIXTURES / "size-report.json"),
    ]
    traces = [(path.name, path) for path in sorted(FIXTURES.glob("[0-9][0-9]-*.json"))]
    return fixed + traces


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(f"{ROOT_NAME}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    info.create_system = 3
    return info


def _build() -> bytes:
    files = _inputs()
    missing = [str(path) for _, path in files if not path.is_file()]
    if missing:
        raise SystemExit("missing bundle inputs: " + ", ".join(missing))
    if len([name for name, _ in files if name[:2].isdigit()]) != 14:
        raise SystemExit("routed bundle must contain exactly fourteen traces")

    payloads = [(name, path.read_bytes()) for name, path in files]
    manifest = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(payloads)
    ).encode("utf-8")
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, data in sorted(payloads):
            archive.writestr(_zip_info(name), data)
        archive.writestr(_zip_info("MANIFEST.sha256"), manifest)
    return stream.getvalue()


def main() -> int:
    payload = _build()
    if "--check" in sys.argv[1:]:
        if not GLYPH_OUT.is_file() or GLYPH_OUT.read_bytes() != payload:
            print(f"routed profile bundle drift: rebuild {GLYPH_OUT}", file=sys.stderr)
            return 1
        if OUT.is_file() and OUT.read_bytes() != payload:
            print(f"routed profile bundle drift: rebuild {OUT}", file=sys.stderr)
            return 1
        checked_bundle = OUT if OUT.is_file() else GLYPH_OUT
        print(json.dumps({
            "bundle": str(checked_bundle),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "deterministic": True,
        }, sort_keys=True))
        return 0

    for path in (OUT, GLYPH_OUT):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    print(json.dumps({
        "bundle": str(OUT),
        "glyph_bundle": str(GLYPH_OUT),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "deterministic": True,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
