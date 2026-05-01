"""Generate the FIX 4.4 + 5.0 SP2 financial-messaging packs.

FIX (Financial Information Exchange) drives most global equity, FX,
and derivatives trading. The protocol has ~5000 message types/fields
across 4.4 and 5.0 SP2; the inline seed covers the most-recurrent
~30 message-type values for documentation, with the authoritative
QuickFIX dictionary behind the values_registry pointer.

Two packs (4.4 and 5.0) ship as separate seed files because production
trading systems often run multiple FIX versions concurrently and a
composition crossing version boundaries needs both vocabularies
loaded simultaneously.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml


# Most-common FIX MsgType values (Tag 35). Aliases include the
# human-readable name that documentation often uses.
COMMON_MSGTYPES = [
    {"canonical": "D",  "aliases": ["NewOrderSingle", "new-order-single"]},
    {"canonical": "F",  "aliases": ["OrderCancelRequest", "order-cancel"]},
    {"canonical": "G",  "aliases": ["OrderCancelReplaceRequest", "cancel-replace"]},
    {"canonical": "8",  "aliases": ["ExecutionReport", "execution-report"]},
    {"canonical": "9",  "aliases": ["OrderCancelReject", "cancel-reject"]},
    {"canonical": "1",  "aliases": ["TestRequest"]},
    {"canonical": "0",  "aliases": ["Heartbeat"]},
    {"canonical": "A",  "aliases": ["Logon"]},
    {"canonical": "5",  "aliases": ["Logout"]},
    {"canonical": "2",  "aliases": ["ResendRequest"]},
    {"canonical": "3",  "aliases": ["Reject"]},
    {"canonical": "4",  "aliases": ["SequenceReset"]},
    {"canonical": "V",  "aliases": ["MarketDataRequest"]},
    {"canonical": "W",  "aliases": ["MarketDataSnapshotFullRefresh"]},
    {"canonical": "X",  "aliases": ["MarketDataIncrementalRefresh"]},
    {"canonical": "h",  "aliases": ["TradingSessionStatus"]},
    {"canonical": "AE", "aliases": ["TradeCaptureReport"]},
    {"canonical": "j",  "aliases": ["BusinessMessageReject"]},
]


def build_pack(version: str) -> dict:
    today = _dt.date.today().isoformat()
    pack_name = f"fix-{version}"
    pack = {
        "pack_name": pack_name,
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Apache-2.0",  # QuickFIX dictionary license
            "source_url": (
                "https://www.fixtrading.org/standards/"
                f"fix-{version.replace('.', '-')}/"
            ),
            "registry_license": "open",
        },
        "derives_from": {
            "standard": f"FIX-{version}",
            "version": version,
            "source_uri": (
                "https://github.com/connamara/quickfixengine/raw/"
                f"main/spec/FIX{version.replace('.', '')}.xml"
            ),
        },
        "dimensions": {
            "fix_msg_type": {
                "description": (
                    f"FIX {version} MsgType (Tag 35). Single-character "
                    "or two-character codes identifying message kind. "
                    "The inline seed covers the ~20 most-common values; "
                    "the QuickFIX dictionary at the values_registry "
                    "pointer is the authoritative source."
                ),
                "field_patterns": [
                    "msg_type",
                    "msgType",
                    "MsgType",
                    "tag_35",
                    "fix_msg_type",
                    "*_msg_type",
                ],
                "description_keywords": [
                    "fix msgtype",
                    "fix message type",
                    "fix tag 35",
                    "msgtype field",
                ],
                "domains": ["financial"],
                "known_values": COMMON_MSGTYPES,
                "values_registry": {
                    "uri": (
                        "https://github.com/connamara/quickfixengine/raw/"
                        f"main/spec/FIX{version.replace('.', '')}.xml"
                    ),
                    "hash": "placeholder:awaiting-ingest",
                    "version": version,
                },
            },
            "fix_side": {
                "description": (
                    "FIX Side (Tag 54) — buy/sell/etc. Closed enum, "
                    "stable across FIX versions."
                ),
                "field_patterns": [
                    "side",
                    "*_side",
                    "tag_54",
                    "order_side",
                ],
                "description_keywords": ["fix side", "fix tag 54"],
                "domains": ["financial"],
                "known_values": [
                    {"canonical": "1", "aliases": ["Buy", "buy"]},
                    {"canonical": "2", "aliases": ["Sell", "sell"]},
                    {"canonical": "3", "aliases": ["BuyMinus"]},
                    {"canonical": "4", "aliases": ["SellPlus"]},
                    {"canonical": "5", "aliases": ["SellShort"]},
                    {"canonical": "6", "aliases": ["SellShortExempt"]},
                    {"canonical": "7", "aliases": ["Undisclosed"]},
                    {"canonical": "8", "aliases": ["Cross"]},
                    {"canonical": "9", "aliases": ["CrossShort"]},
                ],
            },
        },
    }
    return pack


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"4.4", "5.0"}:
        print("Usage: build_fix.py {4.4|5.0}", file=sys.stderr)
        sys.exit(2)
    pack = build_pack(sys.argv[1])
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
