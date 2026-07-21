# Appraisal policy & recourse algebra — v0.1 (DRAFT, non-normative)

**Status:** DRAFT, 2026-07-16. NOT canon. Companion to
`commitment-slot-v0.1-draft.md`. Two extensions the slot lifecycle needs:
(1) how process evidence is contracted without inventing a third ontology, and
(2) how terms and consequences compose across a cross-org receipt DAG.

## Part A — Appraisal policy: attestation is a method, not a kind

The convention `kind` stays `{executable, semantic}` (v0.2 §5). Attestation is
an **evidence method**, not a different kind of rule, so it rides as an optional
**appraisal policy** on a convention rather than as a new `kind: attested`.

```jsonc
"appraisal": {
  "evidence_class": "execution_verified" | "third_party_anchored" | ...,  // §3 grounding
  "comparison_fn": "<namespaced ref, e.g. toploc-lsh-v1 | difr-token-v1 | exact | replay>",
  "tolerance": "<threshold, in the comparison_fn's units>",
  "oracle": { "ref": "<oracle identity>", "policy_hash": "sha256:…" }  // the bonded attester
}
```

This is deliberately the RATS vocabulary (attester / evidence / verifier /
appraisal policy / relying party), so a Bulla appraisal policy maps onto RATS
roles rather than reinventing them.

**Decidability, restated as kind × policy.** The adjudication class is derived,
not declared:

| Convention | Appraisal policy | Class | Enforcement |
|---|---|---|---|
| `executable` | none | **P2** | recompute conformance over `action.subject` |
| `executable` | present | **P3** | recompute the constraint AND check the oracle attestation against the policy |
| `semantic` | none | **P4** | forum + human panel (pinned opaque text) |
| `semantic` | present | **P3** | oracle attestation under policy stands in for the human read, where the policy admits it |

So `attested` is not a third row of the ontology — it is the **P3 cell**: an
otherwise decidable or opaque predicate whose evidence is an oracle attestation
under a named policy. The oracle is a bonded, receipted member (threat model §4),
never a trusted root; its attestation is itself a challengeable act.

**Process equivalence, not process identity.** An appraisal policy contracts an
*equivalence class* of furnishing processes — `{model_family, min_precision,
max_budget, approved_hardware_class, declared_randomness}` — not an exact trace.
A buyer rarely needs every execution detail, and demanding it turns process
assurance into maximal surveillance. The equivalence class is the contracted
object; the oracle attests membership in it, within `tolerance`.

## Part B — Recourse algebra: terms compose; consequences sequence

Two different things travel a subcontracting chain, and they have different
algebra. Conflating them (the "commutative coordination" gloss) is an error this
note corrects.

### B.1 Terms in v0.1: exact commitment, no invented lattice

This draft previously asserted union-of-obligations and intersection-of-permissions
as though a general term algebra already existed. It does not. Structured scope makes
one act's conformance decidable; it does not make arbitrary downstream attenuation or
term intersection decidable.

The v0.1 mechanism is therefore exact `term_root` equality. Every downstream party
accepts the same commitment, and may select a concrete member that satisfies its
executable constraints. Changing the commitment is a new, explicitly countersigned
order or novation — never an inferred intersection.

A future obligation lattice may define typed accumulation, narrowing, evidence-floor,
and contradiction operators. Until those operators have normative wire forms and
cross-implementation vectors, they are research candidates, not protocol claims.

### B.2 Consequences sequence (a total order per commitment)

Events are **not** commutative. `order → countersign → close`, and `challenge`,
`revocation`, `expiry` are strictly ordered within a commitment (slot spec §3–4):
a delivery before a deadline is a close; the same bytes after a timeout are not.
Consequences therefore **sequence** — each is placed by the commitment's ordering
authority, and the remedy ladder (`recompute → challenge → cure → revert → slash
→ escalate`) is itself an ordering. Liability propagation and challenge-window
inheritance across the chain use **explicitly declared** operators (a downstream
challenge window is `min(inherited, local)` unless the terms declare otherwise),
never an implicit default.

### B.3 The one-line rule

> **Terms stay pinned; consequences sequence.** In v0.1, every party accepts the
> identical term root; event ordering is total per commitment and never commutes.

This lets a cross-org procurement chain carry one as-agreed commitment without a
coordinating master while retaining one enforceable history per commitment under that
commitment's named ordering authority.

## Draft status

- General attenuation and term-composition operators: unwritten and deliberately
  unclaimed by v0.1.
- Interaction with v0.2 `detect_contradictions` (convention-value conflicts) is
  the natural machine consumer of the "CONTRADICTION, fail closed" rows — wiring
  unbuilt.
- Whether disclosure/grounding classes are *always* totally ordered under every
  relying-party context is an open modeling question; the partial-order fallback
  (fail closed) is the safe default until it is settled.
