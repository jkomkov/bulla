# Bulla 0.49.0 local release-candidate contract

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

## Candidate status

This document authorizes local preparation only. The candidate is not a PyPI
release, GitHub release, deployment, MCP extension, server-authentication claim,
or adoption result. Publication requires a separately authorized exact-green
public default-branch release ceremony. PyPI publication consumes the version.
