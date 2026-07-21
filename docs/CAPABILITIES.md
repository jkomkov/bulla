# Bulla capability reference

This is the detailed inventory behind the shorter package front door. Maturity
and availability are separate: code can be present in the repository without
being a stable API or part of the current PyPI distribution.

## Stable Core

- **ActionReceipt v0.2.** Canonical JSON, four hash preimages, action and
  diagnostic references, evidence grounding, recourse envelopes, conventions,
  anchors, and zero-import vectors.
- **Receipt verification.** Digest, signature, authority, delegation, bounds,
  grounding, convention, and recourse dimensions remain separately visible.
- **Coverage.** Reconciles published PyPI releases with verified release
  receipts and classifies contemporaneous, reconstructed, missing, and invalid
  records.
- **Registry and inclusion.** RFC-6962-style append-only log, inclusion and
  consistency proofs, pinned-root verification, and optional OpenTimestamps
  anchoring. A host-served root is not independent evidence.
- **Composition toolchain.** Deterministic diagnostics, convention packs,
  bridges, translations, MCP scanning, framework adapters, and exact witness
  geometry. See the legacy-boundary document for the current claim scope.

## Opt-in released drafts

- **ActionReceipt v0.3.** Adds `authorization_hash` and a content-signer proof
  binding the complete authority/bounds/recourse envelope.
- **Delegation.** Domain-separated did:key grants with surfaced chain,
  principal, policy, scope, temporal, and revocation states.
- **Structured bounds.** Reuses `jsonschema+quantum/1` to report whether an
  action subject conforms to its declared bounds.
- **Reliance.** Authored policies produce `RELY`, `REFUSE`, or `ESCALATE`; a
  `bulla.rely` ActionReceipt records the consumer's decision.
- **Routed inference.** A closed single-router/single-provider grammar with
  fourteen zero-import adversarial traces. It demonstrates local conformance,
  not live-provider interoperability.

## Experimental profiles

Experimental modules live under `bulla.experimental` and are not re-exported
from the stable package root.

- **Semantic invention.** Finite FRSL-1 predicate packages, independently
  checked full/partial envelopes, fixed-language countermodels, and preserved
  refusal checks.
- **Semantic refinement.** Evidence offers, conservation manifests, world-set
  narrowing, explicit `PRESERVE / REFINE / REVISE / ROUTE`, and stale epochs.
- **Semantic Finality.** Consequence profiles, worst-case ambiguity reserves,
  provisional execution, conflict non-mutation, finalization, and staleness in
  a finite shadow model.
- **Golden Gate.** Machine, property, and adjudication oracle strata; typed
  correct abstention; metamorphic, mutation, portability, and custody methods.
- **Claim Flow.** Explicit appraisal, forum finding, precedent adoption,
  applicability, and settlement transitions with proposition-specific
  authority.
- **Generalization Frontier.** Checked maximal safe-scope antichains under a
  fixed model, authority regime, closure warrant, harm predicate, and epoch.

## Research only or blocked

- Constructive Beth extraction for geometric sites is not implemented.
- Open-world semantic completeness is not established.
- External adjudication and independent implementation parity are pending.
- Production collateral, settlement custody, witness plurality, stake,
  slashing, and a witness market are not available.

See [What Exists Today](https://glyphstandard.com/status) for the generated
status record and external-participant counts.
