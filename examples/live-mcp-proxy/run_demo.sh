#!/usr/bin/env bash
# Bulla live MCP proxy — manual JSON-RPC demo.
#
# Sends a fixed sequence of JSON-RPC requests to `bulla proxy` over
# stdio and prints the responses. Useful for sanity-checking the
# proxy without standing up a real MCP client.
#
# For the real adoption pattern, point your MCP client (Claude Code,
# Cursor, Continue, etc.) at the proxy command. See README.md.

set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEMETRY_FILE="${HERE}/events.jsonl"

# Use the in-tree fake backends so the demo runs without network
FAKE_FETCH="${HERE}/fake_fetch_backend.py"
FAKE_MEMORY="${HERE}/fake_memory_backend.py"

rm -f "$TELEMETRY_FILE"

echo "─── Bulla live MCP proxy demo ───"
echo "  Backends:  fake fetch + fake memory"
echo "  Telemetry: $TELEMETRY_FILE"
echo

REQUESTS='
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"bulla__fee","arguments":{}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"bulla__should_proceed","arguments":{"server":"fake_memory_backend","tool":"store","arguments":{"content":"x","encoding":"utf-8"}}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"bulla__bridge","arguments":{"server":"fake_memory_backend","tool":"store"}}}
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"bulla__why","arguments":{"about":"should_proceed"}}}
'

BULLA="${BULLA:-bulla}"
echo "$REQUESTS" | "$BULLA" proxy \
    --telemetry-out "$TELEMETRY_FILE" \
    -- \
    "${PYTHON:-python3} $FAKE_FETCH" \
    "${PYTHON:-python3} $FAKE_MEMORY" \
    | "${PYTHON:-python3}" -c "
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except Exception:
        print(f'!! {line}')
        continue
    if 'result' in m:
        result = m['result']
        if 'content' in result and result.get('content'):
            txt = result['content'][0].get('text', '')
            try:
                inner = json.loads(txt)
                print(f\"#{m['id']:>3}  {json.dumps(inner, indent=2)[:600]}\")
                continue
            except Exception:
                pass
        elif 'tools' in result:
            names = [t['name'] for t in result['tools']]
            print(f\"#{m['id']:>3}  tools/list: {names}\")
            continue
        print(f\"#{m['id']:>3}  {json.dumps(result)[:200]}\")
    else:
        print(f\"#{m.get('id','?'):>3}  {m}\")
"

echo
echo "─── Telemetry tail ───"
tail -10 "$TELEMETRY_FILE"
