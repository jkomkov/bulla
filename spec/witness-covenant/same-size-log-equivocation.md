# `bulla.same-size-log-equivocation/1`

Inputs are two authenticated `bulla.witness-checkpoint/0.1-draft` documents and
a separately supplied context accepting their operator, log, epoch, and
covenant. The predicate has one positive conclusion:

`ESTABLISHED` means the same accepted operator signed different roots for the
same accepted log, authority epoch, covenant, and tree size, and both signed
views reached the verifier together.

It does not conclude that either history is true, that any receipt describes a
worldly event, that the witness is independent, or that a nominal bond can be
collected. Missing retrieval and late publication are outside the predicate.
