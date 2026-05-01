# Epistemic Receipt Demo

Three demos showing what makes Bulla different: it tells you whether its recommendation is **exact** or **approximate**.

## Run

```bash
cd bulla/examples/epistemic-demo
python run_demo.py
```

No network calls, no LLM, no randomness. Output is deterministic.

## Demo A: Exact Regime

Two servers (analytics, storage) share `path` and `timestamp` fields. The classifier detects hidden `path_convention` and `date_format` dimensions. Bulla recommends a repair and proves it is optimal:

```json
{
  "fee": 2,
  "geometry_dividend": 10.0,
  "sigma_star": 10.0,
  "regime": "exact"
}
```

**regime=exact** means: the geometry dividend is the true unavoidable cost. No cheaper repair exists.

## Demo B: Surrogate Regime

A synthetic three-server chain where `token` is a coloop — it must be disclosed in every repair, and its cost is unavoidable. Bulla honestly reports that its formula is an approximation:

```json
{
  "fee": 3,
  "geometry_dividend": 6.0,
  "sigma_star": 16.0,
  "regime": "surrogate",
  "forced_cost": 10.0,
  "downgrade": "coloop_burden"
}
```

**regime=surrogate** means: the geometry dividend is useful but not a provable bound. The `downgrade` field says why.

Coloops are rare in practice (0/373 in the calibration corpus) but arise in compositions with single-path credential or authorization dependencies.

## Demo C: Comparison

The same composition through two lenses:

| | Schema validation | Bulla |
|---|---|---|
| Visible problems | 0 | 2 hidden convention dimensions |
| Recommendation | None | Expose `path` and `timestamp` |
| Epistemic status | N/A | **exact** — provably optimal |

Others give you a recommendation. Bulla gives you the epistemic status of the recommendation.

## Files

- `manifests/` — frozen MCP tool manifests (analytics, storage)
- `trace_exact.json` — replay trace for Demos A and C
- `run_demo.py` — self-contained script producing all three demos
- `output/demo_output.json` — frozen output (checked in for reproducibility)
