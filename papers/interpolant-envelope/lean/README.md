# Interpolant Envelope Lean Core

This project pins Lean 4.28.0 and contains the finite semantic statements used
by the FRSL-1 research engine. It deliberately does not import or rely on
SCPI's proposition-level Beth scaffolding.

Result v0.2 also formalizes the non-uniqueness boundary: candidates quotient
by protected behavior and declared finite cost; selecting within one class
preserves the definition, while equal-cost candidates that differ on a
protected observation remain distinct classes requiring an external selector.

Build:

    lake build

Promotion requires:

- no `sorry`;
- a clean local build under the pinned toolchain;
- `#print axioms` checks showing only Lean's propositional extensionality where
  explicitly used by the same-reduct theorem;
- separate abstraction-to-Python tests.
