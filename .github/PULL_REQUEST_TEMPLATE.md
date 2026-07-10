<!-- Thanks for contributing to bulla. Keep this short; delete what doesn't apply. -->

## What & why

<!-- One or two sentences: what changes, and the problem it solves. -->

## Checklist

- [ ] Tests pass locally: `pip install -e ".[test]" && pytest tests/`
- [ ] Lint/type clean: `ruff check src/bulla` and `mypy` on the core allowlist
- [ ] I signed off my commits (DCO): `git commit -s` (see `CONTRIBUTING.md`)

## Integrity-critical surfaces (delete if not touched)

If this touches canonicalization/hashing (`bulla._canonical`, `model`, `certificate`,
`action_receipt`), signing (`identity`), the transparency log (`registry`), or the
wire spec (`spec/`, `WITNESS-CONTRACT.md`):

- [ ] Old artifacts still verify, or a version bump (`CANON_VERSION` /
      `ALGORITHM_VERSION`) + migration is included and explained.
- [ ] The change is reproducible from the spec by an independent implementation
      (the stdlib verifier in `spec/vectors/independent_check.py` still agrees).
- [ ] Rationale recorded (CHANGELOG entry and/or `WITNESS-CONTRACT.md`).
