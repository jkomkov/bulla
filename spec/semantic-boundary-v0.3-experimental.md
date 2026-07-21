# Semantic Boundary Profile 0.3 (Experimental)

Profile identifier: `bulla.semantic-boundary/0.3-experimental`

Status: research-only. This profile does not change stable Bulla imports,
ActionReceipt schemas, FRSL-1, or the Semantic Finality result algebra.

## Boundary model

Bulla keeps three substantive boundaries distinct:

1. **Semantic:** whether the protected vocabulary determines the disputed
   predicate inside the declared model class.
2. **Grounding:** whether the available record warrants that the relevant
   premise obtains for the declared purpose and scope.
3. **Authority:** whether the bound institution may impose the consequence.

The procedure's status is meta-level, not a fourth substantive fact:
`CERTIFIED`, `PARTIAL`, `RESOURCE_BOUNDED`, or `INVALID`. More computation may
change this derivation status without supplying meaning, evidence, or authority.

## Claim separation

`ClaimChain` binds qW, qE, and qS:

- qW is a scope-relative world claim carrying declared warrants;
- qE is model-relative entailment carrying its certificate and derivation
  status;
- qS is an authority- and epoch-bound settlement claim.

qE never upgrades qW. qS never upgrades qE or qW. A mismatch among the bound
assessment, authority regime, semantic epoch, or closure warrant makes the term
stale before action.

## Harm floor

`COMPENSABLE_RESERVED` alone participates in reserve arithmetic.
`REVERSIBLE_ONLY` requires a rollback binding and bars finality;
`HUMAN_REVIEW_REQUIRED` routes to the named forum; and `CATEGORICAL_REFUSE`
refuses. No reserve amount converts the latter three into compensable outcomes.

## Refinement and history

Within one semantic epoch, refinement may resolve ambiguity but must preserve
every prior RELY and REFUSE cell. Cross-epoch reuse is invalid. Supersession is
a new semantic act; it does not rewrite the historical trace certified under
the prior epoch.

## Evidence boundary

All current implementation, Lean, and Golden evidence is captive and relative
to declared finite models and supplied authority/closure artifacts. It does not
establish open-world truth, legal validity, independent witnessing, or
production settlement safety.
