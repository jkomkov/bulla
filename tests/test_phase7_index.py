"""Phase 7 acceptance tests: API/MCP schema-capture pipeline +
indexed seed.

Validates the artifacts produced by
``scripts/standards-ingest/build_phase7_index.py``:

  - The pipeline captured ≥ 60 sources (57 MCP + 8 synthetic = 65
    on the seed run; the threshold leaves headroom for future runs
    that drop a few unloadable manifests).
  - The classifier-training corpus has ≥ 800 rows.
  - The coverage map's top-dimensions list is dominated by base-pack
    + Tier A signals (currency, temporal, country, language, MIME) —
    matches the curated synthetic schemas' design.
  - The forward-compatible record shape is intact (the deferred Part
    B equivalence detector will consume these records).

The tests skip if the artifacts haven't been generated yet, so a
fresh checkout doesn't fail before the pipeline run; CI must invoke
the build script before running these.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path

import pytest


def _api_registry_dir() -> Path:
    pkg = importlib.resources.files("bulla")
    return Path(
        str(pkg / ".." / ".." / "calibration" / "data" / "api-registry")
    ).resolve()


def _coverage_path() -> Path:
    return _api_registry_dir() / "coverage.json"


def _corpus_path() -> Path:
    return _api_registry_dir() / "classifier-corpus.jsonl"


def _load_coverage() -> dict:
    p = _coverage_path()
    if not p.exists():
        pytest.skip(
            f"Phase 7 coverage not yet built at {p}. Run: "
            "python scripts/standards-ingest/build_phase7_index.py"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def _load_corpus_lines() -> list[dict]:
    p = _corpus_path()
    if not p.exists():
        pytest.skip(
            f"Phase 7 classifier corpus not yet built at {p}. Run: "
            "python scripts/standards-ingest/build_phase7_index.py"
        )
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


# ── Sprint deliverable: ≥ 60 sources, ≥ 800 corpus rows ──────────────


class TestPhase7DeliverableShape:
    def test_pipeline_processes_at_least_57_real_mcp_manifests(self):
        """The honest count: ~57 real-world MCP manifests reprocessed
        through the new pipeline. Synthetic pipeline-validation
        fixtures are reported separately in
        ``test_at_least_5_pipeline_validation_fixtures``."""
        cov = _load_coverage()
        mcp_sources = [
            row for row in cov["by_source"]
            if row["source_kind"] == "mcp"
        ]
        assert len(mcp_sources) >= 57, (
            f"Phase 7 reprocessed only {len(mcp_sources)} real MCP "
            f"manifests; sprint deliverable is ≥ 57."
        )

    def test_at_least_5_pipeline_validation_fixtures(self):
        """Synthetic fixtures (OpenAPI / GraphQL) exercise dimensions
        the real MCP corpus doesn't reach. They're test fixtures,
        not real-world coverage."""
        cov = _load_coverage()
        synthetic_sources = [
            row for row in cov["by_source"]
            if row["source_kind"] in {"openapi", "graphql"}
        ]
        assert len(synthetic_sources) >= 5, (
            f"Only {len(synthetic_sources)} synthetic pipeline "
            f"fixtures captured; expected ≥ 5 (Stripe, Shopify, "
            f"GitHub, FHIR, FIX, GS1, etc.)."
        )

    def test_classifier_corpus_at_least_800_rows(self):
        rows = _load_corpus_lines()
        assert len(rows) >= 800, (
            f"Classifier corpus has only {len(rows)} rows; "
            f"the curated 65 schemas were expected to produce ≥ 800."
        )

    def test_at_least_5_distinct_seed_pack_dimensions_fire(self):
        """The top dimensions hit must include at least 5 of the
        21 dimensions the seed packs introduced."""
        cov = _load_coverage()
        seed_dimensions = {
            # Tier A
            "currency_code", "temporal_format", "country_code",
            "language_code", "media_type", "industry_code",
            # Tier B
            "unit_of_measure", "fix_msg_type", "fix_side",
            "gs1_application_identifier", "gs1_id_key_type",
            "edifact_message_type", "fhir_resource_type",
            "icd_10_cm_code",
            # Phase 4 restricted
            "who_icd_10_code", "swift_mt_message_type",
            "swift_mx_message_type", "hl7_v2_segment",
            "hl7_v2_message_type", "umls_concept_id",
            "iso_20022_message_type",
        }
        hit_dimensions = {row["dimension"] for row in cov["by_dimension"]}
        seed_hits = hit_dimensions & seed_dimensions
        assert len(seed_hits) >= 5, (
            f"Only {len(seed_hits)} seed-pack dimensions fired in the "
            f"index: {sorted(seed_hits)}. Expected at least 5."
        )


# ── Synthetic-schema-driven dimensions actually fire ─────────────────


class TestSyntheticSchemasFire:
    """The 8 curated synthetic schemas were designed to exercise
    specific seed-pack dimensions that the real 57-MCP corpus
    doesn't naturally cover. Verify the design holds: each named
    dimension is hit by the corresponding source."""

    @pytest.mark.parametrize(
        "expected_source_id,expected_dimension",
        [
            ("stripe-charges",      "currency_code"),
            ("shopify-admin",       "currency_code"),
            ("twilio-messages",     "currency_code"),
            ("github-v3",           "language_code"),
            ("fhir-patient",        "fhir_resource_type"),
            ("fix-trading-orders",  "fix_msg_type"),
            ("fix-trading-orders",  "fix_side"),
            ("gs1-traceability",    "gs1_id_key_type"),
        ],
    )
    def test_source_hits_dimension(
        self, expected_source_id: str, expected_dimension: str
    ):
        cov = _load_coverage()
        # Find the by_dimension row.
        dim_row = next(
            (r for r in cov["by_dimension"] if r["dimension"] == expected_dimension),
            None,
        )
        assert dim_row is not None, (
            f"Dimension {expected_dimension!r} not hit by any source"
        )
        assert expected_source_id in dim_row["sources"], (
            f"{expected_source_id!r} did not fire for "
            f"{expected_dimension!r}; sources hit: {dim_row['sources']}"
        )


# ── Forward-compatibility: corpus rows have the contract fields ──────


class TestCorpusForwardCompat:
    """The deferred Part B equivalence detector will consume the
    classifier corpus directly. Pin the row shape so a future Part B
    can rely on it."""

    REQUIRED_KEYS = {
        "source_kind", "source_id", "tool",
        "field", "dimensions", "confidence",
    }

    def test_every_row_has_required_keys(self):
        rows = _load_corpus_lines()
        for i, row in enumerate(rows):
            missing = self.REQUIRED_KEYS - row.keys()
            assert not missing, (
                f"row {i} missing keys {missing}: {row}"
            )

    def test_dimensions_field_is_a_list(self):
        rows = _load_corpus_lines()
        for r in rows:
            assert isinstance(r["dimensions"], list)

    def test_confidence_is_one_of_three_tiers(self):
        rows = _load_corpus_lines()
        for r in rows:
            assert r["confidence"] in {"declared", "inferred", "unknown"}

    def test_no_synthetic_description_rows_in_corpus(self):
        """``_description`` synthetic field records exist on captures
        but must NOT leak into the classifier corpus (they aren't
        labeled training examples in the same sense)."""
        rows = _load_corpus_lines()
        for r in rows:
            assert r["field"] != "_description"


# ── Per-source capture files exist ───────────────────────────────────


class TestPerSourceCaptureFiles:
    def test_every_indexed_source_has_a_capture_file(self):
        cov = _load_coverage()
        api_dir = _api_registry_dir()
        for row in cov["by_source"]:
            kind = row["source_kind"]
            sid = row["source_id"]
            # Allow the same sanitization as ``_safe_filename``.
            from bulla.api_registry import _safe_filename  # type: ignore
            expected = api_dir / kind / f"{_safe_filename(sid)}.json"
            assert expected.exists(), (
                f"missing capture file for {kind}/{sid} at {expected}"
            )

    def test_capture_file_has_capture_hash_and_active_packs(self):
        cov = _load_coverage()
        api_dir = _api_registry_dir()
        for row in cov["by_source"][:3]:  # spot-check first three
            kind = row["source_kind"]
            sid = row["source_id"]
            from bulla.api_registry import _safe_filename  # type: ignore
            path = api_dir / kind / f"{_safe_filename(sid)}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            assert "capture_hash" in data
            assert "active_packs" in data
            assert isinstance(data["active_packs"], list)
            assert len(data["active_packs"]) >= 1
