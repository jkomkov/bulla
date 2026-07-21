# Migrating to Bulla 0.44

Bulla 0.44 preserves ActionReceipt v0.2, canonical preimages, stable imports,
and the existing result algebra. The main behavioral changes are explicit
authority binding, bounds conformance, reliance decisions, and verdict objects
that refuse Boolean coercion.

## Required code change

Do not write:

```python
if verify_receipt(receipt):
    use(receipt)
```

Verification has no single truth value. Read `.ok` only for record integrity,
inspect the named authority/grounding/recourse dimensions, or pass the complete
verification view to an authored `ReliancePolicy`.

## ActionReceipt v0.3

Version 0.3 is an opt-in, non-normative released draft. It binds the exact
authority, bounds, and recourse envelope to the content signer. Existing v0.2
receipts and preimages remain byte-compatible; adding an authorization proof
upgrades a produced receipt to v0.3.

## Coherence-fee policies

Fee gating remains available only when a caller explicitly requests it. The
default enforcement path treats the fee as disclosure information and does not
infer execution failure from it.

Read the [0.44 changelog](https://github.com/jkomkov/bulla/blob/main/CHANGELOG.md)
and [release contract](RELEASE-0.44.0.md) for the complete compatibility and
publication record.
