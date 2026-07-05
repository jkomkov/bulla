from __future__ import annotations

import json
from pathlib import Path

from bulla.compute.g27_corpus import build_corpus_with_metadata, evaluate_fast_fail_gate
from bulla.compute.scaling_sweep import run_scaling_sweep


def _write_manifest(path: Path, *, tool_name: str) -> None:
    payload = {
        "tools": [
            {
                "name": tool_name,
                "description": f"{tool_name} tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "number"},
                        "currency": {"type": "string"},
                        "account": {"type": "string"},
                    },
                    "required": ["amount", "currency", "account"],
                },
            }
        ]
    }
    path.write_text(json.dumps(payload))


def test_build_corpus_with_metadata_and_gate(tmp_path: Path) -> None:
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    _write_manifest(manifests / "alpha.json", tool_name="alpha_tool")
    _write_manifest(manifests / "beta.json", tool_name="beta_tool")

    rows, metadata = build_corpus_with_metadata(
        manifests,
        target_size=8,
        seeds_per_combo=2,
        min_high_r_count=30,
    )
    assert rows
    assert metadata.total_rows == len(rows)
    assert metadata.fast_fail_triggered is True
    gate = evaluate_fast_fail_gate(rows, min_high_r_count=30)
    assert gate["status"] == "deferred_underpowered"

    first = rows[0].to_dict()
    assert "schema_properties_hidden" in first
    assert "required_fields_hidden" in first
    assert first["schema_properties_before"] >= first["schema_properties_after"]


def test_build_corpus_is_deterministic_for_same_manifest_bundle(tmp_path: Path) -> None:
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    _write_manifest(manifests / "alpha.json", tool_name="alpha_tool")
    _write_manifest(manifests / "beta.json", tool_name="beta_tool")

    rows_a, meta_a = build_corpus_with_metadata(manifests, target_size=10, seeds_per_combo=3)
    rows_b, meta_b = build_corpus_with_metadata(manifests, target_size=10, seeds_per_combo=3)

    assert [r.to_dict() for r in rows_a] == [r.to_dict() for r in rows_b]
    assert meta_a.manifest_bundle_sha256 == meta_b.manifest_bundle_sha256
    assert meta_a.total_rows == meta_b.total_rows


def test_run_scaling_sweep_deferred_before_analysis(tmp_path: Path) -> None:
    rows = [
        {
            "composition_id": f"cid-{i}",
            "coherence_fee": 1,
            "n_tools": 4,
            "n_edges": 3,
            "servers": ["a", "b"],
            "perturbation_seed": f"s-{i}",
            "schema_properties_before": 12,
            "schema_properties_after": 10,
            "schema_properties_hidden": 2,
            "required_fields_before": 6,
            "required_fields_after": 5,
            "required_fields_hidden": 1,
        }
        for i in range(5)
    ]
    bundle = {"metadata": {"manifest_bundle_sha256": "x"}, "rows": rows}
    corpus_path = tmp_path / "bundle.json"
    csv_out = tmp_path / "out.csv"
    summary_out = tmp_path / "summary.json"
    corpus_path.write_text(json.dumps(bundle))

    summary = run_scaling_sweep(
        corpus_path,
        csv_out=csv_out,
        summary_out=summary_out,
        min_high_r_count=30,
    )
    assert summary.status == "deferred_underpowered"
    assert summary.n_rows == 5
    assert summary.high_r_count == 0
    assert csv_out.exists()
    assert summary_out.exists()


def test_run_scaling_sweep_operational_proxy_completed(tmp_path: Path) -> None:
    rows = []
    for i in range(36):
        rows.append(
            {
                "composition_id": f"cid-{i}",
                "coherence_fee": 2 + (i % 3),
                "n_tools": 6 + (i % 5),
                "n_edges": 5 + (i % 7),
                "servers": ["a", "b", "c"] if i % 2 else ["a", "b"],
                "perturbation_seed": f"s-{i}",
                "schema_properties_before": 20,
                "schema_properties_after": 14 + (i % 3),
                "schema_properties_hidden": 6 - (i % 3),
                "required_fields_before": 10,
                "required_fields_after": 7 + (i % 2),
                "required_fields_hidden": 3 - (i % 2),
            }
        )
    bundle = {"metadata": {"manifest_bundle_sha256": "hash"}, "rows": rows}
    corpus_path = tmp_path / "bundle.json"
    csv_out = tmp_path / "out.csv"
    summary_out = tmp_path / "summary.json"
    corpus_path.write_text(json.dumps(bundle))

    summary = run_scaling_sweep(
        corpus_path,
        csv_out=csv_out,
        summary_out=summary_out,
        min_high_r_count=30,
    )
    assert summary.status == "completed"
    assert summary.artifact_label == "OPERATIONAL_PROXY_V1"
    assert summary.outcome_source == "operational_proxy"
    assert summary.evidence_class == "operational_proxy"
    assert summary.high_r_count >= 30
    assert summary.r2_combined >= 0.0
