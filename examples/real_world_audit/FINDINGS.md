# Real-World Cross-Server Audit Findings (v0.22.0)

## The Concrete Agent Failure

An agent asked to "copy this local file to the GitHub repo" calls
`filesystem.read_file(path="/Users/me/repo/src/main.py")` to get the content,
then calls `github.create_or_update_file(path="/Users/me/repo/src/main.py")`.
The file is created at the wrong location because GitHub expects repo-relative
`src/main.py`, not an absolute local path.

Bulla's cross-server audit detects this: `path_convention` is hidden on both
sides of the filesystem↔github boundary. No individual server reveals the
mismatch — only the composition does.

**"These are arguably different semantic concepts that share a field name."**
That's exactly what a convention blind spot IS: the schema doesn't distinguish
them, and an agent composing across servers has no machine-readable signal that
the same field name means different things.

---

## Audit Configuration

| Server | Package | Tools |
|--------|---------|-------|
| filesystem | `@modelcontextprotocol/server-filesystem` | 14 |
| github | `@modelcontextprotocol/server-github` | 26 |
| memory | `@modelcontextprotocol/server-memory` | 9 |
| puppeteer | `@modelcontextprotocol/server-puppeteer` | 7 |

**Total**: 56 tools across 4 servers. All manifests captured from live servers
on 2026-04-04. Raw `tools/list` JSON with `_bulla_provenance` metadata stored
in `manifests/`.

---

## Version Progression: v0.19.0 → v0.20.0 → v0.21.0 → v0.22.0

| Metric | v0.19.0 | v0.20.0 | v0.21.0 | v0.22.0 (base) | v0.22.0 (base+discovered) |
|--------|---------|---------|---------|-----------------|---------------------------|
| Coherence fee | 17 | 31 | 30 | 30 | **45** |
| Dimensions active | 1 | 2 | 2 | 2 | **5** |
| Blind spots | 153 | 273 | 244 | 244 | **419** |
| Boundary fee | 0 | 1 | 1 | 1 | **5** |
| `_description` noise | — | 29 | 0 | 0 | 0 |

The v0.22.0 column shows the same 4 servers with `bulla discover`-produced
dimensions loaded. Three new dimensions (`entity_namespace`,
`content_transport`, `graph_operation_scope`) produce 175 additional blind
spots and raise the boundary fee from 1 to 5 — four new cross-server
convention risks that the hand-crafted base pack missed entirely.

### Classifier changes (cumulative through v0.22.0)

1. **Negative patterns** (v0.20.0): `per_page`, `page_size`, `limit`, `count`,
   `batch_size` excluded from `id_offset`.
2. **Type-aware exclusion** (v0.20.0): String-typed `*_id` fields excluded from
   `id_offset`.
3. **`path_convention` dimension** (v0.20.0): `path`, `filepath`, `directory`
   classified with known values `absolute_local`, `relative_cwd`,
   `relative_repo`, `uri`.
4. **Temporal patterns** (v0.20.0): `since`, `after`, `before`, `until`
   classified as `date_format`.
5. **Pack-driven keywords** (v0.20.0): Description keywords loaded from YAML.
6. **Per-field description scanning** (v0.20.0): 4th signal source.
7. **`_description` suppression** (v0.21.0): Tool-level description keyword
   matches no longer generate edges or blind spots. Signal is preserved in the
   witness basis for auditability, but does not inflate the output.
8. **Discovery engine** (v0.22.0): `bulla discover` generates micro-pack YAML
   from tool schemas via LLM. Discovered `field_patterns` are compiled into
   classifier name patterns automatically (no classifier code changes needed).
   The kernel is unchanged — it sees pack YAML, same as always.

---

## Finding 1: Nonzero Boundary Fee (path_convention)

**Boundary fee: 1**

The filesystem server's `path` fields (11 tools: `read_file`, `write_file`,
`edit_file`, etc.) use absolute local paths (`/Users/me/repo/src/main.py`).
The GitHub server's `path` fields (2 tools: `create_or_update_file`,
`get_file_contents`) use repository-relative paths (`src/main.py`).

Neither server declares its path convention. When composed, 28 cross-server
edges are created in the `path_convention` dimension. The boundary fee of 1
means this blind spot exists **only** in the cross-server composition — it is
invisible to any individual server audit.

**Minimum disclosure**: Declaring `path_convention` on any one filesystem tool
AND any one GitHub tool would resolve the cross-server ambiguity.

This is the proof of concept for `bulla audit`: a finding that only emerges
from multi-server composition analysis.

---

## Finding 2: Intra-Server Convention Risk (id_offset)

**GitHub intra-server fee: 18** (124 blind spots)

The GitHub server has 153 blind spots across the `id_offset` dimension:
- `page` fields (7 tools): zero-based vs one-based page indexing
- `issue_number` / `pull_number` fields (8 tools): entity identifier convention
- Overlap between tools sharing these fields creates the full edge set

**Filesystem intra-server fee: 11** (92 blind spots in `path_convention`)

All 14 filesystem tools share `path` fields. With `_description` pseudo-fields
removed (v0.21.0), only real schema-field matches contribute. Within the
filesystem server, path convention is consistent (all absolute local), but
this is not declared in the schema.

**Memory and Puppeteer: fee = 0** — these servers have no fields matching
any convention dimension. They are semantically orthogonal.

---

## Finding 3: Temporal Fields Detected

GitHub's `list_issues.since` field is now classified as `date_format` (via the
temporal pattern `since`). However, only one tool has this field, so no edges
are created from it. This is honest: the classifier correctly identifies the
convention-laden field, but a single instance doesn't create a blind spot.

If a second server exposed a `since` or `after` field, cross-server temporal
blind spots would appear automatically.

---

## Finding 4: LLM-Discovered Dimensions (v0.22.0)

`bulla discover --manifests examples/real_world_audit/manifests/` produces a
micro-pack with three dimensions the hand-crafted base pack missed:

### entity_namespace (refines id_offset)

GitHub's `issue_number` and `pull_number` fields share a monotonic counter —
issue #7 and PR #7 cannot coexist. This is a genuine convention risk: an agent
composing `get_issue(issue_number=7)` → `get_pull_request(pull_number=7)` might
assume these are independent sequences when they aren't.

The `refines: id_offset` relationship means this dimension specializes the
existing `id_offset` dimension. The base pack catches the "zero-based vs
one-based" question; `entity_namespace` catches the "shared vs scoped sequence"
question — a strictly finer distinction.

### content_transport

Filesystem tools return raw UTF-8 text in `content` fields. Puppeteer's
`encoded` field returns base64-encoded screenshot data. An agent composing
`read_file` → `puppeteer_screenshot` must handle the encoding difference.
Neither schema declares the transport encoding.

### graph_operation_scope

Memory server's `create_entities`, `create_relations`, and `add_observations`
accept arrays (batch operations). Other tools operate on single items. An
agent composing across these boundaries must handle the batch/single conversion.

### Impact

| Metric | Base pack only | Base + discovered |
|--------|---------------|-------------------|
| Fee | 30 | 45 (+50%) |
| Blind spots | 244 | 419 (+72%) |
| Boundary fee | 1 | 5 (+400%) |
| Active dimensions | 2 | 5 (+150%) |

The boundary fee increase is the key result: 4 new cross-server convention
risks that are invisible to per-server analysis AND invisible to the base pack.
The discovery engine found them by reasoning across tool schemas.

---

## Interpretation

The v0.22.0 audit demonstrates four principles:

1. **Precision over recall**: Removing `per_page` and string `*_id` false
   positives makes remaining findings defensible. Every flagged field is a
   genuine convention question.

2. **Multi-dimensional findings**: Five dimensions are strictly more informative
   than two. The decomposition tells you *what kind* of convention risk exists.

3. **Boundary fee as unique value**: The nonzero boundary fee proves that
   `bulla audit` finds risks invisible to per-server analysis. This is the
   tool's raison d'être.

4. **Dynamic vocabulary**: The convention vocabulary is no longer fixed. An LLM
   can discover dimensions the human pack author didn't anticipate, and the
   kernel handles them without code changes. The vocabulary grows from usage,
   not from committee.

---

## Dimension Coverage

The base pack defines 11 convention dimensions. With discovery, 5 are active:

| Dimension | Source | Activated | Why |
|-----------|--------|-----------|-----|
| `id_offset` | base | Yes | `page`, `issue_number`, `pull_number` fields |
| `path_convention` | base | Yes | `path` fields in filesystem and GitHub |
| `entity_namespace` | discovered | Yes | `issue_number`, `pull_number` shared sequence |
| `content_transport` | discovered | Yes | raw text vs base64 across servers |
| `graph_operation_scope` | discovered | Yes | batch vs single operations |
| `date_format` | base | Partial | `since` in one tool — no edges (need 2+ tools) |
| `timezone` | base | No | No timezone fields in these servers |
| `encoding` | base | No | No encoding fields |
| `amount_unit` | base | No | No financial fields |
| `null_handling` | base | No | No null-semantic fields |

The discovered dimensions cover gaps the base pack's hand-crafted vocabulary
missed. Adding servers that handle timestamps, currencies, or data formats
would activate additional base dimensions automatically. Running `bulla discover`
on new server compositions may find further dimensions specific to those tools.

---

## Reproducibility

```bash
cd bulla
pip install -e .

# Baseline audit (base pack only):
bulla audit --manifests examples/real_world_audit/manifests/

# Discovery + audit with discovered pack:
bulla discover --manifests examples/real_world_audit/manifests/ -o discovered.yaml
bulla audit --manifests examples/real_world_audit/manifests/ --pack discovered.yaml

# Or run the evidence comparison script:
python scripts/run_discover_evidence.py        # mock adapter (no API key needed)
python scripts/run_discover_evidence.py --live  # real LLM (requires API key)
```

All manifests are genuine `tools/list` responses with provenance metadata.
The baseline audit is fully deterministic: same inputs → same fee, same blind
spots. Discovery results vary by LLM model and prompt but the kernel computation
from any given micro-pack is deterministic.
