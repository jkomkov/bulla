# bulla

Witness kernel for agentic compositions — diagnose, attest, seal.

When AI agents compose tools into pipelines, implicit semantic assumptions (date formats, unit scales, path conventions) can silently produce wrong results. Schema validation passes, but the pipeline is broken. Bulla computes the **coherence fee**: the exact number of independent semantic dimensions that bilateral verification cannot detect.

**Zero heavy dependencies.** Only requires PyYAML. No numpy, no scipy, no LLM calls. Installs in under a second.

> **Naming**: *Bulla* is the protocol and tool. *SEAM* is the underlying theory ([paper](https://www.resagentica.com/papers/seam-paper.pdf)).

## Try it now

```bash
pip install bulla

# Audit your Cursor / Claude Desktop MCP setup
bulla audit

# Explicit config path
bulla audit ~/.cursor/mcp.json

# CI gate: fail if any composition exceeds fee threshold
bulla audit --max-fee 3 --format json
```

`bulla audit` auto-detects your MCP configuration, scans all servers, and reports cross-server coherence risks — including the **boundary fee** (convention conflicts that no individual server can detect on its own).

## The seam problem

Two MCP servers. One uses absolute paths (`/tmp/src/main.py`), the other uses repository-relative paths (`src/main.py`). Schema validation passes. The agent silently puts the file in the wrong place. Bulla catches this before execution.

**[See the canonical demo →](examples/canonical-demo/)**

## Calibration results

Tested across 10 real MCP servers (filesystem, github, notion, playwright, tavily, etc.) in 45 pairwise compositions:

| Zone | Fee | P(mismatch) | Compositions |
|------|-----|-------------|--------------|
| **Safe** | 0 | 0% | 15/15 clean |
| **Uncertain** | 1–3 | 0–33% | 12 compositions |
| **Unsafe** | 4+ | ~100% | 18/18 confirmed |

fee=0 guarantees no convention mismatch. fee≥4 guarantees real mismatches exist. The fee is computed from schemas alone — no execution required.

See [calibration data](calibration/data/tier3/report/state-of-agent-coherence.md) for the full report.

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
| `bulla serve` | MCP stdio server |
| `bulla proxy` | Replay a session trace with flow-level structural diagnosis |
| `bulla discover` | LLM-powered convention dimension discovery |

Output formats: `--format text` (default), `--format json`, `--format sarif`.

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

Authoritative-source registry hashes for 6 of 12 open packs (UCUM, NAICS 2022, ISO 639, IANA Media Types, FHIR R4, FHIR R5) are real SHA-256 from live fetches; the other 6 carry the `placeholder:awaiting-ingest` sentinel until their next ingest cycle. See `docs/STANDARDS-INGEST-SOURCES.md` and `docs/STANDARDS-PACK-MAINTENANCE.md` for the full ownership / drift-handling protocol.

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

JSON output adds a `witness_geometry` block only when the flag is set — default output remains byte-identical to 0.34.0. The mathematical backing is described in the [Witness Gram paper](../papers/hierarchical-fee/paper/witness-gram.pdf). The **research-program** Lean ledger documents **56** Aristotle-verified theorems across the witness-geometry chain (0 `sorry`); see [`papers/sheaf/lean/LEAN-CLAIM-LEDGER.md`](../papers/sheaf/lean/LEAN-CLAIM-LEDGER.md). The PyPI package does not vendor Lean — it implements the measurement and receipt layers in Python.

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

See [WITNESS-CONTRACT.md](WITNESS-CONTRACT.md) for the normative specification.

## License

[Business Source License 1.1](LICENSE)

Use grant: non-competing use, plus commercial use processing fewer than 1,000 compositions per month. Converts to Apache 2.0 on 2030-04-01.
