# Bulla Witness Covenant 0.1 Experimental

Status: `experimental · SOURCE_ONLY · synthetic-public · project-operated · r0`

`bulla.witness-covenant/0.1-experimental` is a closed Assurance Linker
promise for one receipt-witness service duty. The operator promises not to
sign different roots for the same log, authority epoch, and tree size. The
closed predicate `bulla.same-size-log-equivocation/1` is satisfied only when a
verifier jointly receives two authentic, comparable checkpoints with different
roots.

The covenant binds the operator, key, log, epoch, predicate, challenge
checkpoint, correction path, beneficiary, settlement authority, destination,
maximum remedy, and dedicated capital allocation. It reuses
`assurance.collateral.bind` and `assurance.settlement.authorize`; it introduces
no new settlement action.

## Reports

The verifier reports checkpoint authenticity, joint observation, provider-claim
inclusion, history consistency, the objective fault predicate, challenge state,
dedicated allocation, remedy eligibility, exact authorization, and a Test
ledger attempt as separate fields. Boolean coercion is rejected.

A reported bond cannot improve witness trust, checkpoint authenticity, history
integrity, receipt truth, occurrence, or control independence. A nominal amount
does not establish deterrence, custody, collectibility, external unencumbrance,
or actual recovery.

## Constitutional boundary

Automatic handling is limited to authentic same-size equivocation. Missing
retrieval, delayed publication, proof unavailability, model identity, semantic
appraisal, and substantive receipt claims do not satisfy the predicate. Version
0.1 accepts only `SAME_SIZE_LOG_EQUIVOCATION` and the leaf-bound
`MODEL_IDENTITY_P3` control. It rejects P4 and every other unsupported class;
this wire format does not express a forum transition.

The fixture uses `USD_CENTS`: a $200.00 reported dedicated allocation and a
maximum $125.00 remedy. The rail is `Test ledger`; no real funds are used.

## Source interfaces

```python
verify_witness_covenant(directory, context_bytes, *, limits)
verify_witness_covenant_report(report_bytes, directory, context_bytes, *, limits)
```

The profile is not exported from installed Bulla, exposed through its CLI, or
included in wheel or sdist artifacts.
