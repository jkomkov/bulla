# Golden v0.5 candidate QA observation

Date: 2026-07-20

Classification: `INTERNAL_CAPTIVE_CONTROL`

Observed on the isolated candidate worktree:

- full Bulla regression: 12,794 passed, 34 skipped, 69 intentionally deselected;
- v0.4/v0.5 and Claim Flow targeted regression: 30 passed;
- Golden v0.5 standalone replay: valid under `python -I` and the AST zero-import audit;
- deterministic regeneration: 144 transfers, 1,000 matched scrambles, 96 frontier points, 52 F11 margins, and 72 F12 cases;
- Generalization Lean bundle: clean build, no `sorry`, and the new v0.5 theorems depend on no axioms;
- Glyph production build: passed and emitted `/status`, `/participate`, and `/review`;
- Res Agentica production build: passed and emitted `/status`;
- canonical status generator: 11 records current;
- formatting: `git diff --check` passed.

The first sandboxed full regression produced ten failures because the test
runner could not bind loopback HTTP sockets. The identical suite was rerun with
loopback permission and passed. This is an execution-environment distinction,
not a waived product failure.

External authors, adjudicators, implementations, integrations, and witnesses
remain zero. No external-evidence classification is permitted by this report.
