#!/usr/bin/env python3
"""Zero-import verifier for a Golden Suite v0.1 packet directory."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROFILE = "bulla.golden-suite/0.1-experimental"
BASELINE_ENGINE = "30619618ed74c134aa94cbf7c6f5f8ef440df460"
BASELINE_PILOT = "cbaa41da"


class VerificationError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def require_digest(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise VerificationError(f"{where} is not sha256:<64 hex>")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise VerificationError(f"{where} is not sha256:<64 hex>") from exc
    return value


def exact(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise VerificationError(f"{where} has unknown or missing fields")
    return value


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read {path.name}: {exc}") from exc


def merkle(values: list[str]) -> str:
    level = sorted(require_digest(value, "merkle leaf") for value in values)
    if not level:
        return digest({"domain": "bulla.golden.empty-merkle/0.1"})
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            digest(
                {
                    "domain": "bulla.golden.merkle-node/0.1",
                    "left": level[index],
                    "right": level[index + 1],
                }
            )
            for index in range(0, len(level), 2)
        ]
    return level[0]


CASE_KEYS = {
    "case_id",
    "family",
    "oracle_class",
    "input_hashes",
    "falsification_rule",
    "margin_coordinates",
    "resource_bounds",
    "provenance",
    "partition",
}
MANIFEST_KEYS = {
    "profile",
    "suite_version",
    "baseline_engine_commit",
    "baseline_pilot_commit",
    "candidate_commit",
    "family_manifests",
    "source_inventory_hash",
    "case_merkle_root",
    "oracle_commitment_root",
    "verifier_hashes",
    "environment_matrix",
    "evidence_status",
    "external_replay_status",
    "manifest_hash",
}
SOURCE_KEYS = {
    "source_id",
    "upstream_owner",
    "source_url",
    "raw_content_hash",
    "captured_at",
    "retrieval_method",
    "redistribution_status",
    "parser_version",
    "direct_capture",
    "executed_code",
    "activity_proxy",
}


def verify(root: Path) -> dict[str, Any]:
    manifest = exact(read_json(root / "manifest.json"), MANIFEST_KEYS, "manifest")
    if manifest["profile"] != PROFILE or manifest["suite_version"] != "0.1":
        raise VerificationError("unsupported Golden Suite profile")
    if manifest["baseline_engine_commit"] != BASELINE_ENGINE or manifest["baseline_pilot_commit"] != BASELINE_PILOT:
        raise VerificationError("baseline lock mismatch")
    if manifest["evidence_status"] != "internally-verified/captive":
        raise VerificationError("manifest overclaims evidence status")
    if manifest["external_replay_status"] != "blocked-by-sprint-scope":
        raise VerificationError("manifest overclaims external replay")
    stored_manifest_hash = require_digest(manifest["manifest_hash"], "manifest_hash")
    if digest({key: value for key, value in manifest.items() if key != "manifest_hash"}) != stored_manifest_hash:
        raise VerificationError("manifest hash mismatch")

    corpus = exact(read_json(root / "cases.json"), {"profile", "cases"}, "case corpus")
    if corpus["profile"] != PROFILE or not isinstance(corpus["cases"], list):
        raise VerificationError("case corpus profile or list is invalid")
    case_hashes: list[str] = []
    family_hashes: dict[str, list[str]] = {}
    ids: set[str] = set()
    for index, record in enumerate(corpus["cases"]):
        item = exact(record, {"case", "case_hash", "input"}, f"cases[{index}]")
        case = exact(item["case"], CASE_KEYS, f"cases[{index}].case")
        if case["case_id"] in ids:
            raise VerificationError("duplicate case id")
        ids.add(case["case_id"])
        if case["oracle_class"] not in {"MACHINE", "PROPERTY", "ADJUDICATION"}:
            raise VerificationError("unknown oracle class")
        if case["partition"] not in {"design", "holdout"}:
            raise VerificationError("unknown case partition")
        case_hash = require_digest(item["case_hash"], "case_hash")
        if digest(case) != case_hash:
            raise VerificationError(f"case hash mismatch for {case['case_id']}")
        if case["input_hashes"] != [digest(item["input"])]:
            raise VerificationError(f"input hash mismatch for {case['case_id']}")
        case_hashes.append(case_hash)
        family_hashes.setdefault(case["family"], []).append(case_hash)
    if set(family_hashes) != {f"F{index}" for index in range(1, 9)}:
        raise VerificationError("family set is not exactly F1 through F8")
    expected_family = {family: digest(sorted(values)) for family, values in sorted(family_hashes.items())}
    if manifest["family_manifests"] != expected_family:
        raise VerificationError("family manifest mismatch")
    if merkle(case_hashes) != require_digest(manifest["case_merkle_root"], "case_merkle_root"):
        raise VerificationError("case Merkle root mismatch")

    source_doc = exact(read_json(root / "source-inventory.json"), {"sources", "inventory_hash"}, "source inventory")
    source_hash = require_digest(source_doc["inventory_hash"], "source inventory hash")
    if digest(source_doc["sources"]) != source_hash or source_hash != manifest["source_inventory_hash"]:
        raise VerificationError("source inventory hash mismatch")
    for index, source_value in enumerate(source_doc["sources"]):
        source = exact(source_value, SOURCE_KEYS, f"sources[{index}]")
        if source.get("executed_code") is not False:
            raise VerificationError("source inventory permits executed third-party code")
        require_digest(source["raw_content_hash"], "source raw content hash")

    commitment_doc = exact(
        read_json(root / "oracle-commitments.json"),
        {"commitment_root", "commitments"},
        "oracle commitments",
    )
    commitment_values: list[str] = []
    commitment_ids: set[str] = set()
    for item in commitment_doc["commitments"]:
        entry = exact(item, {"case_id", "commitment"}, "oracle commitment")
        if entry["case_id"] in commitment_ids or entry["case_id"] not in ids:
            raise VerificationError("duplicate or unknown oracle commitment case")
        commitment_ids.add(entry["case_id"])
        commitment_values.append(require_digest(entry["commitment"], "oracle commitment"))
    if commitment_ids != ids:
        raise VerificationError("oracle commitment coverage mismatch")
    commitment_root = merkle(commitment_values)
    if commitment_root != commitment_doc["commitment_root"] or commitment_root != manifest["oracle_commitment_root"]:
        raise VerificationError("oracle commitment root mismatch")

    custody = exact(
        read_json(root / "custody-status.json"),
        {"status", "blind_label_permitted", "private_material_committed", "external_replay_status"},
        "custody status",
    )
    if custody != {
        "status": "PENDING_REVIEWER_ENCRYPTION",
        "blind_label_permitted": False,
        "private_material_committed": False,
        "external_replay_status": "blocked-by-sprint-scope",
    }:
        raise VerificationError("custody status overclaims blind readiness")
    return {
        "ok": True,
        "profile": PROFILE,
        "manifest_hash": stored_manifest_hash,
        "case_count": len(ids),
        "family_counts": {family: len(values) for family, values in sorted(family_hashes.items())},
        "source_count": len(source_doc["sources"]),
        "evidence_status": "internally-verified/captive",
        "external_replay_status": "blocked-by-sprint-scope",
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify_golden.py <golden-suite-directory>", file=sys.stderr)
        return 2
    try:
        result = verify(Path(argv[1]).resolve())
    except VerificationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
