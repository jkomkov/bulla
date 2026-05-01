"""Generate the NAICS 2022 industry-classification pack.

The full NAICS 2022 has ~1000 codes spanning sector (2-digit) through
national industry (6-digit). We ship a small inline seed of the 20
two-digit sector codes (documentation) plus a ``values_registry``
pointer to the Census-published CSV for the full hierarchy. Like the
IANA pack, this exercises the inline-as-documentation pattern with
``open`` license and registry-as-source-of-truth.

Note: NAICS revisions every 5 years (2022, 2027, ...). The
``derives_from.version`` distinguishes them; old packs continue to
verify against their original revision via Extension C.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from _hash_lookup import lookup as _hash_for  # noqa: E402


# 2022 NAICS sector codes (2-digit) — the canonical top of the hierarchy.
NAICS_2022_SECTORS = [
    {"canonical": "11", "aliases": ["agriculture-forestry-fishing-hunting"]},
    {"canonical": "21", "aliases": ["mining-quarrying-oil-gas"]},
    {"canonical": "22", "aliases": ["utilities"]},
    {"canonical": "23", "aliases": ["construction"]},
    {"canonical": "31-33", "aliases": ["manufacturing"]},
    {"canonical": "42", "aliases": ["wholesale-trade"]},
    {"canonical": "44-45", "aliases": ["retail-trade"]},
    {"canonical": "48-49", "aliases": ["transportation-warehousing"]},
    {"canonical": "51", "aliases": ["information"]},
    {"canonical": "52", "aliases": ["finance-insurance"]},
    {"canonical": "53", "aliases": ["real-estate-rental-leasing"]},
    {"canonical": "54", "aliases": ["professional-scientific-technical-services"]},
    {"canonical": "55", "aliases": ["management-of-companies"]},
    {"canonical": "56", "aliases": ["administrative-support-waste-management"]},
    {"canonical": "61", "aliases": ["educational-services"]},
    {"canonical": "62", "aliases": ["health-care-social-assistance"]},
    {"canonical": "71", "aliases": ["arts-entertainment-recreation"]},
    {"canonical": "72", "aliases": ["accommodation-food-services"]},
    {"canonical": "81", "aliases": ["other-services-except-public-administration"]},
    {"canonical": "92", "aliases": ["public-administration"]},
]


def build_pack() -> dict:
    today = _dt.date.today().isoformat()
    pack = {
        "pack_name": "naics-2022",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Public-Domain",
            "source_url": "https://www.census.gov/naics/?68967",
            "registry_license": "open",
        },
        "derives_from": {
            "standard": "NAICS",
            "version": "2022",
            "source_uri": (
                "https://www.census.gov/naics/2022NAICS/"
                "2-6%20digit_2022_Codes.xlsx"
            ),
        },
        "dimensions": {
            "industry_code": {
                "description": (
                    "NAICS 2022 industry classification code. The full "
                    "registry covers ~1000 codes from 2-digit sector to "
                    "6-digit national industry; the 20 inline values are "
                    "the top-level sector codes for documentation. The "
                    "values_registry pointer is the authoritative source "
                    "for the full hierarchy."
                ),
                "field_patterns": [
                    "naics",
                    "naics_code",
                    "industry",
                    "industry_code",
                    "sector",
                    "sector_code",
                    "sic",
                    "sic_code",
                    "*_naics",
                    "*_industry_code",
                ],
                "description_keywords": [
                    "naics",
                    "industry classification",
                    "industry code",
                    "sector code",
                    "north american industry classification",
                    "sic",
                    "sic code",
                ],
                "domains": ["universal"],
                "known_values": NAICS_2022_SECTORS,
                "values_registry": {
                    "uri": (
                        "https://www.census.gov/naics/2022NAICS/"
                        "2-6%20digit_2022_Codes.xlsx"
                    ),
                    "hash": _hash_for("naics-2022", "industry_code", "2022"),
                    "version": "2022",
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
