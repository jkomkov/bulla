# Wrap your agent's actions

A tiny agent loop whose tools each emit a verifiable ActionReceipt, then a
coverage check that confirms every action carried one.

Each tool is decorated so a call emits a receipt with no envelope tree to
hand-build:

```python
from bulla import wrap_action, verify_receipt, event_coverage

@wrap_action("fs.write", {"path": "/tmp/out.txt"})
def write_file(path, content):
    ...

# every wrapped call leaves a receipt on the scope object
```

The harness records each dispatched call as the independent denominator, then
`event_coverage` reconciles the emitted receipts against it. Here every action
is wrapped, so coverage is full — the positive complement to the incident
replay, where a bypassed action leaves no receipt and the same reconciliation
surfaces it.

## Run it

```sh
cd bulla
PYTHONPATH=src python examples/wrap-your-agent/run_demo.py
```

Output: three observed actions, three receipts, coverage `3/3`, no unreceipted
actions. Writes `demo-output.json`. The Python API is documented at
`/bulla/sdk`; the two-flag CLI equivalent is `bulla receipt create`.
