# Bulla Witness Contract

Normative reference for the witness kernel. Deviation between code and this spec is a bug in one or the other. For theoretical motivation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf).

## Canonical Objects

| Object | Identity | Contents |
|---|---|---|
| `Composition` | `canonical_hash()` — SHA-256 of sorted structural JSON | Tools (name, internal state, observable schema) + edges + dimensions |
| `Diagnostic` | `content_hash()` — SHA-256 of measurement content | Fee, blind spots, bridges, rank data. Excludes timestamps. `BlindSpot.from_tool`/`to_tool` are ergonomic fields excluded from hash |
| `WitnessReceipt` | `receipt_hash` — SHA-256 of all fields except `anchor_ref` | Binds composition + diagnostic + policy + lexical constitution + provenance |

Three hashes, three concerns: what was proposed, what was measured, what was witnessed.

## Hash Coverage

`receipt_hash` includes: `receipt_version`, `kernel_version`, `composition_hash`, `diagnostic_hash`, `policy_profile`, `fee`, `blind_spots_count`, `bridges_required`, `unknown_dimensions`, `disposition`, `timestamp`, `patches`, `active_packs`, `witness_basis`, and conditionally `parent_receipt_hashes`, `inline_dimensions`, `boundary_obligations`, `contradictions`, `structural_contradictions` (each only when not None), `unmet_obligations` (only when > 0), and `contradiction_score` (only when > 0).

`receipt_hash` excludes: `anchor_ref` (external publication proof, added after witness event).

Rationale: the hash must be computable at witness time. Anchor ref arrives later. Conditional fields (`parent_receipt_hashes`, `inline_dimensions`, `boundary_obligations`, `contradictions`, `structural_contradictions`, `unmet_obligations`, `contradiction_score`) are included only when non-None/non-zero to preserve backward compatibility: pre-v0.25.0 receipts (which lack `boundary_obligations`), pre-v0.30.0 receipts (which lack `contradictions`), pre-v0.32.0 receipts (which lack `unmet_obligations`), and pre-v0.34.0 receipts (which lack `structural_contradictions` and `contradiction_score`) verify correctly with new code because their hash was computed without these keys, and the verifier only hashes keys that are present.

## Policy Semantics

`PolicyProfile` fields: `name`, `max_blind_spots`, `max_fee`, `max_unknown`, `require_bridge`, `max_unmet_obligations`, `max_contradictions`, `max_structural_contradictions`.

Disposition priority (first match wins):
1. `blind_spots > 0 AND fee > max_fee` → `refuse_pending_disclosure`
2. `unknown_dimensions > max_unknown` (when `max_unknown >= 0`) → `refuse_pending_disclosure`
3. `unmet_obligations > max_unmet_obligations` (when `max_unmet_obligations >= 0`) → `refuse_pending_disclosure`
4. `contradiction_count > max_contradictions` (when `max_contradictions >= 0`) → `refuse_pending_disclosure`
5. `structural_contradiction_score > max_structural_contradictions` (when `max_structural_contradictions >= 0`) → `refuse_pending_disclosure`
6. `require_bridge AND blind_spots > 0` → `proceed_with_bridge`
7. `blind_spots > max_blind_spots` → `proceed_with_bridge`
8. `structural_contradiction_score > 0` → `proceed_with_caution`
9. `fee > max_fee` → `proceed_with_receipt`
10. Otherwise → `proceed`

Note on caution vs. threshold: rule 8 fires on ANY nonzero `structural_contradiction_score`, independent of `max_structural_contradictions`. The threshold (rule 5) controls the refuse boundary; the caution boundary (rule 8) fires unconditionally on visible schema incompatibility.

`max_unknown = -1` disables the unknown threshold (default). `max_unmet_obligations = -1` disables obligation enforcement (default). `max_contradictions = -1` disables contradiction enforcement (default). `max_structural_contradictions = -1` disables structural contradiction enforcement (default). Setting any threshold to `0` means strict: any occurrence triggers refusal.

## Anti-Reflexivity Laws

**Law 1**: The measurement layer (`diagnostic.py`) has zero imports from the witness layer (`witness.py`). Measurement does not know it is being witnessed.

**Law 2**: The witness kernel never mutates a `Composition` or `Diagnostic`. It proposes patches; it never applies them silently. `Composition`, `Diagnostic`, and `WitnessReceipt` are all `frozen=True` with immutable `tuple` fields.

## Receipt DAG (v0.24.0)

`parent_receipt_hashes` (tuple of strings, or None) links a receipt to one or more prior witness events. A single parent is a 1-tuple; multiple parents form a DAG. This replaces the pre-v0.24.0 `parent_receipt_hash` (singular) field.

**Precedence semantics**: Tuple order IS precedence order. Later entries have higher precedence, consistent with the pack stack convention. The merged receipt records its parents in precedence order: `bulla merge base.json override.json` produces `parent_receipt_hashes = (hash_base, hash_override)`, and override wins on dimension name collision.

**Backward compatibility**: `verify_receipt_integrity()` is key-name-agnostic -- it recomputes the hash over whatever keys are present minus `_HASH_EXCLUDED_KEYS`. Pre-v0.24.0 receipts serialized with `parent_receipt_hash` (singular) verify correctly because the verifier sees that key and includes it. New receipts use `parent_receipt_hashes` (plural). No migration needed.

**Convenience API**: `witness()` accepts both `parent_receipt_hash` (single string, for backward-compatible callers) and `parent_receipt_hashes` (tuple). Providing both raises `ValueError`. A single parent supplied via `parent_receipt_hash` is stored as a 1-tuple on the receipt.

DAG structure is advisory, not enforced by the kernel. Recursive parent validation is the consumer's responsibility.

## Lexical Constitution

Convention packs define the vocabulary under which tools are classified. Packs are ordered; later packs override earlier ones on dimension collision. This order is semantics.

`active_packs` in the receipt is a tuple of `PackRef(name, version, hash)` in precedence order. The receipt binds the measurement to the lexical constitution under which it was taken.

Pack hash is SHA-256 of the parsed canonical JSON (not raw YAML bytes), ensuring format-independent identity.

## Epistemic Provenance

`WitnessBasis(declared, inferred, unknown)` is **caller-attested**. The kernel records it; it does not compute it. The caller (typically `BullaGuard` or an inference pipeline) is responsible for honest attestation.

**Derivation rule**: When `witness_basis` is provided, `unknown_dimensions` is derived from `witness_basis.unknown`. The explicit `unknown_dimensions` parameter is a fallback for non-attested cases. This prevents lying receipts.

Invariant: `witness_basis is not None` implies `receipt.unknown_dimensions == witness_basis.unknown`.

## Verification

**`verify_receipt_consistency(receipt, comp, diag)`**: Checks composition hash, diagnostic hash, fee, blind spots count, bridges required, and basis/unknown agreement. Requires kernel objects.

**`verify_receipt_integrity(receipt_dict)`**: Self-contained tamper detection. Reconstructs the hash input from a serialized dict and compares to the claimed `receipt_hash`. No kernel required. The `to_dict()` round-trip is the verification path.

## Hierarchical Fee Decomposition

**Law**: For any partition of tools into disjoint groups, the coherence fee decomposes as:

```
fee(G) = sum(fee(G_i)) + boundary_fee
```

where `boundary_fee = rho_full - rho_obs >= 0` is the rank contribution of cross-partition edges modulo internal edges, computed independently for full and observable coboundary matrices.

**Non-negativity**: The column-projection from full to observable fields preserves linear independence of cross-partition rows modulo internal rows. Hence `rho_full >= rho_obs` and `boundary_fee >= 0`.

**Vanishing condition**: `boundary_fee = 0` when every cross-partition edge dimension has both endpoint fields in the respective tools' observable schemas.

**Tower Law**: For a partition refined by sub-partitioning each group G_i via P_i: `bf(refined) = bf(coarse) + sum(bf(P_i))`. The boundary fee is additive across levels of hierarchy. Proof: apply the decomposition theorem at both levels; local fees cancel.

**Monotonicity**: Since `bf(P_i) >= 0`, refining a partition can only increase the boundary fee. The boundary fee defines a monotone function on the refinement lattice: 0 at the trivial partition, `total_fee` at singletons. Operationally: every level of delegation adds non-negative hidden cost.

**Interpretation**: `boundary_fee` counts convention dimensions hidden at partition boundaries — blind spots invisible at every level of a hierarchy that appear only in the flat expansion. This is the coherence cost of delegation without disclosure.

**Non-valuation**: The boundary fee is monotone but NOT a valuation on the partition lattice. For the A->B->C chain with P={AB,C} and Q={A,BC}: `bf(P) + bf(Q) = 2` but `bf(P^Q) + bf(P v Q) = 1`. The same hidden convention at B causes boundary fee in both partitions; resolving it once (in the discrete partition) suffices.

**Minimum Disclosure Set**: `minimum_disclosure_set(comp)` returns the smallest set of `(tool, field)` pairs whose disclosure eliminates the coherence fee. The cardinality always equals the fee — it is a basis for the quotient space of the full coboundary column space modulo the observable column space. Disclosures subsume bridges: `len(bridges) >= 2 * len(disclosures)` across all compositions.

**Non-submodularity** (corrected scope, Sprint 0.2 audit, 2026-04-10): The boundary fee `bf` produces 4,061 strict-inequality violations of `bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)` out of 635,095 partition pairs across 10,000 random compositions, with maximum violation magnitude 3, under the producing script `bulla/scripts/adversarial_submodularity_survey.py` at seed=42. This number is correctly computed (exact `Fraction` arithmetic, bit-exact reproduction verified 2026-04-10), but the Sprint 0.2 code-inspection audit (`papers/hierarchical-fee/submodularity_audit.md`) found that **the script tests `bf` on a specific sub-domain of the partition lattice that is adversarially misaligned with realistic compositions**: (1) the partition sampler `lines 111-152` enumerates only binary partitions for `(P, Q)`, while the lattice meets and joins it computes can have any arity, producing an asymmetric `(binary, binary, non-binary, non-binary)` test rather than a uniform-arity submodularity test; (2) the composition generator `lines 43-79` introduces a freshly named convention dimension on every edge (`d{DIM_COUNTER}` at line 73), so no two edges in the entire 10K corpus share a dimension name — the opposite extreme from real MCP compositions like `filesystem+github` where `path_convention_match` appears on 13 edges. The individual `rho_full` and `rho_obs` functions remain submodular (matroid rank on row sets), and their difference `bf = rho_full - rho_obs` is non-submodular *on this sub-domain with this generator*. Whether `bf` is submodular on (a) the full partition lattice rather than the binary-pair sub-domain, or (b) compositions with shared dimension names across edges, is **untested** by this script and is the subject of pre-registered conjectures SA-1 and SA-2 in `papers/hierarchical-fee/submodularity_audit.md` §4, scheduled for Sprint B.3. The 9 original bundled compositions happen to satisfy submodularity; whether this is a topological accident or a general property of realistic compositions is also a Sprint B.3 question. **The honest version of this claim is "bf is non-submodular on the binary-pair sub-domain with the fresh-dimension-per-edge generator," not "bf is non-submodular on the partition lattice."**

**Conditional Diagnosis**: For partial compositions with open ports, placeholder tools with empty observable schemas produce worst-case fee estimates. Boundary obligations — fields the placeholder must expose — are read off the blind spots on placeholder edges.

**Online Resolution Protocol**: The full conditional loop is:
1. `conditional_diagnose(partial_comp, open_ports)` → obligations
2. Candidate tool arrives → `satisfies_obligations(candidate, obligations)` → pass/fail
3. `resolve_conditional(cond, {placeholder: candidate})` → `Resolution` with `resolved_fee`, `fee_delta`, `met_obligations`, `remaining_obligations`
4. If `resolved_fee > 0`: `minimum_disclosure_set(resolved_comp)` → prescribe remaining fixes

`resolve_conditional` supports partial resolution: resolve some placeholders while leaving others. `fee_delta` is always non-negative (replacing a placeholder with a real tool can only improve the fee).

**Structural vs Epistemic Unknown**: Open ports in partial compositions create **structural unknowns** (distinct from **epistemic unknowns** from classifier uncertainty). Structural unknowns count against `structural_unknowns` in the conditional diagnostic, not against `max_unknown` in policy evaluation.

**Trace Gap (Closed)**: The Frobenius trace gap `trace(L_full) - trace(L_obs) = ||delta_full||_F^2 - ||delta_obs||_F^2` equals the total count of hidden-endpoint instances across blind spots: `sum(from_hidden + to_hidden for each blind spot)`. This is a weighted blind-spot count derivable from the existing diagnostic, not a genuine spectral refinement: it can be positive when the fee is zero (hidden columns in the span of observable columns). A continuous spectral refinement requires the eigenvalue spectrum of the sheaf Laplacian, deferred to future work.

## MCP Surface: Prescriptive Witness

`bulla.witness` always returns `disclosure_set` — a list of `[tool_name, field_name]` pairs representing the minimum disclosure set. This makes every witness call prescriptive: the agent knows not just the fee, but the exact fields to fix.

When the optional `partition` parameter is provided (array of arrays of tool name strings), the output includes a `decomposition` field with `total_fee`, `local_fees`, `boundary_fee`, `rho_obs`, `rho_full`, `boundary_edges`. The decomposition field is absent when partition is not provided, preserving backward compatibility.

## CLI Surface: `bulla gauge`

`bulla gauge` is the live-server/manifest analog of `bulla check`. Where `check` operates on hand-authored YAML compositions and enforces CI gates, `gauge` operates on live MCP servers or manifest JSON files (the `tools/list` response) and produces prescriptive output: coherence fee, minimum disclosure set, and witness basis. It combines inference (`scan` + `infer`) and diagnosis (`diagnose`) into a single command for the 30-second adoption experience. CI gating flags (`--max-fee`, `--max-blind-spots`) mirror `check`'s exit-code semantics.

## CLI Surface: `bulla audit`

`bulla audit` reads an MCP configuration file (Cursor or Claude Desktop format), scans all configured servers in parallel, and diagnoses the combined cross-server composition. The unique output is the **cross-server risk decomposition**: using `decompose_fee()` with a partition-by-server, it separates the coherence fee into:

- **Intra-server fee**: blind spots within individual servers (sum of per-server local fees)
- **Boundary fee**: blind spots that only appear between independently-developed servers

The boundary fee quantifies conventions hidden at the seam between servers -- the exact gap that no individual server can detect on its own. This is the direct empirical instantiation of the hierarchical fee non-additivity theorem.

**`--manifests DIR` flag** (v0.21.0): Loads pre-captured manifest JSON files from a directory instead of scanning live servers. Each `*.json` file in the directory is treated as one server's `tools/list` response, with the filename stem as the server name. This enables deterministic CI without live server dependencies. Cannot be combined with a config file argument.

Auto-detection searches `.cursor/mcp.json` (project), `~/.cursor/mcp.json` (user), and Claude Desktop config (macOS). Only stdio-transport servers are scanned; HTTP/SSE entries are skipped with a warning. Failed servers are reported but do not block diagnosis of successful ones (`--skip-failed` default).

### Programmatic API: `BullaGuard.from_tools_list()`

`from_tools_list(tools, *, name="composition")` is the public entry point for building a guard from an in-memory list of MCP tool dicts (the `tools/list` response). This replaces direct use of the private `_composition_from_mcp_tools` helper. Used by `bulla audit` and recommended for any programmatic multi-server composition workflow.

### Server-name prefixing convention

In audit mode, tool names are prefixed with their originating server name using `__` as separator (e.g., `filesystem__read_file`, `github__search_repositories`). This convention:

- Encodes the tool-to-server mapping directly into tool names, surviving any reordering or filtering
- Makes audit output self-documenting (the server origin is visible in every tool reference)
- Enables `decompose_fee` partition derivation from `comp.tools` by splitting on `__`

The `__` prefix is applied only in audit mode; single-server paths (`bulla gauge`, `bulla scan`) do not prefix tool names.

### SARIF output

`bulla audit --format sarif` produces SARIF v2.1.0 JSON with blind spots as `bulla/blind-spot` results and bridge recommendations as `bulla/bridge-recommendation` results, each tied to the MCP config file as the artifact location. This enables direct integration with GitHub Code Scanning.

### GitHub Action (v0.21.0)

The `jkomkov/bulla` GitHub Action supports two modes via the `mode` input:

- **`check`** (default): Analyzes composition YAML files. Backward compatible with v1.
- **`audit`**: Scans MCP manifests (via `manifests-dir`) or live servers (via `mcp-config`). Outputs `coherence-fee` and `boundary-fee` as action outputs. SARIF upload to Code Scanning is supported in both modes.

See `examples/github-action/` for workflow templates and documentation.

## SDK Surface (v0.32.0)

`compose()` and `compose_multi()` are the programmatic entry points for agent framework integration. Each returns a `ComposeResult(receipt, diagnostic, decomposition)`.

### `compose(tools, *, policy, chain, name) -> ComposeResult`

Single-server composition. Accepts a list of MCP tool dicts (the `tools/list` response). Internally:

1. Builds a `BullaGuard` from the tool list.
2. Runs `diagnose()`.
3. If `chain` is provided: extracts `inline_dimensions`, `parent_receipt_hashes`, and `boundary_obligations` from the chain receipt dict.
4. Auto-computes `unmet_obligations`: if the chain provides `boundary_obligations`, calls `check_obligations()` and sets `unmet_obligations = len(unmet)`. The caller never passes obligation counts.
5. Calls `witness()` with all extracted fields + policy.
6. Returns `ComposeResult` with `decomposition=None`.

No guided discovery, no LLM calls. Pure structural diagnosis + receipt.

### `compose_multi(server_tools, *, policy, chain) -> ComposeResult`

Multi-server composition with partition decomposition. Each key in `server_tools` is a server name; its value is the `tools/list` response. Tool names are prefixed with `server_name__` to avoid collisions (same convention as `bulla audit`).

Additionally:
- Computes `decompose_fee()` with partition-by-server.
- Extracts `boundary_obligations_from_decomposition()`.
- Auto-computes `unmet_obligations` from boundary obligations.
- If `chain` provides `inline_dimensions`, calls `detect_contradictions()` and embeds any results in the receipt. Zero LLM cost.
- Returns `ComposeResult` with `decomposition` populated.

### `ComposeResult`

Frozen dataclass: `receipt` (WitnessReceipt), `diagnostic` (Diagnostic), `decomposition` (FeeDecomposition | None). The calibration partner needs `diagnostic.coherence_fee` for time-series tracking and `decomposition.boundary_fee` for cross-server analysis.

## Discovery Engine (v0.22.0)

`bulla discover` is a pure function from tool schemas to micro-pack YAML files. The LLM call is the only external dependency, isolated behind an adapter interface. The kernel never sees the LLM.

### Micro-Pack Format

A micro-pack is a standard pack YAML with two optional fields per dimension:

- **`refines`** (string or null): Parent dimension name for degradation hierarchy (Dublin Core Dumb-Down Principle). Example: `entity_namespace` refines `id_offset`.
- **`provenance`** (dict): Metadata for agent-invented dimensions: `source`, `confidence`, `source_tools`, `boundary`.

Micro-packs are loaded by `load_pack_stack()` identically to any other pack. The kernel is dimension-agnostic — it sees pack YAML, same as always.

### Validation

`validate_pack(parsed: dict) -> list[str]` checks:
- Required: `pack_name`, `dimensions` (at least one)
- Per dimension: `description` required, at least one of `field_patterns` / `description_keywords`
- Type checks for optional fields: `refines` (string/null), `provenance` (dict), `known_values` (list)

CLI: `bulla pack validate FILE` exits 0 on valid, 1 on invalid with error details.

### LLM Adapter Interface

```python
class DiscoverAdapter(Protocol):
    def complete(self, prompt: str) -> str: ...
```

Implementations: `OpenAIAdapter`, `AnthropicAdapter`, `MockAdapter`. Real adapters are optional dependencies (`pip install bulla[discover]`). `get_adapter(provider="auto")` auto-detects from environment variables.

### Prompt Architecture (v0.1)

The prompt uses rigid `---BEGIN_PACK---` / `---END_PACK---` delimiters. Key design decisions:
- Refinement allowed: "Do not duplicate, but you MAY propose refinements"
- Sparse schema acknowledgment: "Reason from field names, types, and cross-tool patterns"
- Existing dimensions listed to prevent duplication

### Discovery Engine

`discover_dimensions(tool_schemas, *, adapter, existing_packs) -> DiscoveryResult`

Returns `DiscoveryResult` with `.pack` (parsed dict), `.raw_response` (full LLM output), `.prompt` (sent prompt), `.errors` (validation failures), `.valid` (bool), `.n_dimensions` (int).

### CLI Surface: `bulla discover`

```
bulla discover --manifests DIR -o FILE [--provider openai|anthropic|auto] [--pack EXTRA.yaml]
```

Reads tool schemas from manifest directory, calls the discovery engine, writes micro-pack YAML and raw LLM response (`.raw.txt`).

The full loop: `bulla discover --manifests DIR -o found.yaml && bulla audit --manifests DIR --pack found.yaml`

### SCPI Readiness

Sprint 22 establishes the deposit format (micro-packs). The SCPI coordination loop is not yet closed: receipts record discovered packs via `active_packs` (PackRef with hash), not inline definitions. Embedding micro-pack contents in receipts is deferred to a future sprint.

## Classifier Architecture (v0.21.0)

The multi-signal classifier uses four independent signal sources:

1. **Field name pattern matching**: Regex patterns from `_CORE_NAME_PATTERNS` plus taxonomy-compiled patterns from pack `field_patterns`. Includes negative pattern exclusions (`_NEGATIVE_PATTERNS`) and type-aware filtering (string-typed `*_id` excluded from `id_offset`).
2. **Tool-level description keywords**: Phrases in the tool's top-level `description`, matched against pack `description_keywords` loaded dynamically from the merged taxonomy.
3. **JSON Schema structural signals**: `format`, `type+range`, `enum`, `pattern` metadata from the field's schema definition.
4. **Per-field description keywords**: Phrases in individual field `description` attributes, matched against the same pack keywords. Source type `field_description` (weak signal alone, promotes to `declared` with corroboration).

Confidence tiers: `declared` (2+ source types agree), `inferred` (1 strong signal: name, schema_format, schema_range, schema_pattern), `unknown` (weak signal only). Domain hints can promote `unknown` → `inferred`.

### `_description` Pseudo-Field Suppression (v0.21.0)

When signal source #2 (tool-level description keywords) matches but no real schema field is involved, the classifier creates an `InferredDimension` with `field_name="_description"`. This is a genuine signal (the tool *mentions* the convention), but it is not a concrete field that an agent interacts with.

**Edge suppression rule**: In `_find_shared_dimensions()`, edges where either endpoint has `field_name == "_description"` are suppressed. This prevents inflated blind spot counts from tool-description-only matches.

**Witness basis preservation**: `_description` signals are still counted in the `WitnessBasis` totals (`declared`, `inferred`, or `unknown` depending on confidence). The signal is recorded; only the edge creation is suppressed.

**Observable schema invariant**: `_description` is never a real field in `internal_state` or `observable_schema`. The suppression affects only the edge graph, not the coboundary matrix structure. Fee computation is unaffected.

### Convention Dimensions (base pack v0.1.0)

11 dimensions: `date_format`, `rate_scale`, `amount_unit`, `score_range`, `id_offset`, `precision`, `encoding`, `timezone`, `null_handling`, `line_ending`, `path_convention`.

`path_convention` (added v0.20.0) detects path reference space mismatches (absolute local vs. repository-relative vs. URI). This dimension creates cross-server edges between filesystem and code hosting tools, producing nonzero boundary fees.

## Coordination Loop (v0.23.0)

### `bulla audit --discover --receipt --chain`

The coordination loop collapses discovery, auditing, and receipt chaining into composable CLI flags:

| Flags | Behavior |
|---|---|
| (neither) | Standard audit, no discovery, no chaining |
| `--discover` only | Scan + discover + audit with enriched vocabulary. No receipt unless `--receipt` also given |
| `--chain prior.json` only | Load prior receipt's vocabulary, audit with inherited dimensions. No LLM call, no cost |
| `--discover --chain prior.json` | Full loop: inherit prior vocabulary + discover new + audit + chain receipt |

The `--chain` without `--discover` case is the CI adoption pattern: a team lead runs `--discover` once, produces a receipt, and the CI pipeline uses `--chain receipt.json` on every PR. Deterministic, no API key needed.

### `WitnessReceipt.inline_dimensions`

Optional field (v0.23.0). When not None, embeds the discovered pack content directly in the receipt. Agents receiving a chained receipt can reconstruct the vocabulary without the original YAML file.

**Backward compatibility**: `inline_dimensions` is included in `_hash_input()` and `to_dict()` **only when not None**. Pre-v0.23.0 receipts (which lack this field) produce identical hashes when verified by v0.23.0 code. This is enforced by test.

### `WitnessBasis.discovered`

Integer count (v0.23.0, default 0). Distinguishes dimensions from LLM-discovered micro-packs vs base-pack inferred dimensions. A dimension is counted as `discovered` when it belongs to a pack other than the base pack. Included in `to_dict()` only when non-zero for backward compatibility.

### Most-Specific-Dimension-Wins (v0.23.0)

When a field matches both a child dimension (from a micro-pack, via `refines`) and its parent dimension (from the base pack), the classifier returns only the child. `classify_field_by_name()` collects all pattern matches, then applies specificity deduplication via a cached `_refines_map` (child -> parent). The common case (single match) has zero overhead.

## Vocabulary Merge (v0.24.0)

### `bulla merge`

`bulla merge` performs vocabulary union from multiple receipts. It does NOT audit -- that is `bulla audit --chain`. Separation of concerns: merge handles vocabulary, audit handles measurement.

```
bulla merge receipt_a.json receipt_c.json --receipt merged.json
bulla audit --manifests DIR --chain merged.json --receipt audited.json
```

**Argument order IS precedence order**: `bulla merge base.json override.json` means override wins on dimension name collision.

### `merge_receipt_vocabularies(receipts)`

Core function in `bulla.merge`. Accepts a list of receipt dicts, returns `(merged_vocab_or_None, overlap_reports)`. Deep-copies all input data to prevent mutation.

### Dimension Overlap Detection

Overlap = non-empty intersection of `field_patterns` glob sets between dimensions from different source receipts. Same-name dimensions from different receipts are trivially overlapping. Overlap is purely informational -- it does not affect the merge (which is union with precedence). The alternative ("do they match the same actual fields on a tool set?") requires tool schemas and belongs in `bulla audit`.

### Merge Receipts

A merge receipt is a vocabulary-only DAG node with no composition or diagnostic backing. Its `composition_hash` is the sentinel string `"no_composition"` and its `diagnostic_hash` is `"no_diagnostic"`. These cannot be confused with real SHA-256 hashes. A merge receipt carries `inline_dimensions` (the merged vocabulary) and `parent_receipt_hashes` (the source receipts), but its `fee`, `blind_spots_count`, and `bridges_required` are all zero. To obtain a diagnostic receipt with the merged vocabulary, chain a `bulla audit --chain merged.json` after the merge.

### No Convergence Fee

The coherence fee is the universal measure. Vocabulary convergence is measured by comparing coherence fees under different pack configurations: `fee(merged) vs fee(A_alone) vs fee(B_alone)`. This is a delta of the existing fee, not a new concept.

## Boundary Obligations (v0.25.0)

**Normative semantic (one sentence):** A boundary obligation asserts that the coherence fee at a partition boundary cannot decrease unless a downstream composition exposes the specified dimension-field pair in its observable schema.

This makes obligations falsifiable: satisfy the obligation, re-diagnose, fee must drop. If it does not, the obligation was wrong.

### `BoundaryObligation` dataclass

Fields: `placeholder_tool`, `dimension`, `field`, `source_edge` (default empty string).

`placeholder_tool` has two production contexts:
- From `conditional_diagnose`: the placeholder tool name inserted for open ports (e.g. `"__placeholder_0"`).
- From `boundary_obligations_from_decomposition`: the server group name at the partition boundary (e.g. `"github"`).

`source_edge` is informational provenance (e.g. `"storage__read_file -> api__list_items"`), not semantic identity. The obligation is the same obligation regardless of which edge surfaced it.

### `boundary_obligations_from_decomposition(comp, partition, diag)`

Extracts obligations from cross-partition blind spots in a full-composition diagnostic. For each blind spot on a cross-partition edge where a field is hidden, produces a `BoundaryObligation` with `placeholder_tool` = server group name (derived from `__` prefix convention).

Deduplicates on `(placeholder_tool, dimension, field)`, keeping the first `source_edge` encountered.

This does NOT use `conditional_diagnose` (which requires explicit open ports). It uses the existing decomposition + blind spot data from a full-composition diagnostic.

### `check_obligations(obligations, comp)` — Three-Way Classification

Returns `(met, unmet, irrelevant)`:
- **met**: field is in some tool's `observable_schema`
- **unmet**: field is in some tool's `internal_state` but NOT in `observable_schema`
- **irrelevant**: no tool in the composition has the field at all

v0.25.0: three-way classification. Future: add `contradictory` when multi-parent DAG merges produce conflicting obligations on the same field.

### Propagation Rule

A receipt's `boundary_obligations` = unmet obligations from parent receipt(s) + new obligations from own boundary decomposition. This is the accumulation principle applied across the chain: parent obligations that the current agent cannot satisfy are carried forward alongside any new obligations the current agent's own boundary produces.

### Obligation Merge = Accumulation, NOT Precedence

Vocabulary merges by precedence (later receipt's definition wins on name collision). Obligations merge by **accumulation**: all parent obligations are carried forward; a successor must satisfy each independently. Duplicates (same `placeholder_tool`, `dimension`, `field`) are deduplicated.

`merge_receipt_obligations(receipts)` in `bulla.merge` implements this additive accumulation across all parent receipts.

### Obligation on Receipt

`WitnessReceipt.boundary_obligations` is an optional field (`tuple[BoundaryObligation, ...] | None`). Conditionally included in `_hash_input()` and `to_dict()` only when not None, following the established pattern for `parent_receipt_hashes` and `inline_dimensions`.

## Bridge-Guided Discovery (v0.26.0)

**Normative semantic:** Guided discovery is a hypothesis test on boundary obligations. For each obligation, the LLM evaluates whether the obligated field is observable in the target tool, returning a verdict: CONFIRMED, DENIED, or UNCERTAIN.

### Collective Repair Invariant

If at least one obligation is confirmed and repaired (field added to `observable_schema`), `fee(repaired) < fee(original)`. The reduction is at least 1 but may be less than the number of confirmed probes. This is because overlapping obligations may share an underlying linear dependency in the coboundary matrix (two blind spots on the same row). All confirmed repairs are applied together, then the composition is re-diagnosed once.

**This invariant is a theorem, not an assumption.** Each confirmed repair adds a nonzero column to the observable coboundary matrix. At least one such column must be linearly independent of existing observable columns (since it was generating a blind spot). Therefore `rank_obs` increases by at least 1, and `fee = rank_full - rank_obs` decreases by at least 1.

### `ObligationVerdict` Enum

- **CONFIRMED**: The field IS observable in the target tool's output.
- **DENIED**: The field is hidden, internal-only, or absent from the tool.
- **UNCERTAIN**: The LLM cannot determine observability from the schema alone.

### `ProbeResult`

Pairs a `BoundaryObligation` with its `ObligationVerdict`, plus `evidence` (LLM reasoning) and `convention_value` (populated when CONFIRMED, e.g. `"zero_based"`).

### `guided_discover(obligations, tool_schemas, adapter, pack_context)`

Batched single-call probing: constructs one prompt with numbered verdict delimiters (`---BEGIN_VERDICT_N---` / `---END_VERDICT_N---`), sends one LLM call, parses N verdicts. Tool matching uses `placeholder_tool` as server group prefix and `source_edge` for specific tool targeting.

### `repair_composition(comp, confirmed_probes)`

Pure function: returns a new `Composition` with confirmed fields made observable. Immutable, idempotent. Does not mutate the original composition.

### `repair_step(comp, partition, tool_schemas, adapter, ...)`

One-round coordination loop: diagnose -> compute obligations -> guided discover -> repair -> re-diagnose. Returns `RepairResult` with before/after fees, all probes, and remaining obligations. `coordination_step()` wraps this in a convergence loop.

### Iterative Convergence Loop (v0.27.0)

`coordination_step()` wraps `repair_step()` in a bounded loop with three termination conditions:

1. **`fee_zero`**: The repaired composition has `coherence_fee == 0`. Full resolution achieved.
2. **`fixpoint`**: A round produced `fee_delta == 0` — no progress was made (all remaining obligations are DENIED or UNCERTAIN with no new context).
3. **`max_rounds`**: The round budget was exhausted (default 5).

**Obligation triage between rounds**: Only UNCERTAIN obligations carry forward for re-probing. CONFIRMED obligations are excluded (already repaired). DENIED obligations are excluded (won't change). `repair_step` independently re-derives obligations from the repaired composition each round, so structurally persistent obligations are naturally re-encountered even if excluded from carry-forward.

**`ConvergenceResult`**: Contains the full round history (`tuple[RepairResult, ...]`), convergence status, final composition, final fee, aggregate statistics (`total_confirmed`, `total_denied`, `total_uncertain`), and `termination_reason`.

**Module structure (v0.27.0)**: `repair_composition`, `repair_step`, `RepairResult`, `coordination_step`, and `ConvergenceResult` live in `bulla.repair`. The measurement layer (`diagnostic.py`) has zero imports from `repair.py`, preserving the anti-reflexivity law. All symbols are re-exported from the `bulla` package.

## Convention Value Extraction (v0.28.0)

`extract_pack_from_probes(probes, composition_hash)` is a pure function from confirmed probe results to micro-pack dict. It transforms ephemeral `ProbeResult.convention_value` strings into persistent dimensions embedded in receipts, closing the loop between guided discovery and the pack system.

### Pack Generation Semantics

Only CONFIRMED probes with non-empty `convention_value` produce dimension entries. The function is deliberately narrow:

- **Exact-match field_patterns**: `field_patterns: ["offset"]`, not glob patterns. The LLM confirmed a specific field, not a pattern family. `provenance.source: "guided_discovery"` signals narrow observation.
- **Deduplication**: Multiple probes on the same dimension merge: `known_values` collects all distinct values, `source_tools` collects all tool names, `field_patterns` collects all fields. Same-value probes deduplicate (no `["zero_based", "zero_based"]`).
- **Multi-value collection**: Two probes on the same dimension with different `convention_value`s both survive in `known_values`. This is the seed for Sprint 30's contradiction detection.
- **Validation**: Output is validated with `validate_pack()` before return. Invalid output raises `ValueError`.

### `ConvergenceResult.discovered_pack`

A `@property` on `ConvergenceResult` that collects all probes across all rounds and calls `extract_pack_from_probes`. Since `ConvergenceResult` is `frozen=True`, this is a derived property (not a stored field).

### `BoundaryObligation.expected_value`

New field (default `""`, backward-compatible). When a parent receipt carries obligations with confirmed values, the downstream agent receives `expected_value` on each obligation. Sprint 29 will use this to detect disagreements between expected and actual convention values.

The field is included in `to_dict()` only when non-empty. It is NOT yet propagated through `merge_receipt_obligations` — the field has no machine consumer until Sprint 30 (contradiction detection).

### Receipt Integration

When `bulla audit --converge` or `--guided-discover` produces confirmed probes with convention values, the discovered pack is embedded as `inline_dimensions` on the receipt. Merge precedence: newly discovered dimensions win over existing inline dimensions from `--chain` (later-wins semantics, consistent with `merge_receipt_vocabularies`).

## Contradiction Detection (v0.30.0)

`detect_contradictions(discovered_pack)` is a pure function from pack dict to `tuple[ContradictionReport, ...]`. Any dimension with `len(known_values) > 1` produces a `ContradictionReport` with `severity=ContradictionSeverity.MISMATCH`.

### `ContradictionSeverity`

Enum following the `ObligationVerdict` pattern. Single member `MISMATCH` (2+ distinct values for the same dimension). `CONFLICT` (logically incompatible per pack definition) reserved for when packs define explicit compatibility rules.

### `ContradictionReport`

Frozen dataclass: `dimension` (str), `values` (tuple[str, ...]), `sources` (tuple[str, ...]), `severity` (ContradictionSeverity). `values` and `sources` are always sorted alphabetically — canonical ordering is enforced at construction time in `detect_contradictions()`, not at hash time. `to_dict()` / `from_dict()` support JSON round-trip.

### Detection Surfaces

- **Intra-run**: `detect_contradictions(pack)` — a single `coordination_step()` discovers conflicting values from different server groups on the same dimension.
- **Intra-agent**: `detect_expected_value_contradictions(probes)` — a probe confirms a `convention_value` that differs from its obligation's `expected_value` (inherited from a parent chain receipt).
- **Inter-agent**: `detect_contradictions_across(*convergence_results)` — merges `discovered_pack` from multiple convergence results (union of known_values and source_tools per dimension), then delegates to `detect_contradictions()`.

### Receipt Integration

`WitnessReceipt.contradictions: tuple[ContradictionReport, ...] | None`. Included in `_hash_input()` only when not None (conditional-include pattern). Pre-v0.30.0 receipts verify correctly because their hash was computed without the `contradictions` key.

### Contradiction Inheritance Across the Receipt DAG

**Normative rule: re-derivation, not union.** When agent B chains from agent A's receipt, B's contradictions are derived from B's `inline_dimensions` via `detect_contradictions()`. B does NOT union A's `contradictions` field with its own. The `contradictions` field on a receipt is a materialized view of the vocabulary, not an independent data source. The source of truth is `inline_dimensions`.

This follows from the structure of `detect_contradictions()`: it is a pure, deterministic function from vocabulary to contradiction set. Given the same `inline_dimensions`, any agent produces the same contradictions. Inheriting a parent's contradiction list and merging it with a locally derived list would either (a) double-count identical contradictions, requiring deduplication, or (b) preserve stale contradictions from a vocabulary that the child has since extended (e.g., the child discovered a third value on a dimension, changing a 2-way mismatch into a 3-way mismatch).

Three cases confirm the rule:

1. **B's vocabulary = A's vocabulary** (pure chain, no new discovery): re-derivation produces identical contradictions. Union adds nothing.
2. **B's vocabulary extends A's** (B discovered new dimensions or values): re-derivation captures everything A found plus new contradictions. Union would miss updated contradiction reports on dimensions where B added a third value.
3. **B's vocabulary is disjoint from A's** (B has a different vocabulary): A's contradictions are irrelevant to B's composition. Inheriting them would pollute B's receipt with contradictions from dimensions B does not use.

The SDK implements this rule: `compose_multi()` calls `detect_contradictions()` on the chain's `inline_dimensions` and does not read the chain's `contradictions` field.

## Structural Contradictions (v0.34.0)

Structural contradictions are a different failure class from convention contradictions above. Convention contradictions detect conflicting *values* for the same dimension (e.g., "zero_based" vs "one_based" for `id_offset`). Structural contradictions detect *schema incompatibilities* between fields that are visibly connected — same-named fields with different types, enum values, formats, or ranges.

### `SchemaContradiction`

Frozen dataclass: `field_a`, `field_b`, `tool_a`, `tool_b`, `mismatch_type` (one of `"type"`, `"format"`, `"enum"`, `"range"`, `"pattern"`), `severity` (float 0.0–1.0), `details` (str). `to_dict()` / `from_dict()` support JSON round-trip.

### `StructuralDiagnostic`

Contains `overlaps` (all findings: agreements + contradictions + homonyms + synonyms), `contradictions` (the diagnostic subset), `n_overlapping_fields`, `n_contradicted`, `contradiction_score` (sum of severities, rounded).

### Receipt Fields

`WitnessReceipt.structural_contradictions: tuple[SchemaContradiction, ...] | None`. `WitnessReceipt.contradiction_score: int` (default 0). Both are conditionally included in `_hash_input()` (structural_contradictions only when not None; contradiction_score only when > 0).

### Disposition Effect

Any nonzero `contradiction_score` triggers `PROCEED_WITH_CAUTION` (rule 8 in disposition priority). The `max_structural_contradictions` policy threshold (rule 5) controls the escalation to `refuse_pending_disclosure`. This is independent of convention contradiction enforcement (`max_contradictions`).

### `expected_value` Hydration

When `--chain` is provided, `BoundaryObligation.expected_value` is hydrated from the chain receipt's `inline_dimensions`: for each obligation, if the dimension exists in the chain's inline_dimensions, the first `known_value` is used as the expected_value. This resolves the Sprint 28/29 TODO and gives `expected_value` its first machine consumer.

## `max_unknown` Definition

A convention dimension is **unknown** when it is relevant to the composition but could not be assigned a `declared` or `inferred` value under the active packs. `max_unknown` bounds the number of such dimensions a policy will tolerate before refusing.

## Future Directions

### Mathematical Framework: Coordination Cohomology

The Bulla protocol rests on a single mathematical structure that does not change across versions:

**Presheaf model.** Each tool `T` defines a presheaf section `F(T) = internal_state(T)`. The observable sub-presheaf `O(T) = observable_schema(T)` is the publicly visible fragment. A composition is a diagram of tools connected by semantic edges; the coboundary operator `δ: C^0 → C^1` encodes dimensional compatibility along edges.

**Obstruction cocycle.** The coherence fee `fee = rank(δ_full) - rank(δ_obs) = dim H^1(G; F/O)` measures the obstruction to extending observable sections to full sections. Each blind spot generates a cocycle in `H^1`. Fee = 0 iff every convention is either globally observable or globally irrelevant.

**Resolution sequence.** The protocol's sprint arc traces a sequence of sub-presheaf inclusions:

```
O_0  ⊂  O_1  ⊂  O_2  ⊂  ...  ⊂  F
```

where each `O_{k+1}` is obtained by making one or more obligated fields observable. The coherence fee is monotonically non-increasing along this sequence: `fee(O_{k+1}) ≤ fee(O_k)`. When a confirmed repair adds a column that is linearly independent modulo existing observable columns, the fee strictly decreases.

### Per-Sprint Thesis Statements

Each sprint adds one mathematical capability to the resolution sequence:

| Sprint | Version | Thesis |
|--------|---------|--------|
| **25** | v0.25.0 | **Obligation generation.** Boundary decomposition extracts generators of `H^1(G; F/O)` from cross-partition blind spots. Each obligation names a specific `(tool, dimension, field)` whose disclosure would eliminate one cocycle generator. |
| **26** | v0.26.0 | **Guided resolution.** LLM-directed probing evaluates whether each generator can be resolved (field is observable). Confirmed resolutions extend `O → O'` with `fee(O') < fee(O)` (collective invariant). |
| **27** | v0.27.0 | **Iterative convergence.** `coordination_step()` wraps `repair_step()` in a loop with three exit paths: `fee_zero`, `fixpoint`, `max_rounds`. Obligation triage carries forward only UNCERTAIN probes; DENIED and CONFIRMED are excluded. Convergence is guaranteed: fee is a non-negative integer that strictly decreases on each round with at least one confirmation. Module split: `repair.py` (coordination) separated from `diagnostic.py` (measurement), preserving anti-reflexivity. |
| **28** | v0.28.0 | **Convention value extraction.** `extract_pack_from_probes()` transforms confirmed probe convention values into persistent micro-pack dimensions embedded in receipts. `ConvergenceResult.discovered_pack` aggregates all rounds. `BoundaryObligation.expected_value` seeds contradiction detection. The presheaf section now carries semantic content -- not just structural observability. |
| **29** | v0.29.0 | **Canonical proof artifact.** Originally planned as contradiction detection; pivoted because the protocol was mature enough that the highest-leverage move was real-world proof, not another library feature. Two real MCP servers (filesystem + GitHub), one convention mismatch (absolute vs relative paths), full pipeline from measurement through guided discovery to receipted value extraction. Convention mismatch display flags multi-value dimensions. Pre-computed receipt is a checked-in cryptographic proof artifact. Contradiction detection deferred to Sprint 30. |
| **30** | v0.30.0 | **Contradiction detection.** `detect_contradictions(discovered_pack)` flags dimensions with 2+ distinct `known_values` as `ContradictionReport(severity=MISMATCH)`. `detect_expected_value_contradictions(probes)` detects intra-agent contradictions when a probe's `convention_value` differs from its obligation's `expected_value`. `detect_contradictions_across(*convergence_results)` merges packs from multiple runs. `WitnessReceipt.contradictions` embeds reports in the receipt hash (conditional-include, backward-compatible). `ContradictionSeverity` enum with single `MISMATCH` member follows the `ObligationVerdict` pattern. Values and sources sorted alphabetically at construction for canonical ordering. `--chain` now hydrates `expected_value` from inherited `inline_dimensions`. See `PROTOCOL-NOTE.md` for the theoretical framing. |
| **31** | v0.31.0 | **Policy enforcement.** `PolicyProfile` gains `max_unmet_obligations` and `max_contradictions` (both default `-1` = disabled). `_resolve_disposition()` adds two refuse rules (priority 3 and 4) gated on these thresholds. `witness()` accepts `unmet_obligations` and `contradiction_count` as caller-attested integers. `BullaGuard.enforce_policy()` collapses diagnose → disposition → receipt into one call. CLI: `--max-unmet` and `--max-contradictions` on `bulla audit` with exit-code semantics; `--max-fee` on `bulla check`. |
| **32** | v0.32.0 | **Compose SDK.** `compose()` and `compose_multi()` collapse diagnosis, obligation checking, contradiction detection, and receipt issuance into single function calls. `compose()` auto-computes `unmet_obligations` from chain obligations. `compose_multi()` auto-detects contradictions from chain `inline_dimensions`. `ComposeResult` bundles receipt + diagnostic + optional decomposition. `WitnessReceipt.unmet_obligations` field (conditional hash). `verify_receipt_consistency` fixed to pass obligation/contradiction counts. `enforce_policy()` extended with all pass-through receipt fields. License changed from MIT to BSL 1.1. |

### Convergence Properties

**Termination theorem (Sprint 27+).** The iterative repair loop `while fee > 0 and confirmed > 0` terminates in at most `fee_0` rounds, where `fee_0` is the initial coherence fee. Proof: fee is a non-negative integer. Each round with at least one confirmation strictly decreases it. Therefore the loop terminates.

**Fixpoint characterization.** The loop's fixpoint has two cases:
1. `fee = 0`: all conventions are observably coherent. The composition is fully resolved.
2. `fee > 0, confirmed = 0`: remaining obligations cannot be resolved by the current tool set. These are genuine coordination gaps requiring human intervention, tool replacement, or composition restructuring.

The protocol distinguishes these cases automatically. Case 1 produces `PROCEED`. Case 2 produces `REFUSE_PENDING_DISCLOSURE` with the remaining obligations enumerated.

### Dependency Structure

The capabilities compose linearly:

```
obligations (25) → guided discovery (26) → iterative repair (27)
                                         → value extraction (28)
                                         → canonical proof (29) [uses 25-28 on real data]
                   → contradiction detection (30) [uses expected_value from 28, pack from 28, receipt from 24]
                   → policy enforcement (31) [requires obligations from 25, contradictions from 30]
                   → SDK (32) [integrates all of 25-31]
```

Sprints 27 and 28 are independent (can proceed in parallel). Sprint 29 is the canonical proof artifact: it exercises the full 25-28 pipeline on real MCP server manifests. Sprint 30 adds contradiction detection using expected_value from Sprint 28 and pack structure from Sprint 28. Sprint 31 depends on obligations (25) and contradictions (30). Sprint 32 integrates everything: `compose()` and `compose_multi()` are thin entry points over the full 25-31 stack.

When a new sprint ships, update this section: move its thesis from future to present tense, and add any new convergence properties discovered during implementation.
