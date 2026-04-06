# State of Agent Coherence, Q2 2026

*Generated: 2026-04-05*

## Executive Summary

We analyzed **6 MCP servers** across **15 pairwise compositions**, computing the coherence fee for each. The coherence fee measures the number of independent semantic convention dimensions that bilateral verification cannot detect.

**Key findings:**
- 60% of pairwise compositions have nonzero coherence fee
- 1,124 total blind spots identified
- Blind spot precision: 35% (240 real mismatches out of 693 annotated)

## Coherence Fee Distribution

| Fee | Compositions | % |
|-----|-------------|---|
| 0 | 6 | 40.0% |
| 11 | 4 | 26.7% |
| 18 | 4 | 26.7% |
| 30 | 1 | 6.7% |

## Top Dangerous Blind Spots

Specific server pairs with confirmed or likely semantic mismatches:

| Dimension | Tool A | Tool B | Field A | Field B | Source |
|-----------|--------|--------|---------|---------|--------|
| path_convention_match | filesystem__read_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_file | github__create_or_update_file | path | path | llm |
| path_convention_match | filesystem__read_file | github__get_file_contents | path | path | llm |
| path_convention_match | filesystem__read_text_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_text_file | filesystem__search_files | path | path | llm |
| path_convention_match | filesystem__read_text_file | github__create_or_update_file | path | path | llm |
| path_convention_match | filesystem__read_text_file | github__get_file_contents | path | path | llm |
| path_convention_match | filesystem__read_media_file | filesystem__search_files | path | path | llm |
| path_convention_match | filesystem__read_media_file | github__create_or_update_file | path | path | llm |
| path_convention_match | filesystem__read_media_file | github__get_file_contents | path | path | llm |

## Calibration Curve: Fee vs Failure Probability

| Fee | Compositions | With Mismatch | P(failure) |
|-----|-------------|---------------|------------|
| 0 | 6 | 0 | 0.00% |
| 11 | 4 | 4 | 100.00% |
| 18 | 4 | 4 | 100.00% |
| 30 | 1 | 1 | 100.00% |

## Boundary Fee Analysis

- Cross-category mean boundary fee: **0.00**
- Intra-category mean boundary fee: **0.07**
- Spearman rho (boundary fee vs real mismatch count): **-0.271**

## Dimension Landscape

| Dimension | Occurrences | Real Mismatch | False Positive | Precision |
|-----------|-------------|---------------|----------------|-----------|
| id_offset_match | 765 | 180 | 336 | 35% |
| path_convention_match | 359 | 60 | 117 | 34% |

## Server Composability Scores

Composability = fraction of pairwise compositions with fee == 0.

| Server | Composability | Mean Fee | Compositions | Top Dimensions |
|--------|--------------|----------|-------------|----------------|
| memory | 60% | 5.8 | 5 | id_offset_match, path_convention_match |
| postgres | 60% | 5.8 | 5 | id_offset_match, path_convention_match |
| puppeteer | 60% | 5.8 | 5 | id_offset_match, path_convention_match |
| sequential-thinking | 60% | 5.8 | 5 | id_offset_match, path_convention_match |
| filesystem | 0% | 14.8 | 5 | path_convention_match, id_offset_match |
| github | 0% | 20.4 | 5 | id_offset_match, path_convention_match |

## Methodology

Manifests were collected permissionlessly from the MCP server ecosystem (official registry, public schema repositories, and local server scanning). Coherence fees were computed using Bulla v0.28.0+ with the base convention pack (11 dimensions). Blind spots were annotated via live execution testing (ground truth) and LLM-assisted classification (extended coverage). The calibration curve maps coherence fee to empirical failure probability.

For full details on the mathematical foundation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf) and [Bulla Witness Contract](https://github.com/jkomkov/bulla/blob/main/WITNESS-CONTRACT.md).
