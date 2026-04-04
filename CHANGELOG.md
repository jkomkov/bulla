# Changelog

## 0.20.0

### Added
- **`path_convention` dimension**: New convention dimension in `base.yaml` with known values `absolute_local`, `relative_cwd`, `relative_repo`, `uri`. Detects `path`, `filepath`, `directory`, `dirname`, `folder` fields. Creates cross-server edges between filesystem and GitHub servers, producing the first **nonzero boundary fee** in real-world audit.
- **Temporal field patterns**: `since`, `after`, `before`, `until` added to `date_format` core patterns and `base.yaml` field_patterns. GitHub's `list_issues.since` now correctly classified as `date_format`.
- **Per-field description scanning**: 4th signal source in `classify_tool_rich`. Per-field descriptions (not just tool-level) scanned against pack keyword lists. Source type `field_description` — weak alone, promotes to `declared` when combined with name/schema signals.
- **Pack-driven description keywords**: `_DESCRIPTION_KEYWORDS` replaced with dynamic loading from merged pack taxonomy via `_get_description_keywords()`. Custom packs automatically enrich description matching. Financial pack keywords become active when loaded.

### Changed
- **Negative patterns for `id_offset`**: `per_page`, `page_size`, `page_count`, `limit`, `count`, `total`, `max_results`, `num_results`, `batch_size` excluded from `id_offset` via `_NEGATIVE_PATTERNS`. These are counts/limits, not indices.
- **Type-aware exclusion**: String-typed `*_id` fields (UUIDs, SHA hashes) excluded from `id_offset` when `schema_type="string"` is available. `commit_id` (string) no longer flagged.
- **`id_offset` description narrowed**: "Whether numeric indices and page numbers are zero-based or one-based" (was "identifiers and indices").
- **Real-world audit findings updated**: Fee 31 (was 17), 2 dimensions (was 1), boundary_fee=1 (was 0), 28 cross-server blind spots (was 0). FINDINGS.md rewritten with concrete agent failure scenario lede and before/after comparison table.
- **Base pack now has 11 dimensions** (was 10).

### Fixed
- `per_page` false positive eliminated from GitHub audit findings
- `commit_id` (string-typed) false positive eliminated
- Description keyword matching now extensible via pack YAML instead of hardcoded

## 0.19.0

### Added
- **`BullaGuard.from_tools_list()`**: New public classmethod for building a guard from an in-memory list of MCP tool dicts. This is the recommended entry point for programmatic multi-server audit, replacing direct use of the private `_composition_from_mcp_tools` helper.
- **SARIF output for `bulla audit`**: `--format sarif` produces SARIF v2.1.0 output with blind spots and bridge recommendations tied to the MCP config file path. Enables GitHub Code Scanning integration for audit results.
- **Server-name prefixed tool names**: In `bulla audit`, tools are now prefixed with their server name using `__` separator (e.g., `filesystem__read_file`). This makes tool-to-server mapping robust and self-documenting, eliminating the fragile index-based mapping from v0.18.0.
- **Real-world audit evidence**: Captured genuine `tools/list` responses from 4 live MCP reference servers (filesystem, github, memory, puppeteer — 56 tools total) with provenance metadata. First real-world cross-server audit found 17 blind spots in the GitHub server's `id_offset` conventions. See `examples/real_world_audit/FINDINGS.md`.
- **`examples/real_world_audit/`**: Reproducible audit demo with `run_audit.py` script and version-pinned server manifests in `manifests/`.
- **8 new tests**: `from_tools_list` API, server-prefixed tool names, SARIF output validation, real-world manifest smoke test.

### Fixed
- `_cmd_audit` no longer imports private `_composition_from_mcp_tools`; uses `BullaGuard.from_tools_list()` instead.
- Tool-to-server mapping in audit is now derived from prefixed tool names in the composition, not pre-predicted from raw tool dicts (eliminates invisible coupling).

## 0.18.0

### Added
- **`bulla audit`**: New CLI subcommand that reads MCP configuration files (Cursor/Claude Desktop format), scans all configured servers in parallel, builds a cross-server composition graph, and diagnoses the combined system. Features:
  - Auto-detection of MCP config in standard locations (`.cursor/mcp.json`, `~/.cursor/mcp.json`, Claude Desktop config)
  - Parallel scanning via `ThreadPoolExecutor` with per-server error isolation
  - Cross-server risk decomposition using `decompose_fee()` -- partitions blind spots into intra-server (within individual servers) vs boundary fee (between servers)
  - Text and JSON output formats, CI gating with `--max-fee` / `--max-blind-spots`, `--verbose` for detailed blind spot listing
  - `--skip-failed` / `--no-skip-failed` for controlling failure behavior
- **`scan_mcp_servers_parallel()`**: New parallel scanner in `scan.py` using `ThreadPoolExecutor`. Returns `list[ServerScanResult]` with per-server success/failure instead of aborting on first error.
- **`ServerScanResult`**: New dataclass in `scan.py` for structured scan results with `name`, `tools`, `error`, and `ok` property.
- **`bulla.config` module**: New module with `McpServerEntry`, `parse_mcp_config()`, and `find_mcp_config()` for parsing Cursor/Claude Desktop MCP configuration files. Supports stdio servers, skips HTTP/SSE transport with warnings.
- **`env` parameter on `scan_mcp_server()`**: Optional environment variable dict merged with `os.environ` before spawning, enabling API key passthrough from MCP configs.
- 12 new tests (561 total): config parser (5), parallel scan (2), audit CLI text/JSON/threshold/failed-server (5).

### Changed
- CLI quick-start help now shows `bulla audit` as the first command.

## 0.17.0

### Added
- **`bulla gauge`**: New CLI subcommand for prescriptive diagnosis of MCP servers and manifests. Accepts a manifest JSON file or `--mcp-server CMD` to diagnose a live server. Returns coherence fee, minimum disclosure set (exact fields to expose), and witness basis in a single command. Supports `--format text|json|sarif`, `--output-composition FILE` to save inferred YAML, CI gating flags `--max-fee N` / `--max-blind-spots N` (exit 1 on violation), and `--verbose` for full blind spot detail and bridge recommendations.
- **`prescriptive_disclosure()`**: New helper in `diagnostic.py` that encapsulates the lazy disclosure guard (skip coboundary construction when fee=0). Used by both the MCP surface (`serve.py`) and the CLI (`bulla gauge`), eliminating the duplicated `if fee > 0` pattern.
- 6 new tests (549 total): gauge text/JSON output, threshold pass/fail, blind spots threshold, composition round-trip.

### Fixed
- **`scan.py` clientInfo version**: Replaced hardcoded `"version": "0.7.0"` with `__version__` import. The MCP initialize handshake now reports the correct Bulla version.
- **`formatters.py` residual string parsing**: Replaced 4 `bs.edge.split(" → ")` calls in `format_text` and `format_sarif` with `bs.from_tool` / `bs.to_tool`, eliminating the same fragile pattern fixed in `diagnostic.py` in v0.16.
- **LangGraph demo dimension naming**: Renamed confusing dimension names `threshold_currency` → `amount_rounding` and `jurisdiction` → `regulatory_framework` to better represent the semantic conventions being measured.
- **Dead code**: Removed unused `from bulla.model import Diagnostic, WitnessBasis` imports from gauge formatter functions.

### Changed
- **README**: Added "Quick start with `bulla gauge`" section as the primary entry point, showing manifest and live-server usage patterns.
- **CLI help text**: Updated quick-start listing to feature `bulla gauge` first.

## 0.16.0

### Added
- **`BlindSpot.from_tool` / `BlindSpot.to_tool`**: Ergonomic fields storing source and target tool names directly on blind spot objects. Eliminates fragile `edge.split(" → ")` string parsing in `diagnose()` bridge generation and `conditional_diagnose()` obligation extraction. These fields are **excluded from `content_hash()`** — they are derivable from the already-hashed `edge` label and do not affect receipt verification against v0.15 receipts.
- **Lazy disclosure test**: `test_serve.py` now verifies that MCP `bulla.witness` returns `disclosure_set=[]` for fee=0 compositions (using `auth_pipeline.yaml`), covering the lazy disclosure guard added in v0.15.
- **LangGraph integration demo**: `examples/langgraph_demo.py` — a self-contained 4-tool trade pipeline that builds a LangGraph graph (schema-valid), extracts a Bulla `Composition` with manual annotation, and diagnoses hidden conventions invisible to the orchestrator. Frames `bulla gauge` (Sprint 17) as the automation target for the annotation step. LangGraph is not a project dependency.
- 1 new test (543 total).

### Changed
- **Paper draft** (`papers/hierarchical-fee/`): Abstract tightened from ~196 to ~153 words (submodularity detail removed). Non-negativity proof expanded with explicit projection lemma. 8-tool case study added to empirical table with fee/disclosure/bridge/boundary metrics. Conditional resolution section expanded with baseline → worst-case → resolved fee-drop numbers. Author affiliation added. Self-citations (`bridge`, `sheaf`, `scpi`) labeled as "Technical Report, Res Agentica" with repository URLs. LangGraph demo referenced in Related Work. Companion version updated to v0.16.
- **Self-citation provenance**: `bridge`, `sheaf`, `scpi` bibitems now carry "Technical Report, Res Agentica, 2026" labels with `\url{https://github.com/jkomkov/bulla}`.
- **Sync script tracked**: `scripts/sync-to-standalone.sh` added to version control.

## 0.15.0

### Added
- **Trace gap investigation**: Computationally verified that the Frobenius trace gap (`||delta_full||_F^2 - ||delta_obs||_F^2`) equals the total count of hidden-endpoint instances across blind spots. Closed as a non-informative weighted blind-spot count: it can be positive when the fee is zero (hidden columns in the span of observable columns) and adds no information beyond the existing blind-spot structure. Counterexample verified. Documented as a remark in the proof note.
- **Survey smoke test**: `tests/test_adversarial_survey.py` — imports core functions from the adversarial submodularity survey script and runs a minimal 10-composition smoke test to guard against silent regressions.
- **Trace gap test suite**: `tests/test_trace_gap.py` — verifies trace_gap == endpoint count for all 10 bundled compositions, fee > 0 implies trace_gap > 0, fee=0/trace_gap>0 counterexample, and same-fee-different-trace-gap distinguishability.
- 26 new tests (542 total): trace gap (22), survey smoke (4).

### Changed
- **Paper draft**: Proof note reorganized from theorem order to story order for submission. New sections: Introduction (opens with financial settlement failure narrative), Related Work (3 areas: contract-based design, sheaf cohomology, multi-agent orchestration), Conclusion (with explicit non-claim: "fee measures structural verifiability, not semantic correctness"). Case study expanded with "what could go wrong" failure scenario. Empirical table trimmed to 6 highlight rows. Bibliography expanded from 4 to 15 references. 831 lines (up from 660). Target venue: AAMAS 2027 or NeurIPS/ICML agent safety workshop.
- **Case study YAML annotations**: `financial_settlement_pipeline.yaml` now includes comment blocks explaining the semantic meaning of each edge's convention propagation (e.g., why `jurisdiction` maps to `risk_model_version`).
- **Lazy disclosure_set in MCP**: `_handle_witness` in `serve.py` now guards the `minimum_disclosure_set(comp)` call with `receipt.fee > 0`, skipping both coboundary matrix constructions when the fee is zero.

## 0.14.0

### Added
- **Submodularity disproved**: Adversarial survey of 10,000 random compositions (635,095 partition pairs) found 4,061 violations of `bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)`, with maximum violation magnitude 3. Minimal counterexample: 4 tools, 5 edges, where two partitions have bf=0 but their meet has bf=1. Individual `rho_full` and `rho_obs` are submodular (matroid rank on row sets), but their difference `bf = rho_full - rho_obs` is not.
- **8-tool case study**: `financial_settlement_pipeline.yaml` — realistic multi-agent financial settlement workflow with 8 tools, 8 edges, betti_1=1 (cycle via audit_log -> compliance_check). Fee=7, 8 blind spots, 15 bridges, 7-element minimum disclosure set (2.1x savings over bridges).
- **MCP `disclosure_set`**: `bulla.witness` now always returns a `disclosure_set` field — the minimum disclosure set as `[[tool, field], ...]`. Makes every witness call prescriptive by default.
- **MCP `partition` parameter**: `bulla.witness` accepts an optional `partition` parameter (array of arrays of tool name strings). When provided, the output includes a `decomposition` field with `total_fee`, `local_fees`, `boundary_fee`, `rho_obs`, `rho_full`, `boundary_edges`. Only present when partition is provided — existing consumers are unaffected.
- **Case study section in proof note**: 8-tool composition analysis with fee, disclosure set table, front/back-office partition decomposition, and conditional resolution round-trip.
- **Adversarial survey script**: `scripts/adversarial_submodularity_survey.py` — generates random compositions with random hidden/visible fields and checks submodularity across partition pairs.
- 7 new tests (516 total): submodularity counterexample (1), MCP disclosure_set and decomposition (6).

### Changed
- **`ConditionalDiagnostic.extended_comp`**: Type annotation fixed from `Composition = None # type: ignore[assignment]` to `Composition | None = None`.
- **Resolution monotonicity proof**: Strengthened from "internal states identical by construction" to "I_real ⊇ I_placeholder is a consequence of composition validity" (edge dimensions must reference existing internal_state fields).
- **Submodularity remark in proof note**: Upgraded from "computationally verified" to "disproved by adversarial counterexample" with formal analysis of why bf is not submodular (difference of submodular functions).
- **Bundled parametrized tests**: Partition sampling for compositions with > 50 binary partitions (8-tool composition has 254), keeping the test suite under 70 seconds.

### Empirical Results
- Submodularity disproved: 4,061/635,095 violations across 10,000 adversarial random compositions (0.64% violation rate). Bundled compositions (833 sampled pairs) still show zero violations — a topological accident of pipeline-like structure.
- 8-tool case study: fee=7, |S|=7=fee, |bridges|=15 >= 2*7=14. Front/back-office partition: local=(2,3), bf=2.
- Tower law verified: 2,778/2,778 sampled pairs across 10 bundled compositions.

## 0.13.0

### Added
- **`resolve_conditional`**: Resolve one or more placeholders in a conditional diagnostic. Rebuilds the composition with real tools swapped in, runs `diagnose`, and partitions obligations into met and remaining. Supports partial resolution (resolve some placeholders, leave others). Returns a `Resolution` dataclass with `resolved_diag`, `resolved_fee`, `fee_delta`, `met_obligations`, and `remaining_obligations`.
- **`Resolution` dataclass**: Result type for `resolve_conditional`. `fee_delta` is `worst_case_fee - resolved_fee` and is always non-negative (a real tool is at least as informative as a placeholder with empty observable schema).
- **`ConditionalDiagnostic.extended_comp`**: Stores the extended composition with placeholders, enabling `resolve_conditional` to work without the caller needing to reconstruct the composition.
- **Extremal boundary fee**: New proposition and tests for the all-hidden star topology. Partition `{Hub} | {S_1..S_n}` achieves `bf = total_fee = n` because all edges are cross-partition and both groups are internally edge-free. Grouping the hub with k spokes reduces `bf` by exactly k.
- **Submodularity survey**: Exhaustive survey across 333 partition pairs from all 9 bundled compositions confirms submodularity (`bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)`) with zero violations. Added helper functions `_partition_meet` and `_partition_join` for lattice operations.
- **Online resolution corollary**: Added to proof note — replacing a placeholder with a real tool can only decrease or maintain the coherence fee (resolution monotonicity).
- **Proof note updates**: Extremal cases section with theorem and landscape remark, submodularity remark, online resolution section with corollary and proof. Abstract and empirical results updated for v0.13.
- **`minimum_disclosure_set` documentation**: Non-uniqueness note in docstring and matroid rank submodularity comment on greedy loop.
- 19 new tests (493 total): `resolve_conditional` (8 unit + bundled parametrized), extremal star (11: hub-vs-spokes, mixed partition, singleton partition), submodularity survey (1 bundled parametrized across 9 compositions).

### Empirical Results
- `resolve_conditional` verified on 7 unit compositions (fee drop, obligation matching, partial resolution, round-trip with `minimum_disclosure_set`, from-scratch equivalence).
- Submodularity verified across 333 partition pairs (333/333).
- Extremal star: `bf = total_fee` for `{Hub}|{spokes}` partition verified for 2-5 spokes.

## 0.12.0

### Added
- **Minimum Disclosure Set** (`minimum_disclosure_set`): Given a composition, returns the smallest set of `(tool, field)` disclosures that reduces the coherence fee to zero. The cardinality always equals the fee — it is a basis for the quotient space `col(delta_full) / col(delta_obs)`. Greedy column selection finds one such basis. Removes at least 2x redundancy versus the existing bridges mechanism.
- **Valuation counterexample**: Computationally proved that the boundary fee is NOT a valuation on the partition lattice. For the A->B->C chain: `bf(P) + bf(Q) = 2` but `bf(P^Q) + bf(P v Q) = 1`. The same hidden convention at B causes boundary fee in both partitions, but resolving it once suffices.
- **Submodularity test**: Verified that the boundary fee satisfies submodularity (`bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)`) for the counterexample chain.
- **Two-step tower law induction test**: Hand-built 4-tool chain (A->B->C->D) verifying `bf(singletons) = bf(coarse) + bf(sub_AB) + bf(sub_CD)`. Also verified on bundled compositions with >= 4 tools.
- **Proof note update**: New "Minimum Disclosure Set" section with theorem (cardinality equals fee), proof, and bridges comparison remark. Non-valuation remark added to Tower Law section. Abstract and empirical results updated for v0.12.
- **`satisfies_obligations` docstring**: Documents that the function checks fields only — the caller filters obligations by placeholder name.
- 44 new tests (474 total): minimum disclosure set (5 unit + 27 bundled parametrized), valuation counterexample (1), submodularity (1), two-step tower law (1 unit + 9 bundled, 6 skipped for < 4 tools).

### Empirical Results
- `len(minimum_disclosure_set) == fee` verified across all 9 bundled compositions (9/9).
- `len(bridges) >= 2 * len(disclosures)` verified across all 9 bundled compositions (9/9).
- Applying disclosures reduces fee to 0 for all 9 bundled compositions.
- Removing any single disclosure from a minimal set leaves fee > 0 (minimality verified).
- Valuation property disproved; submodularity holds for tested cases.

## 0.11.0

### Added
- **Tower Law** (Theorem): The boundary fee is additive across levels of hierarchy. For a partition refined by sub-partitioning each group: `bf(refined) = bf(coarse) + sum(bf(sub_i))`. Proof is a 3-sentence telescoping argument from the decomposition theorem.
- **Monotonicity Corollary**: Refining a partition can only increase the boundary fee. The boundary fee defines a monotone function on the refinement lattice: 0 at the trivial partition, `total_fee` at singletons. Formalizes "every level of delegation adds non-negative hidden cost" as a theorem.
- **`satisfies_obligations`**: Checks whether a `ToolSpec` meets a set of `BoundaryObligation`s. Closes the conditional receipt loop: `conditional_diagnose` -> obligations -> candidate tool arrives -> `satisfies_obligations` -> recompute with real tool.
- **Proof note update**: Tower Law theorem, proof, Monotonicity corollary, and lattice remark added to `papers/hierarchical-fee/`. Empirical results updated with tower law verification data (264/264 pairs verified).
- **WITNESS-CONTRACT.md**: Tower Law and Monotonicity added as sub-properties of the hierarchical decomposition law.
- 25 new tests (430 total): tower law verification across all bundled compositions (9 tests), monotonicity under refinement (9 tests), obligation satisfaction checker (5 tests), edge cases for decompose_fee with 0 edges and shared-placeholder conditional diagnosis (2 tests).

### Changed
- **`_cross_rank_modulo_internal`**: Replaced fragile label string parsing (`split("→")`) with direct `Edge` iteration matching `_edge_basis` row ordering. Coupling comment documents the implicit contract between `diagnostic.py` and `coboundary.py`.
- **`conditional_diagnose` placeholder merging**: Replaced O(n) `tuple` membership check with `set` intermediate for deduplication.
- **LaTeX bibkeys**: Renamed `\bibitem{sheaf-paper}` to `\bibitem{sheaf}` for consistency.

### Empirical Results
- Tower law computationally verified across 264 coarse/refined partition pairs (264/264). Boundary fee survey unchanged: 64/70 (91%) of binary partitions have nonzero boundary fee.

## 0.10.0

### Added
- **Hierarchical fee decomposition** (`decompose_fee`): Takes a `Composition` and a partition of tool names, returns `FeeDecomposition` with per-group local fees, boundary fee, and the independent block-rank characterization (`rho_obs`, `rho_full`). The boundary fee is computed via `rho_full - rho_obs` (rank of cross-partition rows modulo internal rows) and verified against the remainder. Non-negativity proved via column-projection argument.
- **Conditional diagnosis** (`conditional_diagnose`): Diagnose partial compositions with open ports. Creates placeholder tools with empty observable schemas, runs existing `diagnose`, and returns `ConditionalDiagnostic` with worst-case fee, boundary obligations (fields placeholders must expose), and structural unknown count.
- **`FeeDecomposition` model**: Frozen dataclass with `total_fee`, `local_fees`, `boundary_fee`, `partition`, `boundary_edges`, `rho_obs`, `rho_full`.
- **`ConditionalDiagnostic` model**: Frozen dataclass with baseline/extended diagnostics, fee bounds, obligations, structural unknowns.
- **`OpenPort` model**: Describes an unconnected port in a partial composition for conditional diagnosis.
- **`BoundaryObligation` model**: Convention that an unspecified tool must declare observably.
- **WITNESS-CONTRACT.md**: Hierarchical Fee Decomposition law, structural vs epistemic unknown distinction.
- **Proof note**: `papers/hierarchical-fee/` — theorem (fee decomposition from block rank), counterexample, vanishing corollary, SCPI connection, empirical results.
- 37 new tests (405 total): counterexample chain, multi-dimension variant, full-disclosure vanishing, decompose_fee API tests, invariant tests across all bundled compositions (70 partitions), parametrized full-disclosure vanishing (chain + cycle), adversarial hidden interfaces (both-sides, star topology, one-side, mixed), conditional diagnosis (6 tests), empirical boundary fee survey.

### Empirical Results
- Boundary fee is nonzero in 64/70 (91%) of binary partitions across 9 bundled compositions. The hierarchical blind spot is the dominant regime, not a corner case. All 6 vanishing cases come from `auth_pipeline` (total fee = 0).

## 0.9.1

### Changed
- **`verify_receipt_integrity` is now forward-compatible**: Uses dict-exclusion (`to_dict()` minus `receipt_hash` and `anchor_ref`) instead of hardcoded field enumeration. Future field additions are automatically covered without code changes.
- **`WitnessReceipt._hash_input()`**: Single source of truth for the receipt's hashable content. `receipt_hash`, `to_dict()`, and `verify_receipt_integrity` all derive from this one method, eliminating field enumeration duplication.
- **`receipt_hash` is now cached**: Computed once on first access using lazy cache on the frozen dataclass. Eliminates redundant SHA-256 computation when `to_dict()` or receipt chaining access the hash multiple times.
- **`verify_receipt_consistency` now verifies disposition**: Recomputes `_resolve_disposition` from the diagnostic, policy, and unknown_dimensions, and compares to the receipt's claimed disposition. A receipt can no longer claim `proceed` when measurements would produce `refuse_pending_disclosure`.
- **`Diagnostic` is now frozen** with immutable `tuple` fields (`blind_spots`, `bridges`). Completes the immutability chain: `Composition`, `Diagnostic`, and `WitnessReceipt` are all frozen with tuple fields.
- **`Bridge.add_to` is now `tuple[str, ...]`** (was `list[str]`). Consistent with all other frozen dataclass fields.
- **Development Status**: Alpha → Beta. The kernel is feature-complete with 368 tests, verification functions, immutable constitutional objects, and a normative spec.

### Added
- Anti-reflexivity AST test: `diagnostic.py` has zero imports from `serve.py`. The measurement layer is now provably isolated from both witness and transport layers.

## 0.9.0

### Breaking Changes
- **`witness_basis.unknown` overrides `unknown_dimensions`**: When `witness_basis` is provided to `witness()`, `witness_basis.unknown` now determines both the receipt's `unknown_dimensions` field and the policy disposition. The explicit `unknown_dimensions` parameter becomes a fallback for non-attested cases only. This eliminates lying receipts where `witness_basis.unknown=5` could coexist with `unknown_dimensions=0` and a `proceed` disposition under `max_unknown=3`.
- **`Composition.tools` and `Composition.edges` are now `tuple`**: Previously `list`. The `frozen=True` dataclass was misleading when fields were mutable. All construction sites now use `tuple()`. Code that indexes or iterates is unaffected; code that calls `.append()` on these fields must change.

### Added
- **`verify_receipt_consistency(receipt, comp, diag)`**: Checks that a receipt's claimed hashes and counts match the given composition and diagnostic objects. Returns `(is_valid, violations)`.
- **`verify_receipt_integrity(receipt_dict)`**: Self-contained tamper detection from a serialized receipt dict. Recomputes the SHA-256 hash from the dict's fields and compares to the claimed `receipt_hash`. No kernel or original objects required.
- **Public API exports**: `PackRef`, `WitnessBasis`, `verify_receipt_consistency`, and `verify_receipt_integrity` are now exported from `bulla`.
- **MCP active_packs threading**: `bulla.witness` and `bulla.bridge` MCP handlers now pass configured pack refs to the witness kernel. Receipts emitted via MCP record the active lexical constitution.
- **`bulla.bridge` schema parity**: `unknown_dimensions` and `witness_basis` parameters added to `bulla.bridge` input schema, matching `bulla.witness`.
- **Mathematical invariant test suite**: `test_invariants.py` with 67 parametrized tests across all bundled compositions: coherence fee non-negativity, bridging monotonicity, basis/unknown consistency, verification round-trips, tamper detection, hash determinism, and pack order sensitivity.
- 74 new tests (366 total)

### Fixed
- **`bulla://taxonomy` resource**: Now returns the merged pack stack (via `load_pack_stack()`) instead of the raw `taxonomy.yaml` file, consistent with the pack system.
- **Bridge handler parity**: `_handle_bridge` now threads `unknown_dimensions`, `witness_basis`, and `active_packs` through both the original and patched witness calls.

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
