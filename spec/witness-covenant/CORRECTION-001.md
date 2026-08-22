# Witness Covenant Preregistration Correction 001

Date: 2026-08-21

The frozen preregistration remains byte-identical to its original form. Its
SHA-256 digest is
`4e54e8f48f73b3b9133f27ecba75398b497b5bc6d3d74da2d49122e9ccae7fbb`.

R3 review found that the original five scenarios did not contain the controlled
pair needed to test the already-preregistered condition that adding or removing
the reported bond changes only capital and recourse fields. The generated
corpus therefore adds `fork-closed-no-bond` as a paired control for
`fork-closed`. It is not a new success endpoint. The objective fault,
checkpoint, and challenge facts remain the same; capital becomes inadequate and
the remedy becomes ineligible.

The `model-dispute` control now contains the provider's exact signed
`MODEL_IDENTITY_P3` proposition and its leaf-bound witness inclusion. This
closes the evidence construction required by the original success condition;
it does not upgrade the claim beyond `CHALLENGE_REQUIRED`.
