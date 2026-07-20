# Bulla threat model & invariants — v0.1 (draft)

**Status:** draft, 2026-07-16. Scope: the ActionReceipt / recourse-gate / registry
surface (`spec/action-receipt-v0.2.md`, `spec/action-receipt-v0.3-draft.md`,
`WITNESS-CONTRACT.md`) plus the
commitment-slot extension drafted alongside it
(`spec/commitment-slot-v0.1-draft.md`). This document states what each party may
do, what the protocol guarantees against each, and — bluntly — what it does not.
It is the reference for the adversarial vectors and for any external review.

The governing discipline: **a guarantee is only claimed where an adversarial
test exercises the strongest form of the attack.** Where no such test exists, the
row says *unguarded* and is dated. "The test where the host is adversarial IS the
property."

## 1. Actors and their powers

| Actor | Assumed powers | Cannot (by assumption) |
|---|---|---|
| **Issuer / actor** | Mint receipts; sign with a key it holds; choose what to log or omit; terminate before a dispute; rekey freely | Forge another issuer's signature; alter a logged leaf without detection |
| **Counterparty / relying party** | Pin roots; demand inclusion; challenge; refuse to act on an unwitnessed or unauthenticated receipt | Compel a record the issuer never wrote (only refuse to rely) |
| **Slot host / log operator** | Serve receipts and proofs; order closures for a commitment it hosts; attempt to equivocate, censor, or serve selectively | Produce a valid inclusion proof for a leaf that is not in the log; forge issuer signatures |
| **Oracle / attester** | Attest process evidence (execution, TEE, replay) under a named appraisal policy | Be trusted beyond its declared class; escape being itself a receipted, challengeable party |
| **Challenger** | Detect and prove record faults for open bounties; post challenge bonds | Grief without cost (loser-pays); bring act-fault claims without named-party standing |

Trust is **stranger-relative** (spec §3): a counterparty signature grounds a
record only against its signer; against a third party, independent anchors rank
higher. No actor is assumed honest; each guarantee below is stated against the
actor who would break it.

## 2. Invariants (what the protocol holds)

- **I1 — Recomputable verdict.** `deed = f(composition@h, algorithm@v)`. Any
  party re-derives the verdict from pinned inputs before trusting any signature.
  Packs, registry, environment, and wall clock are *not* inputs
  (`tests/test_deed_recomputable.py`).
- **I2 — Content authenticity.** A signature over `content_hash` authenticates
  the claim/verdict. `content_hash` is envelope-free, time-free, signature-free,
  so it is stable across conforming implementations for the receipt's own
  schema version.
- **I3 — Content-signer envelope binding (v0.3 draft, 2026-07-16).** `authorization_hash =
  H(content_hash, envelope_hash)`; an issuer signs it to vouch for *this*
  mandate/remedy envelope. A verifier reports content authenticity and authority
  authenticity as **separate** verdicts. The authorization and content proofs
  must name the same signing identity. Swapping the envelope under a valid
  content signature is caught as `authority_authentic = forged`
  (`tests/test_action_receipt.py`, vectors `tampered-authority*.json`). This closes
  intermediary envelope-swap and signer-substitution gaps. Structured did:key
  delegation under `deed_schema: "0.3"` separately verifies chain, principal,
  policy-digest, and exact receipt-scope binding while reporting time and
  revocation as independent dimensions. This establishes only the bounded claim
  that the principal delegated this exact *declared* capability to the signing
  key; semantic policy obedience remains open.
- **I4 — Modality law.** Every remedy names a persistent `verifier` *and*
  `anchor`; `escalate` requires `authority`; a forum must carry a
  `trusted_root_ref` (Pin-the-Root). Enforced at construction and re-checked on
  served data — a hash-correct but respondentless appeal path is refused.
- **I5 — Tamper-evidence.** The registry is append-only (RFC 6962). A non-prefix
  rewrite fails a consistency proof against an anchored root; a borrowed
  inclusion proof (valid for a different leaf) is refused.
- **I6 — Equivocation is self-incriminating.** Two authentic, same-size,
  same-operator log heads with different roots are, together, proof of
  equivocation — no adjudicator needed for the finding
  (`bulla.experimental.EquivocationEvidence`).
- **I7 — Verdict-slot non-null.** `diagnostic_ref.status` is never bare null: a
  missing verdict must say *why* (`reference`/`not_applicable`/`deferred`), so
  "attested" is never silently read as "recomputable."
- **I8 — Commitment closure (drafted, `commitment-slot-v0.1-draft.md`).** An
  opened slot closes with exactly one of {delivery, cancellation, refusal,
  timeout} under **commitment-local ordering**. Conflicting closures are
  objectively detectable and slashable — *not* prevented by global consensus
  unless a settlement-rail CAS profile is used. A slot still open past its pinned
  checkpoint may establish `RECORD_OMISSION` relative to that pinned state.

## 3. Attack → mechanism → residual

| Attack | Mechanism | Status |
|---|---|---|
| Tamper a served receipt's content | I1/I2 — content hash mismatch | CLOSED |
| Recompute all four hashes, keep stale signature (content tamper) | I2 — signature over old content fails | CLOSED |
| **Swap the mandate/remedy, recompute downstream hashes, keep content signature** | **I3 — v0.3 authorization proof no longer binds the envelope → `forged`** | **CLOSED in opt-in released-draft v0.3 implementation (Bulla 0.44.0)** |
| Swap the envelope and re-sign authorization with an attacker key | I3 — content and authorization proofs must name the same signer | CLOSED in opt-in released-draft v0.3 implementation (Bulla 0.44.0) |
| Sign both proofs while falsely claiming entitlement under another surviving principal | v0.3 did:key chain + principal binding; each upstream key derives from its grantor | CLOSED for the did:key profile; external principal resolution remains open |
| Widen `bounds.scope` under an unchanged, valid delegation chain | `scope_binding`: every grant digest must equal H(this receipt's scope) | CLOSED in opt-in released-draft v0.3 implementation |
| Add an unsigned semantic field (for example `role: admin`) to a valid grant | Closed grant/proof vocabularies; unknown members fail closed | CLOSED in opt-in released-draft v0.3 implementation |
| Treat a missing checkpoint or revocation response as proof that a grant is in force | Separate `temporal_status` and `revocation_status`; strong reliance requires positive evidence for both | CLOSED as an overclaim; revocation transport itself remains unbuilt |
| Rewrite / truncate log history | I5 — consistency proof fails | CLOSED |
| Serve one history to auditor, another to counterparty | I6 — gossiped/anchored checkpoints compared | CLOSED in design; gossip transport unspecified (2026-07-11) |
| Present authentic record + valid proof for an unrelated leaf | I5 — inclusion is leaf-bound | CLOSED |
| Plant a deed under a victim's issuer | Signature check at submission boundary | CLOSED |
| Omit a consequential act from the record | I8 — pre-opened slot makes absence from a pinned state detectable; relying parties refuse the unwitnessed | PARTIAL — record omission only; slot spec and map proof are draft |
| Deliver artifact, withhold the paid-for process | Appraisal-policy convention + oracle attestation + bond (P3) | GATED on MARKET-REAL; oracle σ open |
| Rekey / Sybil to shed a bad record | Standing accrues to surviving stake + external identity binding, not key age | ECONOMIC only — rekey is free by design |
| Slot host forges a unique closure it did not order | I8 — commitment-local ordering; conflicting closures detectable+slashable | DRAFT — trust model is the load-bearing open question (§4) |
| Censor a slot close (make an honest close look like non-closure) | Distinguish seller non-closure from host censorship via a second source / non-membership proof | DRAFT — needs the slot spec's censorship fault class |

## 4. Named open problems (dated)

- **Executable scope/policy semantics (2026-07-16).** `policy_binding` and
  `scope_binding` prove exact digest agreement, not that the act obeys the policy.
  Opaque digests also cannot express or decide attenuation. A semantic capability
  language and evaluator remain unbuilt.
- **Revocation transport (2026-07-16).** Typed same-domain checkpoints can decide a
  closed validity window. They cannot prove no out-of-band revocation exists.
  Until a pinned revocation view is supplied, `revocation_status = unresolved` and
  the strong `fully_delegated` predicate is false.
- **Non-did:key principal resolution (2026-07-16).** External principals need a
  resolver and trust anchor. The did:key profile refuses to infer that binding.
- **Commitment-local ordering trust model (2026-07-16).** Unique closure needs an
  ordering authority: a named slot host, a quorum, or a settlement rail with
  atomic compare-and-swap. Absent one, the honest claim is *detect-and-punish*
  (conflicting closures are evidence), **not** *prevented*. The slot spec must
  state which regime a given commitment runs under; the whitepaper's §3 claim is
  bounded accordingly.
- **Oracle σ (the coincidence of existence).** The gate decides on type signals
  and now authority; it does not verify that the counterparty *did* the work. The
  attested-convention path (`verification_semantics` + oracle ref) is the drafted
  answer; the oracle itself is a bonded, receipted member class, not a trusted
  root. Open.
- **Gossip transport for equivocation checkpoints (2026-07-11).** I6 is closed in
  design; the cross-operator gossip channel is unspecified.
- **Rekey / Sybil.** No mechanical fix; the answer is economic (standing = surviving
  stake) and identity-binding (external persistent identity). Stated, not solved.
- **Effective exit cost.** "Governance by pinset" is still governance; a dominant
  default pinset is a soft root program. Exit must be *measured* (the cost to
  re-home), not merely asserted. Instrument unbuilt.

## 5. Explicitly out of scope

Value/quality adjudication ("was the dog cute" — process, not taste); pricing the
fee (it is the disclosure cap, not the premium); global ordering of unrelated
acts (the anti-L1 stance — one enforceable history per commitment, not one for the
world); key resolution for non-`did:key` issuers (authenticity is `unresolved`
there until a resolver is supplied).
