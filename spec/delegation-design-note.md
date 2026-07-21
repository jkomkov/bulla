# Delegation profile — design note (opt-in released draft)

**Status:** design note, 2026-07-16 — **IMPLEMENTED/RELEASED-DRAFT in Bulla 0.44.0**.
Written to fix the invariants, the versioning decision, the wire shapes, and the
attack matrix **before** any cryptographic code existed, so the implementation could
not drift into claiming more than it proves. It is implemented in-tree as `src/bulla/delegation.py`
with the attack matrix (§8) as `tests/test_delegation.py`, the golden vector
`spec/vectors/delegated-receipt.json` reproduced by `independent_check.py` with zero
bulla imports, and the normative wire text folded into `action-receipt-v0.3-draft.md`
§1.1 (domain separation) and §6 (the profile). v0.3 was unreleased with no external
adopter of its bytes, so v0.3 was revised in place rather than minting a misleading
v0.3.1 — and the (unreleased) v0.3 signed vectors regenerated under the
domain-separated construction, as §3 anticipated.

One implementation note not visible in the design below: the envelope's
`deed_schema` must survive the receipt's mandate/remedy view round-trip, or a v0.3
envelope silently reconstructs as v0.2 and its attestation hash stops matching. Both
the kernel and the independent checker now carry the version through the view.

## 0. The one thing this must not do

Two propositions must never be conflated:

- **P1 — chain authenticity:** an unbroken cryptographic chain connects the named
  `authority.principal` to the identity that signed the receipt.
- **P2 — policy authorization:** that chain authorized *this act* under *this policy
  and scope*.

This profile delivers **P1** and a narrow slice of P2 — **digest agreement on both
legs**: the chain conveys the same policy the receipt names (`policy_binding`) *and*
the same scope the receipt declares (`scope_binding`) — and it **stops there,
loudly**. It cannot decide that an act *violates* a policy, because `authority.policy`
is a pinned reference (checked for presence, not content) and `bounds.scope` is
unconstrained prose today. So the verdicts are called `policy_binding` and
`scope_binding`, never `authorized_scope`. Full P2 needs an executable scope language;
that is a separate, deferred track.

Bounded claim, stated once: **"this self-certifying principal delegated this exact
declared capability to this signing key."** Not: the policy is lawful, legitimate, or
semantically satisfied.

**Both legs are load-bearing, and the scope leg was learned the hard way.** An earlier
implementation of this profile compared `scope_digest` only *across grants* and never
against `H(receipt.bounds.scope)`. Every other dimension verified, so a leaf could
honestly sign a receipt declaring `admin:*` under an untouched, still-verifying grant
and read as fully authorized — which made the bounded claim above **false as
implemented**, while this note still asserted it. The check was a known deferral; the
claim it supported was left standing. That is precisely the drift this note exists to
prevent, so it is recorded here rather than quietly fixed:

> **If you defer a check, delete the claim it supports in the same commit.**

The zero-import checker reproduced the omission exactly as faithfully as the
guarantees — agreement between two implementations of one spec is evidence about
implementation drift, never evidence that the spec claims the right things.

## 1. Versioning decision (do not repeat the v0.2 mistake)

`authority.delegation` is today `tuple[str, ...]` inside a `RecourseEnvelope` whose
`deed_schema` post-init **rejects any value except the current default**
(`envelope.py`). Replacing those opaque strings with signed grant objects is a wire
change to a shape a verifier commits to in the attestation hash. Therefore:

- Structured delegation lives **only** under **`deed_schema: "0.3"`**. A verifier
  dispatches on `deed_schema`:
  - `"0.2"` → `delegation` is `list[str]` (legacy opaque references; unchanged,
    verified byte-for-byte; no chain semantics claimed).
  - `"0.3"` → `delegation` is `list[DelegationGrant]` (structured, signed, §2).
- A `"0.2"` envelope carrying structured grants, or a `"0.3"` envelope carrying bare
  strings, is malformed — fail closed. No silent reinterpretation of a shipped shape.
- The ActionReceipt already stamps `schema_version: "0.3"` for authority binding; a
  v0.3 receipt whose envelope carries structured delegation stamps `deed_schema: "0.3"`
  too. v0.1/v0.2 receipts and their envelopes are untouched.

## 2. The signed `DelegationGrant`

A grant is a capability handed from one identity to the next, pinned so it cannot be
reordered, spliced, lifted into another chain, or broadened. Canonical fields (all
required unless noted):

```jsonc
{
  "grantor":      "did:key:z…",     // the delegating identity (signs this grant)
  "grantee":      "did:key:z…",     // the receiving identity
  "principal":    "did:key:z…",     // the root surviving principal this chain descends from
  "parent":       "sha256:…" | null,// hash of the parent grant; null iff grantor == principal (root grant)
  "policy_digest":"sha256:…",       // H(the exact policy this grant conveys)
  "scope_digest": "sha256:…",       // H(the exact bounds/scope this grant conveys)
  "not_before":   {"domain": "<ordering domain>", "value": 0}, // optional (§5)
  "not_after":    {"domain": "<same domain>", "value": 100},
  "proof":        { /* domain-separated grant proof over grant_hash, §3 */ }
}
```

- `grant_hash = H(canonical(grant \ {"proof"}))` — the proof signs this, so the proof
  is excluded from its own preimage (same discipline as the receipt hashes).
- `parent` binds each grant to its predecessor: a stripped or spliced grant breaks the
  parent-hash continuity and is caught.
- `principal` is carried in **every** grant so a grant minted under principal A cannot
  be replayed inside a chain terminating at principal B.

## 3. Domain separation — the purpose enters the signed bytes

`identity.sign()` today signs the raw hash string. A mutable `purpose` field on a proof
is **not** domain separation. Every v0.3 proof — content, authorization, and each grant —
signs a canonical preimage that carries the purpose:

```
preimage      = {"context": "bulla-proof", "schema": "0.3", "purpose": <P>, "digest": <hash string>}
signed_bytes  = canonical_json(preimage)              # UTF-8, sorted keys, compact
proofValue    = ed25519_sign(signing_key, signed_bytes)
```

with `purpose ∈ {"content", "authorization", "delegation-grant"}`. A content proof
therefore cannot be replayed as an authorization or grant proof (or vice versa): the
purpose is in the bytes the signature commits to. New verifier functions
`sign_domain(purpose, digest)` / `verify_proof_domain(purpose, digest, proof, …)` live
beside the legacy v0.2 `sign` / `verify_proof`; v0.2 receipts keep the legacy
construction, v0.3 receipts use the domain-separated one. This regenerates the
v0.3 signed vectors; the independent checker's identity rung verifies the
new construction.

## 4. Invariants the verifier enforces (chain integrity)

Over the ordered grant list, from root to leaf:

1. **Root-principal equality:** the first grant's `grantor == authority.principal`, and
   its `parent == null`.
2. **Continuity:** each grant's `grantor == previous.grantee`, and its
   `parent == H(previous grant)`.
3. **Leaf-signer equality:** the last grant's `grantee ==` the `verificationMethod` of
   the receipt's content and authorization proofs (the key that actually signed the act
   is the key the chain terminates at).
4. **No cycles:** no identity appears twice in the grantor/grantee sequence.
5. **Bounded depth:** reject a chain longer than `MAX_DEPTH` (proposed 8).
6. **Principal consistency:** every grant names the same `principal`.
7. **Grant-proof validity:** each grant's proof verifies (domain-separated, §3) under
   its `grantor`'s did:key, with both proof `issuer` and `verificationMethod` equal to
   that grantor. A receipt-signing key supplied by a caller is never reused for an
   upstream grant.
8. **Closed signed shape:** unknown grant or proof members are malformed. An ignored
   field would sit outside the signed preimage while a consumer might still assign it
   semantics.

## 5. Policy, scope, and time — what is and is not decided

- **Policy binding (the honest slice of P2):** `policy_binding = verified` **iff** every
  grant's `policy_digest == H(authority.policy)` — i.e. the chain conveys exactly the
  policy the receipt names. A differing digest → `mismatch`. This is **hash agreement,
  not authorization**: it proves the grant is *about* this policy, not that the act
  obeys it.
- **Scope binding:** `scope_binding = verified` **iff** every grant's
  `scope_digest == H(bounds.scope)` for this receipt. Merely requiring the grants to
  agree with one another is insufficient: an intermediary could otherwise widen the
  receipt's scope while leaving every grant and proof untouched.
- **Attenuation is not decidable here.** A child grant narrowing its parent's scope is a
  real delegation feature, but with `scope_digest` an opaque hash there is no way to
  tell "narrower" from "different." So v0.3 requires `policy_digest`/`scope_digest`
  **equality** along the chain and defers true attenuation to the executable-scope
  track. Say so; do not fake a partial order over opaque digests.
- **Time:** positions are typed as exactly `{"domain": <non-empty string>, "value":
  <non-negative integer>}`. A verifier may compare a closed grant window with a
  checkpoint only when all use the same ordering domain. Missing windows, a missing
  checkpoint, or incomparable domains yield `temporal_status = unresolved`; they are
  never interpreted as valid forever.
- **Revocation:** revocation transport (a grant marked revoked out of band) is
  **unbuilt** and separately reported as `revocation_status = unresolved`. A valid
  time window is not evidence that no revocation exists elsewhere.

## 6. Verdict dimensions (separate, never flattened)

A verifier reports, alongside content authenticity and authority authenticity (v0.3):

| Dimension | Values | Meaning |
|---|---|---|
| `chain_integrity` | `verified` / `broken` / `cycle` / `over_depth` / `not_applicable` | §4 holds (or which invariant failed) |
| `principal_binding` | `verified` / `wrong_principal` / `unresolved` | root == principal AND leaf == signer; `unresolved` for non-did:key principals |
| `policy_binding` | `verified` / `mismatch` / `not_applicable` | §5 policy-digest agreement — **not** authorization |
| `scope_binding` | `verified` / `mismatch` / `not_applicable` | exact agreement with this receipt's `bounds.scope` — **not** semantic obedience |
| `temporal_status` | `unresolved` / `within_window` / `expired` / `not_yet_valid` / `not_applicable` | typed checkpoint evaluation, separate from revocation |
| `revocation_status` | `unresolved` / `not_revoked` / `revoked` / `not_applicable` | external revocation evidence; transport is unbuilt |

These are independent and may be simultaneously non-`verified`. Flattening them into one
enum would impose an arbitrary precedence and hide evidence. A relying party that needs
the bounded offline claim requires `chain_integrity = principal_binding =
policy_binding = scope_binding = verified` **and** valid content + authorization
proofs. The implementation names that predicate `cryptographically_bound`. The
stronger `fully_delegated` predicate additionally requires `temporal_status =
within_window` and `revocation_status = not_revoked`; it is deliberately false while
revocation transport is unbuilt.

## 7. did:key only, for now

Principals and delegates are **did:key** in this profile. A non-did:key principal
(`github:*`, `did:web`, corporate identity) needs an external resolver or trust anchor;
`verify_proof` already refuses to infer that binding, and this profile keeps that
refusal — `principal_binding = unresolved`, never a guess. Other schemes are a later
track.

## 8. Attack matrix (D3 must produce these verdicts)

| Attack | Expected verdict |
|---|---|
| Broken link (grantor ≠ prev.grantee, or bad `parent`) | `chain_integrity = broken` |
| Stripped middle grant | `chain_integrity = broken` (parent-hash gap) |
| Added/spliced grant | `chain_integrity = broken` |
| Cycle (repeated identity) | `chain_integrity = cycle` |
| Chain deeper than MAX_DEPTH | `chain_integrity = over_depth` |
| Root grantor ≠ `authority.principal` | `principal_binding = wrong_principal` |
| Leaf grantee ≠ receipt signer | `principal_binding = wrong_principal` |
| Grant minted under principal A replayed under principal B | `principal_binding = wrong_principal` (principal-consistency) |
| Non-did:key principal | `principal_binding = unresolved` |
| Grant `policy_digest` ≠ H(`authority.policy`) | `policy_binding = mismatch` |
| Receipt `bounds.scope` widened under unchanged grants | `scope_binding = mismatch` |
| Grant proof signed by an attacker key while claiming another grantor | `chain_integrity = broken` |
| Caller-supplied receipt key reused to authenticate an upstream grantor | impossible by API; each key derives from its own grantor |
| Unknown grant member such as `role: "admin"` | malformed → `chain_integrity = broken` |
| did:key proof's issuer and verification method disagree | proof invalid → `chain_integrity = broken` |
| Content proof presented as a grant proof (cross-domain replay) | grant-proof invalid (purpose mismatch, §3) → `chain_integrity = broken` |
| Untyped or cross-domain validity checkpoint | `temporal_status = unresolved` |
| Grant with no window taken as permanently valid | `temporal_status = unresolved` (named, not hidden) |
| Valid time window treated as proof of non-revocation | `revocation_status = unresolved` |

The implementation attack matrix is pinned in `tests/test_delegation.py`; the
independent checker's receipt-level variants are pinned in `tests/test_spec_vectors.py`.
The honest chain has a reproducible golden vector (`delegated-receipt.json`).

## 9. Residual (what remains open after D)

Executable scope/policy language (the thing that turns `policy_binding` into real
`authorized_scope` and makes attenuation decidable); non-did:key principal resolution;
revocation transport and validity-window semantics beyond a pinned checkpoint. These are
named `unresolved` in the verdicts, never silently assumed.
