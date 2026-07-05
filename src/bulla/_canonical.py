"""The canonical algorithm version — what a deed's ``f`` is pinned to.

A deed is a *recomputable* certificate: ``deed = f(composition@h, algorithm@v)``.
This constant IS the ``@v`` — committed inside the certificate content hash so a
verifier knows **which algorithm to run**. A mismatch between the deed's
``algorithm_version`` and the verifier's is then a *version difference*, not
"tampered". It bumps ONLY on a **verdict-affecting** change to
``diagnose`` / ``classify`` / ``coboundary`` / ``witness_geometry`` — NOT on every
release (``bulla_version`` stays excluded provenance).

**Honest ladder.** This semver is the *weakest* rung: it is the one **trusted
human input** in a system whose whole pitch is "nothing trusted, recompute it" — a
person must remember to bump it, and the golden seed test (which pins canonical
hashes) is a **stopgap for the missing auto-coupling between ``f``'s source and its
version**, NOT the guarantee. The canonical target, which this program is uniquely
positioned to reach:

  * **now**     — this semver, golden-guarded (forget-prone).
  * **next**    — derive it from the *content* of ``f`` (a hash over the verdict
                  source), so any change to ``f`` bumps it automatically (forget-proof).
  * **target**  — bind it to the Lean-spec hash / Aristotle stamp that *defines* the
                  fee, so the deed's ``f`` IS the machine-checked proof and
                  recomputability becomes provable *correctness*, not just determinism.
                  No eval vendor can pin its algorithm to a proof; Bulla already has
                  the stamps.
"""

ALGORITHM_VERSION = "1"
