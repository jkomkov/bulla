# Composition Risk Assessment
## bulla v0.5.0 | 2026-03-31

## Executive Summary

**44 MCP tools** scanned across 3 tool groups.
**135 fields** analyzed for semantic convention signals.
**8/10** convention dimensions detected (amount_unit, date_format, encoding, id_offset, precision, rate_scale, score_range, timezone).
Missing dimensions: line_ending, null_handling (expected — no tools in scan use these conventions).

### Confidence Distribution
| Tier | Count | Pct |
|------|-------|-----|
| Declared (2+ signals agree) | 13 | 42% |
| Inferred (1 strong signal) | 16 | 52% |
| Unknown (weak/ambiguous) | 2 | 6% |

## Tool Group Analysis

### Official MCP (35 tools)
- Fields analyzed: 66
- Dimensions detected: date_format, encoding, id_offset
- Confidence: declared=0, inferred=2, unknown=1

  - `read_text_file`: 3 fields → encoding(u)
  - `git_log`: 4 fields → date_format(i)
  - `fetch`: 4 fields → id_offset(i)
  - All other tools (32): no conventions detected

### Financial Pipeline (4 tools)
- Fields analyzed: 32
- Dimensions detected: amount_unit, date_format, id_offset, rate_scale
- Domain hint applied: `financial`
- Confidence: declared=4, inferred=7, unknown=0

  - `parse_invoice_markdown`: 11 fields → id_offset(i), date_format(d), amount_unit(d)
  - `compute_settlement`: 7 fields → date_format(i), amount_unit(d), rate_scale(i)
  - `compute_fees`: 7 fields → amount_unit(i), rate_scale(d)
  - `prepare_ledger_entry`: 7 fields → id_offset(i), amount_unit(i), date_format(i)

### Test Fixture (5 tools)
- Fields analyzed: 37
- Dimensions detected: amount_unit, date_format, encoding, id_offset, precision, rate_scale, score_range, timezone
- Confidence: declared=9, inferred=8, unknown=1

  - `stripe_create_charge`: 11 fields → amount_unit(d), id_offset(i), date_format(d)
  - `github_create_issue`: 8 fields → score_range(d), id_offset(i), date_format(d)
  - `datadog_query_metrics`: 7 fields → date_format(d), timezone(i), score_range(d), rate_scale(d)
  - `slack_post_message`: 6 fields → id_offset(i), date_format(i), encoding(u)
  - `ml_predict_score`: 5 fields → id_offset(i), precision(i), score_range(d), rate_scale(d)

## Composition Diagnostic: Financial Invoice Pipeline

### Pipeline Graph
```
parse_invoice → compute_settlement → compute_fees → prepare_ledger
                         └──────────────────────────→ prepare_ledger
```

### Before Bridge Intervention
- **Topology**: 4 tools, 4 edges, beta_1 = 1
- **Coherence fee**: 0 (note: fee measures topological information loss via H^1; blind spots count hidden fields on edges — these are different metrics)
- **Blind spots**: 4
  1. `date_format` (parse_invoice → compute_settlement): invoice_date hidden at settlement
  2. `currency_code` (parse_invoice → compute_settlement): currency hidden at settlement
  3. `amount_unit` (compute_settlement → prepare_ledger): converted_amount hidden at ledger
  4. `settlement_offset` (compute_settlement → prepare_ledger): settlement_date_offset hidden at ledger
- **CI gate**: FAIL (4 unbridged > 0 max)

### Critical Convention Clash
The `parse_invoice` tool outputs amounts in **dollars** (Cluster X convention).
The `compute_settlement` tool interprets amounts as **cents** (Cluster Y convention).
This creates a **100x error** that passes all LLM self-diagnosis checks (see BABEL Experiment 1).
bulla detects the `amount_scale` field is hidden — it's in internal_state but not observable_schema.

### After Bridge Intervention
Bridge: expose `amount_scale`, `invoice_date`, `currency`, `converted_amount`, `settlement_date_offset` in observable_schema.
- **Blind spots**: 0
- **CI gate**: PASS
- The 100x error becomes **detectable** because both sides now expose their amount convention.

## Methodology

1. **Tool scanning**: `bulla manifest --from-json` with multi-signal classifier (v0.5.0)
   - Signal 1: Field name pattern matching (taxonomy-compiled regex)
   - Signal 2: Description keyword extraction
   - Signal 3: JSON Schema structural signals (format, type+range, enum, pattern)
2. **Confidence assignment**: Three-tier model (declared/inferred/unknown)
3. **Composition analysis**: Sheaf cohomology diagnostic via `bulla diagnose`
4. **Bridge recommendation**: Automated identification of fields to expose
5. **Verification**: Before/after diagnostic confirms blind spot elimination

## Key Findings

1. **Infrastructure tools are convention-sparse**: Official MCP servers (filesystem, git, fetch, memory) produce only 3/10 dimensions with 0 declared confidence. This is expected — they don't carry domain-specific semantic conventions.
2. **Domain tools are convention-rich**: Financial pipeline tools produce 4 dimensions with 4 declared (multi-signal confirmed). Description keywords and schema metadata provide strong corroboration.
3. **The classifier works on real inputs**: 8/10 dimensions detected across 44 tools with 42% declared confidence (multi-signal agreement).
4. **Bridge interventions eliminate blind spots**: 4 → 0 blind spots in the financial pipeline by exposing hidden convention fields.
5. **The convention clash that matters is invisible to LLMs**: The dollars-vs-cents mismatch between invoice parser and settlement engine is exactly the kind of failure that produces "all green, still wrong" outcomes.

## Reproducibility

All results can be reproduced with bulla v0.5.0+:

```bash
pip install bulla

# Generate manifests from MCP tool definitions
bulla manifest --from-json audit/financial_pipeline.json

# Diagnose composition (before bridge)
bulla diagnose audit/financial_composition.yaml

# Diagnose composition (after bridge)
bulla diagnose audit/financial_composition_bridged.yaml

# CI gate check
bulla check audit/financial_composition.yaml
bulla check audit/financial_composition_bridged.yaml

# JSON diagnostic output
bulla diagnose audit/financial_composition.yaml --format json

# SARIF output for GitHub Code Scanning
bulla check --format sarif audit/financial_composition.yaml > results.sarif
```

Source data: `audit/mcp_official_tools.json` contains 35 tool definitions extracted from
`github.com/modelcontextprotocol/servers` (filesystem, git, fetch, memory).
