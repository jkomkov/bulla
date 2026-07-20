# Golden Gate v0.2 specification

## Frozen surfaces

Golden v0.2 does not change FRSL-1, stable Bulla imports, ActionReceipt schemas, or `SynthesisResult`. New evidence types live under `bulla.experimental`.

The candidate must be frozen before reviewer-originated cases are created. The freeze binds the full candidate commit, specification hash, scoring hash, public corpus hash, runner hashes, environment manifest, and custody procedure.

## External evidence constitution

Three curator-custodians, one clean-room implementer, at least six adjudicators, and the implementation team are role-disjoint. Each curator contributes twelve hidden cases: eight machine/property cases and four adjudication/open-world challenges. Oracle material is encrypted with SOPS to three reviewer-controlled age key groups using threshold two. The implementation team holds no decryption key.

Both implementations receive plaintext inputs only. Each submits a receipt-bound output hash before two curators reveal. Compensation, organizational ties, conflicts, and prior exposure are recorded. External status cannot promote from a template or an author-generated identity.

## Correct abstention

Adjudication is lexicographic and never collapsed to one score:

1. confirmed unsafe acceptance;
2. confirmed unsafe refusal;
3. unauthorized governance selection;
4. unsupported acceptance under primary-adjudicator disagreement;
5. correct typed abstention, separately `ESCALATE`, `CHOICE_REQUIRED`, and `INDETERMINATE`;
6. useful evidence requests;
7. safe coverage;
8. separate burden coordinates.

An unsafe or safe label requires two agreeing primary adjudicators. Disagreement is retained even after a diagnostic third review.

## Metamorphic semantics

Ninety-six base cases, twelve from each F1-F8 family, receive fourteen declared transformations. A relation declares preserved fields and permitted changes. Canonical byte equivalence requires hash equality; logical equivalence requires semantic-exit and protected-consequence equality only. Forcing transformations must produce their declared fail-closed exit or cause.

## State-space evidence

The economic checker abstracts money at `0`, `reserve-1`, `reserve`, `reserve+1`, and `2*reserve`, enumerates state and guard combinations to a fixed point, and separately checks a two-commitment shared-collateral model. Reports expose denominators for states, accepted and rejected transitions, boundary pairs, causes, terminal phases, fairness, and shortest witnesses.

Weak fairness means a continuously enabled and authorized completion action eventually executes while the witness clock advances. Without that assumption, the watchdog converts starvation to `EXPIRE`; an explicit `ROUTE` action converts it to `ROUTED`.

## Certified-anytime synthesis

Complete positive and negative vectors are generalized into cubes only when literal removal leaves no opposite vector in the cube. Subsumption and greedy sufficient cover may emit a checked formula immediately. Branch-and-bound is a size optimization, not a trust root. If a full formula exceeds 4,096 nodes, checked RELY and REFUSE regions may be emitted as `PARTIAL`; incomplete model enumeration remains `INDETERMINATE`.

## Evidence labels

- `internally verified/captive`: repository-controlled execution;
- `externally reproduced`: clean-room parity after custody reveal;
- `externally adjudicated`: two primary ratings per found-data case, with disagreement visible;
- `blocked`: a required external act or platform observation has not occurred.

No artifact in this directory establishes open-world safety, production settlement readiness, real traffic, incentive independence, or actuary endorsement.
