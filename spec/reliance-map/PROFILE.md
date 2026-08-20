# Bulla Reliance Map 0.1 Experimental

**Maturity:** experimental · source-only · synthetic-public · team-operated

The Reliance Map identifies which declared downstream reliance decisions and
actions must be revisited when an authenticated correction targets one exact
artifact. It is an additive scale profile over the correction-recall semantics
already present in Handoff Admission 0.1; it does not alter that profile.

## Wire profiles

- `bulla.reliance-map/0.1-experimental`
- `bulla.reliance-correction-ledger/0.1-experimental`
- `bulla.reliance-map-context/0.1-experimental`
- `bulla.reliance-map-report/0.1-experimental`

The graph contains `HANDOFF`, `CAPABILITY`, `EVIDENCE`, `RELIANCE`, and
`ACTION` nodes. Typed edges are:

- `SUPPORTS`: handoff, capability, or evidence to reliance;
- `DERIVES`: reliance to reliance; and
- `INFORMS`: reliance to action.

Nodes and edges are canonically ordered, artifact digests are unique, and the
graph is finite and acyclic. The exact graph digest must be accepted by the
separately supplied verification context.

## Corrections

`reliance.correct` is an ActionReceipt v0.4. Its signed subject binds the exact
target and distinct replacement digests, reason digest, sequence, predecessor,
and authority epoch. The external context names the accepted correction
authorities, authority epoch, policy hash, and graph digests. Dossier-carried
keys or graph roots cannot bootstrap acceptance.

Corrections are append-only. Actor timestamps never determine order.
The signed receipt anchor also names the exact graph digest, preventing the
same correction from being replayed into a different accepted graph that
happens to contain the same target artifact digest.

## Report semantics

- `AFFECTED`: an exact declared path exists; the conditional action is
  `RECHECK_REQUIRED`.
- `NOT_AFFECTED`: no declared path exists and inherited ancestry is declared
  complete.
- `UNDETERMINED`: no known path exists but inherited ancestry is incomplete;
  the conditional action is `NO_AUTOMATIC_CLEARANCE`.

A known path takes precedence over incomplete ancestry. Every affected result
contains one deterministic, replayable path certificate per correction.

The result is recall, not rollback. It does not establish that the correction
is true, that the graph captures every worldly dependency, that an affected
action was unsafe, or that any consequence is authorized.

## Limits

- 32 MiB per JSON input;
- 16,384 graph nodes;
- 65,536 graph edges;
- graph depth 64 and 262,144 total emitted path steps;
- 16 corrections;
- depth 24 and 500,000 parsed JSON nodes;
- 4,096 UTF-8 bytes per string;
- safe integers only.

## Interfaces

```python
compute_reliance_map(graph_bytes, ledger_bytes, context_bytes, *, limits)
verify_reliance_map_report(report_bytes, graph_bytes, ledger_bytes, context_bytes)
```

The repository also contains a standalone Node checker and a browser-safe
kernel. Nothing is exported from `bulla`, installed as a CLI command, or added
to wheel or sdist membership.
