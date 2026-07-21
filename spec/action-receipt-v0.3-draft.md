# ActionReceipt v0.3 — authority-binding extension (DRAFT)

**Status:** draft, 2026-07-16. This document extends the shipped normative
`action-receipt-v0.2.md`; it does not amend v0.2 in place. Until v0.3 is
ratified, implementations MUST label authorization-bearing receipts as
`schema_version: "0.3"` and MUST continue to verify v0.1/v0.2 with their
historical preimages.

## 1. Security objective

In v0.2, `content_hash` deliberately excludes the recourse envelope. A content
signature therefore authenticates the claim but not the served mandate,
bounds, remedy, or retention terms. An intermediary can swap that envelope,
recompute the unsigned attestation and log-leaf hashes, and retain the valid
content signature.

v0.3 binds the exact envelope to the same signing identity that signed the
content:

```
envelope_hash      = H(recourse_envelope)
authorization_hash = H({
  "content_hash":  <content hash>,
  "envelope_hash": <envelope hash>
})
```

### 1.1 Domain-separated proofs (v0.3)

A v0.2 proof signs the raw `"sha256:…"` digest string. Distinct digests already
prevent replay between the content and authorization proofs, but do not
*categorically* separate proof purposes. Every v0.3 proof — content,
authorization, and each delegation grant (§6) — therefore signs a canonical
preimage that carries the purpose **in the signed bytes**:

```
preimage    = canonical({"context": "bulla-proof", "schema": "0.3",
                         "purpose": <"content"|"authorization"|"delegation-grant">,
                         "digest":  <the "sha256:…" string>})
proofValue  = ed25519_sign(key, preimage)
```

A proof minted for one purpose can never be replayed for another, regardless of
any digest coincidence. The proof also carries a `purpose` label; a verifier
rebuilds the preimage from the purpose **it expects**, so a mislabelled or
cross-purpose proof fails by construction. The `authorization` proof is such a
proof over `authorization_hash` with `purpose: "authorization"`.

## 2. Wire delta from v0.2

The document shape adds one field:

```jsonc
{
  "schema_version": "0.3",
  // every v0.2 field, unchanged
  "signature": { /* proof over content_hash */ } | null,
  "authorization": { /* proof over authorization_hash */ } | null
}
```

`authorization` is a v0.3 field. A v0.1/v0.2 receipt carrying it is malformed;
an implementation MUST NOT silently reinterpret an already-shipped schema.

The v0.3 content preimage is the v0.2 content preimage with the receipt's own
`schema_version` value (`"0.3"`). The event and log-leaf rules are unchanged.
The attestation preimage is:

```jsonc
{
  "content_hash": <content hash>,
  "signature": <content proof or null>,
  "recourse_envelope": <the reconstructed envelope>,
  "authorization": <authorization proof or null>
}
```

The `authorization` member is always present in the v0.3 attestation preimage,
including when null. Stripping it therefore changes the attestation hash.

## 3. Verification requirements

A verifier reports content and envelope authenticity separately.

For full-depth authority binding it MUST verify all of the following:

1. `signature` is present and valid over `content_hash`;
2. `authorization` is present and valid over `authorization_hash`;
3. both proofs use the supported proof type;
4. both proofs name the same `issuer` and `verificationMethod`;
5. hashes and the v0.2 modality law recompute normally.

An authorization proof from a different valid key is a signer-substitution
attack and MUST fail. An authorization proof without the content proof is an
incomplete proof pair and MUST fail. If the content proof is valid but no
authorization proof exists, the content may reach the attestation rung while
the envelope is reported `unauthenticated`.

## 4. Compatibility

- New verifiers preserve v0.1/v0.2 verification byte-for-byte.
- New v0.3 receipts are not expected to verify under an old v0.2-only verifier.
- `sign_action_receipt` upgrades a v0.2 receipt to v0.3 before computing either
  proof. This changes the content hash because `schema_version` is in the
  content preimage; that is intentional versioning, not tampering.

## 5. Bounded claim and residual

`authority_authentic = verified` means: **the same signing identity that signed
the content also signed this exact envelope**. It prevents intermediary
envelope substitution. It does **not** by itself establish that the signer was
entitled to act for `mandate.authority.principal`. The delegation profile (§6)
closes part of that gap — cryptographic *chain* authenticity from the principal
to the signer — but even a fully verified chain proves only that the principal
delegated a **declared** capability to this key, never that the policy is lawful
or that the act obeys it. That last step needs an executable scope language and
remains out of scope.

Golden vectors: `vectors/signed-authorized.json`,
`vectors/tampered-authority.json`, `vectors/tampered-authority-resigned.json`,
and `vectors/delegated-receipt.json`. The first is v0.3; the second preserves
its content signature while swapping the envelope and recomputing downstream
hashes; the third adds a valid *attacker* authorization signature (exercising the
same-signer invariant); the fourth is a two-link did:key delegation chain. All
verify (or fail) identically under `vectors/independent_check.py` with zero bulla
imports.

## 6. Delegation profile (did:key)

Normative reference: `delegation-design-note.md`. Under `deed_schema: "0.3"`,
`authority.delegation` carries structured, signed `DelegationGrant` objects (a
`"0.2"` envelope carries opaque strings; mixing is malformed). A verifier reports
**six independent dimensions**, never flattened into one enum:

| Dimension | Values |
|---|---|
| `chain_integrity` | `verified` / `broken` / `cycle` / `over_depth` / `not_applicable` |
| `principal_binding` | `verified` / `wrong_principal` / `unresolved` |
| `policy_binding` | `verified` / `mismatch` / `not_applicable` |
| `scope_binding` | `verified` / `mismatch` / `not_applicable` |
| `temporal_status` | `unresolved` / `within_window` / `expired` / `not_yet_valid` / `not_applicable` |
| `revocation_status` | `unresolved` / `not_revoked` / `revoked` / `not_applicable` |

A grant binds `grantor`, `grantee`, `principal`, `parent` (hash of the prior
grant), `policy_digest`, and `scope_digest`, and is signed by its grantor under
`purpose: "delegation-grant"` (§1.1). The verifier enforces: root
`grantor == authority.principal` with `parent == null`; continuity
(`grantor == prior.grantee`, `parent == H(prior)`); leaf `grantee ==` the content
signer; no cycles; depth ≤ 8; and every grant names the same principal.

**Key derivation (normative).** Each grant's proof MUST name its `grantor` as both
`issuer` and `verificationMethod`, and a verifier MUST derive the verifying key
from `grant.grantor` itself — never from the proof's own claim, and never from a
caller-supplied key. A receipt-signer key override (`verify_receipt(public_key=…)`)
MUST NOT be applied to grant proofs: one supplied key must never authenticate an
upstream grantor.

**Unknown members (normative).** A grant carrying any member outside
{`grantor`, `grantee`, `principal`, `parent`, `policy_digest`, `scope_digest`,
`not_before`, `not_after`, `proof`} MUST be rejected. `grant_hash` covers only known
fields, so an ignored extra would sit outside the grantor's signature while riding
inside a "verified" grant.

**Validity positions are typed.** `not_before` / `not_after` / the checkpoint are
`{"domain": str, "value": int}`. Only positions in the *same* named ordering domain
are comparable; anything else is `unresolved`. An untyped scalar is never silently
ordered — lexically "comparing" an arbitrary string is how a verifier invents a
temporal verdict it has no basis for.

**`policy_binding` / `scope_binding` are hash agreement — not authorization.** They
mean every grant's `policy_digest == H(authority.policy)` and
`scope_digest == H(bounds.scope)`. The scope leg is what makes the bounded claim
("this exact declared capability") true: without it a receipt could widen its own
`bounds.scope` to `admin:*` under an untouched, still-verifying grant. Neither
decides that the act *obeys* the policy — `authority.policy` is a pinned reference
and `bounds.scope` is unconstrained prose, so violation cannot be computed here;
attenuation over opaque scope digests is undecidable and deferred. A receipt with no
declared `bounds.scope` fails closed (`scope_binding = mismatch`). Non-`did:key`
principals report `principal_binding = unresolved`.

Delegation dimensions are **surfaced, never folded into the record's
`ok`/`verified_to`**. Two named predicates combine them: `cryptographically_bound`
(chain + principal + policy + scope all `verified`) is the bounded OFFLINE claim;
`fully_delegated` additionally requires `temporal_status = within_window` and
`revocation_status = not_revoked`, and is therefore **false today by construction**,
because revocation transport is unbuilt. That is intended: silence must never read
as "still in force."

## 7. Executable scope and `bounds_conformance` (the two halves of authorization)

`scope_binding` (§6) answers *did the chain convey scope S* — hash agreement between
each grant's `scope_digest` and `H(bounds.scope)`. It does **not** answer *did the act
obey S*. Those are separable questions, and conflating them was a P0 bug once already.

Under `deed_schema: "0.3"`, `bounds.scope` MAY be a structured
`jsonschema+quantum/1` predicate (an object) instead of prose (a string) — the same
closed, decidable form conventions use, over the act's `action.subject`. When it is,
a verifier recomputes a **digest-rung, crypto-free** dimension:

| Dimension | Values |
|---|---|
| `bounds_conformance` | `conforms` / `violates` / `not_checkable` / `not_applicable` |

- `not_applicable` — a prose scope (no predicate to recompute), or no `bounds`.
- `not_checkable` — a structured scope but no `action.subject` to evaluate against.
- `conforms` / `violates` — `action.subject` recomputed against the predicate.

**Authorization is the conjunction:** the principal delegated scope S
(`scope_binding = verified`) **and** the act stayed within S
(`bounds_conformance = conforms`). Either alone is insufficient.

`bounds_conformance` is **surfaced, never folded into `ok`** — an act exceeding its
scope is a *valid record of an out-of-scope act* (`ok = True`,
`authority_authentic = verified`, `scope_binding = verified`), and only
`bounds_conformance = violates` marks it. A relying party refuses on the dimension.

**No new language, and no lattice.** The predicate form, its validator, and its
evaluator are single-sourced (`bulla.executable_form`), shared with conventions. A
prose scope hashes byte-identically to before (`definition_hash`: str → UTF-8,
structured → canonical JSON), so no existing receipt changes. Structured scope makes
*conformance* decidable; it does **not** make grant *attenuation* decidable — a child
narrowing a parent's predicate is still undecidable over opaque digests, so the chain
requires digest **equality** (§6) and true attenuation stays deferred to a later track.
Because equality forbids narrowing, `bounds_conformance` + `scope_binding` suffice for
authorization without it.
