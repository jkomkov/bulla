# bulla

Witness kernel for agent tool compositions — diagnose, attest, seal. Finds semantic blind spots that bilateral verification cannot reach and recommends bridge annotations to eliminate them.

**Zero heavy dependencies.** Only requires PyYAML. No numpy, no scipy, no LLM calls. Installs in under a second.

> **Naming**: *Bulla* is the protocol and tool. *SEAM* is the underlying theory. *Glyph* is the company.

## Install

```bash
pip install bulla
```

## Quick start

Run the built-in examples to see output immediately:

```bash
bulla diagnose --examples
```

Diagnose your own composition:

```bash
bulla diagnose my_pipeline.yaml
```

## Library API

`BullaGuard` is the primary programmatic interface. Use it to embed coherence analysis in any Python application, agent framework, or CI pipeline.

```python
from bulla import BullaGuard, BullaCheckError

# Path A: From raw tool definitions (the framework integration path)
guard = BullaGuard.from_tools({
    "invoice_parser": {
        "fields": ["total_amount", "due_date", "line_items", "currency"],
        "conventions": {"amount_unit": "dollars", "date_format": "ISO-8601"},
    },
    "settlement_engine": {
        "fields": ["amount", "settlement_date", "ledger_entry"],
        "conventions": {"amount_unit": "cents"},
    },
}, edges=[("invoice_parser", "settlement_engine")])

# Path B: From MCP manifest JSON
guard = BullaGuard.from_mcp_manifest("manifest.json")

# Path C: From YAML composition (the v0.1 path)
guard = BullaGuard.from_composition("pipeline.yaml")

# Path D: From a live MCP server via stdio
guard = BullaGuard.from_mcp_server("python my_server.py")

# Diagnose
diag = guard.diagnose()
diag.coherence_fee         # int
diag.blind_spots           # list[BlindSpot]
diag.bridges               # list[Bridge]

# Check (raises BullaCheckError if thresholds exceeded)
guard.check(max_blind_spots=0, max_unbridged=0)

# Export
guard.to_yaml("pipeline.yaml")   # save for CI
guard.to_json()                   # JSON string with version + hash
guard.to_sarif()                  # SARIF string
```

### Framework integration example

A LangChain integration becomes:

```python
from bulla import BullaGuard

class BullaCoherenceCallback(BaseCallbackHandler):
    def on_chain_start(self, serialized, inputs, **kwargs):
        tools = extract_tools_from_chain(serialized)
        guard = BullaGuard.from_tools(tools)
        diag = guard.diagnose()
        if diag.coherence_fee > 0:
            warnings.warn(f"Composition has {len(diag.blind_spots)} blind spots")
```

## What it does

When tools in a pipeline share implicit conventions (date formats, unit scales, encoding schemes), some of those conventions may be invisible to bilateral verification -- each pair of tools looks correct in isolation, but the pipeline as a whole can silently produce wrong results.

bulla computes the **coherence fee**: the number of independent semantic dimensions that fall through the cracks of pairwise checks. For each blind spot, it recommends a **bridge** -- a specific field to expose in the tool's observable schema.

```
  Financial Analysis Pipeline
  ═══════════════════════════

  Topology: 3 tools, 3 edges, beta_1 = 1

  Blind spots (2):
    [1] day_conv_match (data_provider -> financial_analysis)
        day_convention hidden on both sides
    [2] metric_type_match (financial_analysis -> portfolio_verification)
        risk_metric hidden on both sides

  Recommended bridges:
    [1] Add 'day_convention' to F(data_provider) and F(financial_analysis)
    [2] Add 'risk_metric' to F(financial_analysis) and F(portfolio_verification)

  After bridging: fee = 0
```

## Composition format

Compositions are YAML files that describe your tool pipeline. See [`composition-schema.json`](composition-schema.json) for the full schema.

```yaml
name: My Pipeline

tools:
  tool_a:
    internal_state: [field_x, field_y, hidden_z]
    observable_schema: [field_x, field_y]

  tool_b:
    internal_state: [field_x, hidden_z]
    observable_schema: [field_x]

edges:
  - from: tool_a
    to: tool_b
    dimensions:
      - name: x_match
        from_field: field_x
        to_field: field_x
      - name: z_match
        from_field: hidden_z
        to_field: hidden_z
```

- **`internal_state`**: All semantic dimensions the tool operates on internally (the full stalk S(v)).
- **`observable_schema`**: Dimensions visible in the tool's API (the observable sub-sheaf F(v)). Must be a subset of `internal_state`.
- **`edges`**: Bilateral interfaces between tools. Each dimension names a shared convention.

A dimension is a **blind spot** when `from_field` or `to_field` is in `internal_state` but not in `observable_schema` of the respective tool.

## Commands

### `bulla diagnose`

Diagnose compositions and report blind spots, bridges, and the coherence fee.

```bash
bulla diagnose pipeline.yaml                    # text output
bulla diagnose --format json pipeline.yaml      # JSON with version + SHA-256
bulla diagnose --format sarif pipeline.yaml     # SARIF for GitHub code scanning
bulla diagnose --examples                       # run on bundled examples
```

### `bulla check`

CI/CD gate. Exits with code 1 if any composition exceeds the specified thresholds.

```bash
bulla check pipeline.yaml                                   # default: --max-blind-spots 0 --max-unbridged 0
bulla check --max-blind-spots 2 compositions/               # allow up to 2 blind spots
bulla check --format sarif compositions/ > results.sarif    # SARIF for GitHub Actions
```

### `bulla scan`

Scan live MCP servers via stdio. Zero configuration — no YAML required.

```bash
bulla scan "python my_server.py"                             # single server
bulla scan "python server_a.py" "python server_b.py"         # multi-server composition
bulla scan "python server.py" -o pipeline.yaml               # save for CI
bulla scan "python server.py" --format json                  # JSON diagnostic
```

The scanner spawns each server as a subprocess, performs the MCP initialize handshake, queries `tools/list`, and auto-generates a composition using the heuristic dimension classifier. No MCP SDK dependency.

### `bulla manifest`

Generate or validate [Bulla Manifest](bulla-manifest-spec-v0.1.md) files.

```bash
bulla manifest --from-json tools.json -o manifest.yaml       # from MCP manifest JSON
bulla manifest --from-server "python server.py"              # from live MCP server
bulla manifest --validate manifest.yaml                      # validate against spec
```

### `bulla init`

Interactive wizard to generate a composition YAML.

```bash
bulla init
bulla init -o my_pipeline.yaml
```

### `bulla infer`

Infer a proto-composition from an MCP manifest JSON.

```bash
bulla infer manifest.json                # stdout
bulla infer manifest.json -o proto.yaml  # save to file
```

### `bulla --version`

Print the installed version.

## Bulla Manifest Specification

The [Bulla Manifest Spec v0.1](bulla-manifest-spec-v0.1.md) defines a per-tool convention declaration format. Each manifest declares what semantic conventions a single tool assumes (e.g. "amounts are in dollars", "dates are ISO-8601").

See the [spec](bulla-manifest-spec-v0.1.md), [JSON Schema](bulla-manifest-schema.json), and the built-in [taxonomy](src/bulla/taxonomy.yaml) of 10 convention dimensions.

## CI integration

### GitHub Actions with SARIF

```yaml
name: bulla
on: [push, pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install bulla
      - run: bulla check --format sarif compositions/ > bulla.sarif
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: bulla.sarif
```

This uploads results to GitHub's code scanning tab, where blind spots appear as annotations on pull requests.

### Simple pass/fail

```yaml
      - run: pip install bulla
      - run: bulla check compositions/
```

## Output formats

| Format | Flag | Use case |
|--------|------|----------|
| Text | `--format text` (default) | Developer terminal |
| JSON | `--format json` | Orchestrator integration, includes version + SHA-256 |
| SARIF | `--format sarif` | GitHub code scanning, VS Code SARIF viewer |

## How it works

bulla builds a discrete coboundary operator (delta-0) from C^0 (tool dimensions) to C^1 (edge dimensions) for both the observable sheaf F and the full sheaf S. The coherence fee is:

```
fee = H^1(F_obs) - H^1(F_full)
    = (dim C^1 - rank delta_obs) - (dim C^1 - rank delta_full)
    = rank delta_full - rank delta_obs
```

Each unit of fee corresponds to an independent semantic dimension that bilateral verification cannot detect. Bridging (exposing hidden fields in the observable schema) increases rank(delta_obs) until it matches rank(delta_full).

The rank computation uses exact arithmetic (Python's `fractions.Fraction` module) via Gaussian elimination -- no floating-point tolerance, no numpy dependency.

## License

MIT
