# Golden v0.2 formal audit

Local toolchain: `leanprover/lean4:v4.28.0`

Commands:

```sh
lake build InterpolantEnvelope
lake env lean InterpolantEnvelope/Axioms.lean
```

Result: clean build, zero `sorry`.

The four v0.2 theorems—literal-dropping soundness, sufficient-cover soundness, truncation safety, and disjoint truncated regions—report no axioms. Existing formal results retain their previously audited Core/Quotient axioms; v0.2 does not strengthen their claims.

The formal bundle abstracts complete finite feature vectors and checked cubes. It does not verify Python enumeration, parsing, canonicalization, authority signatures, resource accounting, or benchmark generation. Those boundaries remain explicit executable tests.
