# Retired example: fee-gated bond

This example was retired in Bulla 0.44.0 because it treated the coherence fee
as both an objective slash trigger and a collateral cap. That was not licensed
by the evidence: the fee is an optional disclosure diagnostic, not an
execution-failure or harm measure.

The ActionReceipt remains a durable record that an external settlement policy
may reference. Bulla does not currently implement a bond, staking pool, slash
rail, or economic guarantee. A future collateral integration requires an
objective fault predicate, a real settlement rail, and independently operated
witnesses; none is inferred from the fee.
