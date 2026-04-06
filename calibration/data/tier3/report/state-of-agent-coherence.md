# State of Agent Coherence, Q2 2026

*Generated: 2026-04-06*

## Executive Summary

We analyzed **10 MCP servers** across **45 pairwise compositions**, computing the coherence fee for each. The coherence fee measures the number of independent semantic convention dimensions that bilateral verification cannot detect.

**Key findings:**
- 67% of pairwise compositions have nonzero coherence fee
- 2,128 total blind spots identified
- Blind spot precision: 29% (360 real mismatches out of 1223 annotated)

## Coherence Fee Distribution

| Fee | Compositions | % |
|-----|-------------|---|
| 0 | 15 | 33.3% |
| 1 | 5 | 11.1% |
| 2 | 1 | 2.2% |
| 3 | 6 | 13.3% |
| 4 | 1 | 2.2% |
| 11 | 6 | 13.3% |
| 12 | 1 | 2.2% |
| 15 | 1 | 2.2% |
| 18 | 5 | 11.1% |
| 19 | 1 | 2.2% |
| 20 | 1 | 2.2% |
| 22 | 1 | 2.2% |
| 30 | 1 | 2.2% |

## Top Dangerous Blind Spots

Specific server pairs with confirmed or likely semantic mismatches:

| Dimension | Tool A | Tool B | Field A | Field B | Source |
|-----------|--------|--------|---------|---------|--------|
| path_convention_match | filesystem__read_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_text_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_media_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__write_file | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__edit_file | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__create_directory | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__list_directory | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__list_directory_with_sizes | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__directory_tree | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__search_files | paths | path | llm |

## Calibration Curve: Fee vs Failure Probability

| Fee | Compositions | With Mismatch | P(failure) |
|-----|-------------|---------------|------------|
| 0 | 15 | 0 | 0.00% |
| 1 | 5 | 0 | 0.00% |
| 2 | 1 | 1 | 100.00% |
| 3 | 6 | 2 | 33.33% |
| 4 | 1 | 1 | 100.00% |
| 11 | 6 | 6 | 100.00% |
| 12 | 1 | 1 | 100.00% |
| 15 | 1 | 1 | 100.00% |
| 18 | 5 | 5 | 100.00% |
| 19 | 1 | 1 | 100.00% |
| 20 | 1 | 1 | 100.00% |
| 22 | 1 | 1 | 100.00% |
| 30 | 1 | 1 | 100.00% |

## Boundary Fee Analysis

- Cross-category mean boundary fee: **0.00**
- Intra-category mean boundary fee: **0.00**
- Spearman rho (boundary fee vs real mismatch count): **0.305**

## Dimension Landscape

| Dimension | Occurrences | Real Mismatch | False Positive | Precision |
|-----------|-------------|---------------|----------------|-----------|
| id_offset_match | 1,377 | 269 | 626 | 30% |
| path_convention_match | 737 | 87 | 237 | 27% |
| date_format_match | 14 | 4 | 0 | 100% |

## Server Composability Scores

Composability = fraction of pairwise compositions with fee == 0.

| Server | Composability | Mean Fee | Compositions | Top Dimensions |
|--------|--------------|----------|-------------|----------------|
| exa | 56% | 3.7 | 9 | id_offset_match, path_convention_match |
| mcp-server-fetch | 56% | 3.7 | 9 | id_offset_match, path_convention_match |
| memory | 56% | 3.7 | 9 | id_offset_match, path_convention_match |
| puppeteer | 56% | 3.7 | 9 | id_offset_match, path_convention_match |
| sequential-thinking | 56% | 3.7 | 9 | id_offset_match, path_convention_match |
| tavily | 56% | 3.9 | 9 | id_offset_match, path_convention_match, date_format_match |
| filesystem | 0% | 13.7 | 9 | path_convention_match, id_offset_match, date_format_match |
| github | 0% | 20.1 | 9 | id_offset_match, path_convention_match, date_format_match |
| notion | 0% | 4.8 | 9 | id_offset_match, path_convention_match, date_format_match |
| playwright | 0% | 6.6 | 9 | id_offset_match, path_convention_match |

## Methodology

Manifests were collected permissionlessly from the MCP server ecosystem (official registry, public schema repositories, and local server scanning). Coherence fees were computed using Bulla v0.28.0+ with the base convention pack (11 dimensions). Blind spots were annotated via live execution testing (ground truth) and LLM-assisted classification (extended coverage). The calibration curve maps coherence fee to empirical failure probability.

For full details on the mathematical foundation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf) and [Bulla Witness Contract](https://github.com/jkomkov/bulla/blob/main/WITNESS-CONTRACT.md).
