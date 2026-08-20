# Public source mirror

This directory mirrors the source-only Reliance Map profile merged in
`jkomkov/res-agentica` at commit
`322bc29aec2ccb5dd506162bccf3ef89ad1f5243`.

The profile, schemas, Python and Node checkers, generated corpus, expected
report, reviews, and manifest are byte-identical to the files under
`bulla/spec/reliance-map/` at that commit. The Python implementation and its
Handoff Admission parsing dependency are likewise copied without modification.

Public-repository adaptations are limited to:

- the repository-root paths in `tests/test_reliance_map.py`;
- the standalone formal project under `formal/`, containing the exact
  `RelianceMap.lean` and `HandoffAdmission.lean` sources; and
- README and distribution-policy entries that keep every profile member out of
  the wheel, sdist, stable exports, and installed CLI.

The mirror does not promote the profile, publish a package, or increment an
external implementation or replay counter.
