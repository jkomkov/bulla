# Glyph Agent Incident Packet v0.1 — DRAFT

**Profile identifier:** `glyph.agent-incident-packet/0.1-draft`

**Maturity:** experimental · `SOURCE_ONLY` · team-operated · `A0/J0/I0/W0` ·
`r0`

An Agent Incident Packet binds ActionReceipt records, opaque trace artifacts,
event denominators, coverage reports, redaction records, party statements,
corrections, and witness evidence into one independently recomputable
directory. The packet does not standardize trace contents, publish private
model reasoning, establish disclosure safety, or compute reliance.

## 1. Directory contract

```text
packet-core.json
publish-receipt.json
receipts/
indexes/
coverage/
traces/
redactions/
witness/
```

Only `packet-core.json` and `publish-receipt.json` are fixed filenames.
Released component paths are declared in `packet-core.json`. Controlled and
withheld artifacts have no public path.

The parser rejects:

- absolute, non-normalized, parent-traversing, backslash, drive-qualified, or
  colon-bearing path segments on every platform;
- symlinks, Windows reparse points, and non-regular files;
- duplicate normalized paths;
- undeclared files or missing declared files;
- duplicate JSON members, non-finite numbers, malformed Unicode, unknown
  closed fields, and configured resource-limit violations.

On platforms with descriptor-relative directory APIs, the source verifier
opens the packet root once, descends relative to opened directory descriptors,
uses `O_NOFOLLOW` where available, and checks device, inode, type, and size
across each open and read. This prevents an intermediate path component from
being redirected after traversal begins. A directory tree that an adversary
can mutate concurrently remains outside this draft's portable guarantee on
platforms without those descriptor-relative APIs; verification there should
run over an immutable snapshot or access-controlled directory. The fallback
rejects reparse-point metadata before descent; it does not claim equivalent
protection against a concurrently replaced ordinary directory.

Released artifacts must match the declared exact byte length and SHA-256
digest. Controlled and withheld artifacts must not occur in the public
directory and report `UNAVAILABLE`.

## 2. Non-circular publication

`packet-core.json` contains packet identity, roles, an ordered receipt
timeline, artifact manifest, denominator and coverage references, redaction
bindings, statement references, witness references, and correction references.
It does not contain its own signature or the publish receipt's hash.

`publish-receipt.json` is an ActionReceipt v0.4 with action
`incident.packet.publish`. Its signed subject commits to:

```json
{
  "profile": "glyph.agent-incident-packet/0.1-draft",
  "issuer_role": "publisher",
  "packet_id": "…",
  "packet_core_hash": "sha256:…",
  "component_hashes": {
    "roles": "sha256:…",
    "timeline": "sha256:…",
    "artifacts": "sha256:…",
    "denominators": "sha256:…",
    "coverage": "sha256:…",
    "redactions": "sha256:…",
    "statements": "sha256:…",
    "witnesses": "sha256:…",
    "corrections": "sha256:…"
  }
}
```

Every hash uses `bulla-jcs-int/1`, the ActionReceipt v0.4 portable canonical
data model.

Each timeline entry also records the exact `byte_length` and SHA-256 digest of
the stored receipt file. Those values bind the packet member bytes.
`attestation_hash` separately binds the canonical ActionReceipt semantics and
proofs. An exact-byte mismatch is a timeline-integrity failure even when the
modified file parses to the same ActionReceipt.

## 3. Closed actions

New profile records use ActionReceipt v0.4 and the closed action subjects
implemented in `bulla.experimental.incident_packet`. Historical v0.2 or v0.3
receipts may be released as labeled evidence artifacts, but do not enter the
timeline and cannot satisfy occurrence or coverage.

Every timeline receipt and `publish-receipt.json` has `conventions: []`.
A nonempty `conventions` array is a profile receipt failure. This constraint
narrows only `glyph.agent-incident-packet/0.1-draft`; it does not change
ActionReceipt v0.4. Historical receipts that contain conventions remain opaque
evidence artifacts and cannot enter the live profile timeline.

Every live profile receipt also uses the direct ActionReceipt authority form:
`deed_schema` is `0.2` (or omitted with the same default), `delegation` is an
empty array, and `authority.principal` equals the receipt proof issuer.
Delegated ActionReceipts remain valid ActionReceipt records but are not accepted
into a `glyph.agent-incident-packet/0.1-draft` timeline. A later packet-profile
revision may add delegation only with verifier parity for grant signatures,
principal, policy, scope, interval, and revocation dimensions.

The closed action vocabulary is:

- `eval.run.authorize`
- `capability.decide`
- `capability.observe`
- `trajectory.decide`
- `incident.statement`
- `incident.handoff`
- `incident.packet.publish`
- `incident.correct`

Each subject contains the exact profile identifier and `issuer_role`.
`capability.observe` binds a prior permitted decision for the same run and
request, protocol, and mandate. A decision and trajectory use the referenced
mandate's `run_id` and `policy_digest`; an observation uses the mandate's
`run_id`. Every mandate reference resolves to an earlier accepted attestation.
Only the pre-effect `capability.decide.claimed_at` is compared with the
mandate's claimed validity interval. Post-effect observation, trajectory, and
handoff records may be issued later. This comparison uses authenticated actor
claims and does not convert actor time into witness time.

`trajectory.decide.ordered_lineage` equals the exact ordered list of prior
accepted capability decision and observation attestation hashes for that
trajectory's `run_id`. Cross-run receipts are excluded.
`incident.statement` preserves the issuing party and epistemic status.
`counterparty_confirmed` is accepted only from the closed `affected_party`
issuer role; another role cannot characterize its own assertion as
counterparty confirmation.

An `incident.handoff` carries `statement_refs` in the exact order of the named
packet statements, all of which must precede the handoff and reach accepted
attestation depth. Its `parent_refs` equals the complete ordered attestation
list for every prior timeline entry. Omission, addition, or reordering is a
timeline-integrity failure.

## 4. Observation and coverage contract

Every denominator observation has exactly:

```json
{
  "anchor_id": "string",
  "observation_id": "string",
  "run_id": "string",
  "protocol": "http",
  "operation_ref": "sha256:…",
  "phase": "decision",
  "evidence_hash": "sha256:…"
}
```

`protocol` is `http` or `mcp`; `phase` is `decision` or `effect`.

A decision receipt projects its request and ingress event into the decision
denominator. An observation receipt projects its effect and target event into
the effect denominator. The projected object must equal the denominator object
exactly. Adapters hash retained evidence bytes under a named adapter version;
the profile does not define universal HTTP or MCP normalization.

Coverage is computed independently for every `(anchor_id, phase, protocol)`.
For every protocol represented by capability receipts or packet denominators,
the core declares exactly one decision denominator and one effect denominator,
with exactly one matching coverage report for each. A missing, duplicate, or
extra phase anchor is malformed. The protocol set derived from parsed
capability receipts must equal the denominator protocol set. An empty anchor
set is malformed rather than vacuously computed.

Each denominator reference carries `checkpoint_attestation`, which resolves to
an earlier named `incident.statement`. A decision checkpoint is signed by the
`gateway` role for HTTP or the `boundary` role for MCP. An effect checkpoint is
signed by the `target` role. The statement topic is exactly
`denominator:{anchor_id}:{phase}:{protocol}`, its epistemic status is
`observed`, its claim is the closed machine value
`ORDERED_DENOMINATOR_SNAPSHOT`, and its subject `evidence_refs` contains only
the exact SHA-256 digest of the denominator artifact bytes. The ActionReceipt
also carries exactly one evidence reference to that digest with grounding
`self_asserted`; the observer is attesting to its own retained snapshot. The
checkpoint must precede every handoff and its issuer must be accepted through
the external role context.

The checkpoint authenticates what the path-separated observer reported. It
does not prove that the observer reported every event or that the observer is
organizationally independent. A self-shortening observer remains outside the
packet's detection boundary.

The published report must equal the recomputation exactly:

```json
{
  "schema_version": 1,
  "anchor_id": "string",
  "phase": "decision",
  "protocol": "http",
  "denominator_sha256": "sha256:…",
  "minimum_verification_depth": "attestation",
  "total": 2,
  "receipted": 2,
  "covered_ids": ["…"],
  "uncovered_ids": [],
  "phantom_receipt_ids": [],
  "invalid_receipts": []
}
```

Missing, empty, or duplicate denominator identifiers make the group
`NOT_COMPUTED`.
Uncovered events do not make packet integrity fail.

## 5. Trust, time, and disclosure

Accepted issuers by role and trusted witness roots are verification inputs
obtained outside the packet. Packet-carried roles and roots cannot bootstrap
trust.

A packet contains zero or one witness reference. This v0.1 draft does not
define aggregation or precedence across multiple witness chains; a packet with
more than one witness reference is malformed. Multiple-witness support remains
unresolved until a later profile defines per-witness results and a monotonic
aggregate verdict.

The verifier reports:

- `claimed_at` only from ActionReceipt occurrence proofs;
- `received_at` only from witness intake;
- `witnessed_at` only from signed checkpoint evidence;
- `anchored_before` only when external anchoring evidence is present.

A witness artifact is not an unsigned inclusion list. Its
`witness-checkpoint` proof signs, under the ActionReceipt v0.4 proof domain, the
hash of `witness_id`, `root_ref`, `received_at`, `witnessed_at`, and the exact
ordered included-attestation list. The issuer must match the packet role and
the out-of-band accepted witness issuer; the root must occur in the
out-of-band trusted-root set. Only that complete check produces
`witness_inclusion=VERIFIED` and supplies `received_at` or `witnessed_at`.
`anchored_before` remains absent unless a separate external anchor proof is
defined and verified.

A redaction entry binds a released redaction-record artifact, source and
released artifacts, tool and version, rules hash, and reviewer statement. The
record omits the reviewer reference and exactly repeats the source and release
byte hashes, tool, version, rules hash, and
`disclosure_safety=NOT_COMPUTED`. The reviewer statement uses topic
`redaction:{redaction_id}`, status `observed`, claim
`REDACTION_BINDING_REVIEWED`, and one subject and top-level self-asserted
evidence reference to the exact redaction-record artifact SHA. This
non-circular chain establishes byte binding only; it does not establish
disclosure safety.

Party statements retain their epistemic status and signer. Conflicts and
correction forks remain visible; actor time never selects a winner.

An `incident.correct` record resolves references according to
`supersedes_kind`. For `statement`, both `supersedes_ref` and
`replacement_ref` must be attestation hashes of named statement receipts that
occur earlier in the packet timeline. For `packet`, `supersedes_ref` must
match `packet-core.supersedes_core_hash` or the canonical hash of a released
artifact with role `packet-core`; `replacement_ref` must match a different
released `packet-core` artifact. The current packet core is not a valid
replacement reference because embedding its own hash in a timeline receipt
would be circular. A released packet-core artifact establishes content
resolution only; the referenced packet's publish proof must be verified
separately.

## 6. Source-only interface

```python
IncidentVerificationContext(...)
parse_incident_packet(packet_dir, *, limits=...)
verify_incident_packet(packet_or_path, context, *, limits=...)
```

`IncidentPacketVerification` and per-anchor coverage results reject Boolean
coercion. The repository checker maps unsafe or malformed input to exit `2`,
integrity or required proof failures to exit `1`, and completed verification to
exit `0`. Coverage gaps, unavailable controlled evidence, party conflicts, and
untrusted optional witness roots remain explicit completed results.

## 7. Experimental-profile limitation

The incident profile does not interpret executable or semantic ActionReceipt
conventions. Cross-runtime convention evaluation remains outside this draft's
verification contract. A future profile revision may admit a narrower
convention subset after its syntax and evaluation semantics have checker
parity. Until then, live and publish receipts use an empty convention list.
