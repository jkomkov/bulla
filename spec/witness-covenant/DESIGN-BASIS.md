# Design Basis

The covenant uses signed checkpoints and same-size split-view detection from
the Certificate Transparency lineage, including RFC 9162. CoSi supplies the
important limit: witnesses attest to what they saw, not to the truth of the
statement. UMA documents the authored tradeoff between challenge liveness and
bond access. Volume II Appendix A keeps deterrence separate from restitution;
Appendix B separates deterministic, contestable, and subjective attestations.

The withdrawn Witness Gram and Witness Geometry papers concern disclosure
geometry rather than operated receipt witnesses. Their withdrawn conclusions
are not used to size the bond, predict witness failure, or authorize recourse.

Primary references:

- RFC 9162, Certificate Transparency Version 2.0.
- Syta et al., CoSi: Collective Signing.
- UMA documentation, custom bond and liveness parameters.
- Res Agentica Volume II, Appendices A and B.
