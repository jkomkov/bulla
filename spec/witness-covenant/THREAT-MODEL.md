# Witness Covenant 0.1 Threat Model

The verifier treats the dossier, actor timestamps, checkpoint labels, and
packet-carried keys as hostile. Accepted operator keys, role issuers, authority
grants, rail adapters, covenant hash, log, and epoch arrive in a separate
verification context.

Required hostile cases include invalid and borrowed signatures; different log,
epoch, size, or operator; isolated views never jointly compared; missing or late
proof relabelled as equivocation; stale challenge state; wrong allocation, unit,
binding, amount, destination, authority, checkpoint, or promise; double pledge;
P3/P4 automatic recourse; unsafe paths and symlinks; duplicate JSON members;
withholding; cross-covenant replay; and Test-ledger language implying real
settlement.

The executable corpus maps these classes to named tests in
`bulla/tests/test_witness_covenant.py`:

| Threat class | Regression |
|---|---|
| Invalid or borrowed authentication | `test_invalid_checkpoint_signature_fails_closed_in_both_kernels`, `test_borrowed_receipt_proof_is_rejected` |
| Wrong log, epoch, size, operator, or checkpoint shape | `test_validly_signed_incomparable_or_malformed_head_is_rejected` |
| One view or withheld head | `test_single_view_cannot_establish_fault` |
| Packet trust and cross-covenant replay | `test_packet_carried_trust_cannot_replace_context`, `test_cross_covenant_replay_is_rejected` |
| Premature or malformed challenge state | `test_challenge_checkpoint_type_and_range_are_closed` |
| Capital shortfall, unit, binding, double pledge, or boolean coercion | `test_shortfall_wrong_unit_wrong_binding_and_double_pledge_fail_closed`, `test_allocation_active_rejects_integer_boolean_coercion` |
| Wrong consequence binding or authority | `test_authorization_exact_binding_hostiles`, `test_different_settlement_signer_cannot_be_laundered_by_context` |
| Operator acceptance laundering | `test_witness_operator_must_accept_its_own_covenant` |
| P3/P4 automatic recourse | `test_model_identity_cannot_use_objective_witness_remedy`, `test_unknown_semantic_class_is_rejected_not_collapsed_to_p3` |
| Archive, JSON, path, and parser divergence | `test_every_declared_json_member_and_posix_path_is_strict` and the parser-limit regressions |
| Contradictory rail evidence | `test_contradictory_test_ledger_real_funds_language_is_rejected` |

Residual risks include incomplete observation, key compromise, organizational
collusion, unavailable checkpoints, external encumbrance, custody failure,
legal non-enforceability, and non-collection. Version 0.1 neither measures nor
prices those risks.
