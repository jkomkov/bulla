# Bulla GitHub Action

Bulla provides a GitHub Action for automated coherence analysis of agentic compositions. It supports two modes:

- **Check mode** (default): Analyzes composition YAML files for blind spots
- **Audit mode**: Scans MCP server manifests for cross-server convention risk

## Quick Start: Audit Mode

### 1. Capture manifests

Run your MCP servers locally and capture their tool manifests:

```bash
bulla manifest npx -y @modelcontextprotocol/server-github -o manifests/github.json
bulla manifest npx -y @modelcontextprotocol/server-filesystem /tmp -o manifests/filesystem.json
```

Commit the `manifests/` directory to your repo.

### 2. Add the workflow

Copy `coherence-audit.yml` to `.github/workflows/coherence-audit.yml`, or add the action to an existing workflow:

```yaml
- uses: jkomkov/bulla@v0.21.0
  with:
    mode: audit
    manifests-dir: manifests/
    max-fee: "50"
```

### 3. View results

- **SARIF annotations** appear in the Code Scanning tab (Security > Code scanning alerts)
- **Step summary** shows coherence fee and boundary fee
- **Threshold gating**: the step fails if the fee exceeds `max-fee`

## Configuration

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `mode` | `check` | `check` for composition YAMLs, `audit` for MCP manifests |
| `manifests-dir` | — | Directory of manifest JSON files (audit mode) |
| `mcp-config` | — | MCP config file for live server scan (audit mode) |
| `max-fee` | — | Fail if coherence fee exceeds this value (audit mode) |
| `max-blind-spots` | `0` (check) | Fail if blind spots exceed this value |
| `max-unbridged` | `0` | Fail if unbridged edges exceed this value (check mode) |
| `upload-sarif` | `true` | Upload SARIF to GitHub Code Scanning |
| `version` | latest | Specific bulla version to install |
| `python-version` | `3.11` | Python version |

### Outputs

| Output | Description |
|--------|-------------|
| `passed` | `true` or `false` |
| `sarif-file` | Path to generated SARIF file |
| `coherence-fee` | Total coherence fee (audit mode) |
| `boundary-fee` | Cross-server boundary fee (audit mode) |

## What SARIF Annotations Mean

Each annotation corresponds to a **blind spot** — a tool parameter whose convention (date format, ID offset, path style, etc.) is undeclared and shared with another tool. When two tools share a blind spot, an agent composing them may silently mis-translate values between them.

Annotations are grouped by convention dimension (e.g., `id_offset_match`, `path_convention_match`) and show which tools and fields are affected.

## Manifest Mode vs. Live Scan

| | Manifest mode | Live scan |
|---|---|---|
| **Reliability** | Deterministic, no external deps | Requires server runtime |
| **CI suitability** | Excellent | Fragile |
| **Freshness** | Manual recapture needed | Always current |
| **Setup** | Commit JSON files | Configure server commands |

For CI, **manifest mode is recommended**. Recapture manifests when server versions change:

```bash
bulla manifest <server-command> -o manifests/<name>.json
```

## Check Mode (Backward Compatible)

The original check mode still works for composition YAML analysis:

```yaml
- uses: jkomkov/bulla@v0.21.0
  with:
    path: compositions/
    max-blind-spots: "0"
```
