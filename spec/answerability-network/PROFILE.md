# Bulla Answerability Network 0.1 (experimental)

Status: `experimental · SOURCE_ONLY · synthetic-public · project-operated · A0/J0/I0/W0 · r0`

This profile composes one buyer-fixed inference job, four signed procurement
ActionReceipts, a leaf-bound Witness Covenant history, and an accepted Reliance
Map. It answers a narrow question: after two authentic same-size witness heads
conflict, which declared decisions require rechecking and which pre-agreed
witness-bond consequence is eligible?

## Closed computation

Two provider receipts report the same artifact digest. Provider A separately
binds a self-asserted historical delivery statement and a supplied byte
preimage whose SHA-256 digest the verifier recomputes; provider B supplies only
`SELF_ASSERTED`. The buyer's frozen policy accepts `SUPPLIED_BYTE_PREIMAGE` and
selects provider A. The receipt's effective grounding remains `SELF_ASSERTED`.
The byte-preimage check establishes a match among the supplied bytes and
reported digests, not execution occurrence, historical delivery, or result
truth. The selected provider receipt is the exact Merkle leaf in
an authenticated size-one checkpoint. An RFC 6962 consistency proof extends
that checkpoint to the accepted witness view. The accepted checkpoint—not the
provider result—is the exact `source-00` artifact in the reliance graph.

The six derived states are `job`, `published`, `fork-open`, `fork-closed`,
`fork-authorized`, and `model-dispute`. A stage label never selects its result;
the verifier derives it from signed receipts, checkpoint views, challenge
transitions, authorization, and the Test-ledger report.

Same-size equivocation requires two authentic, jointly supplied checkpoints
for the same operator, log, epoch, covenant, and tree size with different roots.
The accepted recall notice then targets the accepted checkpoint hash. Its
graph-relative result is exactly 2,500 `AFFECTED`, 5,000 `NOT_AFFECTED`, and
2,500 `UNDETERMINED` synthetic declared decisions.

The selected 13-hop trace crosses two proof domains. Its first hop is the
leaf-bound inclusion from the provider receipt to the authenticated checkpoint;
the remaining hops are declared graph edges from that checkpoint to
`decision-09996`. A witness fork recalls the checkpoint's declared descendants.
It does not correct, invalidate, or strengthen the provider's receipt.

## Consequence boundary

An open challenge blocks the witness remedy. Once the authenticated challenge
transition closes, the dedicated fixture-reported allocation can make the
bounded remedy eligible. A separately signed authorization is still required
before the Test ledger may report an attempted event. Eligibility is not
authorization; an attempted event is not execution or collection.

A witnessed model-family assertion remains `MODEL_IDENTITY_P3`. It routes to
`CHALLENGE_REQUIRED` and cannot use the objective equivocation remedy.

## Limits

The example uses constructed records and project-operated roles. It does not
establish provider-result truth, complete dependency capture, witness
independence, custody, collectibility, dollars moved, production operation, or
customer activity. The remaining 9,999 graph decisions are synthetic
declarations rather than full transactions. Trust roots and accepted graph
digests remain outside the dossier.

The closed archive permits at most 160 regular files, 4 MiB per member, and
32 MiB in aggregate. All paths are normalized POSIX-relative paths; symlinks,
undeclared members, duplicate JSON members in normative inputs, unsafe integers,
and non-integer numbers fail closed. Generated projections live outside the
accepted dossiers and contain coordinates only. The browser derives decision
states from the verified Reliance Map result and checks the projection bytes
against the separately generated presentation root. A presentation failure
cannot suppress a protected report, and the presentation root remains disjoint
from the semantic corpus root.
