# API/MCP Schema-Capture Pipeline

> Phase 7 deliverable: a separate parallel registry from the convention
> pack layer. Convention packs are the **codomain** of δ₀ (the dimension
> vocabulary space); this API/tool registry is the **domain** of δ₀ (the
> tool-surface space). Every coherence-fee composition has a tool-side
> story (this layer) and a vocabulary-side story (the pack layer); the
> coboundary maps from one to the other.

## Quick start

```bash
# Build the seed index from existing MCP manifests + synthetic schemas
python scripts/standards-ingest/build_phase7_index.py

# Verify the deliverables
python -m pytest tests/test_phase7_index.py -v
```

Output lands at `bulla/calibration/data/api-registry/`.

## What the pipeline ingests

The capture function (`bulla.api_registry.capture`) accepts three
source kinds:

1. **MCP** — the existing `tools/list` response shape. Used by the
   63 captured manifests at `calibration/data/registry/manifests/`.
2. **OpenAPI 3.x** — each operation (path × method) becomes one
   "tool"; the operation's parameters and request body become the
   tool's `inputSchema` properties.
3. **GraphQL introspection** — each top-level Query / Mutation field
   becomes one tool; the field's arguments become the inputSchema
   properties.

Adding a new source kind is a single registration in
`_NORMALIZERS` plus a normalizer function — the rest of the pipeline
(classifier, content-addressing, storage) stays unchanged.

## What the pipeline outputs

For every input schema:

```
calibration/data/api-registry/
├── mcp/
│   ├── airtable-mcp.json
│   ├── any-chat-completions-mcp.json
│   └── ...
├── openapi/
│   ├── stripe-charges.json
│   ├── github-v3.json
│   └── ...
├── graphql/
│   └── shopify-admin.json
├── coverage.json              ← aggregate map: by_source / by_dimension / totals
└── classifier-corpus.jsonl    ← flat (field, dimension, confidence) corpus
```

Each per-source JSON is a `SchemaCapture.to_dict()` record:

- `source_kind`, `source_id`, `schema_hash`, `captured_at`
- `active_packs`: which pack stack the classifier ran under
- `tools`: per-tool ToolRecord (name + description + per-field FieldRecord)
- `aggregate`: pre-computed counts (n_fields / n_declared / n_inferred / n_unknown / dim_hits)
- `capture_hash`: SHA-256 of the full record (binds schema + active_packs + classifier output)

Two captures with identical inputs produce byte-identical files —
the pipeline is content-addressed end to end.

## What's in the coverage map

Aggregates per source and per dimension across the captured corpus:

```json
{
  "by_source": [
    { "source_id": "stripe-charges", "n_tools": 2, "n_fields": 8,
      "n_unknown": 1, "dim_hits": {"currency_code": 1, ...} },
    ...
  ],
  "by_dimension": [
    { "dimension": "currency_code", "total_hits": 6,
      "sources": ["stripe-charges", "shopify-admin", ...] },
    ...
  ],
  "totals": { "n_sources": 65, "n_tools": 370, "n_fields": 883, "n_unknown": ... }
}
```

This is the **coverage map** the Phase 7 plan calls for: which
dimensions are matched per server, where `unknown_dimensions` cluster.

## What's in the classifier-training corpus

`classifier-corpus.jsonl` flattens every (field, dimension, confidence)
triple across the corpus into a labeled training stream:

```json
{"source_kind": "openapi", "source_id": "stripe-charges",
 "tool": "createCharge", "field": "currency",
 "schema_type": "string",
 "description": "Three-letter ISO currency code",
 "dimensions": ["currency_code"], "confidence": "declared"}
```

This is the load-bearing input for the **deferred Part B** equivalence
detector. Each row is a labeled example: "in this real tool surface,
this field corresponded to this dimension under this classifier
confidence." The deferred detector clusters fingerprints across rows;
this format is the contract.

## Adding a new schema to the index

```python
import json
from pathlib import Path
from bulla.api_registry import capture_to_dir, SOURCE_KIND_OPENAPI
from bulla.infer.classifier import configure_packs

# 1. Configure the pack stack (Phase 2/3/4 seed packs)
configure_packs(extra_paths=sorted(
    Path("src/bulla/packs/seed").glob("*.yaml")
))

# 2. Load the schema you want to index
raw_schema = json.loads(Path("schemas/my-api.json").read_text())

# 3. Capture and write
capture_to_dir(
    raw_schema,
    source_kind=SOURCE_KIND_OPENAPI,
    source_id="my-api",
    out_dir=Path("calibration/data/api-registry"),
)

# 4. Re-aggregate the coverage and corpus
# (or: add to SYNTHETIC_SOURCES in build_phase7_index.py and re-run)
```

## Phase 7 sprint scope reminder

Per the plan: **the pipeline is the asset; coverage grows organically
post-sprint.** The seed-run captures **57 real MCP manifests** (pre-
existing, reprocessed through the new pipeline) plus **8 synthetic
pipeline-validation fixtures** (Stripe charges, Shopify Admin,
GitHub v3, FHIR Patient, Slack Web, Twilio Messages, FIX Trading
Orders, GS1 Traceability). The synthetics are pipeline-validation
fixtures — they exercise dimensions the real MCP corpus doesn't
reach (currency, FIX, FHIR, GS1, etc.) and prove the pipeline
end-to-end, but they're test fixtures, not real-world coverage.
Honest accounting: 57 real-world schemas indexed + 8 synthetic
test fixtures = 65 capture records.

**Future schemas land without modification to the pipeline.**

The plan-bounded curation target is ~100 schemas with diversity
across sources (Postman Public Collections, SwaggerHub, RapidAPI,
cloud SDKs, MCP aggregators, FIX/SWIFT/FHIR implementations). The
Phase 7 seed at 65 sources falls short of 100 only because the
network-fetch implementation needed to pull from external sources
(Postman / SwaggerHub / RapidAPI) is itself a follow-on; the pipeline
already accepts those source kinds. **Adding the network fetcher and
the next 35 schemas is a follow-on task, not a sprint blocker.**

## Forward-compatibility with the deferred Part B

The capture-record JSON shape is the input contract for the deferred
Part B equivalence detector. Three guarantees hold:

1. Every captured record carries `active_packs` + `capture_hash` so
   the detector can group records by pack-stack epoch.
2. Every classifier-corpus row carries `dimensions` (multi-set) and
   `confidence` so the detector can weight by signal strength.
3. `_description` synthetic field records exist on captures (they
   carry tool-level signal) but are excluded from the classifier
   corpus (they aren't field-level training examples).

When Part B becomes the active sprint, it consumes this contract
without forcing a migration on already-captured records.

## Verification

`tests/test_phase7_index.py` validates the seed-run artifacts:

- ≥ 60 sources indexed
- ≥ 800 classifier-corpus rows
- ≥ 5 seed-pack dimensions actually firing
- Each curated synthetic schema fires for its designed dimension
  (Stripe → currency_code, Shopify → currency_code, GitHub →
  language_code, FHIR → fhir_resource_type, FIX → fix_msg_type +
  fix_side, GS1 → gs1_id_key_type)
- Forward-compat: every corpus row has the contract fields
- Per-source capture files exist on disk and carry capture_hash +
  active_packs

`tests/test_api_registry.py` validates the pipeline itself
(20 unit tests, 0 dependencies on the seed run).
