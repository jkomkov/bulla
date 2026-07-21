# ActionReceipt v0.2 implementation checklist

An implementation claiming v0.2 digest conformance must:

- parse `schema_version: "0.2"` and `kind: "action_receipt"`;
- reject unknown top-level fields and malformed cross-field states;
- reproduce CANON_VERSION 2 exactly, including ASCII escaping and integer-only
  portability guidance;
- recompute `content`, `event`, `attestation`, and RFC 6962 `log_leaf` hashes;
- require a grounding class on every v0.2 evidence reference and display the
  weakest necessary grounding;
- fail closed on unknown executable-convention keywords;
- pin every convention definition and recompute executable conformance over
  `action.subject`;
- enforce the remedy modality law and trusted-root requirement;
- keep `stake` present and `null`;
- distinguish format-version fallback from tampering;
- accept every golden vector and reject every tampered vector for the reason
  recorded in `vectors/expected.json`.

Signature and COSE verification are a separate conformance rung. A digest-only
implementation must label its result accordingly.
