# ADR-001 — The standing model: operate the commons, never hold the score

**Status:** Accepted 2026-07-11.
**Context:** The program's own theory forces this decision. Chapter 2 of the constitutional volume defines the *trust tax* as the premium extracted for occupying a verification chokepoint, and Chapter 1 records what happens to verification infrastructure that consolidates (the Champagne fairs after royal annexation). A commercial framing in which "the operated record is the asset" is a proposal to occupy exactly such a chokepoint, and the free-market objection — *this layer will centralize and be co-opted* — is not paranoia but the base rate. The contradiction was internal before it was external. This ADR resolves it.

## Decision

The bulla standing model is the **Red Hat position, not the FICO position**: the operator monetizes *operation* — running registries well, assurance and conformance services, bond origination, convening — and may never monetize *data hostage-taking*. Three properties are binding on all future spec and implementation work:

### 1. Standing recomputability
Any party can re-derive a counterparty's standing from published receipts alone. The operator MUST NOT hold, sell, or gate a score that the public record cannot reproduce. Consequence: standing algorithms are published with the spec; a "proprietary standing score" is a spec violation, not a product tier. (This is the receipts analog of the fee lesson: sell the receipt, never the score.)

### 2. Any-log verification
A receipt verifies against **any** log that carries it. The verification path (signature → content recomputation → inclusion proof) MUST NOT depend on which operator's log served the receipt. Design consequence: the log interface follows Certificate Transparency's structural lesson — plurality of logs, gossip/checkpoint comparison for equivocation detection (the hook already exists: OTS-anchored checkpoints and gossiped signed roots, `registry.py:527`). The reference implementation is honestly a single-operator log today (`registry.py:57`); the spec's trajectory is multi-log, and no future feature may assume log monopoly.

### 3. Defection cost (the capture test)
Operator misbehavior scenarios, each with its mechanism and detection latency. Per the house rule, the adversarial test is the property:

| Defection | Mechanism today | Detection latency | Status |
|---|---|---|---|
| Rewrite history | Merkle consistency proofs against anchored roots | first consistency check after anchor | CLOSED |
| Equivocate (fork the log) | gossiped/anchored checkpoints compared across relying parties | first cross-party comparison | CLOSED in design; gossip transport unspecified | 
| Serve selectively / censor reads | `verify_served_deed` closes authenticity; *availability* censorship detectable only via a second source | mirror or second log required | **OPEN (2026-07-11)** — closes with any-log plurality |
| Refuse registration (gatekeep writes) | none in-protocol; mitigated by log plurality + open spec (run your own log) | — | **OPEN (2026-07-11)** — policy: registration criteria must be published and content-neutral |
| Hold standing hostage | Property 1 makes the hostage worthless | immediate (score recomputes publicly) | CLOSED by this ADR |

OPEN items are dated and carry their closing condition. A future revision that cannot close them must say so, dated, in this file.

**Named closing action (from external hostile review, 2026-07-12):** the single highest-leverage step is a second, independently operated log — ideally by a party with adverse interests — demonstrating a record written under one operator verifying and carrying standing under the other. Both OPEN rows close with it. Until it exists, this program is one fair with an excellent constitution.

## Rejected alternative: the FICO position

Operate the record as a proprietary compounding asset; monetize access to standing. Rejected because (a) it contradicts the corpus's own trust-tax theory and would be quoted against the program by any careful reader; (b) it re-creates the capture target the constitutional volumes exist to dismantle — the operator becomes the next kind master; (c) the co-option prediction then requires no adversary, only time. What is genuinely retained from the moat argument: *neutral standing is still scarce and still compounding* — but it compounds as trust in the operation (the auditor's franchise, the CA's franchise), not as exclusive data. Auditors and certificate authorities are large businesses on public standards; that is the revenue model this ADR selects.

## Consequences

- Spec v0.2 carries: evidence grounding classes, any-log verification language, and the standing-recomputability principle (see `action-receipt-v0.2-draft.md`).
- The LSVP deck's moat language shifts from "own the operated standing" to operation/commons verbs (aligned this sprint).
- The canon acquires a forward capture defense (ch12, "The Captured Registry") whose mechanisms are these three properties.
- Any future feature proposal that depends on data exclusivity fails review by citing this ADR.
