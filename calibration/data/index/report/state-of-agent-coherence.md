# State of Agent Coherence, Q2 2026

*Generated: 2026-04-07*

## Executive Summary

We analyzed **10 MCP servers** across **45 pairwise compositions**, computing the coherence fee for each. The coherence fee measures the number of independent semantic convention dimensions that bilateral verification cannot detect.

**Key findings:**
- 53% of pairwise compositions have nonzero coherence fee
- 882 total blind spots identified
- Blind spot precision: 5% (43 real mismatches out of 880 annotated)

## Coherence Fee Distribution

| Fee | Compositions | % |
|-----|-------------|---|
| 0 | 21 | 46.7% |
| 1 | 6 | 13.3% |
| 2 | 1 | 2.2% |
| 10 | 5 | 11.1% |
| 11 | 8 | 17.8% |
| 12 | 2 | 4.4% |
| 13 | 1 | 2.2% |
| 22 | 1 | 2.2% |

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

Logistic fit: P(mismatch) = sigmoid(1.099 * fee + -13.183)

| Fee | Compositions | With Mismatch | P(failure) |
|-----|-------------|---------------|------------|
| 0 | 21 | 0 | 0.00% |
| 1 | 6 | 0 | 0.00% |
| 2 | 1 | 1 | 100.00% |
| 10 | 5 | 0 | 0.00% |
| 11 | 8 | 2 | 25.00% |
| 12 | 2 | 1 | 50.00% |
| 13 | 1 | 1 | 100.00% |
| 22 | 1 | 1 | 100.00% |

## Boundary Fee Analysis

- Cross-category mean boundary fee: **0.20**
- Intra-category mean boundary fee: **0.10**
- Spearman rho (boundary fee vs real mismatch count): **0.996**

## Dimension Landscape

| Dimension | Occurrences | Real Mismatch | False Positive | Precision |
|-----------|-------------|---------------|----------------|-----------|
| path_convention_match | 641 | 38 | 603 | 6% |
| id_offset_match | 189 | 0 | 189 | 0% |
| state_filter_match | 27 | 0 | 27 | 0% |
| date_format_match | 14 | 5 | 9 | 36% |
| sort_direction_match | 11 | 0 | 9 | 0% |

## Server Composability Scores

Composability = fraction of pairwise compositions with fee == 0.

| Server | Composability | Mean Fee | Compositions | Top Dimensions |
|--------|--------------|----------|-------------|----------------|
| exa | 67% | 2.4 | 9 | - |
| mcp-server-fetch | 67% | 2.4 | 9 | - |
| memory | 67% | 2.4 | 9 | - |
| playwright | 67% | 2.7 | 9 | path_convention_match |
| puppeteer | 67% | 2.4 | 9 | - |
| sequential-thinking | 67% | 2.4 | 9 | - |
| tavily | 67% | 2.7 | 9 | date_format_match |
| filesystem | 0% | 12.4 | 9 | path_convention_match |
| github | 0% | 11.9 | 9 | path_convention_match, date_format_match |
| notion | 0% | 3.7 | 9 | date_format_match |

## Methodology

Manifests were collected permissionlessly from the MCP server ecosystem (official registry, public schema repositories, and local server scanning). Coherence fees were computed using Bulla v0.28.0+ with the base convention pack (11 dimensions). Blind spots were annotated via live execution testing (ground truth) and LLM-assisted classification (extended coverage). The calibration curve maps coherence fee to empirical failure probability.

For full details on the mathematical foundation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf) and [Bulla Witness Contract](https://github.com/jkomkov/bulla/blob/main/WITNESS-CONTRACT.md).
