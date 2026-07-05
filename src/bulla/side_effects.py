"""Side-effect classification for MCP tool calls — the gateway's risk lens.

The gateway law is *no unreceipted side effects*: reads pass freely, anything
that changes the world outside the conversation gets a receipt (shadow mode)
or a gate (enforce mode). Classification consumes the MCP spec's
``ToolAnnotations`` where servers declare them and falls back to a
deliberately CONSERVATIVE default: **unknown means write.** A misclassified
read costs one redundant receipt; a misclassified write is an unreceipted
side effect — exactly the thing the gateway exists to prevent — so the
asymmetry points one way.

Classes (coarse, by blast radius):
    read    — no externally visible state change claimed
    notify  — emits a message/event to a third party (send, post, publish)
    write   — mutates state (create/update/delete/move)
    commit  — write flagged destructive or otherwise hard to reverse

Only ``read`` is exempt from receipts/gating; the other three are all
"side-effecting" and differ only in how a policy might weight them.
"""

from __future__ import annotations

READ = "read"
NOTIFY = "notify"
WRITE = "write"
COMMIT = "commit"

SIDE_EFFECTING = (NOTIFY, WRITE, COMMIT)

# Name-stem heuristics — applied ONLY when the server declares no annotations.
# Read-stems must match at a word boundary at the start of the bare tool name;
# anything not provably read-shaped falls through to WRITE (the conservative
# default), so this list can stay short and honest.
_READ_STEMS = (
    "get", "list", "read", "search", "query", "fetch", "describe",
    "show", "stat", "head", "browse", "view", "lookup", "count",
)
_NOTIFY_STEMS = ("send", "notify", "post", "publish", "email", "message", "alert")
_COMMIT_STEMS = ("delete", "remove", "destroy", "drop", "purge", "revoke", "kill")


def _bare(name: str) -> str:
    """The tool's own name, stripped of the proxy's `server__` namespace."""
    return name.rpartition("__")[2].lower()


def classify_tool(tool_def: dict) -> str:
    """Classify one MCP tool definition into read/notify/write/commit.

    Precedence: explicit MCP ``annotations`` (``readOnlyHint``,
    ``destructiveHint``) > name-stem heuristic > WRITE (unknown ⇒ write).
    """
    ann = tool_def.get("annotations") or {}
    if isinstance(ann, dict):
        if ann.get("readOnlyHint") is True:
            return READ
        if ann.get("destructiveHint") is True:
            return COMMIT
        if ann.get("readOnlyHint") is False:
            # declared side-effecting; refine with the name, floor at WRITE
            stem = _bare(tool_def.get("name", ""))
            if any(stem.startswith(s) for s in _COMMIT_STEMS):
                return COMMIT
            if any(stem.startswith(s) for s in _NOTIFY_STEMS):
                return NOTIFY
            return WRITE

    stem = _bare(tool_def.get("name", ""))
    if any(stem.startswith(s) for s in _COMMIT_STEMS):
        return COMMIT
    if any(stem.startswith(s) for s in _NOTIFY_STEMS):
        return NOTIFY
    if any(stem == s or stem.startswith(s + "_") for s in _READ_STEMS):
        return READ
    return WRITE  # unknown ⇒ write — the conservative default is the point


def is_side_effecting(effect_class: str) -> bool:
    return effect_class in SIDE_EFFECTING
