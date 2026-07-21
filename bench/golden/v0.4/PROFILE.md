# Golden Gate v0.4 — Precedent Yield Qualification Profile

This profile evaluates typed warrant flow, finite precedent yield, finality
obstruction, and laundering resistance. Normative rules are in
`bulla/spec/claim-flow-v0.4-experimental.md`.

Qualification requires:

- exactly 240 lineage cases and 48 holdout cases;
- exactly 52 F11 cases across the frozen 13-by-4 matrix;
- zero unsafe transfer, cross-scope reuse, refusal erasure, ambient authority,
  categorical-harm pricing, or accepted strategic truncation;
- every resource exit binds an earlier budget policy, logical counters, and a
  replayable frontier;
- exact minimality only after exhaustive finite search;
- standalone replay with no import of production `bulla`;
- preserved v0.1, v0.2, and v0.3 frozen roots.

Passing this profile is `INTERNAL_CAPTIVE`; it is not external validation.
