# Clean-room reproduction contract

The clean-room implementer receives this contract, Golden v0.2 `SPEC.md`, FRSL-1's written syntax and finite semantics, the frozen scoring declaration, and reviewer-originated plaintext inputs. They do not receive Bulla source code, Bulla's checker, or expected verdicts.

For every machine/property input, emit canonical JSON containing:

- case ID;
- typed exit and cause;
- canonical result or certificate-normalized hash;
- protected consequences;
- authority, epoch, and closure bindings;
- reserve amount where applicable;
- exhaustion frontier where applicable.

Unknown fields and malformed inputs fail closed. Solver proof bytes may vary, but their independently checked normalized certificate must agree. Adjudication inputs may emit a typed abstention or evidence request; they must not manufacture an institutional selection.

The complete result array is sorted by case ID and hash-committed before reveal. The implementer records OS, architecture, locale, timezone, dependency versions, runtime, and peak memory. A Bulla import, copied checker, or access to expected outputs invalidates the clean-room label.
