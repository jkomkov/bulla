# External pilot v0.2 recruitment packet

Status: **ready to recruit; no external participant or result is represented in
this repository.** The engine is frozen at
`30619618ed74c134aa94cbf7c6f5f8ef440df460` in `pilot-plan.json`.

## Roles

- **Authors** contribute seams from one of the three declared domains without
  seeing engine output. Each seam is labeled either
  `hidden_generative_contract` (objective truth generated and retained by the
  pilot operator) or `natural_expert` (genuine institutional interpretation).
- **Adjudicators** evaluate blinded artifacts and applied decisions. They never
  see arm identity.
- **Independent implementers** receive the profile, canonical artifacts, and
  vectors, but not Bulla's checker, and reproduce every verdict.
- Implementation-team, author, and adjudicator identities must be disjoint.

## Author handoff

Provide the local theories, protected vocabulary, available evidence, authority
and scope, intended target decision, and at least three representative
applications. Do not encode the desired compiler output or disclose the hidden
truth inside the shared vocabulary. The intake gate recomputes the problem hash
and rejects undeclared answer fields.

## Adjudicator handoff

For every application, determine whether `RELY` would have been safe. Also mark
whether protected meanings were conserved, escalation was unnecessary, the
failure was grammar-only, an enrichment request was useful, governance was
actually required, and the countermodel explained the missing distinction.
Use `adjudication.schema.json`; narrative notes remain in a separately hashed
document.

## Frozen analysis rule

No efficacy analysis begins until all six domain/stratum cells contain 50
accepted seams and role separation passes. Unsafe acceptance is computed over
applied `RELY` decisions, including `PARTIAL` RELY emissions. No denominator may
be changed after outcomes are visible.
