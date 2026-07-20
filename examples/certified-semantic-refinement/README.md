# Certified semantic refinement demo

This offline demonstration executes the full experimental transition:

```text
same-reduct witness -> exact observable plan -> signed provision ->
constraint admission -> narrower envelope -> applied decision -> receipts ->
Merkle inclusion -> signed checkpoint
```

It also exercises the other honest exits: a total compiled term, a partial
residual, a governed `CHOICE_REQUIRED` selection, epoch staleness, and a signed
same-position split view. The frozen artifact explicitly classifies unchanged,
admission, unresolved, and epoch-change movements as `PRESERVE`, `REFINE`,
`ROUTE`, and `REVISE`. The two observers and witness are locally controlled;
the split-view result validates the mechanism, not independent plurality.

Run and freeze the deterministic artifact with:

```sh
PYTHONPATH=bulla/src python bulla/examples/certified-semantic-refinement/run_demo.py \
  --fixture-keys \
  --output bulla/examples/certified-semantic-refinement/demo-output.json
```

The standalone planning and refinement replays run under `python -I` and import
no Bulla code. This demo makes no external-generalization, settlement, or
production-recourse claim.
