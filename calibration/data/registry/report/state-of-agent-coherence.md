# State of Agent Coherence, Q2 2026

*Generated: 2026-04-07*

## Executive Summary

We analyzed **38 MCP servers** across **703 pairwise compositions**, computing the coherence fee for each. The coherence fee measures the number of independent semantic convention dimensions that bilateral verification cannot detect.

**Key findings:**
- 34% of pairwise compositions have nonzero coherence fee
- 4,125 total blind spots identified
- Blind spot precision: 4% (161 real mismatches out of 4120 annotated)

## Coherence Fee Distribution

| Fee | Compositions | % |
|-----|-------------|---|
| 0 | 463 | 65.9% |
| 1 | 89 | 12.7% |
| 2 | 67 | 9.5% |
| 3 | 10 | 1.4% |
| 4 | 1 | 0.1% |
| 10 | 25 | 3.6% |
| 11 | 36 | 5.1% |
| 12 | 7 | 1.0% |
| 13 | 3 | 0.4% |
| 14 | 1 | 0.1% |
| 22 | 1 | 0.1% |

## Top Dangerous Blind Spots

Specific server pairs with confirmed or likely semantic mismatches:

| Dimension | Tool A | Tool B | Field A | Field B | Source |
|-----------|--------|--------|---------|---------|--------|
| path_convention_match | filesystem__read_file | github__create_or_update_file | path | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__read_file | github__get_file_contents | path | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__read_text_file | github__create_or_update_file | path | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__read_text_file | github__get_file_contents | path | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__read_media_file | github__create_or_update_file | path | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__read_media_file | github__get_file_contents | path | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__read_multiple_files | github__create_or_update_file | paths | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__read_multiple_files | github__get_file_contents | paths | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__write_file | github__create_or_update_file | path | path | auto:cross_server_path_convention_match |
| path_convention_match | filesystem__write_file | github__get_file_contents | path | path | auto:cross_server_path_convention_match |

## Calibration Curve: Fee vs Failure Probability

Logistic fit: P(mismatch) = sigmoid(0.189 * fee + -3.335)

| Fee | Compositions | With Mismatch | P(failure) |
|-----|-------------|---------------|------------|
| 0 | 463 | 0 | 0.00% |
| 1 | 89 | 2 | 2.25% |
| 2 | 67 | 6 | 8.96% |
| 3 | 10 | 4 | 40.00% |
| 4 | 1 | 0 | 0.00% |
| 10 | 25 | 0 | 0.00% |
| 11 | 36 | 5 | 13.89% |
| 12 | 7 | 3 | 42.86% |
| 13 | 3 | 2 | 66.67% |
| 14 | 1 | 1 | 100.00% |
| 22 | 1 | 1 | 100.00% |

## Boundary Fee Analysis

- Cross-category mean boundary fee: **0.20**
- Intra-category mean boundary fee: **0.10**
- Spearman rho (boundary fee vs real mismatch count): **0.996**

## Dimension Landscape

| Dimension | Occurrences | Real Mismatch | False Positive | Precision |
|-----------|-------------|---------------|----------------|-----------|
| path_convention_match | 2,925 | 113 | 2812 | 4% |
| id_offset_match | 792 | 15 | 777 | 2% |
| date_format_match | 144 | 33 | 111 | 23% |
| state_filter_match | 114 | 0 | 111 | 0% |
| score_range_match | 111 | 0 | 111 | 0% |
| sort_direction_match | 39 | 0 | 37 | 0% |

## Server Composability Scores

Composability = fraction of pairwise compositions with fee == 0.

| Server | Composability | Mean Fee | Compositions | Top Dimensions |
|--------|--------------|----------|-------------|----------------|
| airtable-mcp | 81% | 0.8 | 37 | - |
| cognee-mcp-server | 81% | 0.8 | 37 | - |
| exa | 81% | 0.8 | 37 | - |
| fetch-mcp | 81% | 0.8 | 37 | - |
| flightradar24-mcp-server | 81% | 0.8 | 37 | - |
| gtasks-mcp | 81% | 0.8 | 37 | - |
| mcp-mongo-server | 81% | 0.8 | 37 | - |
| mcp-playwright | 81% | 0.8 | 37 | - |
| mcp-server-aws | 81% | 0.8 | 37 | - |
| mcp-server-browserbase | 81% | 0.8 | 37 | - |
| mcp-server-chatsum | 81% | 0.8 | 37 | - |
| mcp-server-fetch | 81% | 0.8 | 37 | - |
| mcp-server-kubernetes | 81% | 0.8 | 37 | - |
| mcp-server-neon | 81% | 0.8 | 37 | - |
| mcp-server-tmdb | 81% | 0.8 | 37 | - |
| mcp-snowflake-server | 81% | 0.8 | 37 | - |
| mcp-vegalite-server | 81% | 0.8 | 37 | - |
| memory | 81% | 0.8 | 37 | - |
| needle-mcp | 81% | 0.8 | 37 | - |
| needle-mcp_tools | 81% | 0.8 | 37 | - |

## Methodology

Manifests were collected permissionlessly from the MCP server ecosystem (official registry, public schema repositories, and local server scanning). Coherence fees were computed using Bulla v0.28.0+ with the base convention pack (11 dimensions). Blind spots were annotated via live execution testing (ground truth) and LLM-assisted classification (extended coverage). The calibration curve maps coherence fee to empirical failure probability.

For full details on the mathematical foundation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf) and [Bulla Witness Contract](https://github.com/jkomkov/bulla/blob/main/WITNESS-CONTRACT.md).
