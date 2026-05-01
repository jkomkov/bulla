"""Generate the UN/EDIFACT D.21B+ pack.

UN/EDIFACT (Electronic Data Interchange For Administration, Commerce
and Transport) is the dominant non-US EDI standard. The standard has
~5000 message-type and code-list entries; the seed inlines the most-
common message types (INVOIC, ORDERS, DESADV, etc.) with the full
catalogue behind the values_registry pointer.

EDIFACT is heavily used in European/Asian supply chains and is a
documented source of cross-standard mismatch losses (the canonical
EDI translation errors industry surveys identify each year).
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml


# Most-common UN/EDIFACT message types.
COMMON_MESSAGE_TYPES = [
    {"canonical": "INVOIC", "aliases": ["invoice"]},
    {"canonical": "ORDERS", "aliases": ["purchase-order"]},
    {"canonical": "ORDRSP", "aliases": ["order-response"]},
    {"canonical": "DESADV", "aliases": ["dispatch-advice", "asn"]},
    {"canonical": "RECADV", "aliases": ["receiving-advice"]},
    {"canonical": "REMADV", "aliases": ["remittance-advice"]},
    {"canonical": "PRICAT", "aliases": ["price-catalogue"]},
    {"canonical": "PROINQ", "aliases": ["product-inquiry"]},
    {"canonical": "QUOTES", "aliases": ["quote"]},
    {"canonical": "PAYORD", "aliases": ["payment-order"]},
    {"canonical": "PAYMUL", "aliases": ["multiple-payment-order"]},
    {"canonical": "BANSTA", "aliases": ["banking-status"]},
    {"canonical": "FINSTA", "aliases": ["financial-statement"]},
    {"canonical": "IFTMIN", "aliases": ["transport-instruction"]},
    {"canonical": "IFTSTA", "aliases": ["transport-status"]},
    {"canonical": "CUSDEC", "aliases": ["customs-declaration"]},
    {"canonical": "CUSRES", "aliases": ["customs-response"]},
    {"canonical": "DELFOR", "aliases": ["delivery-forecast"]},
    {"canonical": "DELJIT", "aliases": ["delivery-just-in-time"]},
    {"canonical": "INVRPT", "aliases": ["inventory-report"]},
]


def build_pack() -> dict:
    today = _dt.date.today().isoformat()
    pack = {
        "pack_name": "un-edifact",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Public-Domain",
            "source_url": "https://service.unece.org/trade/untdid/welcome.html",
            "registry_license": "open",
        },
        "derives_from": {
            "standard": "UN-EDIFACT",
            "version": "D.21B",
            "source_uri": "https://service.unece.org/trade/untdid/d21b/d21b.htm",
        },
        "dimensions": {
            "edifact_message_type": {
                "description": (
                    "UN/EDIFACT message type — the 6-character code at "
                    "the head of every EDIFACT envelope. The inline seed "
                    "covers the ~20 most-common types across commercial, "
                    "transport, financial, and customs domains; the "
                    "values_registry pointer is the authoritative UN/CEFACT "
                    "directory."
                ),
                "field_patterns": [
                    "msg_type",
                    "message_type",
                    "edi_type",
                    "edifact_type",
                    "*_message_type",
                    "*_msg_type",
                ],
                "description_keywords": [
                    "edifact message type",
                    "edi message",
                    "un/edifact",
                    "untdid",
                ],
                "domains": ["universal"],
                "known_values": COMMON_MESSAGE_TYPES,
                "values_registry": {
                    "uri": (
                        "https://service.unece.org/trade/untdid/d21b/"
                        "d21b.zip"
                    ),
                    "hash": "placeholder:awaiting-ingest",
                    "version": "D.21B",
                },
            },
        },
    }
    return pack


def main() -> None:
    pack = build_pack()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
