# bulla

Witness kernel for agentic compositions.

When AI agents compose tools into pipelines, implicit semantic assumptions (date formats, unit scales, path conventions) can silently produce wrong results. Schema validation passes, but the pipeline is broken. Bulla computes the **coherence fee**: the exact number of independent semantic dimensions that bilateral verification cannot detect.

**Zero heavy dependencies.** Only requires PyYAML. No numpy, no scipy, no LLM calls. Installs in under a second.

> **Naming**: *Bulla* is the protocol and tool. *SEAM* is the underlying theory ([paper](https://www.resagentica.com/papers/seam-paper.pdf)).

## Try it now

```bash
pip install bulla

# Primary demo: audit your live MCP setup (Cursor, Claude Desktop, …)
bulla audit

# Explicit config path
bulla audit ~/.cursor/mcp.json

# CI gate: fail if any composition exceeds fee threshold
bulla audit --max-fee 3 --format json

# Deterministic audit from saved MCP manifests (great for docs/screenshots)
# From a checkout of the bulla repo:
bulla audit --manifests examples/canonical-demo/manifests/
```

`bulla audit` auto-detects your MCP configuration when possible, scans servers, and prints a short **receipt**: **boundary fee** first (cross-server seams), then within-server blind spots, then copy-paste next steps (`--max-fee`, `--format json`). If no config is found, stderr suggests a **`bulla scan …`** command you can run with zero setup.

## The seam problem

Two MCP servers. One uses absolute paths (`/tmp/src/main.py`), the other uses repository-relative paths (`src/main.py`). Schema validation passes. The agent silently puts the file in the wrong place. Bulla catches this before execution.

**[See the canonical demo →](https://github.com/jkomkov/bulla/tree/main/examples/canonical-demo)** — frozen MCP manifests, real fee, walks through the bridge runtime.

## Calibration results

Tested across 10 real MCP servers (filesystem, github, notion, playwright, tavily, etc.) in 45 pairwise compositions:

| Zone | Fee | P(mismatch) | Compositions |
|------|-----|-------------|--------------|
| **Safe** | 0 | 0% | 15 compositions, all clean |
| **Uncertain** | 1–3 | 0–33% | 12 compositions |
| **Unsafe** | 4+ | ~100% | 18 compositions, all confirmed |

fee=0 guarantees no convention mismatch. fee≥4 guarantees real mismatches exist. The fee is computed from schemas alone — no execution required.

See [calibration data](https://github.com/jkomkov/bulla/blob/main/calibration/data/tier3/report/state-of-agent-coherence.md) for the full report.

## Python SDK

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
| `bulla check` | CI gate with configurable thresholds |
| `bulla scan` | Scan live MCP servers (zero config) |
| `bulla witness` | Diagnose and emit WitnessReceipt as JSON |
| `bulla bridge` | Auto-bridge and emit patched YAML |
| `bulla translate` | Apply a typed runtime translator (`--dimension X --value V --to T`) |
| `bulla serve` | MCP stdio server |
| `bulla proxy` | Replay a session trace with flow-level structural diagnosis |
| `bulla discover` | LLM-powered convention dimension discovery |
| `bulla import langgraph` | Parse a LangGraph workflow into a Bulla manifest |
| `bulla import crewai` | Parse a CrewAI crew/agent/task tree into a Bulla manifest |

Output formats: `--format text` (default), `--format json`, `--format sarif`.

### Runtime translation, Session API, framework adapters (new in 0.37.0)

Three additions in 0.37.0. `bulla.translate` exposes typed runtime translators that produce a `WitnessReceipt` for every transformation. `bulla.Session` builds compositions tool-by-tool with rank-1 incremental updates. `bulla.LiveSession` extends Session with call tracing for MCP proxies. Native `bulla.langgraph` and `bulla.crewai` adapters round it out.

#### `bulla.translate`

Typed runtime value translation across conventions.

```python
from bulla import translate

result = translate("currency_code", value="USD", to_convention="numeric")
print(result.value)                         # "840"
print(result.evidence.kind)                 # "translator" | "mapping" | "pack"
print(result.receipt.disposition.value)     # "proceed"
```

Five canonical translators ship registered: `currency_code`, `country_code`, `language_code`, `temporal_format`, `fhir_resource_type`. Restricted-pack values raise `TranslationUnavailable` rather than leaking through. Register your own via `@bulla.bridges.register`. CLI: `bulla translate --dimension currency_code --value USD --to numeric`.

#### `bulla.Session`

Long-lived composition built tool by tool.

```python
from bulla import Session

s = Session()
s.add_tool("filesystem.read_file", fields=["path"], conventions={"path_convention": "absolute"})
s.add_tool("github.create_file",  fields=["path"], conventions={"path_convention": "repo_relative"})
s.add_edge("filesystem.read_file", "github.create_file")
print(s.fee)                # 1
receipt = s.diagnose()      # full WitnessReceipt
```

Every `add_tool`, `translate(...)`, and `checkpoint()` extends a chained receipt history. Incremental updates use rank-1 Schur complements; a 10,000-seed property test pins bitwise equality with full-rebuild `witness_gram`.

#### `bulla.LiveSession`

Online MCP composition proxy.

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
    BullaGuard, WitnessBasis, PolicyProfile,
    diagnose, load_composition, witness,
    verify_receipt_consistency, verify_receipt_integrity,
)

# Load and diagnose
comp = load_composition(path="pipeline.yaml")
diag = diagnose(comp)
print(f"Fee: {diag.coherence_fee}, Blind spots: {len(diag.blind_spots)}")

# Witness with provenance
basis = WitnessBasis(declared=3, inferred=1, unknown=0)
policy = PolicyProfile(name="strict", max_unknown=2)
receipt = witness(diag, comp, witness_basis=basis, policy_profile=policy)
print(f"Disposition: {receipt.disposition.value}")

# Verify
ok, violations = verify_receipt_consistency(receipt, comp, diag)
assert verify_receipt_integrity(receipt.to_dict())
```

### BullaGuard (high-level)

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
