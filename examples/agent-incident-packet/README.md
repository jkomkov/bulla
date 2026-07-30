# Agent Incident Packet localhost pilots

These source-only pilots exercise fixed process boundaries and produce runtime
evidence for `glyph.agent-incident-packet/0.1-draft`. They are team-operated
experiments, not production gateways, external validation, or published Bulla
CLI commands.

Run the HTTP pilot:

```text
PYTHONPATH=src python3 examples/agent-incident-packet/run_http_pilot.py --out /tmp/bulla-http-pilot
```

Run the MCP pilot:

```text
PYTHONPATH=src python3 examples/agent-incident-packet/run_mcp_pilot.py --out /tmp/bulla-mcp-pilot
```

Each pilot creates one permitted mediated effect, one pre-dispatch refusal, and
one direct bypass to the same target or backend. The boundary-ingress decision
coverage is `2/2`. The target-side effect coverage is `1/2`. The direct bypass
is the uncovered effect.

The output directory contains the runtime material, an assembled packet under
`packet/`, the out-of-band verification policy in `context.json`, and the
multidimensional result in `verification-report.json`. Verify the assembled
packet with either source-only standalone checker:

```text
python3 -I spec/agent-incident-packet/verify_packet.py /tmp/bulla-http-pilot/packet --context /tmp/bulla-http-pilot/context.json
node spec/agent-incident-packet/verify_packet.mjs /tmp/bulla-http-pilot/packet --context /tmp/bulla-http-pilot/context.json
```

The denominator is path-separate from the receipting boundary but remains under
the same project control. Its provenance label is
`PATH_SEPARATE_TEAM_CONTROLLED`.
