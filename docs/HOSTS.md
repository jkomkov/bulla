# MCP Hosts

Bulla auto-detects MCP server configurations from each registered host.
Adding a new host is one new module under `src/bulla/hosts/` and an
import line in `_load_builtin_hosts()`.

## Built-in hosts

| Name              | Display          | macOS                                                              | Linux                                                  | Windows                                  | Format quirk                |
|-------------------|------------------|--------------------------------------------------------------------|--------------------------------------------------------|------------------------------------------|------------------------------|
| `cursor`          | Cursor           | `~/.cursor/mcp.json`, `.cursor/mcp.json` (workspace)               | same                                                   | same                                     | top-level `mcpServers`       |
| `claude-desktop`  | Claude Desktop   | `~/Library/Application Support/Claude/claude_desktop_config.json`  | `~/.config/Claude/claude_desktop_config.json`          | `%APPDATA%/Claude/...`                   | top-level `mcpServers`       |
| `claude-code`     | Claude Code      | `~/.claude/settings.json`, `.claude/settings.json` (workspace)     | same                                                   | same                                     | top-level `mcpServers` or `mcp.servers` |
| `cline`           | Cline            | `<editor>/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` (per-OS)   | same                                                   | same                                     | top-level `mcpServers`; **5 editor forks scanned** (see below) |
| `windsurf`        | Windsurf         | `~/.codeium/windsurf/mcp_config.json`                              | same                                                   | same                                     | top-level `mcpServers`       |
| `zed`             | Zed              | `~/.config/zed/settings.json`                                      | same                                                   | `%APPDATA%/Zed/settings.json`            | nested at `context_servers`  |
| `codex`           | OpenAI Codex     | `~/.codex/config.toml`, `.codex/config.toml` (workspace, trusted)  | same                                                   | same                                     | **TOML** with `[mcp_servers.<name>]` tables |

## CLI

```bash
bulla hosts list           # show all registered hosts and detected configs
bulla audit                # auto-detect first match, audit
bulla audit --host cline   # force a specific host
bulla audit cfg.json       # explicit path (any host)
bulla audit --host zed cfg.json   # force parser even with explicit path
```

## Adding a new host

Drop a new module under `src/bulla/hosts/<name>.py`:

```python
from bulla.hosts import default_parse, register
from bulla.config import McpServerEntry
from collections.abc import Iterator
from pathlib import Path

class MyHost:
    name = "my-host"
    display_name = "My Host"

    def candidate_paths(self) -> Iterator[Path]:
        yield Path.home() / ".myhost" / "mcp.json"

    def parse(self, path: Path) -> list[McpServerEntry]:
        return default_parse(path)  # or default_parse(path, servers_key="...") for nested

register(MyHost())
```

Then add an import line to `_load_builtin_hosts()` in `src/bulla/hosts/__init__.py`,
add a fixture under `tests/fixtures/hosts/<name>.json`, and add a parse
test in `tests/test_hosts.py`.

## Format quirks

`default_parse()` accepts a `servers_key` argument so hosts whose
servers live under a non-standard top-level key can still delegate to
the shared parser. Four shapes are auto-detected:

- **JSON** with top-level `mcpServers` — Cursor, Claude Desktop, Cline, Windsurf
- **JSON** with top-level `context_servers` — Zed (`servers_key="context_servers"`)
- **JSON** with nested `mcp.servers` — fallback for hosts that wrap MCP under a
  parent config namespace
- **TOML** with `[mcp_servers.<name>]` tables — OpenAI Codex (snake_case, custom
  parser in `hosts/codex.py`)

For genuinely divergent shapes, write a custom `parse()` instead of
delegating. The auto-detection signal-check in `hosts/__init__.py`
dispatches on file extension (`.toml` → `tomllib`, anything else → JSON).

## Debugging detection — `bulla hosts list -v`

Verbose mode prints every candidate path scanned, whether the file
existed, and the reason it did or did not match. Useful when a config
is present but not being detected — most often a path-format quirk
(wrong case on macOS, OneDrive-redirected `%APPDATA%` on Windows, a
remote-mounted home directory, or an unfamiliar VS Code fork):

```bash
bulla hosts list -v
bulla hosts list --host cline -v   # focus on one host
```

Output shows ✓ for matched paths, · for files that exist but didn't
parse / lacked a recognized servers key, and `-` for paths that don't
exist on this system.

## Cline editor-fork coverage

Cline ships as a single VS Code extension (``saoudrizwan.claude-dev``)
that installs cleanly into every VS Code-compatible editor. The host
adapter scans the same extension path under each known fork's data
directory:

| Editor                | macOS / Linux dir name | Windows dir name |
|-----------------------|------------------------|------------------|
| Visual Studio Code    | `Code`                 | `Code`           |
| VS Code Insiders      | `Code - Insiders`      | `Code - Insiders`|
| Cursor (VS Code fork) | `Cursor`               | `Cursor`         |
| Windsurf (Codeium)    | `Windsurf`             | `Windsurf`       |
| VSCodium              | `VSCodium`             | `VSCodium`       |

A user with Cline installed in two editors (e.g. VS Code + Cursor) gets
two `cline` matches from `bulla hosts list` and `detect_all()`. To pin
one, pass `bulla audit --host cline --config <path>`.

Note: Cursor's *own* MCP configs (`~/.cursor/mcp.json`) are detected by
the separate `cursor` host. Cline-running-inside-Cursor and Cursor-itself
are independent files; both can coexist on disk.

**Sources** (cross-checked against upstream):
- [Cline repo](https://github.com/cline/cline) — extension ID `saoudrizwan.claude-dev`
- [VS Code MCP servers documentation](https://code.visualstudio.com/docs/copilot/customization/mcp-servers) — globalStorage path template

## Authority — what each host actually owns

The same machine can have *both* `cursor` and `cline` configs, and Cline
can run inside Cursor. The two are independent — different files,
different writers, different audit scope. Use this table when there's
ambiguity about which host's config is authoritative for a given audit:

| Scenario                              | Authoritative host | Path                                                                                                |
|---------------------------------------|--------------------|------------------------------------------------------------------------------------------------------|
| MCP servers configured in Cursor IDE  | `cursor`           | `~/.cursor/mcp.json`                                                                                 |
| Cline extension running inside Cursor | `cline`            | `~/Library/Application Support/Cursor/User/globalStorage/saoudrizwan.claude-dev/.../cline_mcp_settings.json` |
| Cline extension running inside VS Code| `cline`            | `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/.../cline_mcp_settings.json`   |
| Both above on the same machine        | `cursor` + `cline` (twice)| Both detected; `bulla audit --host` pins one                                              |

The two are separate Bulla detections by design: `cursor` audits the
servers Cursor itself launches; `cline` audits the servers the Cline
extension launches, regardless of which editor it's running inside. A
multi-install setup will surface as multiple distinct detections in
`bulla hosts list` and `detect_all()`.

## Remote/devcontainer caveat

If you run Cline (or any host) inside a devcontainer, SSH remote, WSL
session, or Docker dev environment, the MCP config file lives on the
*remote* machine, not the host running `bulla`. Bulla scans the local
filesystem only. Two workarounds:

1. Run `bulla` inside the same remote/container session, or
2. Pass an explicit path: `bulla audit --host cline --config /mnt/remote/.../cline_mcp_settings.json`

Auto-detection is filesystem-local; this won't change without a
`bulla audit --remote ssh://host/...` mode that's intentionally
out of scope for now.

## Stdio only (today)

`default_parse()` skips servers that declare `type: "http"`,
`"streamableHttp"`, `"sse"`, or include a top-level `"url"` field.
HTTP/SSE transport support is planned for a follow-up sprint and will
extend the parser without changing this protocol.
