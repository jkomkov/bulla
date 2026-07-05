# Cross-boundary bonded transaction

The flagship example: **why receipts exist.**

An agent that acts across an organizational boundary — hired at runtime, stateless,
gone before any consequence lands — has nothing to lose. Every enforcement
mechanism civilization uses (reputation, disbarment, liability) presupposes a
persistent entity that values its future. A runtime agent has none. So the party
relying on it requires a **bond**: something at stake, pre-funded, that persists
when the agent does not.

The **receipt** is what makes the bond slashable. The agent acts; bulla mints a
signed `ToolCallReceipt` whose `diagnostic_ref` is a **recomputable verdict** on
what it did. The agent vanishes. A bystander — the counterparty, an auditor,
anyone — recomputes the verdict from the receipt's pinned inputs and, on an
objective breach, **slashes the bond with no oracle and no arbitrator.**

> You cannot jail a fork. You can slash its bond.

```
python demo.py
```

```
1. Agent B posts a bond: 50000 USD  (slash condition: undisclosed convention deficit, recomputable)
2. Agent B executes the settlement, signs a receipt, terminates.
3. A bystander audits:
     · receipt verifies to 'attestation'  (B's signature — it committed)
     · recomputed the pinned composition: fee=7
     · SLASHED: 50000 USD → Org X
```

### What this is, and is not

- **Is:** the senior tranche. The coherence fee is an **objective, recomputable
  trigger** and a **cap** (the number of disclosures owed). Any party re-derives
  it from the pinned composition — that is the one thing no signed-log competitor
  can offer, because their receipts contain signatures over assertions, not a
  verdict anyone can recompute.
- **Is not:** a severity price. The fee does not say how *bad* the breach was.
  Pricing the harm is the **junior tranche**, and it needs an adjudicator (a
  carrier). That conversation — not more code — is what turns this into an
  insurable instrument.

### Status

The bond here is a **stub** (an in-process escrow object) — the mechanism, not the
rail. Real escrow (x402 / AP2) and the two-tranche pricing arrive when a
cross-boundary design partner and a carrier pull them into existence. The receipt
substrate they consume is what ships now.
