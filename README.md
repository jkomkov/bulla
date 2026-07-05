# bulla

Witness kernel for agentic compositions.

When AI agents compose tools into pipelines, implicit semantic assumptions (date formats, unit scales, path conventions) can silently produce wrong results. Schema validation passes, but the pipeline is broken. Bulla computes the **coherence fee**: the exact number of independent semantic dimensions that bilateral verification cannot detect.

**Zero heavy dependencies.** Only requires PyYAML. No numpy, no scipy, no LLM calls. Installs in under a second.

> **Naming**: **Glyph** is the open standard — the composition rule, the recomputable receipt format, and the convention registry ([glyphstandard.com](https://glyphstandard.com)). ***bulla*** (this repo) is its reference implementation. *SEAM* is the underlying theory ([paper](https://www.resagentica.com/papers/seam-paper.pdf)); the wider research program is [Res Agentica](https://www.resagentica.com).

## Try it now

```bash
pip install bulla
```

### 30-second quickstart: compose two compositions

The fastest way to see what Bulla does — a human-readable report of
exactly which fields to expose to make a composition safe:

```bash
# From a checkout of the bulla repo:
bulla compose examples/two-manifest-quickstart/example_fetch_memory_joint.yaml
```

Output:

```
  Witness rank (fee): 2  ⚠ refuse_pending_disclosure

  2 blind-spot dimensions forming 2 independent obstruction classes.

  To make this composition safe, expose 4 fields:

    1. tool `fetch`, field `encoding`
       Action: add `encoding` to fetch.observable_schema
       Bridges blind spot on edge: encoding_match
    ...

  Apply all bridges automatically:
    bulla bridge ... --output ..._bridged.yaml
```

See [examples/two-manifest-quickstart/README.md](https://github.com/jkomkov/bulla/tree/main/examples/two-manifest-quickstart#readme)
for the full walkthrough. `--format json` emits the same structured
`WitnessReceipt` as `bulla witness`.

### Sign it, log it, verify it — without trusting the operator

A diagnosis you must take someone's word for is a score; a diagnosis anyone can
re-derive is a **deed**. Every claim below is recomputable — run the commands,
don't take the README's word:

```bash
bulla key gen                                  # local ed25519 identity (did:key)
bulla certify examples/two-manifest-quickstart/example_fetch_memory_joint.yaml --sign
bulla registry append <certificate.json>       # append-only log (RFC 6962 Merkle)
bulla registry root                            # the root you anchor + gossip
bulla registry prove <index>                   # inclusion proof for one deed
bulla verify <certificate.json> --registry <url> --trusted-root <root>  # remote: pin the root
bulla registry anchor                          # timestamp the whole log (OpenTimestamps)
```

The trust rule is strict by design: an inclusion proof only counts against a root
you obtained **independently of the host** — your own log, a pinned root, or an
OTS-anchored checkpoint. A remote registry's bare claim about itself is classified
`host-asserted` and refused. What this buys: the record survives the operator
(append-only, no deletion), can't be backdated past its anchor, and omission is
caught because relying parties refuse the unlogged.

### Recourse gate: PROCEED / REFUSE on a deed

Where `bulla compose` *reports* the seam, the **recourse gate** *enforces* a decision on a
counterparty's signed, logged coherence deed — **PROCEED**, or **REFUSE** with a curable
refusal that names the cure. It gates on **type signals only**: coherence (`fee = 0`) +
authenticity + inclusion under a root you trust *independently of the host*. It does **not**
verify delivery — a coherent liar passes; performance bonding is roadmap.

```bash
# From a checkout, after `pip install -e .`:
examples/gate-quickstart.sh        # ~5s: PROCEED on a clean deed, REFUSE-with-cure on a seam
```

Then watch the gate catch a **lying host** (an equivocated registry root) and prevent a real
`git` breach — where the *same* convention disclosure that clears the coherence fee is what
fixes the execution:

```bash
python calibration/recourse_gate_closes_loop_git.py   # LOOP CLOSED
```

Point `bulla gate` at *your own* registry + composition (`bulla gate --help`); nothing is
hardcoded.

### Other entry points

```bash
# Audit your live MCP setup (Cursor, Claude Desktop, …)
bulla audit

# Explicit config path
bulla audit ~/.cursor/mcp.json

# CI gate: fail if any composition exceeds fee threshold
bulla audit --max-fee 3 --format json

# Deterministic audit from saved MCP manifests
bulla audit --manifests examples/canonical-demo/manifests/
```

`bulla audit` auto-detects your MCP configuration when possible, scans servers, and prints a short **receipt**: **boundary fee** first (cross-server seams), then within-server blind spots, then copy-paste next steps (`--max-fee`, `--format json`). If no config is found, stderr suggests a **`bulla scan …`** command you can run with zero setup.

## Bulla as MCP proxy — the safety co-pilot agents query

`bulla audit` and `bulla compose` analyze YAML *before* deployment. The
**live MCP proxy** sits between an agent and its MCP backends while
it runs:

```bash
bulla proxy --inject-prompt          # print the agent system prompt
bulla proxy --config servers.yaml    # spawn backends, listen on stdio
```

The proxy fronts N backend MCP servers as one logical server, namespaces
their tools as `server__tool`, AND injects five `bulla__*` meta-tools
the agent itself calls:

| Meta-tool | What it returns |
|---|---|
| `bulla__fee` | Current witness rank (incremental, no recompute) |
| `bulla__blind_spots` | Enumerated obstruction dimensions |
| `bulla__bridge` | Repair advice classified `value` (apply now) vs `schema` (manifest edit required) |
| `bulla__should_proceed` | Ternary verdict for a pending call: `safe` / `advise` / `refuse` |
| `bulla__why` | Aristotle stamp + Lean theorem name backing the recommendation |

The agent is the consumer. The system prompt at
[`agents/system_prompt_v1.md`](agents/system_prompt_v1.md) tells it
when and how to consult Bulla.

**Why this is different**: every other MCP-aware tool monitors
compositions for humans. The Bulla proxy is the participant the agent
itself queries — and `bulla__why` returns an Aristotle run hash plus
the Lean theorem (`disclosure_characterization`,
`sheaf_realization_characterization_via_cohomology`) that backs the
recommendation. No competitor can attach formally-verified provenance
to safety claims without an analogous formalization arc.

See [examples/live-mcp-proxy/README.md](https://github.com/jkomkov/bulla/tree/main/examples/live-mcp-proxy#readme)
for the runnable demo, telemetry walkthrough, and trust-ladder model
(`observe → advise → auto`). The MVP runs in OBSERVE mode only — it
never modifies agent traffic.

## The seam problem

Two MCP servers. One uses absolute paths (`/tmp/src/main.py`), the other uses repository-relative paths (`src/main.py`). Schema validation passes. The agent silently puts the file in the wrong place. Bulla catches this before execution.

**[See the canonical demo →](https://github.com/jkomkov/bulla/tree/main/examples/canonical-demo)** — frozen MCP manifests, real fee, walks through the bridge runtime.

## Calibration results

Tested across 10 real MCP servers (filesystem, github, notion, playwright, tavily, etc.) in 45 pairwise compositions. Labels are **annotation-derived** (schema-vs-convention), not execution-derived — so this is calibration on a labelled corpus, and real-traffic failure prediction remains open:

| Zone | Fee | P(mismatch) | Compositions |
|------|-----|-------------|--------------|
| **Safe** | 0 | 0% | 15 compositions, all clean |
| **Uncertain** | 1–3 | 0–33% | 12 compositions |
| **Unsafe** | 4+ | ~100% | 18 compositions, all confirmed |

On this corpus, fee=0 had **zero** annotated convention mismatches and fee≥4 concentrated the confirmed mismatches. The fee is computed from schemas alone — no execution required.

See [calibration data](https://github.com/jkomkov/bulla/blob/main/calibration/data/tier3/report/state-of-agent-coherence.md) for the full report.

## Python SDK

<!-- bulla-doc-skip: illustrative — fs_tools / gh_tools are your servers' tools/list payloads -->
```python
from bulla import compose_multi

result = compose_multi({
    "filesystem": fs_tools,
    "github": gh_tools,
})

print(result.diagnostic.coherence_fee)   # 30
print(result.receipt.disposition.value)  # "refuse_pending_disclosure"
print(result.decomposition.boundary_fee) # 1
```

`compose_multi()` returns a `ComposeResult` with the diagnostic, a tamper-evident `WitnessReceipt`, and a fee decomposition partitioned by server. For single-server diagnosis, use `compose()`.

## Architecture

Three layers, cleanly separated:

| Layer | Concern | Module |
|---|---|---|
| **Measurement** | Composition → Diagnostic (fee, blind spots, bridges) | `diagnostic.py` |
| **Binding** | Diagnostic → WitnessReceipt (content-addressable, tamper-evident) | `witness.py` |
| **Judgment** | Policy → Disposition (proceed / refuse / bridge) | `witness.py` |

The measurement layer has **zero imports** from the witness layer. Measurement does not know it is being witnessed.

## Commands

| Command | Purpose |
|---|---|
| `bulla audit` | Scan MCP config, diagnose cross-server coherence |
| `bulla gauge` | Diagnose a single MCP server or manifest |
| `bulla diagnose` | Full diagnostic from a composition YAML |
| `bulla compose` | Prescriptive report (natural-language fix instructions) |
| `bulla check` | CI gate with configurable thresholds |
| `bulla scan` | Scan live MCP servers (zero config) |
| `bulla witness` | Diagnose and emit WitnessReceipt as JSON |
| `bulla bridge` | Auto-bridge and emit patched YAML |
| `bulla translate` | Apply a typed runtime translator (`--dimension X --value V --to T`) |
| `bulla serve` | MCP stdio server |
| `bulla proxy` | Live MCP proxy — injects `bulla__*` meta-tools agents query at runtime |
| `bulla replay` | Replay a session trace with flow-level structural diagnosis (renamed from `bulla proxy`) |
| `bulla discover` | LLM-powered convention dimension discovery |
| `bulla import langgraph` | Parse a LangGraph workflow into a Bulla manifest |
| `bulla import crewai` | Parse a CrewAI crew/agent/task tree into a Bulla manifest |
| `bulla certify-update` | Semantic SemVer verdict (`delta_r`, update kind, bridge lower bound) between old/new compositions |

Output formats: `--format text` (default), `--format json`, `--format sarif`.

### Runtime translation, Session API, framework adapters (new in 0.37.0)

Three additions in 0.37.0. `bulla.translate` exposes typed runtime translators that produce a `WitnessReceipt` for every transformation. `bulla.Session` builds compositions tool-by-tool with rank-1 incremental updates. `bulla.LiveSession` extends Session with call tracing for MCP proxies. Native `bulla.langgraph` and `bulla.crewai` adapters round it out.

#### `bulla.translate`

Typed runtime value translation across conventions.

```python
from bulla import translate

result = translate("currency_code", value="USD", to_convention="iso-4217-numeric")
print(result.value)                          # "840"
print(result.evidence.from_convention)       # "iso-4217"
print(result.evidence.equivalence)           # "exact"
print(result.receipt.disposition.value)      # "proceed"
```

Five canonical translators ship registered: `currency_code`, `country_code`, `language_code`, `temporal_format`, `fhir_resource_type`. Restricted-pack values raise `TranslationUnavailable` rather than leaking through. Register your own via `@bulla.bridges.register`. CLI: `bulla translate --dimension currency_code --value USD --to iso-4217-numeric`.

#### `bulla.Session`

Long-lived composition built tool by tool.

```python
from bulla import Session, ToolSpec, Edge, SemanticDimension

s = Session()
# Both tools carry a `path`, but each also carries a `path_root` convention it
# does NOT expose at the seam (observable_schema omits it): filesystem speaks
# absolute paths, github repo-relative, and neither advertises which.
s.add_tool(ToolSpec("filesystem.read_file",
                    internal_state=("path", "path_root"), observable_schema=("path",)))
s.add_tool(ToolSpec("github.create_file",
                    internal_state=("path", "path_root"), observable_schema=("path",)))
s.add_edge(Edge("filesystem.read_file", "github.create_file",
                (SemanticDimension("path_root", "path_root", "path_root"),)))
print(s.fee)                # 1  — the hidden path_root convention the schemas can't see
receipt = s.diagnose()      # full WitnessReceipt
```

Every `add_tool`, `translate(...)`, and `checkpoint()` extends a chained receipt history. Incremental updates use rank-1 Schur complements; a 10,000-seed property test pins bitwise equality with full-rebuild `witness_gram`.

#### `bulla.LiveSession`

Online MCP composition proxy.

<!-- bulla-doc-skip: needs live MCP servers (fs_tools / gh_tools) -->
```python
from bulla import LiveSession

live = LiveSession(name="checkout")
live.add_server("filesystem", fs_tools)
live.add_server("github", gh_tools)
print(live.fee)             # equals compose_multi({fs, gh}).coherence_fee
live.record_call("github.create_file", inputs={...})
receipt = live.diagnose()
```

`add_server` returns `AddServerResult` with the per-server delta. `LiveSession.from_server_tools(...)` constructs from a single `dict[str, list[dict]]`.

#### Native LangGraph and CrewAI adapters

```bash
pip install bulla[langgraph]    # or bulla[crewai], bulla[all]
```

<!-- bulla-doc-skip: needs the [langgraph]/[crewai] extras and a live graph/crew -->
```python
from bulla.langgraph import bind, BullaCallbackHandler
from bulla.crewai     import bind as crew_bind, BullaCrewCallback

# LangGraph: snapshot a compiled or uncompiled StateGraph
session = bind(graph)
print(session.fee)

# CrewAI: walks crew.agents, crew.tasks, task.context, task.tools
session = crew_bind(crew)
```

Both `bind()` calls return a `bulla.Session` with a deterministic `composition_hash`. Order-independence is property-tested over 50 seeded random graph constructions. `BullaCallbackHandler` and `BullaCrewCallback` record live tool invocations into the session's receipt chain. Static AST adapters (`bulla.frameworks.{langgraph,crewai}`) are unchanged for source-file scanning. See [`docs/FRAMEWORKS.md`](https://github.com/jkomkov/bulla/blob/main/docs/FRAMEWORKS.md).

#### Awareness-gap demo

A reproducible bundle at `examples/awareness-gap-demo/` walks the full failure → diagnose → translate → fix loop on canned filesystem + github manifests, no network or LLM required. `bulla scan` defaults to a prose narrative covering 39 dimension explanations, with a pairwise-vs-global block that fires only when every pair has fee=0 and the global has fee>0.

### Standards ingestion (new in 0.36.0)

Bulla ships **19 seed packs** covering the canonical commercial standards plus 5 restricted-source vocabularies as metadata-only references:

| Tier | Packs | Notes |
|---|---|---|
| **A — fully open, inline** | `iso-4217`, `iso-8601`, `iso-3166`, `iso-639`, `iana-media-types`, `naics-2022` | Currencies, dates, countries, languages, MIME, industry codes |
| **B — large open, registry-backed** | `ucum`, `fix-4.4`, `fix-5.0`, `gs1`, `un-edifact`, `fhir-r4`, `fhir-r5`, `icd-10-cm` | Units, FIX/SWIFT/FHIR/ICD-10/EDIFACT/GS1; `values_registry` points to authoritative source |
| **Restricted (metadata-only)** | `who-icd-10`, `swift-mt-mx`, `hl7-v2`, `umls-mappings`, `iso-20022` | Pack ships dimension metadata only — licensed values stay behind the registry pointer; consumer obtains license to fetch |

```bash
# List + inspect packs
bulla pack status src/bulla/packs/seed/iso-4217.yaml
bulla pack verify src/bulla/packs/seed/ucum.yaml          # static inspection (no network)
bulla pack lint   src/bulla/packs/seed/icd-10-cm.yaml     # advisory style hints
bulla pack validate path/to/your/pack.yaml                # schema check
```

**Architectural extensions (Extensions A–E)** behind the standards-ingestion sprint:

- **`license`** at pack level — `registry_license: open | research-only | restricted` describes the upstream registry, not the pack's own metadata (which is always openly authored).
- **`values_registry`** at dimension level — pointer to an external content-addressed registry. Hash format: real `sha256:<64-hex>` or sentinel `placeholder:awaiting-ingest` / `placeholder:awaiting-license`. Literal `sha256:0...0` is rejected by the validator.
- **`derives_from`** on `PackRef` — per-pack standard-version provenance recorded on every receipt's `active_packs`.
- **Alias-form `known_values`** — items widen from `string` to `{ canonical, aliases, source_codes }`. Strictly additive; legacy packs unchanged. A field whose enum lists `"840"` (ISO-4217 numeric) classifies under the same dimension as `"USD"`.
- **Passive `mappings:`** in regular packs — receipt-side translation tables (e.g. ICD-9 ↔ ICD-10 GEMs, FHIR R4 ↔ R5 resource-type renames). Value-blind: the coboundary uses dimension *names*, so mappings don't change H¹.

End-to-end demos at `calibration/data/demos/`:
- `cross_pack_receipt_billing.yaml` — clinical_emr → billing_system → payer_gateway crossing ISO 4217 + FHIR R4 + ICD-10-CM seams in a single signed receipt.
- `restricted_pack_metadata_only.yaml` — composition referencing a license-gated pack issues a valid receipt without consumer-side credentials; `bulla pack verify` returns `status='placeholder'` until a real ingest is performed.

Authoritative-source registry hashes are real SHA-256 from live fetches for **all 11 fetchable open packs** (UCUM, NAICS 2022, ISO 639, IANA Media Types, FHIR R4, FHIR R5, FIX 4.4, FIX 5.0, GS1, UN-EDIFACT, ICD-10-CM); the 5 restricted packs use `placeholder:awaiting-license` until a license-holder substitutes their own ingest; the 3 fully-inline packs (ISO 3166/4217/8601) carry no registry pointer. Real hashes also propagate onto `derives_from.source_hash` so receipts bind to the underlying-standard revision transitively. See `docs/STANDARDS-INGEST-SOURCES.md` and `docs/STANDARDS-PACK-MAINTENANCE.md` for the full ownership / drift-handling protocol.

### Witness-geometry diagnostics (new in 0.35.0)

Beyond the scalar coherence fee, Bulla can surface the full *witness geometry* of a composition: per-field leverage scores, concentration index (`N_eff`), coloops/loops, and the matroid-greedy minimum-cost disclosure basis. All quantities are exact rationals (`Fraction`), never floats.

```bash
# Show leverage, N_eff, coloops, and greedy basis on a composition
bulla diagnose composition.yaml --witness

# On a live MCP server or manifest (gauge is the prescriptive command)
bulla gauge tools.json --leverage

# Ask which hidden fields substitute for a target (effective resistance)
bulla gauge tools.json --substitutes read_file path

# Cost-weighted greedy: YAML maps "<tool>:<field>" → rational cost string
bulla gauge tools.json --costs costs.yaml
```

JSON output adds a `witness_geometry` block only when the flag is set; default output remains byte-identical to 0.34.0. The mathematical backing is the Witness Gram rank identity and the Kron-reduction theorem, machine-checked in Lean 4. The broader research-program ledger documents 56 Aristotle-verified theorems across the witness-geometry chain (0 `sorry`). The PyPI package does not vendor Lean; it implements the measurement and receipt layers in Python.

### Per-dimension decomposition & interaction score

The scalar fee can be split across the semantic *dimensions* that carry it, so you can see *which* conventions are responsible and whether they interact:

<!-- bulla-doc-skip: illustrative — continues from a `comp` built above -->
```python
from bulla.diagnostic import decompose_fee_by_dimension

d = decompose_fee_by_dimension(comp)
d.by_dimension      # {'amount_unit': 1, 'date_format': 1} — fee_d per dimension
d.total_fee         # 2  — the composition's coherence fee
d.residual          # 0  — interaction score: Σ fee_d − fee
d.dfd_holds         # True — Disjoint Field Decomposition holds
```

The **interaction score** `residual = Σ fee_d − fee` is a structural diagnostic, not a failure predictor: `0` means the dimensions are *modular* (each `fee_d` is independently repairable); a positive value means two dimensions are coupled through a shared hidden field (`d.shared_columns` localizes which `(tool, field)` columns). This rests on the **Per-Dimension Additivity Theorem**: under Disjoint Field Decomposition (distinct dimensions touch disjoint columns), `Σ fee_d = fee` exactly — proven and verified on all 703 corpus compositions ([note](https://github.com/jkomkov/res-agentica/blob/main/papers/coherence-cliff/results/per_dimension_additivity_theorem.md)). The live MCP proxy surfaces the same breakdown: `bulla__blind_spots` returns `fee_by_dimension`, `interaction_score`, and `dimensions_modular` alongside the blind-spot list.

## Quick start with `bulla gauge`

```bash
# Diagnose a live MCP server
bulla gauge --mcp-server "python -m my_mcp_server"

# Diagnose from a manifest JSON (tools/list response)
bulla gauge tools.json

# Save the inferred composition for hand-editing
bulla gauge tools.json -o composition.yaml

# CI gating: fail if coherence fee exceeds threshold
bulla gauge tools.json --max-fee 0
```

## Python API

```python
from bulla import (
    WitnessBasis, PolicyProfile,
    diagnose, load_composition, witness,
    verify_receipt_consistency, verify_receipt_integrity,
)

# Load and diagnose. `load_composition` also takes path="pipeline.yaml";
# the inline text keeps this snippet self-contained.
comp = load_composition(text="""
name: pipeline
tools:
  parser: {internal_state: [amount, amount_unit], observable_schema: [amount]}
  engine: {internal_state: [amount, amount_unit], observable_schema: [amount]}
edges:
  - {from: parser, to: engine, dimensions: [{name: amount_unit, from_field: amount_unit, to_field: amount_unit}]}
""")
diag = diagnose(comp)
print(f"Fee: {diag.coherence_fee}, Blind spots: {len(diag.blind_spots)}")  # Fee: 1, Blind spots: 1

# Witness with provenance
basis = WitnessBasis(declared=1, inferred=0, unknown=0)
policy = PolicyProfile(name="strict", max_unknown=2)
receipt = witness(diag, comp, witness_basis=basis, policy_profile=policy)
print(f"Disposition: {receipt.disposition.value}")

# Verify
ok, violations = verify_receipt_consistency(receipt, comp, diag)
assert verify_receipt_integrity(receipt.to_dict())
```

### BullaGuard (high-level)

<!-- bulla-doc-skip: from_mcp_server spawns a live server subprocess -->
```python
guard = BullaGuard.from_mcp_server("python my_server.py")
guard.check(max_blind_spots=0)  # raises BullaCheckError on failure

guard = BullaGuard.from_tools({
    "parser": {"fields": ["amount", "currency"], "conventions": {"amount_unit": "dollars"}},
    "engine": {"fields": ["amount"], "conventions": {"amount_unit": "cents"}},
}, edges=[("parser", "engine")])
```

## MCP Server

Bulla exposes a JSON-RPC 2.0 stdio server with two tools and one resource:

```bash
bulla serve   # starts MCP stdio server
```

- **`bulla.witness`** — composition YAML → WitnessReceipt (structured output)
- **`bulla.bridge`** — composition YAML → patched YAML + receipt chain
- **`bulla://taxonomy`** — convention pack taxonomy

## Convention Packs

Layered vocabulary for convention recognition. Later packs override earlier ones.

```bash
bulla diagnose --pack financial.yaml pipeline.yaml
bulla scan --pack custom.yaml "python server.py"
```

Ships with `base` (11 dimensions) and `financial` (4 domain-specific dimensions).

## CI Integration

```yaml
# GitHub Actions with SARIF
- run: pip install bulla
- run: bulla check --format sarif compositions/ > bulla.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: bulla.sarif
```

Semantic SemVer CI gate:

```yaml
- uses: jkomkov/bulla@v0.21.0
  with:
    mode: semver
    old-path: compositions/pipeline_old.yaml
    new-path: compositions/pipeline_new.yaml
    fail-on-major: "true"
```

## How it works

Bulla builds a coboundary operator from tool dimensions to edge dimensions for both the observable and full sheaves. The coherence fee is:

```
fee = rank(δ_full) − rank(δ_obs)
```

Each unit of fee is an independent semantic dimension invisible to pairwise checks. Bridging increases rank(δ_obs) until it matches rank(δ_full). Rank computation uses exact arithmetic (`fractions.Fraction`) — no floating-point, no numpy.

## Witness Contract

Every receipt binds three hashes: **composition** (what was proposed), **diagnostic** (what was measured), **receipt** (what was witnessed). Receipts chain via `parent_receipt_hashes` for auditable repair flows.

See [WITNESS-CONTRACT.md](https://github.com/jkomkov/bulla/blob/main/WITNESS-CONTRACT.md) for the normative specification.

## License

[Business Source License 1.1](https://github.com/jkomkov/bulla/blob/main/LICENSE)

Use grant: non-competing use, plus commercial use processing fewer than 1,000 compositions per month. Converts to Apache 2.0 on 2030-04-01.
