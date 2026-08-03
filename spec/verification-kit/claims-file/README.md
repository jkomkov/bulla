# Constructed disputed-payment claims file

`receipt.json` records a constructed `payments.charge` action for USD 125.00
against a declared USD 200.00 limit. The issuer contact uses a reserved example
domain. The local checker does not contact it.

The executable `payment-within-declared-limit` convention recomputes whether
the recorded USD 125.00 action conforms to the declared USD 200.00 limit. The
expected local verdict separates that machine check from authority and event
truth. A payment-rail record is still required to determine whether funds
moved.
