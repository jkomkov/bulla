#!/usr/bin/env python3
"""Build deterministic external-role packet templates without oracle material."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[2]
ROOT = HERE.parents[3]
FIXED_TIME = (2026, 7, 18, 0, 0, 0)


def zip_files(output: Path, files: list[tuple[str, Path]]) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(files):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=HERE / "packets")
    args = parser.parse_args()
    common = [
        ("SPEC.md", HERE / "SPEC.md"),
        ("README.md", HERE / "README.md"),
        ("preregistration.json", HERE / "preregistration.json"),
    ]
    curator = common + [
        ("CURATOR-INSTRUCTIONS.md", HERE / "external/CURATOR-INSTRUCTIONS.md"),
        ("custody-template.json", HERE / "external/custody-template.json"),
        ("custody-transcript-template.json", HERE / "external/custody-transcript-template.json"),
        ("sops.yaml.example", HERE / "external/.sops.yaml.example"),
    ]
    cleanroom = common + [
        ("CLEANROOM-SPEC.md", HERE / "external/CLEANROOM-SPEC.md"),
        ("submission-schema.json", HERE / "external/submission-schema.json"),
        ("challenge-schema.json", HERE / "external/challenge-schema.json"),
        ("EXTERNAL-STATUS.json", HERE / "external-status.json"),
        ("FRSL-1-SPEC.md", ROOT / "papers/interpolant-envelope/FRSL-1-SPEC.md"),
        ("SEMANTIC-FINALITY-SPEC.md", BULLA / "spec/semantic-finality-v0.1-experimental.md"),
    ]
    adjudication = common + [
        ("ADJUDICATOR-INSTRUCTIONS.md", HERE / "external/ADJUDICATOR-INSTRUCTIONS.md"),
        ("adjudication-template.json", HERE / "external/adjudication-template.json"),
        ("provenance-cards.json", HERE / "provenance-cards.json"),
    ]
    hashes = {
        "curator": zip_files(args.output_dir / "golden-v02-curator-template.zip", curator),
        "cleanroom": zip_files(args.output_dir / "golden-v02-cleanroom-template.zip", cleanroom),
        "adjudication": zip_files(args.output_dir / "golden-v02-adjudication-template.zip", adjudication),
    }
    manifest = {
        "profile": "bulla.golden-suite/0.2-experimental",
        "status": "TEMPLATES_ONLY_EXTERNAL_ACTIONS_BLOCKED",
        "packet_hashes": hashes,
        "oracle_material_included": False,
        "reviewer_originated_cases_included": False,
    }
    (args.output_dir / "packet-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
