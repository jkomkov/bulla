# Golden Suite v0.1 clean-room profile

Profile identifier: `bulla.golden-suite/0.1-experimental`.

All JSON hashes use Bulla canonical JSON v2: UTF-8 bytes of JSON with keys
sorted, compact separators, and ASCII escaping, prefixed with `sha256:`.
Unknown or missing fields fail closed.

## Oracle classes

- `MACHINE`: exact program result and certificate shape are objective.
- `PROPERTY`: a declared trace or state invariant decides the result.
- `ADJUDICATION`: there is no machine truth.  A sealed internal judgment is a
  reference only; disagreement is not automatically an outsider error.

Each hidden oracle commitment is
`H({domain, case_id, oracle_hash, nonce})`, where `oracle_hash` commits to the
canonical oracle output and `nonce` contains at least 32 bytes of entropy
material.  Commitments are combined in a sorted binary Merkle tree; an odd
node is duplicated.

## Submission and reveal

1. Reproducer records the suite manifest hash.
2. Reproducer submits canonical result bytes and their hash.
3. The submission is receipted before any oracle is revealed.
4. Custodian reveals the oracle output and nonce.
5. Reproducer verifies the leaf and oracle Merkle root.
6. New cases are added only to v0.2 with authorship/challenge receipts; v0.1
   is never retroactively edited.

The `external_replay_status` value remains `blocked-by-sprint-scope` until a
genuinely independent implementation submits a commitment.  Internal
clean-room exercises do not change that status.
