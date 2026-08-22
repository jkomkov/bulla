# Witness Covenant 0.1 Wire Format

Every dossier is a non-circular directory rooted by `covenant-core.json`.
Component bytes appear in an exact manifest; the core is not permitted to list
itself. Paths are normalized POSIX-relative paths. Symlinks, undeclared files,
duplicate JSON members, non-integers, and unsafe paths are rejected.

The core contains the closed covenant, role issuers, ActionReceipt manifest,
and artifact manifest. The covenant hash is the SHA-256 digest of canonical
JSON over every covenant field except `covenant_hash`. A witness-operator
`assurance.promise.accept` receipt signs that hash. Its issuer must be the
operator named by the covenant and accepted by the external context.

`evidence/head-a.json` and `evidence/head-b.json` use
`bulla.witness-checkpoint/0.1-draft`. Their signed `anchor_evidence` object is
exactly:

```json
{
  "authority_epoch": "witness-epoch-2026-08",
  "covenant_hash": "sha256:..."
}
```

The verification context is supplied separately. It names the accepted
operator, log, authority epoch, covenant hash, role issuers, authority grants,
capital checkpoint, rail adapters, and disclosed control status. Dossier-carried
values cannot bootstrap those decisions.

## Closed predicate

`bulla.same-size-log-equivocation/1` returns `ESTABLISHED` only when both
checkpoints:

1. authenticate under the accepted operator key;
2. bind the accepted covenant and authority epoch;
3. name the same operator and log;
4. have the same tree size;
5. are jointly supplied to this verifier; and
6. contain different roots.

A false predicate result does not establish witness innocence. Missing or
incomparable evidence blocks the protected conclusion.

## Claim-class control

The only non-objective control accepted by version 0.1 is
`MODEL_IDENTITY_P3`. It requires an exact provider proposition in a signed
ActionReceipt and a leaf-bound inclusion proof under the compared witness root.
Inclusion preserves the provider's claim; it does not establish which model
ran. The report remains `CHALLENGE_REQUIRED`. Unknown and P4 classes fail
closed because this profile has no forum-state vocabulary.

The provider uses role `provider` and action `inference.delivery`. Its subject
contains exactly:

```json
{
  "profile": "bulla.witness-covenant/0.1-experimental",
  "issuer_role": "provider",
  "covenant_id": "...",
  "covenant_hash": "sha256:...",
  "proposition_digest": "sha256:...",
  "claim_class": "MODEL_IDENTITY_P3",
  "claim_value": "..."
}
```

`evidence/model-claim-inclusion.json` contains exactly `attestation`, `index`,
`tree_size`, `leaf`, `proof`, and `root`. The RFC 6962 leaf is
`SHA256(0x00 || canonical_json({issuer, content_hash, attestation_hash}))`,
using the provider receipt's signer and hashes. The verifier requires safe
non-negative integer positions, lowercase SHA-256 digests, a proof bound to
that exact leaf, and the accepted checkpoint root. The P3 `finding_ref` is the
canonical SHA-256 digest of:

```json
{
  "claim_attestation": "sha256:...",
  "proposition_digest": "sha256:...",
  "claim_class": "MODEL_IDENTITY_P3"
}
```

`claim_inclusion=VERIFIED` means only that this signed provider claim is a
member of the supplied accepted history. It does not establish model identity,
claim truth, occurrence, independence, or complete observation.

The capital receipt is optional so that a controlled pair can distinguish
witness evidence from recourse. Without it, checkpoint and fault fields may
still verify, while allocation is `INADEQUATE` and remedy is `INELIGIBLE`.

## Limits

- 64 files and 4 MiB per dossier.
- 256 KiB per JSON member.
- JSON depth 20 and 10,000 nodes per member.
- Safe integers only.
- 4,096 Unicode scalar values and 16 KiB UTF-8 per string.
