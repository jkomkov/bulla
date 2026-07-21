# Semantic Settlement Pilot Operations

The pilot's transport-neutral signed actions are implemented in
`bulla.experimental.pilot`: role enrollment, seam submission, deadline
commitment, blind-ID creation, deterministic arm assignment, adjudication,
corpus freeze, analysis attempt, and hostile challenge.

Operational slice target: 12 accepted foreign seams, two in each of six
domain/stratum cells, at least three independent authors and three independent
adjudicators with no implementation-team overlap, two adjudications per seam,
and three assignments per arm. Before 300 cases close, only operational facts
may be reported.

Current live status: `EXTERNALLY_BLOCKED_AWAITING_PARTICIPANTS`. The repository
contains schemas, the executable gates, recruitment copy, and signed-artifact
code. It contains no synthetic participant identities, no synthetic claim of
external adjudication, and no efficacy or arm-comparison statistic.

Hostile findings use signed `bulla.pilot.challenge` artifacts with an accepted
or rejected disposition and optional bounty reference. Stake, slashing, and a
challenge market are not implemented.
