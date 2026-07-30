# Control-plane alpha threat model

Status: experimental. The findings in this document remain open review items;
this implementation does not close or waive them.

## Protected claims

The verifier protects the exact bytes and semantic mappings of a small
AuthZEN-shaped MCP control loop. It distinguishes:

- receipt integrity from issuer trust;
- request/decision coverage from effect coverage;
- observed mediated effects from denominator-only bypass effects;
- the pinned MCP final schema from the earlier RC snapshot; and
- verified evidence from a reliance decision.

## Hostile-test matrix

The named tests below are regression anchors, not claims that the residual
limitations are closed. A runtime-only row without a landed runtime test is a
deployment blocker for this source-only profile.

| Threat family | Mechanism | Named regression test or blocker | Residual limitation |
|---|---|---|---|
| Byte tamper, truncate, rehash, or publisher-only re-sign | The packet core commits exact path, byte length, and SHA-256; timeline entries commit the exact receipt bytes and attestation; the non-circular publish receipt cannot replace a boundary or target signature. | `test_request_mapping_tamper_fails_after_packet_is_validly_republished`; `test_tampering_fails_closed_and_suppresses_dependent_claims`; `test_publisher_alone_cannot_republish_a_self_shortened_denominator` | Detects mutation at verification time; it does not prevent deletion or provide an independent copy. |
| Receipt re-sign, role substitution, one key in several roles, or proof-purpose transplant | Six out-of-band role pins bind did:key issuer, verification method, and key fingerprint; incident rules close each action to an issuer role; content, occurrence, authorization, and witness proofs are domain-separated. | `test_trust_substitution_does_not_bootstrap_from_packet`; `test_context_rejects_cross_role_issuer_collision`; `test_attacker_resigning_cannot_assume_a_declared_role`; `test_unsigned_or_transplanted_boundary_proofs_fail` | Correct key custody and organizational separation remain operational assumptions. |
| Actor time promotion, replay, invalid mandate, or a receipt/request grafted across request, run, policy, decision, or phase | Signed subjects bind `run_id`, `request_id`, mandate, policy, canonical request, decision event, decision attestation, and anchor; the runtime verifies the mandate's exact closed subject, role issuers, policy, and active interval before denominator persistence; actor-claimed time is kept distinct from witness time. | `test_resigned_semantic_and_binding_mutations_match_all_checkers`; `test_cross_phase_action_type_transplant_fails_closed`; `test_actor_claimed_time_cannot_promote_itself_to_witness_time`; `validates the closed mandate and fixed sequence before recording a denominator` | No trusted external clock proves wall-clock freshness, and this fixture does not maintain a global replay cache. |
| AuthZEN/MCP shape smuggling, wrong tool, resource, input, audience, PDP, decision reason, or scenario order | Exact-key validation and fixed constants allow only `tools/call` to `mcp://synthetic-receiver/tools/sandbox.append`, the two committed values, `synthetic-receiver`, and the closed permit-then-refusal policy sequence. Persisted denominator recovery recomputes the complete closed evaluation. | `test_authzen_mapping_is_closed_and_uses_exact_sandbox_scenarios`; `test_request_mapping_tamper_fails_after_packet_is_validly_republished`; `rejects open or misbound persisted AuthZEN records before minting a receipt`; `rejects refusal before the fixed permit-then-refusal sequence without a denominator` | This proves the synthetic mapping, not arbitrary MCP tool semantics or policy correctness. |
| Host header, origin, bearer-header, capability-header, media-type, or endpoint confusion at the hosted MCP/HTTP surface | Hosted reads are intended to be public; mutating routes require the run capability and exact-byte idempotency binding; persisted exact replays resolve before quota, while misses, unauthorized calls, and unknown runs remain rate-limited before new state changes; MCP routing, positive-quality exact media types, body metadata, tool arguments, and permit commitments must agree. | `browser-hosted proof and local tamper contract`; `rate-limits unknown mutations without allocating a run object`; `returns exact creation replays before quota while rate-limiting new allocations`; `returns exact mutation replays before quota while rate-limiting new mutations`; `rejects zero or malformed MCP Accept quality values`; `maps JSON-RPC root and params shape failures to closed numeric errors` | CORS and Origin are browser controls, not authorization. Replay probes perform bounded allocation and per-run idempotency reads before quota; first-arrival concurrent misses can each consume quota before coalescing at durable allocation. Production proxy normalization remains a deployment concern. |
| Concurrent decision, dispatch, direct effect, or finalization; crash between denominator persistence, snapshot commit, remote effect, receipt persistence, witnessing, and response | Fixed sequencing and per-run serialization prevent interleaved decision order; persisted exact denominator records recover deterministic decision event and claimed time; durable intent/state and idempotency commitments precede effect dispatch; recovery does not infer success from absence; the receiver deduplicates an effect by its closed effect key. | `distinguishes before-denominator and after-denominator receipt failures`; `recovers the exact persisted decision after a denominator-to-snapshot crash`; `serializes concurrent decision scenarios without sequence or state corruption`; `stops before receiver dispatch without reporting an observed effect`; `recovers the same receiver effect after an in-flight crash window`; `persists pre-publication receipts before a witness outage without finalizing`; `recovers effect after durable IN_FLIGHT crash before observation persistence`; `allows one effect and rejects the competing different-idempotency dispatch`; `allows one direct bypass mutation and closes a competing idempotency key`; `allows one finalization, closes a competing key, and replays without new witness writes` | The tests exercise real local Workers/SQLite object boundaries, not a production Cloudflare outage. A receipt can be persisted before a witness outage, and an accepted receiver effect can remain without a completed observation response. |
| Empty, truncated, fabricated, reordered, cross-anchor, or self-shortened denominator; duplicate or phantom coverage | Denominator artifacts are separately signed by boundary/target roles, committed by checkpoint statements, and recomputed rather than trusted from declared totals; publisher cannot rewrite the target checkpoint. | `test_hostile_denominator_mutations_never_preserve_declared_coverage`; `test_duplicate_denominator_ids_make_coverage_not_computed`; `test_phantom_claim_in_declared_coverage_is_rejected`; `test_publisher_alone_cannot_republish_a_self_shortened_denominator` | The synthetic target denominator demonstrates separation but does not prove production completeness or organizational independence. |
| Witness rollback, fork/split view, omitted receipt, false inclusion, inconsistent extension, alternate receipt serialization, or concurrent duplicate intake | `DeedLog` leaves use exact `{issuer,content_hash,attestation_hash}`; every newly accepted service receipt produces an atomic signed checkpoint; RFC 6962 inclusion and consistency proofs and exact receipt bytes remain separately retrievable. The compact packet witness remains a separate incident-profile artifact rooted at `witness://bulla-control-plane-alpha/public-alpha-v1`. | `test_service_witness_retains_bytes_and_proves_inclusion_and_consistency`; `test_signed_witness_mutations_fail_closed`; `test_split_view_drill_exposes_equivocation`; `test_trailing_receipt_whitespace_has_deep_checker_parity`; `rolls back a witness batch when a later member is invalid`; `persists a signed checkpoint for every accepted append in a multi-receipt batch`; `serializes concurrent different batches that contain the same attestation` | These are team-operated checkpoints. No independent gossip, globally witnessed root, or durable external archive rules out an organizationally coordinated split view or rollback. |
| Duplicate JSON keys, invalid UTF-8, deep/wide JSON, booleans as integers, unsafe paths, symlinks, reparse points, archives, undeclared files, or file/aggregate exhaustion | Strict JSON parsing, exact-key schemas, descriptor-relative no-follow reads, regular-file checks, a closed manifest, and producer/materializer limits of 32 files, 64 KiB per file or generated receipt, and 2 MiB total run before publication. | `test_packet_core_strict_ingestion_matches_standalone_checkers`; `test_packet_json_structural_resource_limits`; `test_unsafe_manifest_path_is_rejected_before_file_access`; `test_symlink_and_undeclared_file_are_rejected`; `test_archive_input_is_rejected_without_decompression`; `test_file_count_limit_fails_before_semantic_verification`; `rejects a generated ActionReceipt larger than 64 KiB before publication` | Filesystem guarantees depend on the host implementation; limits bound this profile but are not general denial-of-service protection. |
| Secret, credential, personal-data, host-path, exception, or unbounded free-text leakage | The vector and hosted loop are synthetic-only; request and incident subjects are closed; public error responses do not echo rejected private values; disclosure conditions prohibit credentials; privacy scans cover nested, derived, witnessed, and downloaded material. | `test_public_packet_has_no_heuristic_privacy_findings`; `test_privacy_detector_catches_nested_and_derived_material`; `test_bundle_carries_no_inline_secret`; `keeps live capabilities, signing secrets, rejected private data, host paths, and exception text out of retained artifacts` | The privacy detector is heuristic, not a noninterference proof. Public release is irreversible and operators must not substitute production secrets into the alpha. |
| Packet-supplied issuer lists, witness roots, or policy hashes bootstrap their own trust | Canonical `alpha-context.json` is supplied out of band, pins six distinct keys and the policy hash, and deterministically projects only the incident verifier's three context fields. Packet/service documents are never trust inputs. | `test_outer_and_incident_contexts_have_exact_parity_and_six_keys`; `test_trust_substitution_does_not_bootstrap_from_packet`; `test_packet_roles_do_not_bootstrap_trust`; `test_untrusted_policy_fails_independently_of_signature_integrity` | Distribution and approval of the external context remain deployment governance problems. |
| False standards promotion or unreviewed final-schema drift | The MCP final commit, bytes, and RC-to-final definition delta are pinned separately. The implemented `tools/list` and `tools/call` subset is tested against the final mapping. AuthZEN final retains its own official URL/date/digest. | `test_standards_pin_binds_authzen_and_mcp_finals_separately`; `test_mcp_final_pin_tamper_is_rejected` | The pin establishes compatibility for the closed synthetic subset, not every MCP capability or future revision. |
| Commit, trust-context, or evidence substitution at the production entrypoint | The manual workflow is validation-only: it proves one merged clean commit, installs the pinned toolchain, strictly validates the closed blocker contract, runs dry-run and package checks, and then deliberately fails. It has read-only repository permission and contains no Cloudflare, Vercel, release, routing, promotion, rollback, or other remote-mutation command. | `test_production_entrypoint_is_manual_validation_only_and_exact_commit_gated`; `test_blocked_workflow_contains_no_remote_mutation_command`; `test_blocker_contract_is_closed_and_names_every_unimplemented_safety_boundary`; `test_required_source_ci_executes_the_exact_two_state_projection_checker` | No deployment or rollback path is implemented or attested. Production remains blocked on a durable external reconciler, exact Glyph provenance, a signed complete release-asset manifest, retirement-export evidence, and a known read-compatible recovery release. |
| Premature deletion or private-treatment ambiguity | Every released packet artifact declares `retention = P180D` and public access; the alpha exposes no deletion or replacement operation; the service witness retains exact accepted receipt bytes. | `test_full_loop_reproduces_expected_multidimensional_verdict`; `test_service_witness_retains_bytes_and_proves_inclusion_and_consistency`; `retains every released packet artifact as public P180D evidence and exposes no delete route` | The contract requests at least 180 days but has no independent replication, durability SLA, erasure monitor, or availability proof. Production remains blocked until the operator retirement exporter is implemented and tested. |

## Open review findings

1. **Future MCP drift.** The implementation targets the pinned `2026-07-28`
   final and its closed tool subset. A later protocol revision requires a new
   immutable pin and mapping review.

2. **Denominator provenance.** The synthetic effect denominator demonstrates
   omission accounting but does not establish production-grade independence.
   A deployment needs a separately controlled target-side checkpoint or an
   incident-packet witness chain. `team_controlled_roles` is context, not proof
   that operational control separation actually exists.

3. **Witness independence and availability.** ActionReceipt v0.4 signatures
   verify to the attestation rung, and the team-operated service witness
   supplies authentic RFC 6962 inclusion plus prefix-to-final consistency
   proofs. That is a real append-only consistency proof for the represented
   tree, but it is not organizational independence: there is no independently
   gossiped checkpoint, globally observable root, or durable external
   availability guarantee.

4. **Bypass prevention.** The profile detects an unreceipted effect when a
   trusted denominator exposes it. It does not itself mediate or prevent direct
   effects.

5. **Policy semantics.** A trusted policy hash authenticates the selected
   policy bytes; this experiment does not execute or prove the policy's
   substantive correctness.

6. **Availability and freshness.** Claimed occurrence time is signed, but no
   trusted external time, liveness guarantee, or completeness proof is present.

7. **Reliance.** The verifier reports `NOT_COMPUTED`. No application should
   collapse a clean evidentiary result into permission to rely.

8. **Privacy and retention enforcement.** Released alpha artifacts are public,
   have no deletion operation, and declare a `P180D` minimum. The declaration
   is not a durability proof; replication, retention monitoring,
   data-minimization, and prevention of accidental production-data release are
   outside this experiment.
