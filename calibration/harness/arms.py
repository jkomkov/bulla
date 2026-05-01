"""Treatment arm definitions for the agent confusion experiment.

Three arms:
  A. CYCLIC — the filesystem read-triple (betti_1=1, fee=2)
  B. ACYCLIC — three disjoint-domain tools (betti_1=0, fee=0)
  C. DISAMBIGUATED — same overlap as A but hidden conventions made explicit
     (tests whether making conventions visible eliminates confusion)

Design principle: arms A and C are schema-equivalent in tool count and
parameter overlap. The ONLY difference is whether the hidden convention
(which tool to use for which content type) is expressed in the description.
If arm C error rate drops to arm B levels, the causal factor is specifically
the hidden convention, not the tool count or domain overlap.
"""

from __future__ import annotations

# ─── ARM A: CYCLIC (real filesystem tools, verbatim from MCP manifest) ───────
#
# These are the actual tool schemas from @modelcontextprotocol/server-filesystem.
# The hidden conventions:
#   1. read_file is deprecated (mentioned in description but not in schema)
#   2. read_text_file handles text encodings specifically
#   3. read_media_file returns a DIFFERENT output format (array of typed objects)
#   4. All three accept the same `path: string` input
#
# An agent must infer from context which tool to use. The schema alone
# does not disambiguate for tasks like "read config.yaml" vs "read logo.png".

CYCLIC_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read the complete contents of a file as text. "
            "DEPRECATED: Use read_text_file instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "tail": {
                    "description": "If provided, returns only the last N lines of the file",
                    "type": "number",
                },
                "head": {
                    "description": "If provided, returns only the first N lines of the file",
                    "type": "number",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_text_file",
        "description": (
            "Read the complete contents of a file from the file system as text. "
            "Handles various text encodings and provides detailed error messages "
            "if the file cannot be read. Use this tool when you need to examine "
            "the contents of a single file. Use the 'head' parameter to read only "
            "the first N lines of a file, or the 'tail' parameter to read only "
            "the last N lines of a file. Operates on the file as text regardless "
            "of extension. Only works within allowed directories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "tail": {
                    "description": "If provided, returns only the last N lines of the file",
                    "type": "number",
                },
                "head": {
                    "description": "If provided, returns only the first N lines of the file",
                    "type": "number",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_media_file",
        "description": (
            "Read an image or audio file. Returns the base64 encoded data and "
            "MIME type. Only works within allowed directories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
]


# ─── ARM B: ACYCLIC CONTROL ──────────────────────────────────────────────────
#
# Three tools from completely disjoint domains. No parameter overlap,
# no hidden convention cycle. Same tool count as arm A.
# Tasks for this arm are unambiguous: each task clearly maps to exactly one tool.

ACYCLIC_TOOLS = [
    {
        "name": "search_web",
        "description": (
            "Search the web for current information on a topic. "
            "Returns a list of relevant results with titles and snippets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "number",
                    "description": "Maximum number of results to return",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Create a new event on the user's calendar. "
            "Requires a title and start time. Optionally accepts "
            "duration and description."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start_time": {
                    "type": "string",
                    "description": "ISO 8601 datetime",
                },
                "duration_minutes": {"type": "number"},
                "description": {"type": "string"},
            },
            "required": ["title", "start_time"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Send an email to a specified recipient. "
            "Requires recipient address, subject, and body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


# ─── ARM C: DISAMBIGUATED (same overlap as A, but conventions made visible) ──
#
# These tools cover the SAME domain as arm A (reading files) with the SAME
# parameter overlap (all accept path: string). The difference: the descriptions
# explicitly state which tool to use for which content type, eliminating the
# hidden convention.
#
# If arm C error rate ≈ arm B, then the hidden convention is the causal factor.
# If arm C error rate ≈ arm A, then the confusion is about domain overlap, not
# hidden conventions specifically.

# ─── ARM D: SCHEMA-OVERLAP CONTROL ───────────────────────────────────────────
#
# Three tools in the SAME domain (file operations) with the SAME parameter
# overlap (all accept path: string) but NO hidden convention cycle:
#   - No deprecated tool (all three are current and valid)
#   - Clear, non-overlapping scope (read vs write vs metadata)
#   - No ambiguity about which tool handles which intent
#
# This isolates the key variable: arm D has the same schema-level overlap
# as arm A (all tools share `path: string`) but no hidden convention cycle.
#
# Expected result: arm D error rate ≈ arm B (acyclic)
# If arm D error rate ≈ arm A (cyclic), then parameter overlap alone drives
# confusion and the hidden-cycle story adds nothing.
# If arm D << arm A, the hidden convention cycle is the causal factor
# ABOVE AND BEYOND mere parameter overlap.

OVERLAP_CONTROL_TOOLS = [
    {
        "name": "read_file_contents",
        "description": (
            "Read the contents of a file and return it as text. "
            "Use this when you need to see what is inside a file. "
            "Only works within allowed directories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file_contents",
        "description": (
            "Write content to a file, creating it if it doesn't exist or "
            "overwriting if it does. Use this when you need to save or "
            "update a file. Only works within allowed directories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to write"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_file_metadata",
        "description": (
            "Get metadata about a file (size, creation date, modification date, "
            "permissions) without reading its contents. Use this when you need "
            "information about a file but not its content. "
            "Only works within allowed directories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"},
            },
            "required": ["path"],
        },
    },
]


DISAMBIGUATED_TOOLS = [
    {
        "name": "read_file_legacy",
        "description": (
            "DEPRECATED — do not use. This tool exists only for backwards "
            "compatibility. Use read_file_text for all text files and "
            "read_file_media for all image/audio files. If you call this tool, "
            "it will return an error directing you to the correct tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "tail": {
                    "description": "If provided, returns only the last N lines of the file",
                    "type": "number",
                },
                "head": {
                    "description": "If provided, returns only the first N lines of the file",
                    "type": "number",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file_text",
        "description": (
            "Read a TEXT file (e.g., .txt, .json, .yaml, .py, .md, .csv, .log). "
            "Returns the file content as a UTF-8 string. Do NOT use this for "
            "images, audio, or binary files — use read_file_media instead. "
            "Supports head/tail parameters for partial reads."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "tail": {
                    "description": "If provided, returns only the last N lines of the file",
                    "type": "number",
                },
                "head": {
                    "description": "If provided, returns only the first N lines of the file",
                    "type": "number",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_file_media",
        "description": (
            "Read a MEDIA file (images: .png, .jpg, .gif, .svg; audio: .mp3, "
            ".wav, .ogg). Returns base64-encoded data with MIME type. Do NOT "
            "use this for text files — use read_file_text instead. "
            "Only works within allowed directories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
]
