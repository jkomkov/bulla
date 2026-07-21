# Commitment Slot — v0.1 (DRAFT, non-normative)

**Status:** DRAFT, 2026-07-16. NOT canon; no CANON_VERSION change; no wire
guarantee. Extends `action-receipt-v0.2.md` and the authority-binding
`action-receipt-v0.3-draft.md` with a pre-action lifecycle so that **record
omission at a pinned checkpoint becomes a recomputable predicate** and a cross-organizational
procurement carries one enforceable history *per commitment*. Companion:
`threat-model-v0.1.md` (I8), `commitment-slot-recourse-algebra-v0.1-draft.md`.

The one-line motivation: a post-action receipt can prove what happened, but it
cannot prove that something *failed to* happen — absence is not evidence unless
the system first recorded an expectation that something must later appear. The
commitment slot records that expectation.

## 1. What a slot is

A **commitment slot** is a witnessed, append-only record with a pinned deadline
that must be closed by exactly one terminal event. It has four phases:

```
  OPEN ── countersign ──▶ ACTIVE ── close ──▶ CLOSED{delivery|cancellation|refusal|timeout}
                                     │
                                     └── deadline passes, still ACTIVE ──▶ (omission evidence)
```

1. **Order (OPEN).** The buyer publishes an order: a `term_root` (the hash of the
   agreed terms — conventions, accepted evidence classes, appraisal policy), a
   `deadline` (a pinned checkpoint, §4), a `witness_policy`, and a
   `remedy_adapter` reference. The order is itself an ActionReceipt
   (`action.type = "commitment.order"`).
2. **Countersign (ACTIVE).** The seller countersigns the order — assent, and
   only assent. Countersignature opens the slot at a named **slot host** (§3).
   *Acceptance ≠ stake:* any required collateral is a separate, optionally
   attached proof (§6), never implied by countersigning.
3. **Close (CLOSED).** The slot closes with exactly one terminal
   event, each a parent-chained ActionReceipt:
   - `delivery` — the artifact + evidence that its furnishing process is in the
     contracted equivalence class (the `attested`-policy path, §5 of the
     recourse-algebra note);
   - `cancellation` — mutually agreed non-performance;
   - `refusal` — the seller declines, within terms;
   - `timeout` — the deadline passed with no close (may be asserted by either
     party or the host, but requires a separately verified record-state basis;
     see §4).
4. **Record omission.** A slot still `ACTIVE` at a state commitment taken
   *after* its deadline may yield `RECORD_OMISSION` — precisely bounded (§7).

## 2. What a slot proves, and what it does not

A slot proves **failure to close by the pinned checkpoint**. It does **not**
prove that real-world performance did not occur. A seller who performed but did
not record the close still loses the slot game — that is the designed incentive
to record, not a claim about the world. This boundary is load-bearing: it is the
difference between an honest omission predicate and an impossible one.

## 3. Ordering: the honest trust model (the load-bearing choice)

Unique closure requires an ordering authority. A witnessed append-only log plus a
verifiable keyed state map (slot id → state) makes closures **detectable**, not
**unique**: two conflicting closes can both be well-formed. The protocol names
its regime per commitment in `witness_policy.ordering`:

| Regime | Uniqueness | Trust | Note |
|---|---|---|---|
| `local-host` | detect-and-punish | a named **slot host** sequences closes for this commitment | conflicting closures are objective, slashable evidence — not prevented. The default. |
| `quorum` | prevented (BFT) | a named operator set; a close is final at quorum | heavier; for high-value commitments |
| `rail-cas` | prevented (atomic) | a settlement rail providing compare-and-swap on the slot state | uniqueness borrowed from the rail; the strongest, where a rail exists |

**No global consensus in any regime.** There is one enforceable history *per
commitment*, ordered by that commitment's named authority — never one history for
the world. This is the precise anti-L1 claim: uniqueness is commitment-local, and
in the default `local-host` regime it is *detectable-and-slashable*, not
consensus-prevented. A spec, paper, or product that says "no consensus needed"
without this qualifier is overclaiming.

**Conflicting-closure predicate (decidable).** Two distinct terminal receipt
hashes for the same `slot_id`, both countersigned-valid, both under the host's
ordering, at the same winning position → an
`EQUIVOCATED_CLOSURE` finding, structurally analogous to log equivocation (I6):
the pair is the evidence, no adjudicator needed for the finding.

## 4. Time and the deadline

- `deadline` is a **pinned checkpoint reference**, never wall-clock: a log size, a
  block height, an anchored timestamp, or a beacon round. A verifier decides
  "past deadline" by comparing the checkpoint a party pins to the deadline, both
  recomputable. Golden vectors freeze the evaluation checkpoint.
- **Timeout race.** If a `delivery` close and a `timeout` assertion both reference
  positions around the deadline, the ordering authority (§3) decides which came
  first *at that authority's sequence*. Under `local-host`, a delivery sequenced
  before the deadline checkpoint wins; a timeout asserted before a validly
  sequenced delivery is itself a false closure (slashable). The race is resolved
  by sequence, not by wall-clock, and the resolution is recomputable from the
  host's ordering.
- **Timeout basis.** A timeout is a derived close, not a unilateral magic word.
  It requires an upstream-verified non-membership/current-state proof bound to
  this `slot_id`, checkpoint domain, checkpoint value, and pinned root. A proof-
  shaped JSON object or a host's nonresponse is not such a verdict.
- **Censorship vs non-closure.** A seller who closed but whose close the host
  refuses to serve is in a *different* fault class than a seller who never closed.
  Distinguishing them requires a second source: the seller's own inclusion proof
  of the close under an independently pinned root (I5), or a non-membership proof
  from the host (§7). Absent either, the finding is `UNDETERMINED_CLOSURE`, not
  `RECORD_OMISSION` — the protocol refuses to convict on ambiguity.

## 5. The order's terms

The `term_root` commits to:
- **conventions** (v0.2 §5): executable or semantic predicates over the
  delivery's subject, optionally carrying an appraisal policy
  (recourse-algebra note);
- **accepted evidence classes**: the grounding classes (§3 of the receipt spec)
  and appraisal policies the buyer will accept as a valid close;
- **appraisal policy** for an attestation-backed convention: evidence class, comparison
  function, tolerances, oracle reference (RATS vocabulary);
- **process equivalence class**, not exact process identity: e.g. `{model_family,
  min_precision, max_budget, approved_hardware_class, declared_randomness}`. A
  buyer contracts an equivalence class so process assurance does not become
  maximal surveillance.

A `delivery` close is **conforming** iff its subject satisfies the executable
  conventions AND its carried evidence meets the accepted classes AND any
  attestation-backed predicate's oracle attestation satisfies the appraisal policy. The
first two are recomputable (P2); the third is the bonded challenge path (P3).

## 6. Stake attachment (optional, separate)

A slot MAY carry a `stake_ref` — a reference to externally-escrowed collateral
(the `ActionReceipt.stake` slot stays `null`; collateral is a sidecar, per
`WITNESS-CONTRACT.md`). The stake is bound to the `slot_id` and the surviving
principal, and is slashable on a proven `EQUIVOCATED_CLOSURE`, a proven false
`timeout`, or a proven non-conforming `delivery`. Stake is never implied by
countersignature — assent and collateral are distinct acts with distinct
evidence.

## 7. Omission, precisely

`RECORD_OMISSION` requires **all** of:
1. a valid OPEN order and a valid countersign (the slot genuinely existed);
2. a state commitment (a pinned checkpoint) taken strictly after `deadline`;
3. an independently verified **non-membership proof** at that checkpoint that
   no valid terminal receipt for this `slot_id` is in the authenticated map;
4. proof bindings match the slot id, checkpoint domain, checkpoint value, and
   pinned root.

Host nonresponse, an incomparable checkpoint, an unverified proof object, or a
proof for another slot yields `UNDETERMINED_CLOSURE`, never omission. Even a
valid finding says only *no close exists in this pinned record state*. It does
not prove non-performance, failed submission, or seller blame.

## 8. Relationship to existing objects

- Order, countersign, and every close are **ActionReceipts**. Full authority
  binding uses the draft v0.3 receipt (`sign_action_receipt` upgrades v0.2), so
  a swapped order or close is caught as `authority_authentic = forged`.
- The slot's history is a **receipt DAG** (parent_receipt_hashes): countersign
  chains the order, each close chains the countersign. Obligations accumulate down
  the chain per the recourse algebra.
- Cross-org subcontracting is a slot whose seller opens a downstream slot. In v0.1,
  exact term-root equality is the only mechanically decidable conservation rule; a
  profile may select a permitted member under those unchanged terms. General term
  attenuation or "flow down by intersection" requires a sound obligation lattice and
  is deferred. The DAG still records the seam where any explicit divergence entered.

## 8a. Remedy modality — what a finding establishes, and does not

A finding is not a remedy. Each fault class establishes a *specific* fact, and the
permissible remedy is bounded by that fact. The load-bearing distinction: Bulla
verifies **record and predicate facts**, not worldly performance — so a slot
finding must never masquerade as proof that real-world work was or was not done.

| Finding | What is established | What is NOT established | Permissible remedy |
|---|---|---|---|
| **deterministic-predicate failure** (P2) | the act violates an executable convention or bounds, recomputable by anyone | intent; harm magnitude | performance remedy or slash (the fact is objective) |
| **appraisal-policy failure** (P3) | a designated oracle's attestation contradicts the claim, or the appraisal tolerance is exceeded — decidable *given the attestation* | that the underlying real-world fact is settled beyond the oracle's remit | the **bonded challenge path**: the attester (or the challenged party) is slashed per the pre-registered bond — NOT automatic, and never beyond the attestation's declared scope |
| **unclosed slot** (record-omission) | the required close was not recorded by a pinned checkpoint | that performance did not occur | procedural penalty / burden-shift onto the non-recorder — never automatic full slashing |
| **censorship ambiguity** (`UNDETERMINED_CLOSURE`) | the host may have suppressed a close; the seller may hold an independent inclusion proof | either party's fault | none on the merits; escalate the availability question |
| **equivocation** (`EQUIVOCATED_CLOSURE`) | two incompatible signed histories/closures exist | who benefits | witness/operator sanction (publicly verifiable proof of equivocation) |
| **semantic dispute** (P4) | a `semantic` convention or a non-executable term is contested | any deterministic verdict | human adjudication at the named forum |

The one rule that ties the table together: **automatic** slashing attaches only to a
**deterministic-predicate failure (P2)** or a proven **equivocation** — the two facts
that recompute for anyone with no designated third party. **Appraisal (P3) also slashes,
but never automatically**: it resolves through the bonded challenge path against a
designated oracle, because it is decidable only *given* that oracle's attestation, not by
recomputation alone. Everything else shifts a burden or routes to a forum. Letting the
record's silence stand in for reality is the exact pathology the kernel exists to refuse.

## 9. Draft status & open items

- Normative wire format for the order/close subjects: unwritten (this is a
  design draft).
- `bulla.slots` reference implementation: NOT built; a minimal experimental
  prototype is a stretch goal, explicitly not a v0.1 commitment.
- The ordering-authority interface (`local-host`/`quorum`/`rail-cas` adapters):
  named here, unspecified in detail.
- Non-membership proof format: TBD (verifiable-map exclusion proof; the
  key-transparency analog).
- The adversarial vectors (`slot-vectors/`) exercise the DECIDABLE
  properties — closure uniqueness under a given ordering, deadline arithmetic
  against a frozen checkpoint, omission vs undetermined — with a stdlib checker.
  The checker consumes explicit upstream validation verdicts; it does not
  cryptographically verify receipts or map proofs. The vectors illustrate the
  spec; they are not a conformance suite until the wire
  format is normative.
