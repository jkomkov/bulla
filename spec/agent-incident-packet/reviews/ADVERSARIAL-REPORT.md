# Agent Incident Packet v0.1 adversarial and pilot report

## Evidence under review

- Deterministic HTTP packet with `2/2` decision coverage and `1/2` effect
  coverage.
- Deterministic MCP packet with `2/2` decision coverage and `1/2` effect
  coverage.
- Python standard-library checker with a separately implemented Ed25519
  verifier.
- Node built-in-crypto checker with a separately implemented strict JSON
  parser.
- Live localhost HTTP and MCP permit/refuse/bypass pilots.

The clean checkers must deep-equal the generated expected-verdict ledger.
Mutation tests require exit `2` for malformed or unsafe input and exit `1` for
integrity, proof, role, lineage, artifact, coverage-report, correction, or
required witness-proof failures. Coverage gaps, party conflicts, unavailable
controlled artifacts, and untrusted optional evidence remain explicit
completed results.

## Hostile review results

The first pass found:

- Pilot output paths followed pre-existing symlinks.
- Pilot coverage accepted ID-only and duplicate projections.
- The MCP signing seed appeared in process arguments and the subprocess
  inherited ambient environment variables.
- Pilot request parsing accepted duplicate JSON and unbounded stdio lines.
- Kernel exit codes omitted failed coverage, witness, and correction
  dimensions.
- Boolean values could compare equal to integer fields.
- An unsigned witness list was initially presented too strongly.
- Receipt roles were not fully bound to packet-core roles.
- Duplicate coverage projections overwrote one another.
- Observation evidence hashes were not projected independently from effect
  hashes.
- Timeline attestations did not commit to exact receipt file bytes.
- Standalone checkers initially omitted closed action-field semantics, handoff
  commitments, correction-kind resolution, and cross-role context collisions.
- `counterparty_confirmed` statements did not initially require an
  `affected_party` issuer.
- Correction forks were keyed without their correction kind, and packet
  corrections could resolve against the wrapper core without a released
  packet-core artifact.
- Generated report sidecars were initially placed inside strict public packet
  roots as undeclared files.
- A third duplicate projection could re-enter a pilot coverage candidate set.
- A publisher could initially shorten a denominator and erase a bypass by
  repackaging the packet around the shorter bytes.
- A publisher could initially remove an entire effect phase while retaining a
  decision denominator.
- Downstream receipts initially resolved a mandate by hash without requiring
  exact run, policy, validity-window, parent, and accepted-receipt bindings.
- Handoff statement and parent references initially used set/subset checks
  that did not preserve exact order or completeness.
- A redaction reviewer statement initially named only a topic and did not bind
  an exact released redaction record.
- Actor-authenticated time needed an explicit hostile test proving that it
  cannot populate witness or external-anchor labels.
- HTTP pilot helpers initially accepted an arbitrary URL at the lowest request
  layer.

The denominator attack is now blocked against a publisher acting alone. Each
denominator requires an earlier accepted role-constrained checkpoint statement
whose subject and top-level evidence bind only the exact denominator artifact
hash. Each represented protocol also requires exactly one decision and one
effect denominator. The executable regressions are
`test_publisher_alone_cannot_republish_a_self_shortened_denominator` and
`test_protocol_requires_one_decision_and_one_effect_anchor`.

The remaining limitation is narrower and explicit: an accepted target,
gateway, or boundary observer can sign a shortened denominator. The checkpoint
authenticates what that observer reported; it does not establish completeness
or organizational independence. Both deterministic packets therefore retain
`PATH_SEPARATE_TEAM_CONTROLLED`, and the external evidence counters remain
`A0/J0/I0/W0 · r0`.

Corrections are recorded in `review-ledger.json`. Findings remain open or
`awaiting_recheck` until the same hostile reviewer and a cross-lane reviewer
complete two clean passes against the unchanged invariant set.

The public-archive scanner exercises nested credential-like keys and values,
exception and traceback leakage, personal-data sentinels, user-specific host
paths, and a fixed dictionary of low-entropy secret digests. It remains a
heuristic regression aid. It does not compute disclosure safety.

## Required commands

```text
PYTHONPATH=src .venv/bin/python spec/agent-incident-packet/generate_vectors.py --check
.venv/bin/pytest -q tests/test_agent_incident_packet_adversarial.py
.venv/bin/pytest -q tests/test_event_coverage.py tests/test_receipt_witness.py
python3 -I spec/agent-incident-packet/verify_packet.py spec/agent-incident-packet/vectors/http-clean --context spec/agent-incident-packet/contexts/http-context.json
node spec/agent-incident-packet/verify_packet.mjs spec/agent-incident-packet/vectors/http-clean --context spec/agent-incident-packet/contexts/http-context.json
```

Repeat the standalone commands for `mcp-clean` with `mcp-context.json`.
