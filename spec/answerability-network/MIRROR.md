# Public source mirror

This directory mirrors the source-only Answerability Network profile merged in
`jkomkov/res-agentica` at commit
`e03bce5a18bf9d4a3dc326cc5938421ccd58305d`.

Apart from this provenance note, the profile, checkers, fixtures, reports,
projection data, benchmark, and manifest are byte-identical to
`bulla/spec/answerability-network/` at that commit. The Python implementation
and finite formal project are copied from the same reviewed source.

The public-repository test changes only its repository-root lookup. Distribution
policy excludes the profile, implementation, formal project, and tests from the
wheel and sdist. This mirror does not publish a package or promote the profile.
