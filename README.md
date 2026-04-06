# bulla

Witness kernel for agent tool compositions — diagnose, attest, seal.

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
print(result.receipt.disposition.value)  # "refuse"
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
| `bulla discover` | LLM-powered convention dimension discovery |

Output formats: `--format text` (default), `--format json`, `--format sarif`.

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

Every receipt binds three hashes: **composition** (what was proposed), **diagnostic** (what was measured), **receipt** (what was witnessed). Receipts chain via `parent_receipt_hash` for auditable repair flows.

See [WITNESS-CONTRACT.md](WITNESS-CONTRACT.md) for the normative specification.

## License

[Business Source License 1.1](LICENSE)

Use grant: non-competing use, plus commercial use processing fewer than 1,000 compositions per month. Converts to Apache 2.0 on 2030-04-01.
