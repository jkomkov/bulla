# Public source mirror

This directory mirrors the source-only Witness Covenant profile merged in
`jkomkov/res-agentica` at commit
`25c03bee9b86574de2668246e83505d00554b588`.

Apart from this provenance note, the profile, checkers, fixtures, reports, and
manifest are byte-identical to `bulla/spec/witness-covenant/` at that commit.
The Python implementation and finite formal project are copied from the same
reviewed source.

The public-repository test changes only its repository-root lookup. Distribution
policy excludes the profile, implementation, formal project, and tests from the
wheel and sdist. This mirror does not publish a package or promote the profile.
