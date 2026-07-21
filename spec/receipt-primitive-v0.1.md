# The receipt primitive (v0.1, DRAFT)

**Status:** definitional note, 2026-07-16. Non-normative framing for the wire specs
(`action-receipt-v0.2.md`, `action-receipt-v0.3-draft.md`) and the doctrine. It exists
to fix *what a receipt is* before more surface accretes on top of it — because every
security bug this system has had was a departure from the definition below, and naming
the definition is how the next one gets caught at design time instead of in production.

## In one paragraph (the "so what")

> Your agent hired another agent to do something that cost you money. It's gone now.
> The receipt says what it was told to do, who let it, what it actually did, and what
> you can do about it — and anyone can check all four without asking us. Before you act
> on a receipt, you say what you require; that gets recorded too. When it goes wrong,
> **who eats it is a calculation, not an argument.**

Each clause is now a built, testable thing: *what it was told to do* is a structured
`bounds.scope` predicate; *who let it* is the did:key delegation chain to the surviving
principal; *what it actually did* is `action.subject`, checked against the scope by
`bounds_conformance`; *what you can do about it* is the recourse envelope; *anyone can
check* is the zero-import verifier; *you say what you require* is a `ReliancePolicy`
pinned into your own `bulla.rely` receipt; and *who eats it is a calculation* is
`verify_reliance` recomputing whether your reliance was faithful to your policy. The
paragraph is this note's acceptance test: it is true exactly to the extent the
architecture below is.

## The definition

> **A receipt is a bundle of separable claims about a consequential act, where each
> claim names its own evidence, its own verifier, and its own limit — such that a
> stranger, later, without the actor, can recompute which claims hold and route the rest
> to a forum.**

The load-bearing word is **separable**. A receipt does not deliver *one* verdict; it
delivers a set of *independent* answers to *distinct* questions, and it is a category
error to combine them into a single truth value. The whole design is the discipline of
keeping questions apart — and every failure has been a re-conflation.

## Why "separable" is the primitive, not a slogan

The evidence is that every good thing in the system is an instance of the separation
principle, and every bug has been a violation of it.

**The instances** (each keeps two questions from collapsing into one):

- **Four hashes**, each answering exactly one question — `content` ("recompute the
  verdict"), `event` ("which occurrence"), `attestation` ("who vouched"), `log_leaf`
  ("where logged"). The Certificate-Transparency leaf-vs-STH lesson: one hash cannot
  answer two questions without lying about at least one.
- **Content authenticity vs authority authenticity** — a signature over `content` is
  envelope-free by design, so it says nothing about the mandate. `verify_receipt`
  reports the two as separate facts; a valid content signature over a *swapped* envelope
  is caught precisely because the questions were kept apart.
- **Six delegation dimensions** (`chain_integrity`, `principal_binding`, `policy_binding`,
  `scope_binding`, `temporal_status`, `revocation_status`) — independent, never
  flattened. A chain can be structurally sound yet bound to the wrong principal.
- **Grounding classes** separate evidence *quality* from record *validity*: a valid
  record of a self-asserted act is still just testimony.
- **`executable` vs `semantic` conventions** — the discriminator *is* the decidability
  boundary: what a verifier recomputes vs what a forum must adjudicate.
- **`diagnostic_ref` is never bare `null`** — a missing verdict must say *why* ("no
  composition to diagnose" is a different claim from "we skipped it").

**The violations** — the security holes this system has had are the *same bug* wearing
different clothes: two separable claims got conflated, so proving the cheap one was
mistaken for proving the expensive one.

| The conflation | The hole it produced |
|---|---|
| scope folded into policy | a receipt widened its own bounds to `admin:*` under a valid, untouched grant |
| one key standing for many identities | a single supplied key authenticated *any* claimed upstream grantor |
| `fully_delegated` absorbing revocation | an `unresolved` revocation status read as effective authority |
| **truthiness absorbing every verdict** | **`if verify_receipt(d): ship()` was always taken — it never read `.ok`, and shipped forged receipts** |

The last row is the sharpest, because the definition *predicted* it. A `ReceiptVerification`
is a bundle of separable claims; asking it for one boolean is asking the forbidden
question; a plain object answers that question `True`; so the most natural line a
consumer writes silently accepts everything. The fix is not a better boolean — there is
no correct boolean — it is to make the ambiguous question *raise* (see the R0 changelog
entry). The definition is generative: use it to predict where the next conflation will
be, and the answer is always "wherever two claims are being read through one gate."

## What a receipt is *not*

- **Not a log line.** A log line records that something happened. A receipt carries a
  *recomputable verdict* — `diagnostic_ref` is a claim a stranger re-derives, not a
  timestamped assertion to be trusted.
- **Not an attestation.** An attestation says "I vouch for X." A receipt additionally
  carries its *appeal path* — the recourse envelope names how X is contested when it is
  wrong.
- **Not a contract.** A receipt *references* terms (by hash) and *performs nothing*. The
  obligation lives in the referenced convention/policy; the receipt is the durable,
  adjudicable record that the obligation existed and what became of it.
- **Not a judgment.** A receipt makes deciding *possible* for a party who was not
  present; it does not decide. It routes the undecidable to a named forum — it never
  simulates the forum.
- **Not a capability** — *with exactly one exception* (below).

## The one exception: delegation is where Bulla mints

Everywhere else, **Bulla signs but never mints.** It binds a certificate to an identity
the agent already holds (`did:key` is self-certifying — the id is derived from the key,
so verification recovers the key from the issuer itself); the verdict recomputes from
inputs that already exist; the act already happened. In every case the signature
*records a prior fact*.

Delegation is the sole exception. When principal P signs a grant to M, **that signature
*is* the delegation** — there is no prior fact it records; the grant *creates* the right.
This is the one place a receipt is a capability rather than a record of one.

That distinction has a consequence nobody has designed for yet, and it belongs in the
definition so it is not forgotten:

> Lose a content proof and you lose *evidence* — the act still happened, and other
> evidence may reach it. **Lose a grant and you lose *the right itself*** — there is no
> other record of it, because the grant was its only existence.

Grants therefore need **availability guarantees that nothing else in the receipt needs**.
Evidence can tolerate being one witness among many; a minted right cannot tolerate being
lost. This is an open problem (named here, unbuilt): the retention and replication policy
for grants is not the same as for the rest of the receipt, and treating them alike will
eventually lose someone a right they were validly delegated.

## Consequence for consumers

Because a receipt is separable claims and not a verdict, a consumer must **combine the
dimensions it cares about, deliberately and on the record** — which is exactly what the
reliance policy (`bulla.reliance`, `bulla.rely` receipts) exists to make declarable,
pinnable, and recomputable. The kernel testifies; it does not resolve. Resolution is the
relying party's declared act, and — by this same definition — that act takes a receipt too.
