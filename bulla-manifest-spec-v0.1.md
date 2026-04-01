# Bulla Manifest Specification v0.1

**Status:** Draft  
**Authors:** Glyph  
**Schema:** `bulla-manifest-schema.json`

---

## 1. Purpose

A **Bulla Manifest** declares the semantic conventions that a single tool assumes.  When two tools in a composition make different assumptions about the same dimension (e.g. one emits amounts in dollars, the other expects cents), a *blind spot* exists.  Manifests make these assumptions explicit so they can be statically checked by `bulla` and its programmatic API (`BullaGuard`).

A manifest describes **one tool**.  A **composition** describes a **pipeline of tools** and is the input to `bulla diagnose`.  Manifests feed into compositions either manually (by a human writing YAML) or automatically (via `bulla scan` or `BullaGuard.from_tools()`).

## 2. Format

Manifests are YAML files.  The canonical schema is `bulla-manifest-schema.json` (JSON Schema 2020-12).

### Minimal example

```yaml
bulla_manifest: "0.1"
tool:
  name: "invoice-parser"
conventions:
  amount_unit:
    value: "dollars"
    confidence: "declared"
```

### Full example

```yaml
bulla_manifest: "0.1"
tool:
  name: "invoice-parser"
  version: "1.2.0"
  description: "Parse invoice documents and extract financial data"
conventions:
  amount_unit:
    value: "dollars"
    confidence: "declared"
  date_format:
    value: "ISO-8601"
    confidence: "declared"
  precision:
    value: "2_decimal"
    confidence: "inferred"
  timezone:
    confidence: "unknown"
```

## 3. Fields

### Top level

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `bulla_manifest` | string | yes | Must be `"0.1"`. (`seam_manifest` also accepted for backward compatibility.) |
| `tool` | object | yes | Tool identification. |
| `conventions` | object | yes | Convention declarations keyed by dimension name. |

### `tool`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Tool identifier, matching the MCP tool name. |
| `version` | string | no | Semantic version of the tool. |
| `description` | string | no | Human-readable description. |

### Convention entry

Each key under `conventions` is a **dimension name** from the Convention Taxonomy (see `taxonomy.yaml`).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `value` | string | no | The assumed convention value (e.g. `"dollars"`, `"ISO-8601"`). Omit if unknown. |
| `confidence` | string | yes | One of `"declared"`, `"inferred"`, `"unknown"`. |

**Confidence levels:**

- **`declared`**: The tool's author explicitly states this convention.  Highest trust.
- **`inferred`**: `bulla`'s heuristic classifier guessed this from field names or types.  Should be reviewed.
- **`unknown`**: The dimension is relevant but the value is not known.  Flags a potential blind spot.

## 4. Convention Taxonomy

The taxonomy is versioned alongside the spec and shipped as `taxonomy.yaml` inside the `bulla` package.  v0.1 defines 10 dimensions:

| Dimension | Description | Example values | Domain |
|-----------|-------------|----------------|--------|
| `date_format` | Date/time representation | ISO-8601, unix_epoch | universal |
| `amount_unit` | Monetary amount scale | dollars, cents, basis_points | financial |
| `rate_scale` | Rate/percentage normalization | decimal_0_1, percentage_0_100 | financial, ml |
| `score_range` | Score/rating range | 0_to_1, 0_to_10, 1_to_5 | universal |
| `id_offset` | Index base convention | zero_based, one_based | universal |
| `precision` | Numeric precision | 2_decimal, full_float64 | financial, scientific |
| `encoding` | Text encoding | UTF-8, ASCII | devops, universal |
| `null_handling` | Missing value representation | explicit_none, empty_string, zero | universal |
| `timezone` | Datetime timezone | UTC, local, US_Eastern | universal |
| `line_ending` | Text line endings | LF, CRLF | devops |

Custom dimensions (not in the taxonomy) are permitted.  Tools should prefix custom dimensions with their namespace (e.g. `myapp_color_space`).

## 5. Validation

Use `bulla manifest --validate <file>` to check a manifest against the schema.

Validation checks:
1. YAML parses correctly
2. `bulla_manifest` (or deprecated `seam_manifest`) field equals `"0.1"`
3. `tool.name` is present and non-empty
4. Each convention entry has a `confidence` field with a valid value
5. If `value` is present, it is a non-empty string
6. Dimension names that match the taxonomy are noted; custom dimensions produce an info-level message

## 6. Relationship to MCP

The Bulla Manifest is designed to be embeddable in MCP tool definitions as an optional metadata field.  A future MCP specification change could add:

```json
{
  "name": "invoice-parser",
  "description": "...",
  "inputSchema": { ... },
  "bulla_manifest": {
    "conventions": {
      "amount_unit": { "value": "dollars", "confidence": "declared" }
    }
  }
}
```

Until then, manifests are standalone YAML files colocated with tool source code.
