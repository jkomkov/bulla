#!/usr/bin/env python3
"""Repository-only verifier for bulla.inference-clearing/0.1-experimental."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "python-src" if (HERE / "python-src").is_dir() else HERE.parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from bulla.experimental.inference_clearing import (  # noqa: E402
    InferenceClearingContext,
    InferenceClearingError,
    InferenceClearingVerificationError,
    verify_inference_clearing_bundle,
)


def _strict_context(path: Path) -> InferenceClearingContext:
    raw = path.read_bytes()
    if len(raw) > 262_144:
        raise InferenceClearingError("verification context exceeds 262144 bytes")
    seen_error: list[str] = []

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                seen_error.append(f"duplicate JSON member {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {value!r}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise InferenceClearingError(f"invalid verification context: {exc}") from exc
    if seen_error:
        raise InferenceClearingError(seen_error[0])
    try:
        return InferenceClearingContext.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise InferenceClearingError(f"invalid verification context: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--context", required=True, type=Path)
    args = parser.parse_args()
    try:
        context = _strict_context(args.context)
        report = verify_inference_clearing_bundle(args.bundle, context)
    except InferenceClearingVerificationError as exc:
        print(json.dumps({"error": str(exc), "exit_code": 1}, sort_keys=True))
        return 1
    except (InferenceClearingError, OSError) as exc:
        print(json.dumps({"error": str(exc), "exit_code": 2}, sort_keys=True))
        return 2
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
