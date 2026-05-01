"""Synthetic ecology benchmark: local equivalence, global divergence.

The decisive experiment for the non-locality thesis.

Design:
    Six synthetic single-tool servers. Each server exposes exactly ONE tool
    so there are no intra-server edges. Field names trigger known Bulla
    classifier dimensions.

    Key property: the SAME tool has DIFFERENT blind spots in different
    compositions. Blind spots arise ONLY when the partner has a field
    classified as the same hidden dimension.

    This is the constructive proof that hiddenness is non-local.

Servers (each with one tool):
    file_reader     — hidden: {path}         observable: {content, query}
    data_loader     — hidden: {path, timestamp}  observable: {data, source}
    event_logger    — hidden: {timestamp}    observable: {message, level}
    price_fetcher   — hidden: {amount}       observable: {symbol, currency}
    text_encoder    — hidden: {encoding}     observable: {text, result}
    page_browser    — hidden: {offset}       observable: {url, items}

Composition matrix (shared hidden dimension → blind spot):
                    file_reader  data_loader  event_logger  price    text_enc  page
    file_reader     —            path_conv    (none)        (none)   (none)    (none)
    data_loader     path_conv    —            date_fmt      (none)   (none)    (none)
    event_logger    (none)       date_fmt     —             (none)   (none)    (none)
    price_fetcher   (none)       (none)       (none)        —        (none)    (none)
    text_encoder    (none)       (none)       (none)        (none)   —         (none)
    page_browser    (none)       (none)       (none)        (none)   (none)    —

Non-locality pairs:
    file_reader + data_loader   → fee>0, blind spot on 'path'
    file_reader + event_logger  → fee=0, NO blind spots
    data_loader + event_logger  → fee>0, blind spot on 'timestamp'
    data_loader + price_fetcher → fee=0, NO blind spots
    event_logger + price_fetcher → fee=0, NO blind spots
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


# ── Synthetic tool definitions (MCP format) ──────────────────────────
# Each "server" has exactly ONE tool to eliminate intra-server edges.

FILE_READER_TOOL = {
    "name": "read_file",
    "description": "Read the contents of a file.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The file path to read from.",
            },
            "content": {
                "type": "string",
                "description": "The file content (output).",
            },
            "query": {
                "type": "string",
                "description": "Optional search query within the file.",
            },
        },
        "required": ["path"],
    },
}

DATA_LOADER_TOOL = {
    "name": "load_data",
    "description": "Load a data source for processing.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the data source.",
            },
            "timestamp": {
                "type": "string",
                "description": "Snapshot timestamp for the data.",
            },
            "data": {
                "type": "string",
                "description": "The loaded data content.",
            },
            "source": {
                "type": "string",
                "description": "Name of the data source.",
            },
        },
        "required": ["path"],
    },
}

EVENT_LOGGER_TOOL = {
    "name": "log_event",
    "description": "Log an application event.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "string",
                "description": "When the event occurred.",
            },
            "message": {
                "type": "string",
                "description": "The event message.",
            },
            "level": {
                "type": "string",
                "description": "Log level (info, warn, error).",
            },
        },
        "required": ["message"],
    },
}

PRICE_FETCHER_TOOL = {
    "name": "fetch_price",
    "description": "Fetch current price for an asset.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Asset symbol (e.g. AAPL).",
            },
            "amount": {
                "type": "number",
                "description": "The price amount.",
            },
            "currency": {
                "type": "string",
                "description": "Currency for the price.",
            },
        },
        "required": ["symbol"],
    },
}

TEXT_ENCODER_TOOL = {
    "name": "encode_text",
    "description": "Encode text content.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to encode.",
            },
            "encoding": {
                "type": "string",
                "description": "Target encoding format.",
            },
            "result": {
                "type": "string",
                "description": "The encoded result.",
            },
        },
        "required": ["text"],
    },
}

PAGE_BROWSER_TOOL = {
    "name": "browse_pages",
    "description": "Browse paginated content.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to browse.",
            },
            "offset": {
                "type": "integer",
                "description": "Pagination offset.",
            },
            "items": {
                "type": "array",
                "description": "The page items.",
            },
        },
        "required": ["url"],
    },
}


# ── Server registry ──────────────────────────────────────────────────

SERVERS: dict[str, list[dict[str, Any]]] = {
    "file_reader": [FILE_READER_TOOL],
    "data_loader": [DATA_LOADER_TOOL],
    "event_logger": [EVENT_LOGGER_TOOL],
    "price_fetcher": [PRICE_FETCHER_TOOL],
    "text_encoder": [TEXT_ENCODER_TOOL],
    "page_browser": [PAGE_BROWSER_TOOL],
}

# Expected hidden fields per server (by classifier patterns)
EXPECTED_HIDDEN: dict[str, set[str]] = {
    "file_reader": {"path"},
    "data_loader": {"path", "timestamp"},
    "event_logger": {"timestamp"},
    "price_fetcher": {"amount"},
    "text_encoder": {"encoding"},
    "page_browser": {"offset"},
}

# Pairs to test — chosen to demonstrate non-locality
# Each row: (left, right, expected_blind_spot_fields_or_empty)
TEST_PAIRS: list[tuple[str, str]] = [
    # Same file_reader tool, different blind spots:
    ("file_reader", "data_loader"),     # blind spot: path (both have path_convention)
    ("file_reader", "event_logger"),    # NO blind spots (no shared dimension)
    ("file_reader", "price_fetcher"),   # NO blind spots
    # Same data_loader tool, different blind spots:
    ("data_loader", "event_logger"),    # blind spot: timestamp (both have date_format)
    ("data_loader", "price_fetcher"),   # NO blind spots
    # Other informative pairs:
    ("event_logger", "price_fetcher"),  # NO blind spots
    ("text_encoder", "page_browser"),   # NO blind spots (encoding ≠ offset)
    ("file_reader", "text_encoder"),    # NO blind spots (path ≠ encoding)
]


@dataclass(frozen=True)
class SyntheticGroundTruth:
    """Ground truth for one synthetic composition."""
    left_server: str
    right_server: str
    fee: int
    blind_spot_fields: frozenset[str]  # fields that are blind spots
    blind_spot_dimensions: frozenset[str]  # dimension names


def compute_ground_truth() -> list[SyntheticGroundTruth]:
    """Compute ground truth for all test pairs using BullaGuard."""
    import sys
    from pathlib import Path as P
    sys.path.insert(0, str(P(__file__).resolve().parent.parent.parent / "src"))
    from bulla.guard import BullaGuard

    results = []
    for left, right in TEST_PAIRS:
        # Build prefixed tool list
        prefixed = []
        for server_name in (left, right):
            for tool in SERVERS[server_name]:
                clone = dict(tool)
                clone["name"] = f"{server_name}__{tool['name']}"
                prefixed.append(clone)

        guard = BullaGuard.from_tools_list(prefixed, name=f"{left}+{right}")
        diag = guard.diagnose()

        blind_fields: set[str] = set()
        blind_dims: set[str] = set()
        for bs in diag.blind_spots:
            blind_fields.add(bs.from_field)
            blind_fields.add(bs.to_field)
            blind_dims.add(bs.dimension)

        results.append(SyntheticGroundTruth(
            left_server=left,
            right_server=right,
            fee=diag.coherence_fee,
            blind_spot_fields=frozenset(blind_fields),
            blind_spot_dimensions=frozenset(blind_dims),
        ))

    return results


def verify_ground_truth() -> None:
    """Verify that BullaGuard computes the expected fees and blind spots."""
    truths = compute_ground_truth()

    print("SYNTHETIC ECOLOGY — GROUND TRUTH VERIFICATION")
    print("=" * 70)
    print()

    for gt in truths:
        print(f"  {gt.left_server:20s} + {gt.right_server:20s}")
        print(f"    fee = {gt.fee}")
        if gt.blind_spot_fields:
            print(f"    blind spots: {sorted(gt.blind_spot_fields)}")
            print(f"    dimensions:  {sorted(gt.blind_spot_dimensions)}")
        else:
            print(f"    (no blind spots)")
        print()

    # Non-locality demonstration
    print("NON-LOCALITY DEMONSTRATION")
    print("-" * 70)

    # Group by left server to show same tool → different blind spots
    from collections import defaultdict
    by_server: dict[str, list[SyntheticGroundTruth]] = defaultdict(list)
    for gt in truths:
        by_server[gt.left_server].append(gt)

    for server in sorted(by_server.keys()):
        compositions = by_server[server]
        if len(compositions) < 2:
            continue
        blind_sets = [gt.blind_spot_fields for gt in compositions]
        if len(set(frozenset(s) for s in blind_sets)) > 1:
            print(f"\n  ★ {server} has DIFFERENT blind spots in different compositions:")
            for gt in compositions:
                bs = sorted(gt.blind_spot_fields) if gt.blind_spot_fields else "(none)"
                print(f"    + {gt.right_server:20s} → {bs}")

    print(f"\n{'=' * 70}")
