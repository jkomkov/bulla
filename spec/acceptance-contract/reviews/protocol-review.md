# Acceptance Contract alpha — protocol review

**Tier:** R3  
**Scope:** contract identity, external trust context, receipt binding, requirement
routing, conditional closure, consequence staging, parser limits, manifests, and
finite-model correspondence.

## Material findings and corrections

1. **A request cannot include its own contract hash.** The first draft required
   `contract_hash` inside a request that was itself a member of the contract.
   The request now binds contract ID and revision, while the later signed
   artifact binds the resulting contract hash. Regression coverage checks that
   the contract remains non-circular.
2. **Coverage status needed structural support.** A bare `COVERED` string did
   not establish the declared receiver anchor or the observed/receipted set
   relation. The accepted coverage adapter now binds the anchor and exact finite
   sets, and the hostile `effect-bypass-001` case proves that an unmatched
   observed effect refuses the consequence without breaking receipt integrity.
3. **The evaluator imported private inference helpers.** Strict JSON parsing and
   canonical hashing now live in the profile module, eliminating an unrelated
   experimental dependency from the source closure.
4. **ActionReceipt v0.4 terminates at attestation.** The initial check expected a
   nonexistent `authorization` verification rung. The evaluator now requires
   attestation plus direct signer-bound authority, matching the published v0.4
   verifier semantics.

## Disposition

`PASS` after focused regression. The three canonical outcomes, hostile parser
and trust cases, deterministic generation, distribution exclusions, and Lean
model pass. The review does not promote the profile or establish external
implementation.
