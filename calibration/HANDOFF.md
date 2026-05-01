# Coherence Index: Handoff Notes

*Written 2026-04-07. For the Glyph team picking up the ecosystem indexing pipeline.*

## What this is

The Coherence Index is a continuous scanning pipeline that measures how well MCP servers compose with each other. It takes real tool schemas from the ecosystem, builds every pairwise composition, runs Bulla's coboundary diagnostic on each, and produces:

- **Coherence fees**: how many semantic blind spots exist in a composition
- **Boundary fees**: the subset of blind spots that cross server boundaries (the actionable metric)
- **Witness receipts**: content-addressed, DAG-chainable JSON records attesting to each composition's diagnostic
- **Reports**: markdown and JSON summaries of ecosystem composability

The key finding so far: **boundary_fee=0 has zero false negatives across 678 compositions** (Spearman rho=0.996). If the boundary fee says two servers compose cleanly, they do. See `data/index/FINDINGS.md` for the full writeup.

## Current state

### What exists and works

| Scope | Servers | Real-schema | Compositions | Receipts | Data dir |
|-------|---------|-------------|--------------|----------|----------|
| curated (index) | 13 | 10 | 45 | 46 | `data/index/` |
| registry | 57 | 38 | 703 | 706 | `data/registry/` |

- Pipeline runs end-to-end: collect → scan → compute → report → receipts
- Incremental and idempotent — re-running on unchanged data is a no-op
- Content-addressed storage throughout (manifests, compositions, receipts)
- Receipts include `receipts/index.json` as the compatibility database seed

### What's committed

All code is on `main` (commit `c67cc4c` and subsequent). Key files:

```
bulla/calibration/
├── corpus.py          # Phase 1: manifest collection from 3 sources
├── compute.py         # Phase 2: pairwise fee computation + SQLite storage
├── index.py           # Orchestrator: scopes, phases, receipt generation
├── analyze.py         # Phase 3c: statistical analysis
├── annotate.py        # Phase 3b: LLM-assisted blind spot annotation
├── validate.py        # Phase 3a: live execution validation
├── report.py          # Phase 4: report generation
├── scripts/
│   ├── run_index.py           # CLI entry point
│   ├── run_calibration.py     # Legacy QA script
│   └── annotate_blind_spots.py # Heuristic annotation script
└── data/
    ├── index/         # Curated scope (10 real-schema servers)
    ├── registry/      # Registry scope (38 real-schema servers)
    ├── tier1-3/       # Legacy tier directories (superseded by scopes)
    └── .gitignore     # *.db, individual receipts, runs.jsonl excluded
```

### Bugs found and fixed

1. **Hyphen normalization** (`compute.py`): BullaGuard normalizes hyphens to underscores in tool names (e.g., `mcp-xmind` → `mcp_xmind`). The boundary fee partition check used the original server name, so it never matched normalized tool names. Fixed by adding `.replace("-", "_")` before partition matching. This was causing 18 false negatives at registry scale.

2. **Schema key normalization** (`corpus.py`): The oslook/mcp-servers-schemas repo uses `input_schema` (underscore) instead of MCP-standard `inputSchema` (camelCase), and some schemas are JSON strings instead of parsed dicts. Added `_normalize_tool_schemas()` to handle both.

## How to run

From `bulla/`:

```bash
# Full curated run (scan + compute + receipts + report):
python calibration/scripts/run_index.py

# Registry scope (pulls from schemas repo + registry API):
python calibration/scripts/run_index.py --scope registry

# Scan only (no compute):
python calibration/scripts/run_index.py --scan-only

# Regenerate receipts from existing compute data:
python calibration/scripts/run_index.py --receipts-only

# Report only:
python calibration/scripts/run_index.py --report-only

# With LLM dimension discovery:
python calibration/scripts/run_index.py --discover --provider openrouter
```

Requires Node.js (for `npx` server scanning) and the bulla package installed (`pip install -e bulla/src`).

## Corpus sources (by scope)

| Scope | Sources |
|-------|---------|
| **curated** | Live `npx` scan of 26 KNOWN_SERVERS in `index.py` |
| **registry** | curated + oslook/mcp-servers-schemas tarball + MCP registry API (5 pages) |
| **full** | registry + deep registry crawl (50 pages, up to 2000 servers) |

The curated list includes 14 official MCP servers, 7 community servers (no API key needed), and 5 API-key-required servers (which fail gracefully). Servers with fewer than 3 `inputSchema` fields are excluded from composition as "non-real-schema."

## What to do next

### Priority 1: Pack tightening

The base convention pack generates massive within-server noise. 98.9% of blind spots are within-server false positives. The `owner_convention_match` and `id_offset_match` dimensions produce thousands of within-server hits that inflate the total fee without adding cross-server signal.

Options:
- Tighten `field_patterns` in the pack YAML to be more selective
- Make boundary fee the primary output instead of total fee
- Both (recommended)

### Priority 2: Glyph site integration

The `receipts/index.json` from the registry scope is the compatibility database seed. It contains 706 entries with composition name, servers, fees, boundary fees, blind spot counts, and disposition. This should feed an interactive composability matrix on the Glyph site.

Key data points per composition:
- `fee` / `boundary_fee` — total and cross-server coherence costs
- `blind_spots` — count of semantic mismatches
- `disposition` — "proceed" / "warn" / "block"
- `pair_type` — "intra_category" / "cross_category"

### Priority 3: Live validation

`validate.py` supports starting real MCP servers and calling tools to confirm blind spots with ground truth. This hasn't been run at registry scale. The script infrastructure is there but needs a machine with Node.js and the patience for 57 server startups.

### Priority 4: Scheduled re-indexing

The pipeline is designed for scheduled runs. The MCP registry API returns new servers regularly. A weekly cron job running `--scope registry` would keep the index fresh. Run metadata is appended to `runs.jsonl` for tracking.

### Priority 5: Receipt chaining

Currently each receipt is standalone. The `WitnessReceipt` format supports DAG chaining (parent hashes), which would let you build a merkle history of ecosystem composability over time. Not yet wired up.

## Key concepts

- **Coherence fee**: `fee(G) = rank(delta_full) - rank(delta_obs)`. The number of semantic dimensions where tools in a composition disagree. Computed by the bulla kernel's coboundary algebra.
- **Boundary fee**: The subset of the coherence fee attributable to cross-server disagreements. Computed by `decompose_fee()` with a server-partition. This is the metric that matters.
- **Blind spot**: A specific (dimension, tool_a, tool_b) triple where the diagnostic detects a semantic mismatch.
- **Convention pack**: YAML vocabulary defining what dimensions to check (e.g., `path_convention_match`, `date_format_match`). Lives in `bulla/src/bulla/packs/`.
- **Real-schema server**: A server with ≥3 `inputSchema` fields across its tools. Servers below this threshold are excluded from composition because they lack enough typed surface for meaningful diagnosis.

## Data format: manifests

Each manifest in `data/{scope}/manifests/{name}.json`:

```json
{
  "_bulla_provenance": {
    "captured_via": "index_scan:npx -y @modelcontextprotocol/server-filesystem /tmp",
    "server_package": "@modelcontextprotocol/server-filesystem",
    "capture_date": "2026-04-07T...",
    "bulla_version": "0.33.0",
    "content_hash": "sha256:...",
    "category": "official"
  },
  "tools": [
    {
      "name": "read_file",
      "description": "Read the complete contents of a file...",
      "inputSchema": {
        "type": "object",
        "properties": { "path": { "type": "string", "description": "..." } },
        "required": ["path"]
      }
    }
  ]
}
```

## Data format: receipts/index.json

```json
{
  "generated_at": "2026-04-07T...",
  "scope": "curated",
  "count": 45,
  "receipts": [
    {
      "composition": "exa+memory",
      "servers": ["exa", "memory"],
      "composition_hash": "abc123...",
      "fee": 0,
      "boundary_fee": 0,
      "blind_spots": 0,
      "disposition": "proceed",
      "pair_type": "cross_category",
      "categories": ["community", "official"]
    }
  ]
}
```

## Three conventions dominate cross-server mismatches

If you're looking at why two servers have a nonzero boundary fee, it's almost certainly one of these:

| Convention | What's happening | Servers involved |
|-----------|-----------------|------------------|
| **Path format** | filesystem uses absolute paths, github uses repo-relative, playwright uses local | 163 of 183 real mismatches |
| **Date format** | github ISO-8601 vs tavily enum vs notion timestamp | 5 mismatches |
| **Sort direction** | similar semantics, different value conventions | 5 mismatches |

These three account for 173 of 183 confirmed cross-server mismatches.
