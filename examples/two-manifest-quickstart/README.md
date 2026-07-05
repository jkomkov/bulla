# Bulla Two-Manifest Quickstart

**Goal**: in 2 minutes, take two MCP-style composition manifests, get
back the witness rank and the precise list of fields to expose to make
the composition safe.

This is the "Shannon-moment" demo: the theorem is the target; this
example is the running implementation that lets engineers feel it
directly.

## Setup (30 seconds)

```bash
pip install bulla
```

(Or from this repo: `pip install -e bulla/` from the repo root.)

## Run the quickstart (10 seconds)

```bash
bulla compose example_fetch_memory_joint.yaml
```

This composition is extracted from the real source code of two MCP
servers (modelcontextprotocol/servers — `fetch` and `memory`). On the
surface they look composable: their output schemas type-check. Below
the surface they carry hidden convention assumptions about encoding
and content format that disagree silently.

You should see:

```
  Witness rank (fee): 2  ⚠ refuse_pending_disclosure

  2 blind-spot dimensions forming 2 independent obstruction classes.

  To make this composition safe, expose 4 fields:

    1. tool `fetch`, field `encoding`
       Action: add `encoding` to fetch.observable_schema
       Bridges blind spot on edge: encoding_match
    ...

  Apply all bridges automatically:
    bulla bridge example_fetch_memory_joint.yaml --output joint_bridged.yaml
```

## Apply the auto-bridge

```bash
bulla bridge example_fetch_memory_joint.yaml --output joint_bridged.yaml
bulla compose joint_bridged.yaml
```

The bridged version should report `fee = 0` (coherent).

## What you just did

You verified, on real composition data, the substance of the
**Disclosure Characterization Theorem**: the witness rank `r` is a
unique structural invariant. Going from `r = 2` to `r = 0` required
exposing exactly `r = 2` independent cocycle generators (the four
listed field-exposures form 2 independent classes). No amount of
clever evaluator engineering can avoid that cost — the theorem says
you need at least `r` bits of receipt to certify coherence.

The prescriptive output tells you, for your composition, exactly
where to pay that cost: which tools' internal state to surface
as observable schema.

## What's next

- `bulla compose --format json` — emit the structured `WitnessReceipt`
  for programmatic use.
- `bulla.langgraph.bind(graph)` — same analysis on a live LangGraph
  workflow (no YAML needed).
- `bulla.crewai.bind(crew)` — same for CrewAI.
- `bulla diagnose --witness yourfile.yaml` — full diagnostic output
  with witness geometry.

## References

- The theorem: `papers/composition-doctrine/paper.md` §5 (Disclosure
  Characterization Theorem 5.1).
- The empirical contact: `papers/composition-doctrine/one-invariant-four-resources/paper.md`
  (4-model EvalGap panel; chance accuracy without receipts, 100%
  with receipts).
- The Lean formalization: `papers/composition-doctrine/lean/`
  (Aristotle-verified, sorry-free).
