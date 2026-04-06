# State of Agent Coherence, Q2 2026

*Generated: 2026-04-05*

## Executive Summary

We analyzed **50 MCP servers** across **1,225 pairwise compositions**, computing the coherence fee for each. The coherence fee measures the number of independent semantic convention dimensions that bilateral verification cannot detect.

**Key findings:**
- 8% of pairwise compositions have nonzero coherence fee
- 10,804 total blind spots identified
- Blind spot precision: 32% (2098 real mismatches out of 6575 annotated)

## Coherence Fee Distribution

| Fee | Compositions | % |
|-----|-------------|---|
| 0 | 1,128 | 92.1% |
| 11 | 48 | 3.9% |
| 18 | 48 | 3.9% |
| 30 | 1 | 0.1% |

## Top Dangerous Blind Spots

Specific server pairs with confirmed or likely semantic mismatches:

| Dimension | Tool A | Tool B | Field A | Field B | Source |
|-----------|--------|--------|---------|---------|--------|
| path_convention_match | filesystem__read_file | filesystem__read_media_file | path | path | llm |
| path_convention_match | filesystem__read_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_file | filesystem__create_directory | path | path | llm |
| path_convention_match | filesystem__read_text_file | filesystem__read_media_file | path | path | llm |
| path_convention_match | filesystem__read_text_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_text_file | filesystem__create_directory | path | path | llm |
| path_convention_match | filesystem__read_media_file | filesystem__read_multiple_files | path | paths | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__write_file | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__edit_file | paths | path | llm |
| path_convention_match | filesystem__read_multiple_files | filesystem__create_directory | paths | path | llm |

## Calibration Curve: Fee vs Failure Probability

| Fee | Compositions | With Mismatch | P(failure) |
|-----|-------------|---------------|------------|
| 0 | 1128 | 0 | 0.00% |
| 11 | 48 | 46 | 95.83% |
| 18 | 48 | 48 | 100.00% |
| 30 | 1 | 1 | 100.00% |

## Boundary Fee Analysis

- Cross-category mean boundary fee: **0.00**
- Intra-category mean boundary fee: **0.07**
- Spearman rho (boundary fee vs real mismatch count): **0.745**

## Dimension Landscape

| Dimension | Occurrences | Real Mismatch | False Positive | Precision |
|-----------|-------------|---------------|----------------|-----------|
| id_offset_match | 7,497 | 1605 | 3376 | 32% |
| path_convention_match | 3,307 | 493 | 1101 | 31% |

## Server Composability Scores

Composability = fraction of pairwise compositions with fee == 0.

| Server | Composability | Mean Fee | Compositions | Top Dimensions |
|--------|--------------|----------|-------------|----------------|
| airtable-mcp | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| any-chat-completions-mcp | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| cognee-mcp-server | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| e2b-code-mcp-server | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| exa-mcp-server | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| fetch-mcp | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| flightradar24-mcp-server | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| gtasks-mcp | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| homeassistant-mcp | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| inoyu-mcp-unomi-server | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-bigquery-server | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-mongo-server | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-obsidian | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-pandoc | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-pinecone | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-playwright | 96% | 0.6 | 49 | id_offset_match |
| mcp-server-aws | 96% | 0.6 | 49 | path_convention_match, id_offset_match |
| mcp-server-axiom | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-server-bigquery | 96% | 0.6 | 49 | id_offset_match, path_convention_match |
| mcp-server-browserbase | 96% | 0.6 | 49 | id_offset_match, path_convention_match |

## Methodology

Manifests were collected permissionlessly from the MCP server ecosystem (official registry, public schema repositories, and local server scanning). Coherence fees were computed using Bulla v0.28.0+ with the base convention pack (11 dimensions). Blind spots were annotated via live execution testing (ground truth) and LLM-assisted classification (extended coverage). The calibration curve maps coherence fee to empirical failure probability.

For full details on the mathematical foundation, see the [SEAM paper](https://www.resagentica.com/papers/seam-paper.pdf) and [Bulla Witness Contract](https://github.com/jkomkov/bulla/blob/main/WITNESS-CONTRACT.md).
