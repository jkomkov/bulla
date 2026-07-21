# Semantic control-plane demo

This offline demo launches two real local subprocess tools. A delivery tool and
a claim router disagree about the acceptance predicate until the contract-time
engine compiles a protected shared term. The package is cached by its complete
compilation key, checked by the zero-Bulla-import verifier, and applied twice in
the data plane. Each application becomes a signed ordinary ActionReceipt and is
replayed from the tool output plus the pinned package.

```sh
PYTHONPATH=bulla/src python bulla/examples/semantic-control-plane/run_demo.py \
  --fixture-keys \
  --output bulla/examples/semantic-control-plane/demo-output.json
```

This establishes local protocol composition, not a production provider,
external witness, insurance, or settlement integration.
