# An agent run with a blind spot

Four actions dispatched by an agent harness. Three are wrapped with
`wrap_action` and emit verifiable ActionReceipts. The payment — the most
consequential action in the run — is dispatched outside the wrapped path and
emits none.

Every receipt that exists verifies. Verification cannot surface the payment,
because there is no receipt to verify. `event_coverage` reconciles the emitted
receipts against the harness dispatch log — the independent denominator — and
returns the payment as the unreceipted delta:

```
coverage 3/4, unreceipted: ["payments.charge:3"]
```

This is the negative complement to `examples/wrap-your-agent` (every action
covered) and the fleet-operations counterpart to the evaluation-incident
replay (a gateway bypass caught the same way).

## Run it

```sh
cd bulla
PYTHONPATH=src python examples/agent-fleet-blind-spot/run_demo.py
```

Output is deterministic (unsigned receipts, no timestamps); the committed
`demo-output.json` is byte-reproducible and drives the site's blind-spot
walkthrough at `/bulla/blind-spot`.
