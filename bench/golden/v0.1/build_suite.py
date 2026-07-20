#!/usr/bin/env python3
"""Build the Golden Suite case corpus, commitments, and packet projections."""

from __future__ import annotations

import argparse
import ast
import json
import os
import secrets
import subprocess
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bulla.experimental.frsl import canonical_hash
from bulla.experimental.golden import (
    BASELINE_ENGINE_COMMIT,
    BASELINE_PILOT_COMMIT,
    GoldenCase,
    GoldenSuiteManifest,
    OracleClass,
    OracleCommitment,
    SourceCapture,
    merkle_root,
    sha256_bytes,
)


HERE = Path(__file__).resolve().parent
BULLA = HERE.parents[2]
REPO = BULLA.parent
REGISTRY = BULLA / "calibration/data/registry"
API_REGISTRY = BULLA / "calibration/data/api-registry"
PRIVATE = HERE / "private"
PACKETS = HERE / "packets"

F1_ATTACKS = (
    "canonical_reordering",
    "numeric_type_confusion",
    "unknown_field",
    "oracle_nonce_swap",
    "witness_correlation",
    "closure_epoch_change",
    "reserve_shortfall",
    "conflict_nonmutation",
    "same_epoch_widening",
    "semantic_nonuniqueness",
)

FOUND_FAMILIES = {
    "units": {"amount_unit", "unit_of_measure"},
    "bounded_time": {"date_format", "temporal_format"},
    "interval_boundaries": {"timezone", "id_offset"},
    "enums": {"state_filter", "sort_direction"},
    "null_absent": {"null_handling"},
    "namespaces": {"path_convention", "owner_convention"},
    "integer_rounding": {"precision", "rate_scale", "currency_code"},
    "delivery_acceptance": {"gs1_application_identifier", "gs1_id_key_type"},
    "evidence_floors": {"score_range", "industry_code"},
    "revocation_windows": {"temporal_format", "date_format"},
    "authority_scopes": {"owner_convention", "path_convention"},
    "intentionally_non_definable": {"encoding", "media_type"},
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def current_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def candidate_revision() -> str:
    paths = (
        BULLA / "src/bulla/experimental/golden.py",
        BULLA / "src/bulla/experimental/invention.py",
        BULLA / "src/bulla/experimental/observability.py",
        BULLA / "src/bulla/experimental/constitutional.py",
        BULLA / "src/bulla/experimental/semantic_finality.py",
    )
    tree_hash = canonical_hash(
        {str(path.relative_to(BULLA)): sha256_bytes(path.read_bytes()) for path in paths}
    )
    return f"{current_commit()}+working-tree@{tree_hash}"


def partition(index: int, total: int) -> str:
    return "holdout" if index >= total - max(1, total // 5) else "design"


def case_record(
    *,
    family: str,
    index: int,
    total: int,
    oracle_class: OracleClass,
    payload: dict[str, Any],
    falsification: str,
    margins: tuple[str, ...],
    provenance: dict[str, Any],
    bounds: dict[str, int] | None = None,
) -> dict[str, Any]:
    input_hash = canonical_hash(payload)
    case = GoldenCase(
        case_id=f"{family}-{index:04d}",
        family=family,
        oracle_class=oracle_class,
        input_hashes=(input_hash,),
        falsification_rule=falsification,
        margin_coordinates=margins,
        resource_bounds=bounds or {"time_ms": 10_000, "memory_mib": 1024},
        provenance=provenance,
        partition=partition(index, total),
    )
    return {"case": case.to_dict(), "case_hash": case.case_hash, "input": payload}


def external_sources() -> list[tuple[str, str, str]]:
    source = (BULLA / "scripts/standards-ingest/build_phase7_index.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "EXTERNAL_SOURCES":
            return [tuple(item) for item in ast.literal_eval(node.value)]
    raise RuntimeError("could not locate the curated EXTERNAL_SOURCES allowlist")


def upstream_owner(url: str) -> str:
    parsed = urlparse(url)
    parts = [item for item in parsed.path.split("/") if item]
    if parsed.netloc == "raw.githubusercontent.com" and parts:
        if parts[0] == "APIs-guru" and "APIs" in parts:
            index = parts.index("APIs")
            return f"api-provider:{parts[index + 1]}" if index + 1 < len(parts) else "APIs-guru"
        return f"github-org:{parts[0]}"
    if parsed.netloc.endswith("hl7.org"):
        return "HL7"
    if parsed.netloc == "api.weather.gov":
        return "NOAA"
    return f"host:{parsed.netloc}"


def source_inventory() -> list[dict[str, Any]]:
    index = json.loads((REGISTRY / "index.json").read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for source_id, entry in sorted(index.items()):
        capture = SourceCapture(
            source_id=source_id,
            upstream_owner=f"unverified-package-owner:{entry.get('package') or source_id}",
            source_url=str(entry.get("captured_via") or "unknown:indirect-capture"),
            raw_content_hash="sha256:" + entry["content_hash"],
            captured_at=entry["capture_date"],
            retrieval_method="indirect-seed-capture",
            redistribution_status="redistributable",
            parser_version="bulla.api_registry/0.1",
            direct_capture=False,
            activity_proxy=(
                {"basis": "captured-popularity-rank", "value": entry["popularity_rank"]}
                if entry.get("popularity_rank")
                else None
            ),
        )
        records.append(capture.to_dict())
    cache_manifest = json.loads(
        (API_REGISTRY / "_cache/manifest.json").read_text(encoding="utf-8")
    )
    by_url = {url: (kind, source_id) for kind, source_id, url in external_sources()}
    for url, entry in sorted(cache_manifest.get("entries", {}).items()):
        kind, source_id = by_url.get(url, ("unknown", f"unmapped-{len(records)}"))
        capture = SourceCapture(
            source_id=f"{kind}:{source_id}",
            upstream_owner=upstream_owner(url),
            source_url=url,
            raw_content_hash="sha256:" + entry["sha256"],
            captured_at=entry["fetched_at"],
            retrieval_method="direct-schema-http-cached",
            redistribution_status="hash-only",
            parser_version="bulla.external_fetcher/0.1",
            direct_capture=True,
        )
        records.append(capture.to_dict())
    return records


def found_fields() -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(API_REGISTRY.glob("*/*.json")):
        if path.parent.name == "_cache":
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source_id = document.get("source_id")
        if not source_id:
            continue
        for tool in document.get("tools", []):
            for field in tool.get("fields", []):
                dims = set(field.get("dimensions", []))
                for family, accepted in FOUND_FAMILIES.items():
                    if dims & accepted:
                        grouped[family].append(
                            {
                                "source_id": source_id,
                                "source_kind": document.get("source_kind"),
                                "schema_hash": "sha256:" + document["schema_hash"],
                                "tool": tool.get("tool_name"),
                                "field": field.get("field_name"),
                                "dimensions": sorted(dims & accepted),
                            }
                        )
    return grouped


def build_cases() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index in range(256):
        attack = F1_ATTACKS[index % len(F1_ATTACKS)]
        records.append(
            case_record(
                family="F1",
                index=index,
                total=256,
                oracle_class=OracleClass.MACHINE,
                payload={"attack": attack, "seed": index, "expected": "fail-closed"},
                falsification="any unsafe acceptance blocks packet freeze",
                margins=(
                    "overlap_violations",
                    "protected_consequence_changes",
                    "same_reduct_disagreements",
                    "refusal_cells_retracted",
                    "authority_requirements_missing",
                    "reserve_shortfall",
                    "resource_slack",
                ),
                provenance={"kind": "synthetic-adversarial", "generator": "golden/F1-v0.1"},
            )
        )

    fields = found_fields()
    found_index = 0
    for family, entries in sorted(fields.items()):
        if len(entries) < 2:
            raise RuntimeError(f"found-data family {family} has fewer than two captured fields")
        for local_index in range(10):
            left = entries[local_index % len(entries)]
            right_offset = 1
            while right_offset < len(entries) and entries[(local_index + right_offset) % len(entries)]["source_id"] == left["source_id"]:
                right_offset += 1
            right = entries[(local_index + right_offset) % len(entries)]
            payload = {
                "semantic_family": family,
                "producer": left,
                "consumer": right,
                "compatibility_basis": sorted(set(left["dimensions"]) & set(right["dimensions"])),
                "claim_boundary": "candidate-edge-not-proven-composition",
            }
            records.append(
                case_record(
                    family="F2",
                    index=found_index,
                    total=120,
                    oracle_class=OracleClass.ADJUDICATION,
                    payload=payload,
                    falsification="internal labels may not masquerade as machine truth",
                    margins=("grammar_limit", "compute_frontier", "escalation_mass"),
                    provenance={"kind": "found-data", "capture": "api-registry"},
                )
            )
            found_index += 1
    if found_index != 120:
        raise RuntimeError(f"found-data suite cardinality drift: {found_index}")

    for family, count, oracle, modes in (
        ("F3", 48, OracleClass.PROPERTY, ("delete", "insert", "type", "reorder", "truncate", "unknown", "signature", "nonce")),
        ("F4", 64, OracleClass.PROPERTY, ("economic-schedule",)),
        ("F5", 24, OracleClass.PROPERTY, ("null-boundary", "opaque-drift", "regenerated-drift")),
        ("F6", 64, OracleClass.PROPERTY, ("planted-secret", "countermodel-minimization")),
        ("F7", 64, OracleClass.MACHINE, ("normative-choice", "forged-selection", "authored-order", "route-select-apply")),
        ("F8", 64, OracleClass.PROPERTY, ("refinement", "revision", "inside-neighborhood", "outside-neighborhood")),
    ):
        for index in range(count):
            mode = modes[index % len(modes)]
            payload: dict[str, Any] = {"mode": mode, "seed": index}
            if family == "F6":
                payload.update(
                    {
                        "planted_secret_hash": canonical_hash({"secret": f"synthetic-{index}"}),
                        "public_artifact": {"case": index, "secret": "REDACTED"},
                    }
                )
            records.append(
                case_record(
                    family=family,
                    index=index,
                    total=count,
                    oracle_class=oracle,
                    payload=payload,
                    falsification={
                        "F3": "malformed structures must fail closed and canonical bytes must agree",
                        "F4": "any reserve or finality invariant violation blocks freeze",
                        "F5": "null crossing bound or undeclared carrier claim falsifies result",
                        "F6": "recoverable planted secret outside budget blocks freeze",
                        "F7": "normative choice without authority blocks freeze",
                        "F8": "same-epoch widening or un-staled closure change blocks freeze",
                    }[family],
                    margins={
                        "F3": ("structural_mutations_to_accept",),
                        "F4": ("reserve_shortfall", "logical_steps_to_finality"),
                        "F5": ("threshold_distance_ppm", "detection_delay"),
                        "F6": ("recoverable_secret_bits", "countermodel_size"),
                        "F7": ("authority_requirements_missing",),
                        "F8": ("envelope_width", "reserve_shortfall", "epoch_distance"),
                    }[family],
                    provenance={"kind": "synthetic-property", "generator": f"golden/{family}-v0.1"},
                )
            )
    return records


def oracle_for(record: dict[str, Any]) -> dict[str, Any]:
    case = record["case"]
    if case["oracle_class"] == OracleClass.ADJUDICATION.value:
        return {
            "status": "REFERENCE_WITHHELD",
            "claim_boundary": "not-machine-truth",
            "expected_protocol_exit": "ROUTE/ADJUDICATION_REQUIRED",
        }
    return {
        "status": "PASS_REQUIRED",
        "family": case["family"],
        "falsification_rule": case["falsification_rule"],
    }


def private_oracles(cases: list[dict[str, Any]]) -> dict[str, Any]:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    path = PRIVATE / "oracle-custody.unencrypted.json"
    existing: dict[str, Any] = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    entries = dict(existing.get("entries", {}))
    for record in cases:
        case_id = record["case"]["case_id"]
        if case_id not in entries:
            entries[case_id] = {
                "oracle": oracle_for(record),
                "nonce": secrets.token_hex(32),
            }
    payload = {
        "warning": "UNENCRYPTED GITIGNORED MATERIAL; REVIEWER CUSTODY PENDING",
        "entries": dict(sorted(entries.items())),
    }
    write_json(path, payload)
    os.chmod(path, 0o600)
    return payload


def packet(path: Path, files: list[tuple[Path, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arcname in sorted(files, key=lambda item: item[1]):
            info = zipfile.ZipInfo(arcname, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())


def build() -> dict[str, Any]:
    cases = build_cases()
    inventory = source_inventory()
    private = private_oracles(cases)
    commitments: list[OracleCommitment] = []
    design_oracles: dict[str, Any] = {}
    for record in cases:
        case = record["case"]
        case_id = case["case_id"]
        secret = private["entries"][case_id]
        commitment = OracleCommitment.create(case_id, secret["oracle"], secret["nonce"])
        commitments.append(commitment)
        if case["partition"] == "design":
            design_oracles[case_id] = secret["oracle"]

    family_hashes: dict[str, str] = {}
    by_family: dict[str, list[str]] = defaultdict(list)
    for record in cases:
        by_family[record["case"]["family"]].append(record["case_hash"])
    for family, hashes in sorted(by_family.items()):
        family_hashes[family] = canonical_hash(sorted(hashes))

    case_leaves = [OracleCommitment(item["case"]["case_id"], item["case_hash"], item["case_hash"]) for item in cases]
    source_hash = canonical_hash(inventory)
    verifier_path = BULLA / "scripts/verify_golden.py"
    verifier_hash = sha256_bytes(verifier_path.read_bytes()) if verifier_path.exists() else canonical_hash({"status": "pending"})
    implementation_paths = {
        "golden_module": BULLA / "src/bulla/experimental/golden.py",
        "invention_kernel": BULLA / "src/bulla/experimental/invention.py",
        "observability_kernel": BULLA / "src/bulla/experimental/observability.py",
        "constitutional_kernel": BULLA / "src/bulla/experimental/constitutional.py",
        "finality_kernel": BULLA / "src/bulla/experimental/semantic_finality.py",
        "golden_lean": REPO / "papers/interpolant-envelope/lean/InterpolantEnvelope/Golden.lean",
        "suite_builder": HERE / "build_suite.py",
        "suite_runner": HERE / "run_suite.py",
        "anytime_runner": HERE / "run_anytime_conversion.py",
        "smt_smoke": BULLA / "scripts/golden_smt_smoke.py",
    }
    implementation_hashes = {
        name: sha256_bytes(path.read_bytes()) for name, path in implementation_paths.items()
    }
    matrix = tuple(
        {"os": os_name, "python": py, "backend": backend}
        for os_name in ("ubuntu", "macos", "windows")
        for py, backend in (("3.10", "reference"), ("3.12", "reference"), ("3.13", "reference"), ("3.12", "smtinterpol"))
    )
    manifest = GoldenSuiteManifest(
        suite_version="0.1",
        baseline_engine_commit=BASELINE_ENGINE_COMMIT,
        baseline_pilot_commit=BASELINE_PILOT_COMMIT,
        candidate_commit=candidate_revision(),
        family_manifests=family_hashes,
        source_inventory_hash=source_hash,
        case_merkle_root=merkle_root(case_leaves),
        oracle_commitment_root=merkle_root(commitments),
        verifier_hashes={**implementation_hashes, "zero_import": verifier_hash},
        environment_matrix=matrix,
    )

    write_json(HERE / "cases.json", {"profile": manifest.profile, "cases": cases})
    write_json(HERE / "source-inventory.json", {"sources": inventory, "inventory_hash": source_hash})
    write_json(
        HERE / "oracle-commitments.json",
        {
            "commitment_root": manifest.oracle_commitment_root,
            "commitments": [item.to_dict() for item in sorted(commitments, key=lambda item: item.case_id)],
        },
    )
    write_json(HERE / "open-oracles.json", {"oracles": design_oracles})
    write_json(HERE / "manifest.json", {**manifest.to_dict(), "manifest_hash": manifest.manifest_hash})
    write_json(
        HERE / "custody-status.json",
        {
            "status": "PENDING_REVIEWER_ENCRYPTION",
            "blind_label_permitted": False,
            "private_material_committed": False,
            "external_replay_status": "blocked-by-sprint-scope",
        },
    )
    write_json(
        HERE / "found-data-report.json",
        {
            "seed_sources": sum(not item["direct_capture"] for item in inventory),
            "direct_sources": sum(item["direct_capture"] for item in inventory),
            "direct_owners": len({item["upstream_owner"] for item in inventory if item["direct_capture"]}),
            "direct_redistributable_sources": sum(
                item["direct_capture"] and item["redistribution_status"] == "redistributable"
                for item in inventory
            ),
            "label": "indirect-seed-corpus",
            "found_cases": len(by_family["F2"]),
            "direct_capture_disposition": "hash-only-scout; redistribution-not-established",
            "claim_boundary": "candidate-edges-not-proven-compositions; no-traffic-claim",
        },
    )
    write_json(
        HERE / "source-allowlist.generated.json",
        {
            "schema_version": "0.1",
            "sources": [
                {
                    "source_id": source_id,
                    "upstream_owner": upstream_owner(url),
                    "url": url,
                    "parser_hint": source_kind,
                    "redistribution_status": "hash-only",
                }
                for source_kind, source_id, url in external_sources()
            ],
        },
    )

    common = [
        (HERE / "README.md", "README.md"),
        (HERE / "SPEC.md", "SPEC.md"),
        (HERE / "manifest.json", "manifest.json"),
        (HERE / "cases.json", "cases.json"),
        (HERE / "source-inventory.json", "source-inventory.json"),
        (HERE / "oracle-commitments.json", "oracle-commitments.json"),
        (HERE / "custody-status.json", "custody-status.json"),
        (HERE / "found-data-report.json", "found-data-report.json"),
        (HERE / "source-allowlist.generated.json", "source-allowlist.generated.json"),
    ]
    if (HERE / "anytime-conversion.json").exists():
        common.append((HERE / "anytime-conversion.json", "anytime-conversion.json"))
    packet(PACKETS / "golden-open.zip", common + [(HERE / "open-oracles.json", "open-oracles.json")])
    packet(PACKETS / "golden-blind-candidate.zip", common)
    cleanroom = common + [(verifier_path, "verify_golden.py")]
    packet(PACKETS / "golden-cleanroom.zip", cleanroom)
    summary = {
        "manifest_hash": manifest.manifest_hash,
        "case_count": len(cases),
        "family_counts": {family: len(hashes) for family, hashes in sorted(by_family.items())},
        "design_oracles": len(design_oracles),
        "holdout_oracles_committed": len(cases) - len(design_oracles),
        "source_count": len(inventory),
        "blind_label_permitted": False,
    }
    write_json(HERE / "build-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    print(json.dumps(build(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
