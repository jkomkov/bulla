"""Generate the GS1 General Specifications pack (GTIN, GLN, SSCC + AIs).

GS1 governs the global trade item identification system: barcodes,
RFID tags, EDI exchanges. The seed inlines the GS1 Application
Identifier (AI) categories and the most-common AI codes; the full AI
table (~500 entries with continuous additions) lives behind the
values_registry pointer.

GTIN miscoding (e.g. shifting a check digit, swapping GTIN-13 vs
GTIN-14) is a well-documented FDA-recall failure mode that this
pack's classifier signal makes detectable at composition time.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml


# Most-common GS1 Application Identifiers (the AI prefix codes).
COMMON_AIS = [
    {"canonical": "00",  "aliases": ["sscc"]},
    {"canonical": "01",  "aliases": ["gtin", "gtin-14"]},
    {"canonical": "02",  "aliases": ["content-gtin"]},
    {"canonical": "10",  "aliases": ["batch", "lot"]},
    {"canonical": "11",  "aliases": ["prod-date", "production-date"]},
    {"canonical": "13",  "aliases": ["pack-date", "packaging-date"]},
    {"canonical": "15",  "aliases": ["best-before"]},
    {"canonical": "17",  "aliases": ["expiry", "expiration-date"]},
    {"canonical": "21",  "aliases": ["serial", "serial-number"]},
    {"canonical": "240", "aliases": ["additional-id"]},
    {"canonical": "30",  "aliases": ["count", "variable-count"]},
    {"canonical": "310", "aliases": ["net-weight-kg"]},
    {"canonical": "320", "aliases": ["net-weight-lb"]},
    {"canonical": "400", "aliases": ["customer-po", "customer-purchase-order"]},
    {"canonical": "401", "aliases": ["consignment"]},
    {"canonical": "410", "aliases": ["ship-to-loc-gln", "ship-to-gln"]},
    {"canonical": "411", "aliases": ["bill-to-gln"]},
    {"canonical": "414", "aliases": ["physical-loc-gln"]},
    {"canonical": "421", "aliases": ["ship-to-postal"]},
    {"canonical": "422", "aliases": ["country-of-origin"]},
]


def build_pack() -> dict:
    today = _dt.date.today().isoformat()
    pack = {
        "pack_name": "gs1",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Proprietary-Open-Reference",
            "source_url": (
                "https://www.gs1.org/standards/barcodes-epcrfid-id-keys/"
                "general-specifications"
            ),
            "registry_license": "open",
        },
        "derives_from": {
            "standard": "GS1-General-Specifications",
            "version": f"snapshot-{today}",
            "source_uri": (
                "https://www.gs1.org/standards/barcodes-epcrfid-id-keys/"
                "general-specifications"
            ),
        },
        "dimensions": {
            "gs1_application_identifier": {
                "description": (
                    "GS1 Application Identifier (AI) — the 2-4 digit "
                    "prefix that identifies what data follows in a "
                    "GS1-encoded barcode or message. The inline seed "
                    "covers the ~20 most-recurrent AIs; the full table "
                    "(~500 entries) lives at the values_registry "
                    "pointer."
                ),
                "field_patterns": [
                    "ai",
                    "application_identifier",
                    "gs1_ai",
                    "*_ai",
                    "*_application_id",
                ],
                "description_keywords": [
                    "gs1 application identifier",
                    "gs1 ai",
                    "application identifier",
                ],
                "domains": ["universal"],
                "known_values": COMMON_AIS,
                "values_registry": {
                    "uri": (
                        "https://www.gs1.org/sites/default/files/docs/"
                        "barcodes/GS1_General_Specifications.pdf"
                    ),
                    "hash": "placeholder:awaiting-ingest",
                    "version": today,
                },
            },
            "gs1_id_key_type": {
                "description": (
                    "GS1 identification-key type (GTIN, GLN, SSCC, "
                    "GRAI, GIAI, GSIN, GSRN). Closed enum at this "
                    "level."
                ),
                "field_patterns": [
                    "id_key_type",
                    "gs1_key",
                    "*_gs1_key",
                    "identifier_type",
                ],
                "description_keywords": [
                    "gs1 identification key",
                    "gs1 id key",
                    "gtin / gln / sscc",
                ],
                "domains": ["universal"],
                "known_values": [
                    {"canonical": "GTIN", "aliases": ["gtin", "trade-item-id"]},
                    {"canonical": "GLN",  "aliases": ["gln", "location-id"]},
                    {"canonical": "SSCC", "aliases": ["sscc", "shipping-container"]},
                    {"canonical": "GRAI", "aliases": ["grai", "returnable-asset"]},
                    {"canonical": "GIAI", "aliases": ["giai", "individual-asset"]},
                    {"canonical": "GSIN", "aliases": ["gsin", "shipment-id"]},
                    {"canonical": "GSRN", "aliases": ["gsrn", "service-relation"]},
                    {"canonical": "GDTI", "aliases": ["gdti", "document-type"]},
                ],
            },
        },
    }
    return pack


def main() -> None:
    pack = build_pack()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
