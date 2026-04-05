# bulla

Witness kernel for agent tool compositions — diagnose, attest, seal.

When AI agents compose tools into pipelines, implicit semantic assumptions (date formats, unit scales, encoding schemes) can silently produce wrong results. Type-checking passes, but the pipeline is broken. Bulla computes the **coherence fee**: the exact number of independent semantic dimensions that bilateral verification cannot detect. For each blind spot, it recommends a **bridge** and issues a tamper-evident **WitnessReceipt**.

**Zero heavy dependencies.** Only requires PyYAML. No numpy, no scipy, no LLM calls. Installs in under a second.

> **Naming**: *Bulla* is the protocol and tool. *SEAM* is the underlying theory ([paper](https://www.resagentica.com/papers/seam-paper.pdf)). *Glyph* is the company.

## The Seam Problem (canonical demo)

Two MCP servers. One uses absolute paths, the other uses relative paths. Schema validation passes. The agent silently puts the file in the wrong place. **[See the demo →](examples/canonical-demo/)**

## Install

```bash
pip install bulla
```

## Architecture

Three layers, cleanly separated:

| Layer | Concern | Module |
|---|---|---|
| **Measurement** | Composition → Diagnostic (fee, blind spots, bridges) | `diagnostic.py` |
| **Binding** | Diagnostic → WitnessReceipt (content-addressable, tamper-evident) | `witness.py` |
| **Judgment** | Policy → Disposition (proceed / refuse / bridge) | `witness.py` |

The measurement layer has **zero imports** from the witness layer. Measurement does not know it is being witnessed.

## Quick start with `bulla gauge`

The fastest way to diagnose an MCP server or tool manifest:

```bash
# Diagnose a live MCP server
bulla gauge --mcp-server "python -m my_mcp_server"

# Diagnose from a manifest JSON (tools/list response)
bulla gauge tools.json

# Save the inferred composition for hand-editing
bulla gauge tools.json -o composition.yaml
bulla diagnose composition.yaml     # re-diagnose after edits

# CI gating: fail if coherence fee exceeds threshold
bulla gauge tools.json --max-fee 0
```

`bulla gauge` returns the coherence fee, minimum disclosure set (the exact fields to expose to eliminate all blind spots), and witness basis in a single command.

## Audit your MCP setup

`bulla audit` reads your MCP configuration (Cursor or Claude Desktop), scans all servers in parallel, and diagnoses cross-server coherence:

```bash
# Auto-detect your Cursor/Claude config and audit all servers
bulla audit

# Explicit config file
bulla audit ~/.cursor/mcp.json

# See cross-server blind spots in detail
bulla audit -v

# JSON output for CI integration
bulla audit --format json --max-fee 5
```

The unique insight is the **cross-server risk decomposition**: `bulla audit` partitions blind spots into those _within_ a single server (intra-server fee) versus those that only appear _between_ independently-developed servers (boundary fee). The boundary fee represents conventions that no individual server can detect on its own.

## Other commands

```bash
bulla diagnose --examples          # run on bundled compositions
bulla scan "python my_server.py"   # scan a live MCP server
bulla check compositions/          # CI gate (exit 1 on failure)
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

Ships with `base` (10 dimensions) and `financial` (4 domain-specific dimensions).

## Witness Contract

Every receipt binds three hashes: **composition** (what was proposed), **diagnostic** (what was measured), **receipt** (what was witnessed). Receipts chain via `parent_receipt_hash` for auditable repair flows.

See [WITNESS-CONTRACT.md](WITNESS-CONTRACT.md) for the normative specification.

## CI Integration

```yaml
# GitHub Actions with SARIF
- run: pip install bulla
- run: bulla check --format sarif compositions/ > bulla.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: bulla.sarif
```

## Commands

| Command | Purpose |
|---|---|
| `bulla diagnose` | Full diagnostic with blind spots, bridges, fee |
| `bulla check` | CI gate with configurable thresholds |
| `bulla scan` | Scan live MCP servers (zero config) |
| `bulla witness` | Diagnose and emit WitnessReceipt as JSON |
| `bulla bridge` | Auto-bridge and emit patched YAML + patches |
| `bulla manifest` | Generate/validate Bulla Manifest files |
| `bulla serve` | MCP stdio server |
| `bulla init` | Interactive composition wizard |
| `bulla infer` | Infer proto-composition from MCP manifest |

Output formats: `--format text` (default), `--format json`, `--format sarif`.

## How it works

Bulla builds a coboundary operator from tool dimensions to edge dimensions for both the observable and full sheaves. The coherence fee is:

```
fee = rank(δ_full) − rank(δ_obs)
```

Each unit of fee is an independent semantic dimension invisible to pairwise checks. Bridging increases rank(δ_obs) until it matches rank(δ_full). Rank computation uses exact arithmetic (`fractions.Fraction`) — no floating-point, no numpy.

## License

MIT
