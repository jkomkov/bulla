# Bulla 0.49.1 publication contract

## Scope

Bulla 0.49.1 is a bounded portability correction to the Doorstep MCP capture
commands introduced by the 0.49.0 candidate. The failed 0.49.0 tag and draft
release remain immutable correction evidence and are not reused or promoted.
0.49.0 was not published to PyPI.

On Windows, first publication of a capture session root uses one adjacent,
exclusive local initialization claim. A contender waits at most five seconds,
polling every 25 milliseconds. It accepts a valid root only after the claim has
disappeared and the completed root validates exactly. A claim that remains for
the full window fails closed even when the root already appears complete; a
contender never removes, replaces, or steals another initializer's claim. The
owner reports successful initialization only after removing its exact-token
claim. On Windows the claimant holds one no-sharing native handle from
`CREATE_NEW` through initialization, marks that same handle delete-pending, and
then closes it. No pathname read/unlink decision can delete a different claim.
A failed delete-pending transition or close raises an initialization error and
never reports success; after a successful close a later initializer may acquire
the deterministic name without being mistaken for the prior owner.

Every managed root, `sessions` member, individual session, and capture parent
is inspected with no-follow metadata and rejected when Windows marks it as a
reparse point. Native Windows directory handles deny delete sharing and remain
open during initialization and across capture writes so a junction or rename
cannot substitute a validated managed directory.

The installed receipt-listing regression recognizes absolute paths with the
host platform's `Path.is_absolute()` rule rather than a POSIX-only leading
slash. Capture status output remains ASCII-only and safe under a CP1252 Windows
console.

This release changes no ActionReceipt schema, canonicalization rule,
verification meaning, reliance policy, capture receipt semantics, or MCP server
interface. Package and receipt-format versions remain separate clocks. The
capture directory and initialization claim are implementation-local; neither is
a portable protocol object. An optional signature authenticates
only the local observer's statement; it does not prove execution, physical
occurrence, or result correctness.

## Privacy and restart conditions

- Commitments and minimal metadata are retained by default.
- Exact request and response payloads require `--retain-payloads` and remain
  local to one private session directory.
- Existing valid session roots admit repeated and concurrent server
  lifecycles; malformed, symlinked, unexpectedly populated, or stranded-claim
  roots fail closed.
- Incomplete and uncheckable calls never produce an ordinary ActionReceipt.
- Session, root, and initialization-claim identifiers never enter portable
  receipt semantics.

## Publication sequence

1. Merge the reviewed public Bulla pull request.
2. Run the signed default-branch preparation workflow for the exact green `main`
   commit. The exact green public `main` commit is the sole `source_commit`; a PR
   head, synthetic merge commit, pre-rebase commit, or local build is insufficient.
3. Publish through the trusted PyPI workflow. PyPI publication consumes the version.
4. Verify PyPI's accepted wheel, sdist, provenance, installed Doorstep commands,
   archive membership, and unchanged ActionReceipt v0.2 verification kit against
   the reviewed candidate.

This record does not authorize a separate MCP extension, server-authentication
claim, deployment, adoption claim, unrelated release, external-counter
increment, or settlement activity. It also does not authorize publication
before review or reuse or promotion of the failed 0.49.0 candidate.
