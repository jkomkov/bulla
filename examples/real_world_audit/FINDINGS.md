# Real-World Cross-Server Audit Findings (v0.21.0)

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

## Version Progression: v0.19.0 → v0.20.0 → v0.21.0

| Metric | v0.19.0 | v0.20.0 | v0.21.0 | Notes |
|--------|---------|---------|---------|-------|
| Coherence fee | 17 | 31 | **30** | Slight decrease from cleaner edge generation |
| Dimensions found | 1 | 2 | **2** | `id_offset`, `path_convention` |
| Blind spots | 153 | 273 | **244** | −29 `_description` pseudo-field noise removed |
| `per_page` false positive | Yes | No | No | Fixed in v0.20.0 |
| `commit_id` (string) flagged | Yes | No | No | Fixed in v0.20.0 |
| Cross-server blind spots | 0 | 28 | **28** | All real field-to-field matches |
| Boundary fee | 0 | 1 | **1** | The key result, preserved |
| `_description` pseudo-fields | — | 29 | **0** | Suppressed from edge generation in v0.21.0 |

### Classifier changes (cumulative through v0.21.0)

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

## Interpretation

The v0.21.0 audit demonstrates three principles:

1. **Precision over recall**: Removing `per_page` and string `*_id` false
   positives makes remaining findings defensible. Every flagged field is a
   genuine convention question.

2. **Multi-dimensional findings**: Two dimensions (`id_offset` +
   `path_convention`) are strictly more informative than one. The decomposition
   tells you *what kind* of convention risk exists, not just *how much*.

3. **Boundary fee as unique value**: The nonzero boundary fee proves that
   `bulla audit` finds risks invisible to per-server analysis. This is the
   tool's raison d'être.

---

## Dimension Coverage and Future Servers

The base pack defines 11 convention dimensions. This audit activates 2 of them:

| Dimension | Activated | Why |
|-----------|-----------|-----|
| `id_offset` | Yes | `page`, `issue_number`, `pull_number` fields |
| `path_convention` | Yes | `path` fields in filesystem and GitHub |
| `date_format` | Partial | `since` in one tool — no edges (need 2+ tools) |
| `timezone` | No | No timezone fields in these servers |
| `encoding` | No | No encoding fields |
| `amount_unit` | No | No financial fields |
| `null_handling` | No | No null-semantic fields |
| `sort_order` | No | No sort fields |
| `error_format` | No | No error format fields |
| `case_convention` | No | No case-sensitive fields |
| `bool_representation` | No | No boolean representation fields |

This is expected: the 4 servers tested are developer tools, not financial or
data-processing services. Adding servers that handle timestamps (e.g.,
`@modelcontextprotocol/server-postgres`), currencies, or data formats would
activate additional dimensions automatically — no classifier changes needed.

---

## Reproducibility

```bash
cd bulla
pip install -e .

# Using the CLI (v0.21.0+):
bulla audit --manifests examples/real_world_audit/manifests/

# Or using the script:
python examples/real_world_audit/run_audit.py
```

All manifests are genuine `tools/list` responses with provenance metadata.
The audit is fully deterministic: same inputs → same fee, same blind spots.
