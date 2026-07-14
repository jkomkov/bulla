# Bulla Live MCP Proxy — Agent Safety Co-Pilot Demo

**Goal**: in 5 minutes, see an agent ask Bulla "is this composition
safe?" and get back a formally-verified answer with a Lean theorem hash
attached — *before* the composition silently fails.

This is the runtime sibling of the [two-manifest
quickstart](../two-manifest-quickstart/README.md). The quickstart
analyzes static YAML; this demo shows Bulla observing live MCP traffic
and answering the agent's safety questions in real time.

## What you'll see

The demo runs the same agent twice, against the same two MCP servers,
once **without** Bulla and once **with** Bulla as the MCP aggregator.

**Without Bulla**: the agent makes its tool calls. The third call
crosses a hidden encoding-convention seam between `fetch` and `memory`.
The agent doesn't notice — the JSON shapes match. The composition
produces wrong results downstream.

**With Bulla**: before the third call, the agent invokes
`bulla__should_proceed`. It receives `{verdict: "refuse", fee_after: 2,
n_blind_spots: 1}`. It calls `bulla__bridge` for repair advice, gets a
schema-level patch (the producer's `encoding` field is hidden, runtime
can't fix it). It calls `bulla__why` to verify the recommendation is
backed by a real theorem (Aristotle stamp `fdf8fb06...` ⇒
`sheaf_realization_characterization_via_cohomology`). It chooses to
surface the blocker to the operator instead of proceeding.

The **agent's choice** is the headline. Bulla never modified the
agent's traffic. It returned formally-verified information; the agent
decided what to do with it.

## Setup (30 seconds)

```bash
pip install bulla    # or pip install -e bulla/ from the repo root
```

## Get the system prompt for your agent

```bash
bulla proxy --inject-prompt
```

This prints the v1 system-prompt fragment (200 words). Paste it into
your agent's system prompt above your existing instructions. It tells
the agent when and how to call the `bulla__*` meta-tools.

## Quick smoke demo

```bash
PYTHON=python3.11 ./run_demo.sh
```

This pipes a canned JSON-RPC sequence (initialize, tools/list, then
four of the eight `bulla__*` meta-tools) through `bulla proxy` and
prints the responses. The two backends are minimal Python scripts in
this directory (`fake_fetch_backend.py`, `fake_memory_backend.py`)
that respond as MCP servers without needing npx or network.

**Note on scope**: this smoke demo verifies the proxy wires up
correctly — backends start, meta-tools dispatch, telemetry flushes,
Aristotle stamps return. The fake backends use an internal-test
metadata format that bypasses Bulla's auto-discovery, so the smoke
demo does NOT produce a positive fee.

**For the real prevention story, run the replay script**:

```bash
python bulla/examples/live-mcp-proxy/replay_real_manifests.py
```

This loads captured `tools/list` responses from
`@modelcontextprotocol/server-filesystem` and `server-github`
(in `bulla/examples/real_world_audit/manifests/`), runs them
through `BullaGuard.from_tools_list` (the same auto-discovery the
live proxy uses against real backends), and dispatches four of the
meta-tools. Expected output: `fee=22, blind_spots=27,
verdict=refuse` for a `filesystem.write_file` call — 13 schema-level
obstructions touching that single call. Real conventions on real
MCP server schemas.

See `bulla/spikes/auto_discovery/results.md` for the empirical study
of how well auto-discovery recovers documented ground-truth fees
across 7 real MCP server pairs (88% coverage on the canonical pair).

## Run the live proxy (15 seconds, manually piped JSON-RPC)

The Shannon-moment minimum: agents talk to Bulla over stdio JSON-RPC.
Real adoption uses an MCP client (Claude Code, Cursor, Continue,
etc.) configured to point at `bulla proxy` instead of directly at the
backend servers. For this demo we'll talk to Bulla manually.

### A. Configure two backend servers

`servers.yaml` (already in this directory):

```yaml
servers:
  fetch:
    command: "npx -y @modelcontextprotocol/server-fetch"
  memory:
    command: "npx -y @modelcontextprotocol/server-memory"
```

### B. Start the proxy

```bash
bulla proxy --config servers.yaml --telemetry-out events.jsonl
```

The proxy spawns both backends, fronts them as one logical MCP server,
and injects all eight `bulla__*` meta-tools into the aggregated
`tools/list` response. Per-call telemetry streams to `events.jsonl`.

### C. (Alternative) Zero-config

```bash
bulla proxy -- \
  "npx -y @modelcontextprotocol/server-fetch" \
  "npx -y @modelcontextprotocol/server-memory"
```

## Wire it to a real MCP client

If your agent framework supports custom MCP servers (Cursor, Claude
Code, Continue), point it at the proxy command instead of the
individual backends:

```json
{
  "mcpServers": {
    "bulla-proxied": {
      "command": "bulla",
      "args": ["proxy", "--config", "/abs/path/to/servers.yaml"]
    }
  }
}
```

The agent sees one logical server. Tools appear with namespaced names
(`fetch__get`, `memory__store`, `bulla__should_proceed`, ...). Your
agent's system prompt (from `--inject-prompt`) tells it when to
consult the `bulla__*` ones.

## What lives in `events.jsonl`

Per-call telemetry, one JSON object per line:

```jsonl
{"event":"backends_started","backends":["fetch","memory"],"n_tools":6,"starting_fee":0,"ts":...}
{"event":"meta_tool","tool":"bulla__should_proceed","args":{"server":"memory","tool":"store"},"latency_ms":3.1,"ts":...}
{"event":"tools/call","server":"fetch","tool":"get","fee_after":0,"n_blind_spots":0,"ts":...}
```

This file is the empirical evidence for whether agents actually
consult the meta-tools. Bulla's pivot from "static analyzer" to
"safety co-pilot agents query" is only true if the data confirms it.

## What's NOT in this demo

This is the OBSERVE-mode MVP: the proxy never modifies agent traffic.
The trust ladder graduates to ADVISE (responses are tuned to actively
recommend bridges) and AUTO (value-level bridges silently applied)
once telemetry validates that OBSERVE is correct and the latency
budget holds (p99 < 100 ms on `tools/call` overhead).

Schema-level bridges are NEVER auto-applied. They require a manifest
edit and a redeploy by a human operator. That's a correctness
boundary, not a UX choice.

## Verify the formal provenance

```bash
# In one shell, start the proxy:
bulla proxy --config servers.yaml

# In another, manually send a JSON-RPC `bulla__why` request via the
# MCP client of your choice. The response contains an Aristotle run
# hash (e.g., "fdf8fb06-aa2a-475a-82de-0f787b1fd5c1"). Verify it
# against the canonical list:
grep -n "$ARISTOTLE_RUN" \
  ../../papers/composition-doctrine/lean/CompositionDoctrine.lean
```

The hash matches a verified Lean 4 / Aristotle run. The theorem
backing the safety claim is `sheaf_realization_characterization_via_cohomology`
(uniqueness of witness rank on concrete cellular-sheaf data — Phase 5
of the realization story). No other MCP proxy can attach
formally-verified provenance to its safety claims.
