# External role packet

This directory is a protocol template, not evidence that external execution occurred.

Curators must replace the age-recipient placeholders, generate their own cases after the candidate freeze, and encrypt oracle material outside implementation-team custody. Do not commit plaintext answers, nonces, identities, private keys, or adjudication notes.

Required case distribution per curator:

- eight `MACHINE` or `PROPERTY` cases;
- four `ADJUDICATION` or open-world closure challenges.

Submission order is enforced by receipts: Bulla output and clean-room output must both be hash-committed before a two-curator reveal. Found-data primary ratings use opaque adjudicator IDs; diagnostic third reviews never overwrite disagreement.
