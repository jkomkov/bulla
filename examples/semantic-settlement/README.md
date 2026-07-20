# Procurement Shadow: Semantic Settlement v0.1

Run from `bulla/`:

```sh
PYTHONPATH=src python examples/semantic-settlement/demo.py
python -I scripts/verify_semantic_finality.py \
  bench/invention/semantic-settlement/reproduction-vectors/procurement-provisional.internal.json
```

The deterministic shadow workflow keeps seller “dispatch” and buyer “custody
transfer” distinct, locks the exact worst-case reserve in a simulated adapter,
executes provisionally, admits carrier evidence, releases the antitone reserve,
finalizes, witnesses a closure/authority revision through two operators, and
returns `TERM_STALE` for the old epoch. Two governance-limited cases return
`ROUTE/CHOICE_REQUIRED`.

This is not a payment system. The adapter does not prove custody,
collectibility, legal authorization, or production settlement.
