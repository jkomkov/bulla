# Answerability Network 0.1 wire format

The directory root contains `network-core.json`. Its closed fields are
`profile`, `revision`, `stage`, `network_id`, `transaction_id`, and
`artifact_manifest`. The external context is not a dossier member. It contains
the accepted core hash, four accepted procurement issuers, the accepted
`bulla.fixed-sha256-delivery/1` evidence verifier, the exact accepted Witness
Covenant hash, and the separately authored Witness Covenant and Reliance Map
contexts.

`artifact_manifest` commits every other regular member by normalized
POSIX-relative path, exact byte length, media type, and SHA-256 digest. The
closed example permits JSON members plus the exact
`procurement/provider-a-delivery.bin` byte artifact. Symlinks, backslashes, absolute or
escaping paths, undeclared members, duplicate paths, non-integer numbers,
unsafe integers, and duplicate members in normative JSON fail closed. The
archive limits are 160 files, 4 MiB per member, and 32 MiB in aggregate.

## Procurement records

The four ActionReceipt v0.4 actions are:

- `inference.promise.accept`, signed by the buyer;
- `inference.delivery`, once for each provider;
- `inference.selection`, signed by the buyer's selection role.

The promise fixes transaction `inference-job-001`, the exact task and artifact
digests, accepted evidence class `SUPPLIED_BYTE_PREIMAGE`, `12500 USD_CENTS`,
and the exact Witness Covenant hash. Both provider receipts bind the same
artifact digest. Provider A's receipt binds two necessary evidence references:
`provider-a-delivery-assertion.json` is a `SELF_ASSERTED` historical delivery
statement, while `provider-a-delivery.bin` is the `EXECUTION_VERIFIED` input to
the exact byte-preimage relation. The receipt therefore retains an effective
grounding of `SELF_ASSERTED`. `provider-a-evidence.json` names the closed
SHA-256 verifier and supplied byte artifact. The verifier recomputes the
assertion, evidence, and byte digests before applying the buyer policy. Provider
B supplies `SELF_ASSERTED`. Both model-family fields remain `SELF_ASSERTED`.
The selection receipt binds provider A's exact attestation and the frozen
policy digest. These checks establish supplied-record agreement, not that
either provider ran a model or historically returned the bytes.

## Witnessed history and recall

`external-receipt-checkpoint.json` is an authentic size-one checkpoint whose
root is the selected receipt's deed leaf. `external-receipt-consistency.json`
extends that prefix to `head-a.json`; `external-receipt-inclusion.json` binds
the same receipt leaf to the final head. The embedded Witness Covenant derives
history consistency, same-size equivocation, challenge state, capital status,
remedy eligibility, authorization, and Test-ledger reporting.

For fork stages, `source-00` in `reliance/graph.json` is the exact authenticated
`head-a` checkpoint hash. The accepted `reliance.correct` notice targets that
checkpoint, not the provider receipt. The selected trace prepends the verified
receipt-to-checkpoint inclusion hop to the Reliance Map path certificate. It
therefore contains 14 retained identifiers and 13 hops.

`projections/<stage>.json` sits outside every accepted dossier. It contains
only `node_id`, `x`, and `y` coordinates, is nonnormative, and belongs solely to
the presentation root. The browser checks the exact projection digest and
presentation root, derives decision states from the verified Reliance Map
result, and rejects missing, extra, or duplicate projected nodes as a
presentation failure. A malformed, unavailable, or semantically inconsistent
projection cannot suppress an otherwise verified protected report.

## Report

`bulla.answerability-network-report/0.1-experimental` reports these dimensions
separately: network integrity, procurement verification, provider-digest and
supplied-byte agreement, buyer
policy, selected provider and receipt, receipt inclusion, history extension,
history consistency, same-size equivocation, challenge, witness remedy,
settlement authorization, Test-ledger attempt, model-identity control,
graph-relative reliance, substantive truth, and actual funds.

The reliance member contains the accepted graph and report digests, exact
tri-state counts, selected receipt attestation, selected checkpoint hash,
selected result state, and selected trace. A report digest commits the report
without its own digest field. Supplied reports must equal recomputation with
JSON-type exactness.

No stage label selects a result. Verifiers derive the stage from authenticated
component facts and reject disagreement. No report dimension implies complete
observation, substantive truth, rollback, custody, collectibility, or funds
movement.
