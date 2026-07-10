# Contributing to Bulla

Bulla's diagnostic coverage depends on its vocabulary — the set of convention dimensions it can recognize. The most valuable contribution is discovering and submitting new dimensions.

## Contributing a Dimension

Convention dimensions live in [`src/bulla/packs/community.yaml`](src/bulla/packs/community.yaml). To add a new dimension, open a pull request that adds an entry to this file.

### Required Fields

```yaml
dimensions:
  your_dimension_name:
    description: "One sentence: what semantic convention does this capture?"
    known_values:
      - "value_a"
      - "value_b"
    field_patterns:
      - "*_suffix"          # glob-style, compiled to regex boundary match
    description_keywords:
      - "phrase from tool descriptions that signals this convention"
```

Every dimension **must** have:

- `description` — a clear, concise explanation of what the convention is
- `known_values` — at least two distinct values that tools could disagree on
- At least one of `field_patterns` or `description_keywords` (ideally both)

### Quality Bar

A dimension must describe a **semantic convention that two tools could silently disagree on**. Not every parameter is a convention. Ask:

1. Can two tools use the same field name but mean different things? (e.g., `path` meaning absolute vs. repository-relative)
2. Would this disagreement cause a silent failure? (schema validation passes, but the pipeline produces wrong results)
3. Is this cross-server? Would this convention appear in tools from different vendors?

If the answer to all three is yes, it's a good dimension.

### Pattern Guidelines

Field patterns are compiled to boundary-anchored regex: `*_direction` matches `sort_direction` but not `direction_vector`. Keep patterns tight:

- Prefer `*_suffix` patterns over bare words
- Test against real tool schemas before submitting
- If a pattern matches >50% false positives, it's too broad

Validate your changes:

```bash
bulla pack validate src/bulla/packs/community.yaml
```

### Optional: Provenance Metadata

If you discovered the dimension using `bulla discover` or observed it in real server compositions, add provenance:

```yaml
  your_dimension:
    # ... required fields ...
    provenance:
      discovered_by: [your-github-handle]
      server_affinity: [server_a, server_b]
      discovery_method: "bulla discover"  # or "manual"
      independent_discoveries: 1
```

The `independent_discoveries` counter tracks how many people have independently found the same dimension. If your PR adds a dimension that already exists, increment this counter instead of adding a duplicate.

### The `refines` Field

If your dimension is a more specific version of an existing one, use `refines`:

```yaml
  github_path_convention:
    refines: path_convention
    # ... rest of definition
```

The classifier's deduplication logic keeps the most specific child when both parent and child match the same field. This is how domain-specific dimensions extend the base vocabulary without creating noise.

## Other Contributions

**Bug reports and feature requests** — open an issue at [github.com/jkomkov/bulla/issues](https://github.com/jkomkov/bulla/issues).

**Code contributions** — the kernel (measurement, witness, SDK) is stable and intentionally minimal. If you're proposing a code change, open an issue first to discuss scope.

## Merge Policy for `src/bulla`

`src/bulla` on `main` is authoritative. Research branches sometimes carry their
own copies of kernel modules (in particular `registry.py` predating the
Pin-the-Root and inclusion-leaf-binding hardening). When such a branch merges,
those files surface as add/add or content conflicts — **always resolve toward
`main`'s copy**, then rebase the research work on top. Never resolve a
`src/bulla` conflict toward a research copy: the read-side trust semantics
(host-asserted roots refused, borrowed inclusion refused) would silently
regress. `tests/test_registry_merge_guard.py` exists to make that mistake fail
loudly at collection time; if it fails after a merge, the merge took the wrong
side.

## License and sign-off (DCO)

By contributing, you agree that your contributions are licensed under the
project's [Apache License 2.0](LICENSE) — inbound equals outbound (Apache-2.0
§5). There is no CLA. Instead we use the lightweight
[Developer Certificate of Origin](https://developercertificate.org/): add a
`Signed-off-by` line to every commit with **`git commit -s`**, certifying you
wrote the change (or otherwise have the right to submit it) under Apache-2.0.
