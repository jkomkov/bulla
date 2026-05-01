# Session Proxy

## Purpose

`bulla.proxy.BullaProxySession` is the Phase 2 programmatic proxy surface for
composition-aware sessions.

It does **not** proxy JSON-RPC transport directly yet. Instead, it adds session
state above the current witness kernel:

- static composition diagnosis for the participating server set;
- explicit tracking of which prior output field flowed into which later input
  field;
- accumulation of flow-level structural conflicts;
- receipt chaining across the session.

This is the smallest useful product step because it is already enough for:

- semantic regression checks,
- deployment preflight traces,
- and theorem work on local updates.

## Current API

```python
from bulla import BullaProxySession

session = BullaProxySession(
    {
        "source": [source_tools_list],
        "target": [target_tools_list],
    }
)

left = session.record_call("source", "list_orders", result={"status": "open"})
right = session.record_call(
    "target",
    "filter_orders",
    arguments={"status": "open"},
    argument_sources={"status": session.make_ref(left.call_id, "status")},
)
```

Each `record_call()` returns a `ProxyCallRecord` containing:

- the call identity,
- the arguments/result snapshot,
- the concrete source-to-target `flows`,
- the `local_diagnostic` for the traced call cluster,
- and the new chained `receipt`.

## CLI replay surface

The current user-facing replay path is:

```bash
bulla proxy --manifests DIR trace.json
```

The trace file is either:

- a JSON array of call objects, or
- a JSON object with a top-level `calls` array.

Each call object may include:

- `server`
- `tool`
- `arguments`
- `result`
- `argument_sources`

where `argument_sources` maps target field name to:

```json
{
  "call_id": 1,
  "field": "path"
}
```

The replay output includes:

- the static baseline diagnostic,
- each traced call's `flows`,
- each call's `local_diagnostic`,
- the final chained receipt,
- and any accumulated structural conflicts.

## What counts as a flow conflict

The proxy reuses the structural classifier already used at composition time:

- `contradiction` flows are direct visible incompatibilities;
- `homonym` flows are escalated to runtime structural conflicts because the same
  field name is being routed across incompatible schemas;
- `agreement` and `synonym` flows are recorded but do not raise the structural
  contradiction score.

## Local diagnostic and repair geometry

Each `record_call()` computes a `LocalDiagnosticSummary` for the **call cluster** —
the transitive closure of tools connected by explicit flows. The summary includes:

- `cluster_call_ids`: which prior calls are in the cluster
- `n_tools`, `n_edges`, `betti_1`, `coherence_fee`, `blind_spots`, `contradictions`
- `repair_geometry`: present only when `coherence_fee > 0`

`RepairGeometry` contains the full repair landscape for the cluster: `fee`, `beta`
(basis count), `repair_entropy`, `component_sizes`, `stability_ratio`,
`robustness_margin`, `repair_mode`, `recommended_basis`, `greedy_basis`,
`field_costs`, and the forced/residual decomposition fields: `forced_cost`,
`geometry_dividend`, `sigma_star`, `residual_regime`.

## Epistemic receipt

`EpistemicReceipt` is a narrow product-facing view derived from `RepairGeometry`.
It answers one question: **what does Bulla promise here, and with what confidence?**

Access it via `record.local_diagnostic.repair_geometry.epistemic_view()`.

### Fields

| Field | Always present | Description |
|---|---|---|
| `fee` | yes | Obligation count |
| `geometry_dividend` | yes | Saved cost from geometry-aware repair |
| `sigma_star` | yes | Optimal repair cost |
| `regime` | yes | `"exact"` or `"surrogate"` |
| `forced_cost` | regime != exact | Cost of coloop obligations |
| `downgrade` | regime != exact | Reason: `"coloop_burden"`, `"nonuniform_essential"`, or both joined with `+` |
| `recommended_repair` | when available | Minimum-cost basis as `[[tool, field], ...]` |

### Regime gate

- **exact**: `residual_regime == "uniform_product"` and `forced_cost == 0`. The
  geometry dividend is the true unavoidable cost; `sigma_star` is exact.
- **surrogate**: coloops present (`forced_cost > 0`) or non-uniform essential matroid
  (`residual_regime != "uniform_product"`). The formula is a useful approximation
  but not a provable bound. The `downgrade` field says why.

### Surface separation

The epistemic receipt is **local to a call cluster** and is NOT part of the sealed
`WitnessReceipt` hash. The session-level `WitnessReceipt` is content-addressed and
covers the full composition. The `EpistemicReceipt` is a derived view emitted as a
sibling `"epistemic_receipt"` key in CLI JSON output.

### CLI output

When `bulla proxy` replays a trace, each call record includes:

```json
{
  "call_id": 2,
  "local_diagnostic": { ... },
  "epistemic_receipt": {
    "fee": 1,
    "geometry_dividend": 3.0,
    "sigma_star": 7.0,
    "regime": "exact"
  },
  "receipt": { ... }
}
```

The `epistemic_receipt` key appears only when `repair_geometry` is non-null (i.e.,
when `coherence_fee > 0` in the local cluster).

## Intentional limitations

The current proxy is intentionally narrower than a full transport proxy:

- flow sources are explicit (`argument_sources`) rather than inferred;
- there is no live `tools/call` forwarding layer yet;
- it chains receipts but does not mutate the underlying composition;
- it reuses the current schema classifier rather than introducing a new typed
  coercion system.

Those limitations are deliberate. They keep the proxy factual and make it a
clean bridge between the present product and the next incremental-update theorem
surface.
