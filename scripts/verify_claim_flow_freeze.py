#!/usr/bin/env python3
"""Zero-import verifier for the Claim Flow v0.4 freeze record."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys


BULLA = Path(__file__).resolve().parents[1]
ROOT = BULLA.parent
TOP_LEVEL_KEYS = {"content", "content_hash"}
CONTENT_KEYS = {
    "schema_version",
    "profile",
    "classification",
    "merge_pr",
    "frozen_main_commit",
    "source_head",
    "artifacts",
    "ci_observations",
    "external_counts",
    "known_external_gaps",
}
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT_ID = re.compile(r"[0-9a-f]{40}")


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fail(message: str) -> None:
    raise ValueError(message)


def verify(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != TOP_LEVEL_KEYS:
        fail("freeze record must contain exactly content and content_hash")
    content = document["content"]
    if not isinstance(content, dict) or set(content) != CONTENT_KEYS:
        fail("freeze content has unknown or missing fields")
    if content["schema_version"] != "0.4-claim-flow-freeze":
        fail("unexpected schema_version")
    if content["profile"] != "bulla.claim-flow/0.4-freeze":
        fail("unexpected profile")
    if content["classification"] != "INTERNAL_CAPTIVE":
        fail("classification must remain INTERNAL_CAPTIVE")
    if content["merge_pr"] != 175:
        fail("unexpected merge PR")
    for key in ("frozen_main_commit", "source_head"):
        if not isinstance(content[key], str) or COMMIT_ID.fullmatch(content[key]) is None:
            fail(f"{key} must be a lowercase full commit id")

    expected_hash = "sha256:" + digest_bytes(canonical_bytes(content))
    if document["content_hash"] != expected_hash:
        fail("content hash mismatch")

    artifacts = content["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        fail("artifacts must be a non-empty list")
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            fail("artifact entries require exactly path and sha256")
        relative = artifact["path"]
        expected = artifact["sha256"]
        if not isinstance(relative, str) or relative in seen:
            fail("artifact paths must be unique strings")
        if not isinstance(expected, str) or SHA256.fullmatch(expected) is None:
            fail(f"invalid artifact digest for {relative}")
        candidate = (ROOT / relative).resolve()
        try:
            candidate.relative_to(ROOT.resolve())
        except ValueError:
            fail(f"artifact escapes repository root: {relative}")
        if not candidate.is_file():
            fail(f"artifact missing: {relative}")
        actual = digest_bytes(candidate.read_bytes())
        if actual != expected:
            fail(f"artifact hash mismatch: {relative}")
        seen.add(relative)

    observations = content["ci_observations"]
    if not isinstance(observations, list) or {item.get("name") for item in observations if isinstance(item, dict)} != {"checks", "golden-suite"}:
        fail("CI observations must bind checks and golden-suite")
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {"name", "run_id", "head_sha", "conclusion", "url"}:
            fail("invalid CI observation shape")
        if observation["conclusion"] != "success" or observation["head_sha"] != content["source_head"]:
            fail("CI observation does not bind a successful source-head run")

    counts = content["external_counts"]
    if counts != {"authors": 0, "adjudicators": 0, "implementations": 0, "witnesses": 0}:
        fail("external counts must remain the observed zero-count vector")
    gaps = content["known_external_gaps"]
    if not isinstance(gaps, list) or not gaps or not all(isinstance(item, str) and item for item in gaps):
        fail("known_external_gaps must be a non-empty string list")

    return {
        "ok": True,
        "profile": content["profile"],
        "classification": content["classification"],
        "frozen_main_commit": content["frozen_main_commit"],
        "artifacts_verified": len(artifacts),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_claim_flow_freeze.py FREEZE.json", file=sys.stderr)
        return 2
    try:
        result = verify(Path(sys.argv[1]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
