# Proxy Bridge Calibration

This example pack exercises the Phase 2 proxy through replayable traces before
moving back into theorem selection.

It contains:

- `traces/` — five curated session traces;
- `run_curated_sessions.py` — runs the traces against captured manifests and
  writes a calibration report.

The traces deliberately mix:

- one known-clean control,
- one known broken path-convention case,
- and three middle cases where the local fee signal is informative but the
  exact operational failure is less obvious from the flow alone.

## Run

```bash
cd bulla/examples/proxy-bridge
python run_curated_sessions.py
```

The script reads manifests from `../real_world_audit/manifests` and writes
`curated_session_report.json`.
