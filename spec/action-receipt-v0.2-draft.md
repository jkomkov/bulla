# ActionReceipt v0.2 — DRAFT additions (not normative; folds into the v0.2 release)

**Status:** FOLDED into the normative `action-receipt-v0.2.md` (2026-07-13);
retained as the design record. Original status line follows.

**Status:** draft, 2026-07-11. These sections extend v0.1 and ship together with the already-planned v0.2 changes (RFC 8785/JCS canonicalization, the CANON_VERSION-2 reslice, a signed golden vector) so that canon migrates exactly once. Nothing here is implemented; per the program's build gate, implementation waits for the v0.2 release decision. Design authority: `ADR-001-standing-model.md`.

## 1. Evidence grounding classes

v0.1's `evidence_refs` entries are opaque `{name, hash}` pointers. v0.2 adds a required `grounding` field per entry:

```
"evidence_refs": [
  { "name": "<str>", "hash": "sha256:…", "grounding": "<class>" }
]
```

`grounding` ∈:

| Class | Meaning | Canonical example |
|---|---|---|
| `self_asserted` | produced by the acting party; testimony with a timestamp | the actor's own execution log |
| `third_party_anchored` | held or countersigned by an independent system the actor does not control | a rail's transaction record, a git commit on a remote with protected history the actor does not administer |
| `counterparty_signed` | bears the signature of the party on the other side of the act | a countersigned mandate, a signed delivery acknowledgment |
| `execution_verified` | re-derivable by re-running a pinned computation from pinned inputs | a deterministic recomputation, a replayed transformation |

**Relativity note:** classes rank relative to the challenging party — a counterparty signature grounds the record only against its signer; against a stranger to the transaction (a regulator, an insurer, a third party harmed), bilateral collusion makes independent third-party anchors the stronger class.

**Display rule:** a receipt's effective grounding is the **minimum** class over its *necessary* evidence — the set without which the verdict does not recompute. Verifiers MUST surface effective grounding alongside the verdict; a `digest`-valid receipt whose necessary evidence is `self_asserted` MUST NOT be presented as more than attested testimony.

**Rationale:** the receipt proves the claim and the authority; the world enters through the anchors, and the record inherits the grounding of its weakest necessary anchor. Making grounding first-class prevents the elision ("recomputable" heard as "true") that this field's absence invites.

## 2. Any-log verification

The verification path — signature check, content recomputation, inclusion proof — MUST NOT depend on which operator's log served the receipt. A receipt carried by any log verifies identically. Consequences: log identifiers are informative, never authoritative; consistency is checked against anchored/gossiped checkpoints (the hook exists at `registry.py:527`); the reference implementation's single-operator scope (`registry.py:57`) is a stated limitation of the implementation, not a property of the format. Design target: Certificate Transparency's plurality structure.

## 3. Standing recomputability (normative principle)

Any standing, score, or reputation derived from receipts MUST be recomputable from published records using a published algorithm. An implementation or operator offering standing that the public record cannot reproduce is non-conformant. (ADR-001 §1; the receipts analog of "sell the receipt, never the score.")

## Open items (dated)

- Gossip transport for checkpoint comparison: unspecified (2026-07-11).
- Registration-refusal mitigation beyond open-spec/run-your-own-log: policy only (2026-07-11) — registration criteria must be published and content-neutral.
- Tolerance semantics (`verification_semantics` block: comparison_fn/threshold/seed regime/environment class) — specified in the companion-profile one-pager; merges here at v0.2 release.
