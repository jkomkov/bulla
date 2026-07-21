# Bulla answerability kernel — external review package

**For:** one external implementer or hostile reviewer. **From:** John Komkov.
**Date:** 2026-07-16. **Ask:** break it. Specifically — *break closure
uniqueness, forge authority, or defeat the omission predicate.* A confirmed break
is worth more to this program than agreement; it becomes a named limit in the
whitepaper (the self-attack tradition, after Nakamoto §11).

This is a **standing invitation**, not a claim of completeness. The targets below
include draft specs with executable decision procedures but no production
integration; the authority and reliance targets have an opt-in released-draft
reference implementation with adversarial tests you can run.

## What to attack

### 1. Authority binding (OPT-IN RELEASED v0.3 DRAFT — run it)
`content_hash` is envelope-free by design, so a signature over content does not
bind the mandate/remedy. The versioned fix (`authorization_hash = H(content_hash,
envelope_hash)`, signed) is in `src/bulla/action_receipt.py` and
`spec/action-receipt-v0.3-draft.md`. The claim: you
cannot swap the `authority`/`bounds`/`recourse` envelope of a fully-signed
receipt and have it verify as authentic.

- Reproduce the *original* attack and the fix after installing the actual declared
  extras: `python -m pip install -e '.[identity]' pytest`, then
  `pytest tests/test_action_receipt.py -k authority`.
- Attack the fix: the golden set is `spec/vectors/signed-authorized.json`
  (verifies, authority `verified`) and `spec/vectors/tampered-authority.json`
  (envelope swapped, downstream hashes recomputed, content signature retained —
  must fail as `forged`) plus `tampered-authority-resigned.json` (the swapped
  envelope is re-signed with an attacker key and must fail the same-signer
  invariant). Verify from the spec alone, zero bulla imports:
  `python spec/vectors/independent_check.py`.
- v0.3 proofs are **domain-separated**: the signature covers a canonical
  `{context, schema, purpose, digest}` preimage, so a proof minted for one purpose
  (content / authorization / delegation-grant) cannot be replayed as another. The
  same-signer invariant is separate and complementary: it stops key substitution,
  which domain separation does not.
- **The bounty questions:** can you construct an envelope swap that verifies as
  `authority_authentic = verified` against the honest content signer's key? Can
  you substitute a separately valid attacker authorization proof, transplant a
  proof across receipts or purposes, or exploit canonicalization ambiguity?

### 1a. Delegation chain (OPT-IN RELEASED v0.3 DRAFT — run it)
`spec/delegation-design-note.md` + `src/bulla/delegation.py`. Under
`deed_schema: "0.3"`, `authority.delegation` carries signed did:key grants. A
verifier reports **six independent dimensions** — `chain_integrity`,
`principal_binding`, `policy_binding`, `scope_binding`, `temporal_status`,
`revocation_status` — which are surfaced, never folded into the record's `ok`.

- Reproduce: `pytest tests/test_delegation.py` (the attack matrix: broken link,
  stripped/spliced grant, cycle, over-depth, wrong principal, leaf ≠ signer,
  cross-domain proof replay, grant signed by a non-grantor, a grant forging an
  upstream grantor, receipt-scope widening, unknown grant members, untyped
  checkpoints). Golden vector: `spec/vectors/delegated-receipt.json`, reproduced with
  zero bulla imports by `python spec/vectors/independent_check.py`.
- **The claim, stated narrowly:** a verified chain proves *this self-certifying
  principal delegated this exact declared capability to this signing key*. Nothing
  more.
- **Four holes an earlier draft of this profile had — found by review, now closed
  and regression-tested. Re-break them:** (a) the receipt's `bounds.scope` was not
  bound to the grant's `scope_digest`, so a leaf could honestly sign a receipt
  claiming `admin:*` under an untouched grant and still read as fully authorized;
  (b) `verify_receipt(public_key=…)` forwarded the receipt-signer override to every
  grant, so one attacker key authenticated any claimed grantor; (c) unknown grant
  members were ignored rather than rejected, so an unsigned `role: "admin"` rode
  inside a verified grant; (d) the reliance predicate ignored unresolved revocation.
- **The bounty questions:** can you make a chain report `chain_integrity =
  principal_binding = scope_binding = verified` while the signer is not the delegated
  party, or while the act exceeds the granted scope? Splice, reorder, or lift a grant
  from one principal's chain into another's? Replay a grant proof as a
  content/authorization proof (or vice versa)? Get a key other than
  `H(grant.grantor)` to validate a grant? Walk past the depth/cycle cap?
- **The known residual, which we want attacked as a *claim*, not a bug:**
  `policy_binding` / `scope_binding` are **hash agreement** — the chain conveys the
  policy the receipt names and the scope it declares — and are deliberately NOT a
  decision that the act obeys the policy. `authority.policy` is a pinned reference
  and `bounds.scope` is unconstrained prose, so obedience is not computable here;
  attenuation over opaque scope digests is undecidable, so the profile requires digest
  *equality* and defers true narrowing. `temporal_status` and `revocation_status`
  report `unresolved` (no pinned checkpoint; transport unbuilt), so `fully_delegated`
  is false by construction today. Non-`did:key` principals report `unresolved`, never
  a guess. Tell us if any of those boundaries is drawn in the wrong place, or if a
  name still over-promises.

### 2. Commitment-slot closure uniqueness (DRAFT — decision procedure provided)
`spec/commitment-slot-v0.1-draft.md` claims: an opened slot closes with exactly
one of {delivery, cancellation, refusal, timeout} under **commitment-local
ordering**, with conflicting closures objectively detectable and slashable. It
does NOT claim consensus-prevented uniqueness in the default `local-host` regime.

- Run the adversarial vectors: `python spec/slot-vectors/slot_check.py`.
- **The bounty questions:** (a) construct a closure ambiguity the decision
  procedure resolves *wrongly* (e.g. a delivery/timeout race it mis-orders, or a
  conflict it fails to flag as `EQUIVOCATED_CLOSURE`). (b) Show a `local-host`
  ordering assumption that is unenforceable without a stronger trust model than
  the spec admits — i.e. prove the "detect-and-punish" claim is weaker than
  stated. (c) Break the anti-L1 claim: exhibit a case that genuinely needs global
  ordering, not commitment-local.

### 3. The omission predicate (DRAFT)
The claim (`commitment-slot-v0.1-draft.md` §7): a slot proves *failure to close by
a pinned checkpoint*, never that real-world performance did not occur, and an
`RECORD_OMISSION` finding requires a separately verified, slot- and checkpoint-bound
non-membership proof — otherwise the finding is
`UNDETERMINED_CLOSURE` (possible censorship), never a conviction.

- Vectors 2 (omission) and 5 (censorship) are the pair.
- **The bounty questions:** (a) make the checker return `RECORD_OMISSION` where the true
  state is host censorship (a false conviction). (b) Make it return
  `UNDETERMINED_CLOSURE` where a clean non-membership proof should yield `RECORD_OMISSION`
  (a false acquittal). (c) Defeat the seller-non-closure vs host-censorship
  distinction with a construction the spec's §4 does not cover.

### 4. Routed-inference answerability conservation (v0.1 DRAFT — run it)

`spec/routed-inference-profile-v0.1-draft.md` closes a finite workflow over one
orderer, one router, one provider, one delivery assertion, and one relier. It requires
full term disclosure, retains every binding, distinguishes recourse conveyance from
unverified operational reachability, and balances signed declarations without claiming
actual usage or payment.

- Reproduce fourteen local traces: `python spec/routed-inference-vectors/check.py`.
- Verify one trace with verdict exit codes:
  `python spec/routed-inference-vectors/check.py verify
  spec/routed-inference-vectors/14-attempted-discharge.json --json`.
- Run the isolated local handoff from the repository's `bulla/` directory:
  `PYTHONPATH=src python examples/routed-inference/run_demo.py --fixture-keys`.
- Use `spec/routed-inference-IMPLEMENTER.md` to write an independent checker. Running
  the supplied checker is fixture reproduction, not an independent implementation.
- **The bounty questions:** can a re-signed mutation orphan either half of a parent
  reference, alter accepted terms or recourse, duplicate a constrained transition, or
  discharge a binding while returning `CONFORMS`? Can an invalid same-size witness
  history evade `LOG_EQUIVOCATION`, or a valid different-size history trigger it?
- **Known boundaries:** no selective disclosure, multi-hop or DAG routing, closure,
  novation, live provider, settlement adapter, operational recourse proof, or
  independent witness exists in this package.

## The package

| Artifact | What it is |
|---|---|
| `spec/threat-model-v0.1.md` | actors, invariants (I1–I8), attack→mechanism→residual table, dated open problems |
| `spec/action-receipt-v0.2.md` | the shipped wire spec (normative and unchanged) |
| `spec/action-receipt-v0.3-draft.md` | the authority-binding wire extension + domain-separated proofs (§1.1) + the delegation profile (§6) (draft) |
| `spec/action-receipt-v0.3.schema.json` | strict draft schema; content and authorization proofs are paired |
| `spec/delegation-design-note.md` | delegation invariants, versioning decision, and the attack matrix — fixed *before* the crypto was written |
| `src/bulla/delegation.py` + `tests/test_delegation.py` | the did:key profile: signed grants, six independent verdict dimensions, the attack matrix as tests |
| `spec/vectors/` + `independent_check.py` | signed golden vectors incl. delegation, executable-scope, and reliance cases + stdlib verifier (two rungs: stdlib digest, optional ed25519); reproduces 15/15 verdicts with zero bulla imports |
| `bench/bench_receipt.py` | sign/verify cost + wire growth by delegation depth (median and p95) |
| `spec/commitment-slot-v0.1-draft.md` | the slot lifecycle + the honest ordering trust model |
| `spec/commitment-slot-recourse-algebra-v0.1-draft.md` | appraisal policy; "terms compose, consequences sequence" |
| `spec/slot-vectors/` + `slot_check.py` | adversarial slot vectors + stdlib decision procedure over explicit upstream verdicts |
| `spec/routed-inference-profile-v0.1-draft.md` + `routed-inference-vectors/` | finite single-router/single-provider profile, fourteen traces, zero-Bulla checker, and violation taxonomy |
| `spec/dist/routed-inference-profile-v0.1-draft.zip` | deterministic external reproduction bundle; supplied-checker reproduction is explicitly not independent implementation |

## Ground rules (so the review is honest)

- **Attack the strongest form.** For any guarantee, the adversary-controls-the-
  bytes construction is the property. A break that only works against a weaker
  variant is a note, not a break.
- **Say which rung you reached.** Digest (structure/hashes), attestation
  (signatures), or the slot decision procedure. The tooling reports depth; please
  do too.
- **Released draft ≠ normative.** Target 1 is implemented and released only as
  an opt-in draft. Targets 2–3 remain design drafts with decision procedures,
  not production code. "This isn't implemented" is known where the relevant
  spec says so; "the design is unsound" is the finding worth having.
- **No agreement theater.** The useful outputs are: a confirmed break, a named
  unstated assumption, or "target N's claim is sound but overstated as written —
  here is the honest form." Any of those improves the whitepaper.
