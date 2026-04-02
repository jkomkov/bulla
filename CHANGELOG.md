# Changelog

## 0.8.0

### Added
- **Structured MCP output**: `bulla.witness` and `bulla.bridge` now return `structuredContent` (typed dict) alongside the `content` text fallback. Both tools declare `outputSchema` so agents can consume receipts as typed objects without JSON parsing.
- **Operative policy thresholds at MCP boundary**: policy input accepts a full object (`name`, `max_blind_spots`, `max_fee`, `max_unknown`, `require_bridge`) in addition to a bare string name. Custom thresholds now actually govern disposition — `max_unknown` is no longer dead code.
- **Receipt chaining**: `WitnessReceipt` gains `parent_receipt_hash`. When `bulla.bridge` produces a patched receipt, it links back to the original. Enables auditable chains: original → repair → patched.
- **Convention pack overlays**: `src/bulla/packs/` directory with layered, mergeable convention packs. Ships `base.yaml` (the 10 reference dimensions, moved from `taxonomy.yaml`) and `financial.yaml` (4 financial-specific dimensions: `day_count_convention`, `settlement_cycle`, `fee_basis`, `rounding_mode`). Later packs override earlier ones with `logger.warning` on dimension collisions.
- **`--pack` CLI flag**: `bulla diagnose`, `bulla check`, `bulla infer`, and `bulla scan` accept `--pack FILE` (repeatable) to load additional convention packs.
- **`PackRef` model**: ordered pack references (`name`, `version`, `hash`) stored on receipts. Order is semantics — `[base, financial]` and `[financial, base]` produce different receipt hashes.
- **`WitnessBasis` model**: epistemic provenance dataclass (`declared`, `inferred`, `unknown`). Accepted as a parameter on `witness()` — the kernel records what the caller attests, never fabricates provenance.
- **Provenance threading**: `BullaGuard.from_mcp_manifest()` and `BullaGuard.from_mcp_server()` now aggregate classifier confidence tags into a `WitnessBasis`, available via `guard.witness_basis`.
- **Pack-aware classifier**: `classifier.py` loads from a configurable pack stack via `configure_packs()` / `load_pack_stack()`. Content hashes are SHA-256 of parsed canonical JSON, not raw YAML bytes.
- 55 new tests (292 total)

### Changed
- `_resolve_disposition()` now enforces `max_unknown`: compositions exceeding the threshold receive `REFUSE_PENDING_DISCLOSURE`.
- MCP `bulla.witness` input schema accepts `unknown_dimensions` (integer) and `witness_basis` (object) parameters.
- MCP `bulla.bridge` handler sets `parent_receipt_hash` on the patched receipt.
- `WitnessReceipt.receipt_hash` computation includes `parent_receipt_hash`, `active_packs`, and `witness_basis`.
- `WitnessReceipt.to_dict()` includes `parent_receipt_hash`, `active_packs`, and `witness_basis`.

## 0.7.0

### Changed
- **Renamed from `seam-lint` to `bulla`** — package, CLI, Python imports, and all public API names.
- `SeamGuard` → `BullaGuard`, `SeamCheckError` → `BullaCheckError`
- `to_seam_patch()` → `to_bulla_patch()`, `seam_patch_version` → `bulla_patch_version`
- `seam_manifest` YAML key → `bulla_manifest` (parser accepts both for one version cycle)
- MCP server tools: `bulla.witness`, `bulla.bridge`; resource URI: `bulla://taxonomy`
- CLI entry point: `bulla` (was `seam-lint`)
- SARIF rule IDs: `bulla/blind-spot`, `bulla/bridge-recommendation`
- PyPI package: `pip install bulla`

## 0.6.0

### Added
- **Witness kernel** (`witness.py`): deterministic measurement → receipt pipeline with three-layer separation (measurement / binding / judgment)
- **Constitutional objects**: `Disposition` enum (5 levels), `BridgePatch` (frozen, Bulla Patch v0.1), `WitnessReceipt` (content-addressable, tamper-evident)
- **`bulla serve`** — MCP stdio server exposing 2 tools + 1 resource:
  - `bulla.witness`: composition YAML → WitnessReceipt (atomic measure-bind-judge)
  - `bulla.bridge`: composition YAML → patched composition + receipt + before/after metrics
  - `bulla.taxonomy` resource: convention taxonomy for agent inspection
- **`bulla bridge`** — auto-generate bridged composition YAML or Bulla Patches from diagnosed composition
- **`bulla witness`** — diagnose and emit WitnessReceipt as JSON
- **`Diagnostic.content_hash()`** — deterministic SHA-256 of measurement content (excludes timestamps)
- **`load_composition(text=)`** — parser accepts string input for MCP server use
- **Policy profile**: `witness()` and `_resolve_disposition()` accept named `policy_profile` parameter (default: `witness.default.v1`), recorded in receipt and receipt hash
- **Bulla Patch v0.1**: `BridgePatch.to_bulla_patch()` — explicitly typed patch format, not RFC 6902
- **Typed error vocabulary**: `WitnessErrorCode` enum (4 codes), `WitnessError` exception
- **Anti-reflexivity enforcement**: AST-level test proves `diagnostic.py` has zero imports from `witness.py` (Law 1); bounded recursion via `depth` parameter with `MAX_DEPTH=10` (Law 7)
- **Three-hash boundary**: `composition_hash` (what was proposed), `diagnostic_hash` (what was measured), `receipt_hash` (what was witnessed) — tested for independence
- 33 new tests (233 total)

### Fixed
- **Bridge generation bug**: when `from_field != to_field` and both sides hidden, destination tool received wrong field. Now generates separate Bridge per side with correct field.

### Changed
- `to_json_patch()` renamed to `to_bulla_patch()` with `bulla_patch_version: "0.1.0"` field
- `receipt_hash` docstring documents timestamp inclusion semantics (unique event identity vs deduplication via `diagnostic_hash`)
- Bridge response includes `original_composition_hash` for traceability

## 0.5.0

### Added
- **Three-tier confidence: "unknown" tier now live** — single description-keyword-only or weak schema signals (enum partial overlap, integer type inference) now correctly produce `unknown` instead of the dead-branch `inferred`
- **0-100 range disambiguation** — fields with `minimum: 0, maximum: 100` now check field name and description for rate/percent indicators before choosing `rate_scale` vs `score_range`
- **Domain-aware prioritization** — `classify_tool_rich()` accepts `domain_hint` (e.g. `"financial"`, `"ml"`) to boost domain-relevant dimensions from `unknown` → `inferred`
- **`_normalize_enum_value()` helper** — single source of truth for enum normalization (lowercase, strip hyphens/underscores), replacing duplicated inline logic
- **Real MCP validation suite** — 5 realistic tool definitions (Stripe, GitHub, Datadog, Slack, ML) with per-tool coverage assertions
- **End-to-end coverage test** — real MCP JSON → generate manifests → validate → assert ≥6/10 dimensions detected
- **Domain map API** — `_get_domain_map()` loads taxonomy `domains` metadata (previously defined but unused)
- 16 new tests covering unknown tier, range disambiguation, domain boosting, normalization, real-tool coverage, and E2E pipeline (178 total)

### Changed
- `_merge_signals()` accepts `domain_hint` parameter for confidence boosting
- `classify_tool_rich()` accepts `domain_hint` parameter (backward-compatible, defaults to `None`)
- Description-only signals now produce `unknown` confidence (was incorrectly `inferred`)

### Fixed
- Dead `else` branch in `_merge_signals()` — the "unknown" confidence tier was unreachable (all paths produced "inferred")
- Field name propagation for description hits in `_merge_signals()` — description hits now inherit field names from co-occurring name/schema hits
- Circular import between `classifier.py` and `mcp.py` now documented with inline comment
- False positive: `format: "uri"` / `"email"` / `"uri-reference"` no longer mapped to `encoding` dimension — these are string formats, not encoding conventions
- False positive: `count` removed from `id_offset` field name patterns — count is a quantity, not an index
- Text formatter now explains fee-vs-blind-spots divergence when fee = 0 but blind spots exist

## 0.4.0

### Added
- **Multi-signal convention inference**: classifier now uses three independent signal sources instead of field-name regex alone
  - Signal 1: Field name pattern matching (existing, now taxonomy-compiled)
  - Signal 2: Description keyword matching — detects conventions from tool/field descriptions (e.g. "amounts in cents", "ISO-8601 timestamps")
  - Signal 3: JSON Schema structural signals — `format`, `type`+range, `enum`, `pattern` metadata
- **Nested property extraction**: recursive extraction of fields from nested JSON Schema objects with dot-path naming (e.g. `invoice.total_amount`), depth limit 3
- **Taxonomy as single source of truth**: `field_patterns` from `taxonomy.yaml` now compile into classifier regex at load time; `known_values` drive enum matching
- **Three-tier confidence model**: `declared` (2+ independent signals agree), `inferred` (1 strong signal), `unknown` (weak/ambiguous) — replaces the binary high/medium system
- `FieldInfo` dataclass for rich field metadata (type, format, enum, min/max, pattern, description)
- `classify_tool_rich()` high-level API for full multi-signal classification of MCP tool definitions
- `classify_description()` for extracting dimension signals from tool descriptions
- `classify_schema_signal()` for extracting dimension signals from JSON Schema metadata
- `description_keywords` per dimension in taxonomy (v0.2)
- Currency codes (USD, EUR, GBP, JPY, CNY, BTC) added to `amount_unit` known_values
- `extract_field_infos()` public API for rich field extraction from tool schemas
- Manifest generation now uses multi-signal classifier with `sources` metadata in output
- 41 new tests covering all signal types, confidence tiers, and round-trip validation (162 total)

### Changed
- Confidence values in generated manifests are now directly `declared`/`inferred`/`unknown` — the `_CONFIDENCE_MAP` translation layer is removed
- `infer_from_manifest()` output now includes signal sources in review comments
- Taxonomy version bumped to 0.2

### Fixed
- Version string tests now use `__version__` import instead of hardcoded values

## 0.3.0

### Added
- `bulla manifest --publish` — anchor manifest commitment hash to Bitcoin timechain via OpenTimestamps
- `bulla manifest --verify` — verify OTS proof on a published manifest
- `bulla manifest --verify --upgrade` — upgrade pending proofs to confirmed after Bitcoin block inclusion
- Optional `[ots]` extra: `pip install bulla[ots]` (base install stays single-dependency)
- Commitment hash excludes OTS fields for deterministic verification after publish
- 11 new OTS tests (mocked calendars, no network required)

## 0.2.0

### Added
- `bulla manifest` — generate and validate Bulla Manifest files from MCP tool definitions
- `bulla manifest --from-json` — generate from MCP manifest JSON
- `bulla manifest --from-server` — generate from live MCP server
- `bulla manifest --validate` — validate existing manifest YAML
- `bulla manifest --examples` — generate example manifests to see the format
- `bulla scan` — scan live MCP server(s) via stdio and diagnose
- `bulla init` — interactive wizard to generate a composition YAML
- `bulla diagnose --brief` — one-line-per-file summary output
- `BullaGuard` Python API for programmatic composition analysis
- Convention taxonomy (10 dimensions) with field-pattern inference
- Auto-validation after manifest generation
- "Now what?" guidance in `check` output on failure
- Quickstart guide when running bare `bulla` with no subcommand
- SARIF output format for GitHub Code Scanning integration

### Fixed
- Confidence mapping: classifier internal grades (`high`/`medium`) now correctly map to manifest spec vocabulary (`declared`/`inferred`/`unknown`)
- `_examples_dir()` portability for installed packages

## 0.1.0

### Added
- `bulla diagnose` — full sheaf cohomology diagnostic with blind spot detection
- `bulla check` — CI/CD gate with configurable thresholds
- `bulla infer` — infer proto-composition from MCP manifest JSON
- Text, JSON, and SARIF output formats
- Exact rational arithmetic (no floating-point) via Python `Fraction`
- 9 bundled example compositions (financial, code review, ETL, RAG, auth, MCP)
- 107 tests, single dependency (PyYAML)
