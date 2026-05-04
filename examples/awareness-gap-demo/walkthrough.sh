#!/usr/bin/env bash
# Awareness-gap walkthrough — three acts.
#
# Run this from inside `bulla/`:
#
#     bash examples/awareness-gap-demo/walkthrough.sh
#
# It assumes:
#   - python3.11 on PATH
#   - npx on PATH (for Act 2; the npm-installed MCP servers download
#     on first invocation, ~5-10 s cold-start)
#
# Each act is a discrete demo with its own pause; the script asks
# you to press Enter between acts so the prose has room to breathe.

set -e
cd "$(dirname "$0")/../.."  # run from bulla/

PYTHON="${PYTHON:-python3.11}"
PYTHONPATH="${PWD}/src"
export PYTHONPATH

bold()    { printf "\033[1m%s\033[0m\n" "$1"; }
dim()     { printf "\033[2m%s\033[0m\n" "$1"; }
hr()      { printf "\033[2m%s\033[0m\n" "────────────────────────────────────────────────────────────"; }
pause()   {
    if [[ -t 0 ]]; then
        printf "\n\033[2m  press Enter to continue...\033[0m"
        read -r _
        printf "\n"
    else
        echo
    fi
}

clear || true
bold "bulla — awareness-gap walkthrough"
hr
echo
echo "  Three acts. The same machinery runs in all three; only the"
echo "  source of the MCP tool descriptions changes."
echo
echo "    Act 1.  Canned filesystem + GitHub manifests (deterministic,"
echo "            no network)."
echo "    Act 2.  Live filesystem MCP server, spawned via npx."
echo "    Act 3.  Your machine — auto-detect via Cursor / Claude Code"
echo "            / Claude Desktop config files."
echo
pause


# ── Act 1 ──────────────────────────────────────────────────────────

clear || true
bold "Act 1 — canned demo"
hr
echo
echo "  examples/awareness-gap-demo/repro.py runs the same pipeline that"
echo "  bulla scan runs on real configs, but against canned manifests so"
echo "  the failure-then-fix arc is reproducible without any network."
echo
echo "  Three steps:"
echo
echo "    1. Simulated GitHub create-file rejects an absolute filesystem"
echo "       path with a 422 ('repository-relative path expected')."
echo "    2. bulla diagnoses the path_convention seam."
echo "    3. bulla.translate('path_convention', ...) normalizes the path,"
echo "       and the simulated GitHub call accepts."
echo
pause

"$PYTHON" examples/awareness-gap-demo/repro.py
echo
hr
pause


# ── Act 2 ──────────────────────────────────────────────────────────

clear || true
bold "Act 2 — live filesystem MCP server"
hr
echo
echo "  A single server has no cross-server seams — fee is always 0."
echo "  The point of this act is that bulla scan launches a real MCP"
echo "  subprocess, queries its tools/list over stdio, and produces the"
echo "  same narrative output. First run takes ~10 s while npx downloads"
echo "  the package."
echo
dim "  command:"
dim "    bulla scan \"npx -y @modelcontextprotocol/server-filesystem /tmp\""
echo
pause

"$PYTHON" -m bulla scan "npx -y @modelcontextprotocol/server-filesystem /tmp" || true
echo
hr
pause


# ── Act 3 ──────────────────────────────────────────────────────────

clear || true
bold "Act 3 — your machine"
hr
echo
echo "  bulla scan with no arguments auto-detects the host config from"
echo "  Cursor, Claude Code, Claude Desktop, Cline, Windsurf, Zed, or"
echo "  Codex. When MCP servers are configured, the same narrative"
echo "  diagnostic runs against your actual tool set."
echo
echo "  When no MCP servers are configured anywhere, the error message"
echo "  is structured: which host configs were found, which were"
echo "  recognized, and the next-step command to run."
echo
dim "  command:"
dim "    bulla scan"
echo
pause

"$PYTHON" -m bulla scan || true
echo
hr
echo
bold "End of walkthrough."
echo
echo "  Code:    https://github.com/jkomkov/res-agentica/tree/main/bulla"
echo "  Install: pip install bulla"
echo "  Source:  bulla/src/bulla/{scan_format,explanations,bridges}.py"
echo
