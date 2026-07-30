# Causal Answerability v0.1

Status: source-only experimental profile. It does not change ActionReceipt v0.2,
the stable Bulla API, or any production payment path.

## What it adds

The reference path is deliberately causal:

```text
durable intent -> dispatch commitment -> external I/O -> outcome/reconciliation
              -> challenge -> finding -> authorized remedy -> close
```

`bulla.experimental.action_boundary` commits a signed v0.4 intent to a local
SQLite/WAL journal before calling an adapter. The journal uses compare-and-swap
transitions, exact retained receipt bytes, parent hashes, and an idempotency key
binding. A remote effect that may have happened is `UNKNOWN`, never silently
reported as failure. The guarantee is durable local precommitment and typed
recovery under the declared adapter contract—not distributed atomicity.

`bulla.experimental.challenge` represents the challenge lifecycle as another
append-only ActionReceipt trace. Opening a case does not confer authority to
decide it. Forum and remedy authority are separately checked. A finding cannot
mutate the challenged record, and a completed remedy requires a sustained
finding, an authorized remedy transition, execution evidence, and explicit
closure.

## Run the captive demo

```bash
bulla experimental boundary demo
bulla experimental boundary inspect JOURNAL INTENT_ID \
  --authority-regime-hash sha256:... --semantic-epoch sha256:...
bulla experimental challenge open RECEIPT.json \
  --scope-hash sha256:... --semantic-epoch sha256:... \
  --deadline-domain witness --deadline-value 100 \
  --deficiency "amount disputed" --remedy challenge \
  --claimed-at 2026-07-21T22:00:00Z --key challenger.key -o case.json
bulla experimental challenge replay case.json
```

The demo is network-free and classified `INTERNAL_CAPTIVE`. The local payment
rail proves the runtime contract can be exercised; it does not prove a provider,
forum, or institution will honor it.

## Recourse evidence is a trace

The replay reports achieved levels rather than one reachability Boolean:

1. `DECLARED`
2. `TRANSPORT_REACHABLE`
3. `CASE_ACKNOWLEDGED`
4. `FINDING_ISSUED`
5. `REMEDY_AUTHORIZED`
6. `REMEDY_COMPLETED`

The included forum fixture may reach every level but remains team controlled.
Operational recourse requires a separately controlled forum to complete a path.

## Query-relative survivability

`bulla.query-answerability/0.1-experimental` is a separate finite reference
profile. It asks whether declared evidence custody preserves one consequential
query against a mobile administrative adversary:

```bash
bulla experimental answerability assess problem.json -o assessment.json
bulla experimental answerability verify problem.json assessment.json
bulla experimental answerability plan problem.json catalog.json -o plan.json
```

The assessor computes an exact model-relative corruption margin or emits a
replayable execution-pair and corruption-schedule counterexample. The planner
selects a minimum-cost set from at most twenty declared witness candidates.
Both fail closed at their explicit search limits.

The custody document is a literal time-expanded graph: each edge advances one
epoch, missing retention cannot be skipped, and only delivery enters the
challenge epoch. Occurrence bindings and opaque values must be canonical
SHA-256 digests.

This profile does not infer a real-world occurrence from a receipt. It does not
assess non-equivocation, authority, or remedy, and it does not change either
ActionReceipt wire format. The temporal path, label-cut, and test-cover
mathematics are treated as prior art; the surviving contribution is a bounded
engineering instrument.

## Qualification boundary

Golden F13 contains 96 internal machine cases. The finite reference model
exhausts one-intent schedules, bounded two-intent interleavings, forum/remedy
authority assignments, and eight critical guard mutants. All external counters
remain zero. See `bench/golden/v0.6` for the packet and exact classification.

The query-answerability campaign adds 5,152 exhaustive single-chain normal
forms and 10,000 seeded differential cases with zero mismatches between the
Bulla implementation and the standalone checker. These remain internal machine
cases; they do not increment any external counter.
