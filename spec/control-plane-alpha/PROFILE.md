# Bulla control-plane alpha

Profile: `bulla.control-plane-alpha/0.1-experimental`

Status: source-only experiment. It is not a PyPI export, stable CLI, production
deployment contract, or reliance decision.

## Boundary and profiles

The outer service manifest, run state, and AuthZEN records use the Bulla
control-plane profile. Every receipt exported into the evidence packet uses the
existing closed `glyph.agent-incident-packet/0.1-draft` profile in
`action.subject.profile`. The outer profile is allowed only in producer
evidence; it is not added to closed incident subjects.

This profile has two distinct verification surfaces:

- The deterministic `vectors/full-loop` conformance fixture contains the
  generic incident packet and a control-plane outer archive:
  `service-manifest.json`, `run-state.json`, `standards-pin.json`, AuthZEN
  records, and service-witness records. Only that fixture is input to
  `verify_control_plane_fixture`.
- A hosted run exports a generic
  `glyph.agent-incident-packet/0.1-draft` packet. The standalone Python and Node incident-packet checkers verify that packet. The hosted service manifest, run
  summary, witness checkpoint, inclusion proof, consistency proof, and exact
  witnessed receipt bytes remain separately retrievable live resources. The
  manifest and run summary require their own closed-contract checks. The
  checkpoint signature, inclusion proof, consistency proof, and exact witnessed
  receipt bytes are verified against the selected live checkpoint. None of these
  resources is a member of the hosted packet.

A hosted export does not claim deterministic outer-archive closure and is not an input to `verify_control_plane_fixture`.

The deterministic packet timeline is exactly:

1. `eval.run.authorize`, signed by `evaluation_authority`;
2. `capability.decide` with `PERMIT`, signed by `boundary`;
3. `capability.decide` with `REFUSE`, signed by `boundary`;
4. `capability.observe`, signed by `boundary` for the mediated MCP result;
5. `incident.statement`, signed by `boundary` for the decision denominator;
6. `incident.statement`, signed by `target` for the path-separated backend
   effect denominator;
7. `incident.statement`, signed by `incident_commander`, recording
   `EFFECT_RECEIPT_GAP_CAUSE_UNRESOLVED`; and
8. `incident.handoff`, signed by `incident_commander`.

The non-circular `incident.packet.publish` receipt is signed separately by
`publisher`. A `witness` signs the checkpoint that includes all eight timeline
attestations. Custom `control.*` receipt actions are outside this profile.

## Standards pins

`standards-pin.json` pins two standards:

- OpenID AuthZEN Authorization API 1.0 Final at its official OpenID URL and a
  SHA-256 digest of the retrieved final document, published `2026-01-11`.
  Authorization API 1.0 permits
  a decision response `context`; the keys inside this profile's context are
  Bulla-specific and do not claim general AuthZEN meaning.
- The final MCP `2026-07-28` schema at immutable commit
  `271ecc9accafdd9b83a3c869fa67c22953b2af80` and SHA-256
  `ef70b61f99b6d2e5e3b46863822eab08dff6a45bedc7a08914e0e5b133f40203`.

The final-schema comparison records two added subscription-result definitions,
one removed definition, and one changed `SubscriptionsListenResult`
definition. The closed `tools/list` and `tools/call` subset does not use those
definitions. The MCP standards gate is `PASSED`. Production remains
`BLOCKED_NO_REMOTE_MUTATION_AUTHORIZED` under the separate operational blocker
ledger.

## Closed AuthZEN mapping

No additional fields are accepted at the listed levels.

### Request

| Object | Exact fields | Rule |
|---|---|---|
| root | `subject`, `action`, `resource`, `context` | all required |
| `subject` | `type`, `id` | exactly `{"type":"principal","id":"agent:synthetic-alpha"}` |
| `action` | `name` | `name = "tools/call"` |
| `resource` | `type`, `id` | `type = "mcp-tool"` |
| `context` | `profile`, `run_id`, `request_id`, `tool_name`, `arguments_hash`, `audience`, `pdp_id`, `policy_digest`, `valid_from`, `valid_until` | closed constants and SHA-256 commitments |

The experiment accepts only these exact fixed mappings:

| Resource | Tool | Input commitment |
|---|---|---|
| `mcp://synthetic-receiver/tools/sandbox.append` | `sandbox.append` | canonical SHA-256 of `{"value":"alpha-mediated"}` for `PERMIT` |
| `mcp://synthetic-receiver/tools/sandbox.append` | `sandbox.append` | canonical SHA-256 of `{"value":"alpha-refused"}` for `REFUSE` |

`audience` is exactly `synthetic-receiver`, `pdp_id` is exactly
`bulla-closed-authzen/1`, and the subject is scenario data—not an issuer key.

The request commitment is the incident profile's
`canonical_hash(request)` (`bulla-jcs-int/1`).

### Decision

The response has exactly `decision` and `context`. `decision` is a JSON
boolean. Context has exactly `profile`, `run_id`, `request_id`, `decision_id`,
`request_hash`, `sequence`, `reason_code`, `policy_ref`, `policy_hash`,
`policy_digest`, `pdp_id`, `audience`, `tool_name`, `arguments_hash`, and
`valid_until`.
The policy reference is exactly
`policy://bulla-control-plane-alpha/synthetic-authzen`.

`decision.context.request_hash` equals the canonical request hash; sequence is
`1` for the permit scenario and `2` for the refusal scenario. Response
tool/input/policy/audience/expiry fields equal the request. `true` maps to the packet
decision `PERMIT`; `false` maps to `REFUSE`. The packet receipt's request,
decision event, policy digest, and sole rationale code must match.

The hosted runtime accepts the scenarios only in that order. Before either
decision denominator is recorded, it verifies the signed
`eval.run.authorize` receipt against the out-of-band evaluation-authority key,
the exact run, policy, target, budgets, prohibitions, six role issuers, and its
active ten-minute validity interval. Recovery from a denominator-to-snapshot
failure reuses the persisted request and decision event only after every
closed AuthZEN field, hash, sequence, run binding, policy binding, and validity
interval recomputes.

## Exact packet subjects and roles

This profile reuses, without extending, the incident packet's existing closed
subjects for:

- `eval.run.authorize`;
- `capability.decide`;
- `capability.observe`;
- `incident.statement`;
- `incident.handoff`; and
- `incident.packet.publish`.

All are ActionReceipt v0.4 with content, occurrence, and authorization proofs,
no conventions, and an authority principal equal to the proof issuer.
`boundary` is the MCP receiver/enforcement service and signs its
`capability.observe` with `self_asserted` evidence. `target` is a different key
that signs only the backend effect-denominator checkpoint. This separation
prevents the receiver from defining both the mediated observation and the
target-side denominator.

## Six-role trust context

`contexts/alpha-context.json` is the canonical rich trust context. It is
external to the packet and has exactly:

- `profile`;
- `accepted_issuers_by_role`;
- `role_identities`;
- `trusted_policy_hashes`;
- `trusted_witness_roots`; and
- `team_controlled_roles`.

It pins six distinct roles and did:key issuers:
`evaluation_authority`, `boundary`, `target`, `witness`,
`incident_commander`, and `publisher`. Each `role_identities` value carries
exactly `issuer`, `verification_method`, and `public_key_sha256`. Packet or
service data never bootstraps those trust choices. One issuer cannot occupy two
roles.

`contexts/incident-context.json` is a generated projection containing exactly
the incident verifier's three external fields:
`accepted_issuers_by_role`, `team_controlled_roles`, and
`trusted_witness_roots`. The generator enforces parity with the rich context;
it is not an independent trust source.

## Run state

The complete ordered progression is:

```text
CREATED
  -> AUTHORIZED
  -> MEDIATED_EFFECT_RECORDED
  -> BYPASS_RECORDED
  -> FINALIZED
```

`FINALIZED` is terminal. `run-state.json` materializes the final state and all
five ordered transitions. Each transition has exactly `sequence`, `state`, and
`evidence_ref`, and every evidence reference is a SHA-256 commitment.

The run record has exactly `schema_version`, `profile`, `vector_id`, `run_id`,
`status`, `mandate_receipt_path`, `transactions`, `effect_receipt_paths`,
`decision_checkpoint_path`, `effect_checkpoint_path`, `gap_statement_path`,
`handoff_receipt_path`,
`transitions`, and `errors`. Each transaction has exactly `request_id`,
`decision_id`, `request_path`, `decision_path`, and
`decision_receipt_path`. Each error has exactly `code`, `sequence`, and
`detail`.

## Coverage and packet evidence

In the deterministic conformance fixture, the incident packet core is the
closed byte manifest. It binds the fixture's released outer records, AuthZEN
records, denominator and coverage reports, receipts, and packet witness
checkpoint. `publish-receipt.json` signs the completed core without
circularity.

In a hosted export, the incident packet core binds only the released members
declared by that generic incident packet. Service-level manifest, run, and
witness resources remain outside the packet and require separate verification.

The verifier derives coverage from independently declared observations and
accepted receipts:

- decision: two denominator observations and two accepted
  `capability.decide` receipts = `2/2`;
- effect: two target-side denominator observations and one accepted mediated
  `capability.observe` receipt = `1/2`;
- `effect-bypass-001` remains explicitly uncovered.

The `incident_commander` gap statement uses topic `effect-coverage`, claim
`EFFECT_RECEIPT_GAP_CAUSE_UNRESOLVED`, epistemic status `observed`, and an
evidence reference equal to the exact released effect-coverage artifact hash.
Its ActionReceipt evidence is named
`recomputation:effect-coverage-report` with grounding
`execution_verified`.

The witness checkpoint includes every timeline attestation and must verify
under the separately trusted witness issuer and root. The service witness also
retains the exact bytes of all eight timeline receipts in one base64 archive
with a byte-length/hash index, logs exact deed triples
`{issuer,content_hash,attestation_hash}` in `DeedLog`, signs a three-entry
prefix checkpoint and eight-entry final checkpoint, and carries RFC 6962
permit inclusion and prefix-to-final consistency proofs. The final run
transition binds the final service checkpoint. `reliance` remains
`NOT_COMPUTED`.

The hosted service witness supports checkpoint-targeted reads. A verifier first
selects and authenticates one signed checkpoint of size `N`, then requests
inclusion with `at=N` and consistency with `from=P&to=N`. Both proofs use the
immutable historical prefix ending at `N`; concurrent appends cannot change
the selected root. Omitting `at` or `to` retains the latest-tree behavior.

## Errors and exits

Closed outer error codes are:

- `MALFORMED_FIXTURE`
- `INCIDENT_PACKET_FAILED`
- `UNTRUSTED_POLICY`
- `MAPPING_MISMATCH`
- `RUN_STATE_INVALID`
- `COVERAGE_MISMATCH`
- `STANDARDS_GATE_INVALID`

Exit `0` means every named evidence dimension verified, not that reliance is
authorized. Exit `1` means well-formed evidence failed trust, integrity,
mapping, coverage, or standards-gate verification. Exit `2` means malformed,
unsafe, unsupported, or over-limit input.

## Resource limits

Limits apply before semantic or cryptographic work:

| Limit | Default |
|---|---:|
| files | 32 |
| one generated receipt/file | 65,536 bytes |
| all files | 2,097,152 bytes |
| JSON depth | 24 |
| JSON nodes | 20,000 |
| one JSON string | 65,536 UTF-8 bytes |
| transactions | 2 |

The reader reuses the incident packet's descriptor-relative, no-follow
boundary. Symlinks, reparse points, non-regular files, undeclared files,
missing files, and digest/length mismatches fail closed.

## Alpha retention and disclosure

Every released artifact declares `retention = P180D` and
`access_condition = public-synthetic`. This is the alpha contract: the
synthetic packet is public, the service exposes no deletion operation, and
operators are expected to preserve the released bytes for at least 180 days.
This is not a durability proof. No independent archive, replication SLA, or
external availability monitor establishes that a deployment will actually
retain the bytes for the full period.

## Hosted run creation capability

`POST /experimental/v1/runs` requires an `Idempotency-Key` containing a fresh,
cryptographically generated UUIDv4. The key is a secret-equivalent, one-time
creation capability. An exact replay returns the same run bearer credential
only while the capability key remains unchanged. After a capability-key change,
replay fails closed without returning a replacement token.
Clients must not log, persist, publish, reuse, or select a predictable value for
that key. The general 16–128 visible-ASCII idempotency-key rule applies only to
authenticated mutations after run creation.

The service master `CAPABILITY_TOKEN_SECRET` is canonical unpadded base64url
encoding of exactly 32 cryptographically generated bytes. Runtime and
deployment gates reject padded, noncanonical, wrong-length, and obviously
low-diversity inputs. Operators provision the secret without logging it.

Creation and mutation idempotency bind the exact bounded request-body bytes
accepted by the strict parser. JSON whitespace, member order, and escape
spelling are not normalized for replay. Exact key-and-byte replay returns the
original response; the same key with different bytes fails with
`IDEMPOTENCY_CONFLICT`. A persisted exact replay or conflict is resolved before
rate-limit quota is consumed. Allocation misses, mutation misses, unauthorized
mutations, and unknown-run mutations remain rate-limited before any new
allocation or state change. Per-run mutation serialization begins before the
snapshot is loaded, preventing concurrent requests from committing stale
state.

## Determinism

`generate_vectors.py` derives UUIDv4 values and Ed25519 keys from labelled
SHA-256 seeds. `--check` rebuilds the complete packet, canonical outer and
derived incident trust contexts, and expected verdict and compares every byte.
This byte-identical outer-archive guarantee applies only to the deterministic
conformance fixture, not to ephemeral hosted run exports.
The expected verdict records the
blocked deployment gate, trusted witness inclusion, `2/2` decision coverage,
`1/2` effect coverage, and `NOT_COMPUTED` reliance.
