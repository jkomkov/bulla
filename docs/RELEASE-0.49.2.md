# Bulla 0.49.2 publication contract

## Scope

Bulla 0.49.2 is an evidence-before-identity release correction for the Doorstep
0.49 line. The failed 0.49.0 and 0.49.1 public slots, tags, and drafts remain
immutable correction evidence. Neither version was published to PyPI, reused,
or promoted.

The Windows loser/winner regression now pauses the existing
`_release_acquired_windows_root_claim` boundary. That boundary is used both by
the identity-bound native HANDLE release and by the platform-neutral fallback,
so the test proves that a contender cannot accept a complete root until the
actual owner-release operation finishes. The installed capture runtime remains
byte-identical to the exact public 0.49.1 `main` source. This release introduces
no new capture behavior or recovery path.

This release changes no ActionReceipt schema, canonicalization rule,
verification meaning, reliance policy, capture receipt semantics, or MCP server
interface. Package and receipt-format versions remain separate clocks. The
capture directory and initialization claim remain implementation-local and are
not portable protocol objects. An optional signature authenticates
only the local observer's statement; it does not prove execution, physical occurrence,
or result correctness.

## Evidence-before-identity sequence

1. Merge the reviewed public pull request. The exact green public `main` commit is the sole `source_commit`;
   PR head, synthetic merge commit, pre-rebase commit, or local build is
   insufficient.
2. Dispatch `release-preflight.yml` for version 0.49.2 and that exact current
   `main` commit. The workflow has read-only repository permissions and no
   release-signing, tag-creation, draft-creation, or PyPI authority.
3. Before any identity is minted, preflight must pass the supported Linux,
   macOS, and Windows/Python compatibility matrix, the complete standalone test
   inventory, two reproducible byte-identical builds, both archive gates, the
   isolated installed-wheel suite, installed Doorstep commands and CLI checks, exact verification-kit
   reconstruction, and two unchanged server lifecycles against one session root.
4. Preflight freezes one strict manifest binding the repository, workflow, run
   ID, version, source commit, source-tree digest, deterministic build epoch,
   exact compatibility matrix, both build records, test summaries, artifact
   inventory, sizes, and SHA-256 digests. The retained artifact contains exactly
   the reviewed wheel, sdist, verification kit and detached digest, three test
   summaries, and this manifest.
5. Only after the exact preflight succeeds may `prepare-release.yml` run. It
   authenticates the preflight workflow/run/commit and verifies the complete
   downloaded artifact against the frozen manifest before the signing key is
   exposed or a slot, tag, or draft is created.
6. The preparation workflow opens the signed public slot, creates the immutable
   package tag and draft, and dispatches `publish.yml` with both exact run IDs.
   Publication authenticates both runs, downloads the named preflight artifact,
   re-verifies its identity, hashes, archive inventory, and runtime parity, and
   stages it unchanged. It does not rebuild the wheel or sdist.
7. The sole OIDC-authorized job independently rechecks the frozen version,
   commit, run ID, distribution sizes, and SHA-256 digests, copies only the wheel
   and sdist into a fresh two-file directory, and submits those exact bytes to
   PyPI. PyPI publication consumes the version.
8. Post-publication verification compares PyPI's accepted bytes and provenance
   commit with the exact preflight artifact before receipt finalization.

The preflight artifact is evidence, not release identity. Uploading it to the
workflow run does not authorize or imply publication. A failed or expired
preflight creates no slot or tag and can be repeated for a later exact `main`
commit. Once preparation creates a public identity, that version is never
reused even if publication fails.

## Privacy and restart conditions

- Commitments and minimal metadata are retained by default.
- Exact request and response payloads require `--retain-payloads` and remain
  local to one private session directory.
- Existing valid session roots admit repeated and concurrent server lifecycles;
  malformed, symlinked, unexpectedly populated, or stranded-claim roots fail
  closed.
- Incomplete and uncheckable calls never produce an ordinary ActionReceipt.
- Session, root, and initialization-claim identifiers never enter portable
  receipt semantics.

This record does not authorize a separate MCP extension, server-authentication
claim, deployment, adoption claim, unrelated release, external-counter
increment, or settlement activity. It does not authorize preparation before an
exact successful preflight, publication before review, or reuse or promotion of
the failed 0.49.0 or 0.49.1 identities.
