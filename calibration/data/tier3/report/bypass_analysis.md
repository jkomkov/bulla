# Bypass Analysis on tier3 13-server corpus

**Question:** for each pair of servers (A, B) with direct coherence fee > 0,
does there exist a third server C such that fee(A, C) = 0 AND fee(C, B) = 0?
If yes, the disagreement between A and B is *bypassable* — the path-metric
`d_S(A, B) = 0` because route-around through C exists. The fee says A and B
directly disagree, but in the larger ecosystem the disagreement is
**operationally invisible** because traffic can be routed through C.

Per Sprint 2 retrospective: this is the operationally-meaningful Frontier 4 finding.

## Setup
- 13 MCP server manifests from tier3
- 78 pairwise compositions
- 78 successfully computed

## Fee distribution

| Fee | Count |
|---:|---:|
| 0 | 45 |
| 1 | 9 |
| 2 | 1 |
| 10 | 17 |
| 11 | 4 |
| 13 | 1 |
| 21 | 1 |

## Bypass results: three criteria

- Pairs with positive direct fee: **33**
- **Zero-zero bypass** (path A → C → B with both segment fees = 0): 0
- **Lower-fee bypass** (path-fee < direct fee for some C): 6
- **Per-dimension bypass** (some blind-spot dimension absent in some C): 33

### Server dimension coverage

Which convention dimensions does each server reference?

| Server | Dimensions referenced |
|---|---|
| exa | *(none)* |
| filesystem | path_convention |
| github | date_format, id_offset, path_convention, sort_direction, state_filter |
| mcp-server-fetch | *(none)* |
| memory | *(none)* |
| notion | date_format, sort_direction |
| npm-search | *(none)* |
| playwright | path_convention |
| postgres | *(none)* |
| puppeteer | *(none)* |
| sequential-thinking | *(none)* |
| tavily | date_format |
| youtube-transcript | *(none)* |

### Lower-fee bypasses

Pairs where a third server C provides a path with `fee(A,C) + fee(C,B) < fee(A,B)`.
These are the operationally-meaningful 'soft' bypasses: routing through C reduces
the path-fee below the direct fee.

| Server A | Server B | Direct fee | Best bypass C | Best path fee |
|---|---|---:|---|---:|
| filesystem | github | 21 | exa | 20 |
| filesystem | playwright | 11 | exa | 10 |
| github | notion | 13 | exa | 11 |
| github | playwright | 11 | exa | 10 |
| github | tavily | 11 | exa | 10 |
| notion | tavily | 2 | exa | 1 |

### Per-dimension bypasses

For each pair (A, B), each blind-spot dimension d may be bypassable by routing
through a server C that LACKS dimension d entirely (no field of that convention
type). At any seam involving C, dimension d is invisible — the disagreement on
d doesn't contribute to the path-fee.

| Server A | Server B | Direct fee | Bypassable dimensions |
|---|---|---:|---|
| exa | filesystem | 10 | `path_convention_match` via 9 servers |
| exa | github | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| exa | notion | 1 | `date_format_match` via 9 servers |
| filesystem | github | 21 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 10 servers; `id_offset_match` via 11 servers |
| filesystem | mcp-server-fetch | 10 | `path_convention_match` via 9 servers |
| filesystem | memory | 10 | `path_convention_match` via 9 servers |
| filesystem | notion | 11 | `date_format_match` via 9 servers; `path_convention_match` via 9 servers |
| filesystem | npm-search | 10 | `path_convention_match` via 9 servers |
| filesystem | playwright | 11 | `path_convention_match` via 10 servers |
| filesystem | postgres | 10 | `path_convention_match` via 9 servers |
| filesystem | puppeteer | 10 | `path_convention_match` via 9 servers |
| filesystem | sequential-thinking | 10 | `path_convention_match` via 9 servers |
| filesystem | tavily | 10 | `path_convention_match` via 9 servers |
| filesystem | youtube-transcript | 10 | `path_convention_match` via 9 servers |
| github | mcp-server-fetch | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| github | memory | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| github | notion | 13 | `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `date_format_match` via 10 servers; `id_offset_match` via 11 servers; `sort_direction_match` via 11 servers |
| github | npm-search | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| github | playwright | 11 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 10 servers; `id_offset_match` via 11 servers |
| github | postgres | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| github | puppeteer | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| github | sequential-thinking | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| github | tavily | 11 | `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `date_format_match` via 10 servers; `id_offset_match` via 11 servers; `sort_direction_match` via 10 servers |
| github | youtube-transcript | 10 | `sort_direction_match` via 10 servers; `state_filter_match` via 11 servers; `path_convention_match` via 9 servers; `id_offset_match` via 11 servers |
| mcp-server-fetch | notion | 1 | `date_format_match` via 9 servers |
| memory | notion | 1 | `date_format_match` via 9 servers |
| notion | npm-search | 1 | `date_format_match` via 9 servers |
| notion | playwright | 1 | `date_format_match` via 9 servers |
| notion | postgres | 1 | `date_format_match` via 9 servers |
| notion | puppeteer | 1 | `date_format_match` via 9 servers |
| notion | sequential-thinking | 1 | `date_format_match` via 9 servers |
| notion | tavily | 2 | `date_format_match` via 10 servers |
| notion | youtube-transcript | 1 | `date_format_match` via 9 servers |

### Pairs with no bypass possible (any criterion)

These pairs are **structurally non-bypassable** by all three criteria.
By Theorem C.2 (revised), disagreements here are detectable on every path.

| Server A | Server B | Direct fee | Blind dimensions |
|---|---|---:|---|

## Operational implication

**Bypassable pairs are 'hidden risk.'** The fee correctly identifies a disagreement,
but in the larger ecosystem the agent's traffic can be routed through tools that
don't see the disagreement at any seam. Bulla's standard `audit` returns fee N for
these pairs — but the operational impact depends on whether the agent's actual
composition path crosses the disagreement or routes around it.

**Non-bypassable pairs are 'topological risk.'** The disagreement cannot be avoided
no matter what intermediate tools the agent uses. These are the genuinely structural
obstructions in the ecosystem.

This is the Frontier 4 result expressed as something an operator can use:
  bulla audit --bypass-analysis my-mcp-config.json

would output bypassable vs structural disagreements as separate categories.