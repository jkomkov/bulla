#!/usr/bin/env python3
"""Zero-import structural verifier for Golden Gate v0.2 reports."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROFILE = "bulla.golden-suite/0.2-experimental"


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


def read(root: Path, name: str) -> Any:
    try:
        return json.loads((root / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"cannot read {name}: {exc}") from exc


def verify(root: Path) -> dict[str, Any]:
    summary = read(root, "internal-summary.json")
    if summary.get("profile") != PROFILE:
        raise VerificationError("profile mismatch")
    if "internally verified/captive" not in summary.get("classification", ""):
        raise VerificationError("internal evidence label is absent")
    if "external replay blocked" not in summary.get("classification", ""):
        raise VerificationError("external blocker is absent")
    for value in summary.get("v01_frozen_hashes", {}).values():
        require_digest(value, "v0.1 frozen hash")

    meta = read(root, "metamorphic-report.json")
    if (meta.get("base_count"), meta.get("relation_count"), meta.get("paired_test_count")) != (96, 14, 1344):
        raise VerificationError("metamorphic denominators changed")
    if meta.get("unexpected_failure_count") != 0 or len(meta.get("observations", [])) != 1344:
        raise VerificationError("metamorphic failure or missing observation")
    for item in meta["observations"]:
        require_digest(item.get("base_input_hash"), "metamorphic base hash")
        require_digest(item.get("transformed_input_hash"), "metamorphic transformed hash")
        if item.get("passed") is not True:
            raise VerificationError("unexpected metamorphic relation failure")

    economic = read(root, "economic-model-check.json")
    coverage = economic.get("coverage", {})
    if coverage.get("transition_count") != coverage.get("accepted_transition_count", 0) + coverage.get("rejected_transition_count", 0):
        raise VerificationError("economic transition denominator mismatch")
    if coverage.get("invariant_violations"):
        raise VerificationError("economic invariant violation")
    required_terminal = {"FINALIZED", "ROUTED", "STALE", "EXPIRED"}
    if not required_terminal.issubset(set(coverage.get("terminal_phases", []))):
        raise VerificationError("economic terminal-state coverage is incomplete")
    if coverage.get("fairness_model", {}).get("authorized_completion_reaches_finalized") is not True:
        raise VerificationError("bounded fair completion witness failed")
    two = economic.get("two_commitment", {})
    if two.get("violations"):
        raise VerificationError("two-commitment invariant violation")

    mutation = read(root, "mutation-campaign.json")
    expected = {"structural": 40, "cryptographic": 40, "semantic": 48, "lifecycle": 40, "witness": 32, "economic": 48}
    if mutation.get("family_counts") != expected or mutation.get("total") != 248:
        raise VerificationError("mutation denominator mismatch")
    if mutation.get("critical_total") != mutation.get("critical_killed") or mutation.get("mutation_score", 0) < 0.95:
        raise VerificationError("mutation gate failed")
    if len(mutation.get("witnesses", [])) != 248:
        raise VerificationError("mutation witnesses are incomplete")
    for item in mutation["witnesses"]:
        require_digest(item.get("non_equivalence_witness"), "mutant witness")

    provenance = read(root, "provenance-cards.json")
    if provenance.get("card_count") != 120 or len(provenance.get("cards", [])) != 120:
        raise VerificationError("provenance-card denominator mismatch")
    for card in provenance["cards"]:
        require_digest(card.get("card_hash"), "provenance card hash")
        for value in card.get("content_hashes", []):
            require_digest(value, "provenance content hash")
        if card.get("adjudication_status") != "UNADJUDICATED":
            raise VerificationError("repository packet fabricates adjudication")
    found = read(root, "found-data-status.json")
    if found.get("label") != "seed-corpus" or found.get("direct_public_harvest_permitted") is not False:
        raise VerificationError("found-data status overclaims direct public harvest")
    if found.get("primary_ratings_received") != 0:
        raise VerificationError("found-data status fabricates ratings")

    external = read(root, "external-status.json")
    if external.get("status") != "BLOCKED_MISSING_EXTERNAL_PARTICIPANTS":
        raise VerificationError("external status overclaims completion")
    if external["reviewer_originated_hidden_cases"].get("received") != 0:
        raise VerificationError("repository packet fabricates hidden cases")
    if external["primary_adjudication_ratings"].get("received") != 0:
        raise VerificationError("repository packet fabricates ratings")
    if external["custody_reveal"].get("received") is not False:
        raise VerificationError("repository packet fabricates reveal")

    abstention = read(root, "abstention-scorecard.json")
    if abstention.get("external_complete") is not False or abstention.get("primary_rating_count") != 0:
        raise VerificationError("empty adjudication scorecard overclaims external completeness")

    dag = read(root, "provenance-dag-scout.json")
    if dag.get("disposition") != "KILLED" or dag.get("runtime_module_created") is not False:
        raise VerificationError("DAG scout did not honor its kill gate")

    scaling_path = root / "scaling-report.json"
    if scaling_path.exists():
        scaling = read(root, "scaling-report.json")
        if (scaling.get("case_count"), scaling.get("design_count"), scaling.get("holdout_count")) != (240, 192, 48):
            raise VerificationError("scaling denominator mismatch")
        if scaling.get("unsafe_count") != 0:
            raise VerificationError("unsafe scaling package")

    pathology_path = root / "pathology-regression.json"
    if pathology_path.exists():
        pathology = read(root, "pathology-regression.json")
        if pathology.get("case_count") != 12:
            raise VerificationError("pathology-regression denominator mismatch")
        if not all(pathology.get(key) is True for key in ("all_compiled", "all_under_64_nodes", "all_verified")):
            raise VerificationError("original AST-pathology gate failed")

    drift_path = root / "drift-stress.json"
    if drift_path.exists():
        drift = read(root, "drift-stress.json")
        if drift.get("matching_null_failures"):
            raise VerificationError("matched drift calibration failed")

    return {
        "ok": True,
        "profile": PROFILE,
        "metamorphic_pairs": 1344,
        "economic_states": coverage.get("abstract_state_count"),
        "economic_transitions": coverage.get("transition_count"),
        "mutants": 248,
        "provenance_cards": 120,
        "external_status": external["status"],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify_golden_v02.py <golden-v0.2-directory>", file=sys.stderr)
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
