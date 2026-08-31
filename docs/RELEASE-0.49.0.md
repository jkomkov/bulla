# Bulla 0.49.0 publication contract

## Scope

Bulla 0.49.0 adds a transparent, restart-safe local capture edge for existing
stdio MCP servers. Complete observed `tools/call` request/response pairs leave
an existing opt-in ActionReceipt v0.4. The release changes no ActionReceipt
schema, canonicalization rule, verification meaning, reliance policy, or MCP
server interface. Package and receipt-format versions remain separate clocks.

The capture directory is implementation-local. Portable receipt verification
does not make that directory a wire profile or authenticate the MCP server.
Unsigned observations reach the digest rung. A signed observation authenticates
only the local observer's statement; it does not prove execution, physical
occurrence, or result correctness.

## Privacy and restart conditions

- Commitments and minimal metadata are retained by default.
- Exact request and response payloads require `--retain-payloads` and remain
  local to one private session directory.
- Existing valid session roots admit repeated and concurrent server
  lifecycles; malformed, symlinked, or unexpectedly populated roots fail
  closed.
- Incomplete and uncheckable calls never produce an ordinary ActionReceipt.
- Session and root identifiers never enter portable receipt semantics.

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
claim, deployment, adoption claim, unrelated release, external-counter increment,
or settlement activity.
