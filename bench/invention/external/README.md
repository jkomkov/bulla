# External validation gate

This directory contains the v0.2 intake, blind-adjudication, recruitment, and
freeze contracts; it contains no purported external result. The preregistered
target is 100 foreign-authored seams in each of three domains, split evenly
between hidden-generative-contract and natural-expert strata. Authors,
adjudicators, and the implementation team must be disjoint. Efficacy analysis
is blocked until every domain/stratum quota and the role-separation gate close.

`freeze_external.py` recomputes every `SeamProblem` hash and rejects answer
labels or other undeclared instance fields. It also requires a frozen Git
commit, a pinned FRSL-1 specification digest, typed unique identities, and
disjoint implementation/author/adjudicator roles. `--allow-incomplete` may
validate and hash an intake batch but cannot mark it ready for blind evaluation.

Unsafe acceptance is counted per applied `RELY` decision; `RELY` applications
from `PARTIAL` packages enter the denominator. Seam-level and application-level
results remain separate, and escalation is always co-reported with certified
useful yield.

The remaining promotion gates are events, not repository edits: recruit
foreign authors, conduct blind adjudication, commission a zero-import
reproduction, and complete one external service integration.
