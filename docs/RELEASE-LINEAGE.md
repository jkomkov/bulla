# Release lineage

PyPI is the publication boundary. Versions that reached a signed preparation
slot but not PyPI remain recorded and are not reused.

| Version | PyPI | Status |
| --- | --- | --- |
| 0.45.0 | not published | The Windows prepublication gate found checkout-dependent kit bytes. The tag and slot remain as the correction record. |
| 0.45.1 | published 2026-08-03 | Portable verification-kit release. The immutable GitHub release body retained its provisional preparation sentence; the attached signed receipt, kit, detached digest, PyPI provenance, and changelog are the final release evidence. |
| 0.47.0 | not published | The Windows prepublication gate found a Unicode arrow in bare CLI help that the default console encoding could not emit. PyPI publication did not run; the tag and slot remain as the correction record. |
| 0.49.0 | not published | The Windows prepublication gate found a capture-root initialization race. PyPI publication did not run; the failed tag and draft remain as the correction record. |
| 0.49.1 | not published | The Windows prepublication gate found that its release regression paused the portable unlink helper rather than the shared native/portable release boundary. PyPI publication did not run; the failed tag, slot, and draft remain as the correction record. |
| 0.49.2 | candidate; not published | Evidence-before-identity release correction under local review. This row may be marked published only after PyPI accepts the exact preflight artifacts. |

The 0.45.1 release body cannot be rewritten after immutable publication. Future
release finalization generates and verifies the complete release body before
the draft becomes public.
