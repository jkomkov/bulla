# Real-World MCP Audit Findings

**Date**: 2026-04-04
**Bulla version**: 0.18.0 (pre-0.19.0 release)
**Methodology**: Genuine `tools/list` responses captured from live MCP servers via `bulla.scan.scan_mcp_server()`. No hand-constructed or simulated data.

## Servers audited

| Server | Package | Tools | Provenance |
|--------|---------|-------|------------|
| filesystem | `@modelcontextprotocol/server-filesystem` | 14 | Live capture via `npx -y` |
| github | `@modelcontextprotocol/server-github` | 26 | Live capture via `npx -y` |
| memory | `@modelcontextprotocol/server-memory` | 9 | Live capture via `npx -y` |
| puppeteer | `@modelcontextprotocol/server-puppeteer` | 7 | Live capture via `npx -y` |

Total: **56 tools** across 4 servers.

## Headline results

| Metric | Value |
|--------|-------|
| Total edges (inferred tool pairs) | 153 |
| Coherence fee | **17** |
| Blind spots | 153 |
| Unbridged edges | 153 |
| Minimum disclosure set | 17 fields |

## Fee decomposition by server

| Server | Intra-server fee |
|--------|-----------------|
| filesystem | 0 |
| github | **17** |
| memory | 0 |
| puppeteer | 0 |
| **Boundary fee** | **0** |
| **Boundary edges** | 0 |

## Interpretation

### Finding 1: GitHub server has 17 internal blind spots

The `@modelcontextprotocol/server-github` server exposes 26 tools, many of which share the `id_offset` convention dimension. Fields like `page`, `issue_number`, `pull_number`, and `per_page` are classified as index/offset fields, but their offset convention (zero-based vs one-based) is not declared in the tool schemas.

This creates 153 edges between GitHub tools where the `id_offset_match` dimension is hidden on both sides. The coherence fee of 17 corresponds to the rank deficiency: 17 independent convention assumptions are implicit in the server's API.

**What this means in practice**: An agent composing these tools could pass a `page` value from `search_repositories` to `list_commits` without knowing whether pagination is zero-indexed or one-indexed. Similarly, `issue_number` from `get_issue` could be confused with `pull_number` in `merge_pull_request` — both are numeric identifiers with undeclared offset conventions.

**Minimum disclosure set**: Bulla identifies exactly 17 fields that, if their `id_offset` convention were declared, would eliminate all blind spots:

- `page` in: `search_repositories`, `list_commits`, `list_issues`, `search_code`, `search_issues`, `search_users`
- `issue_number` in: `update_issue`, `add_issue_comment`, `get_issue`
- `pull_number` in: `get_pull_request`, `create_pull_request_review`, `merge_pull_request`, `get_pull_request_files`, `get_pull_request_status`, `update_pull_request_branch`, `get_pull_request_comments`
- `per_page` in: `list_pull_requests`

### Finding 2: No cross-server (boundary) blind spots

The boundary fee between all four servers is **zero**. This means:

- **filesystem**, **github**, **memory**, and **puppeteer** are semantically orthogonal
- Their convention-laden fields do not overlap across server boundaries
- Composing these servers introduces no additional risk beyond what each server carries individually

This is the expected result for general-purpose utility servers operating in distinct domains (local filesystem, remote Git hosting, knowledge graphs, browser automation). Cross-server blind spots are more likely in domain-specific compositions where multiple servers handle the same semantic concepts (dates, amounts, rates) with potentially different conventions.

### Finding 3: Filesystem, memory, and puppeteer have fee 0

These three servers have zero coherence fee individually. Their tool interfaces either:
- Don't expose convention-laden fields (memory's `entities`, `relations`; puppeteer's `selector`, `script`)
- Use fields that don't match known convention dimensions in the base pack taxonomy

This does not mean these servers are risk-free in all compositions — it means the base pack's dimension vocabulary (date_format, amount_unit, timezone, encoding, etc.) does not flag any of their fields. Domain-specific packs could reveal additional conventions.

## Reproducibility

All findings are reproducible by running:

```bash
cd bulla/examples/real_world_audit
python run_audit.py
```

The raw server manifests in `manifests/` contain `_bulla_provenance` metadata documenting the exact capture command, server package, and capture date.

## Implications

1. **The framework works**: Bulla detects genuine convention blind spots in real MCP server responses — not synthetic or hand-crafted examples.

2. **Intra-server risk is real**: Even a single well-maintained reference server (GitHub) has 17 implicit convention assumptions. In a multi-agent workflow, these create real failure modes around pagination and entity ID handling.

3. **Cross-server risk requires domain overlap**: General-purpose servers are orthogonal. The highest-risk compositions are domain-specific (e.g., multiple financial APIs, multiple database interfaces) where convention dimensions like `amount_unit`, `date_format`, and `timezone` span server boundaries.

4. **Pack taxonomy is the lever**: The findings are only as comprehensive as the active pack vocabulary. The base pack captures `id_offset` (which flagged 17 fields in GitHub) but a financial pack would flag additional dimensions in payment or accounting servers.
