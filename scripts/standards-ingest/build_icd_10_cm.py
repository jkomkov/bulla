"""Generate the ICD-10-CM (US clinical modification) pack.

ICD-10-CM has ~70k diagnosis codes — the canonical case for
``values_registry`` indirection. We ship a small inline seed of the
21 chapter prefix codes (A00-Y99 chapter starts) for documentation;
the authoritative annual CMS release lives behind the registry
pointer.

The pack also carries Extension E `mappings:` to ICD-9-CM via the
CMS-published GEMs (General Equivalence Mappings). This is the
canonical example of how a pack ships a translation table for a
deprecated companion standard.

ICD-10-CM is US public domain (CMS publishes annually); the
upstream registry license is ``open``. WHO ICD-10 (international,
non-CM) is a *separate* restricted-source pack (Phase 4A).
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml


# ICD-10-CM chapter prefix codes (the 21 disease-class chapters).
ICD_10_CM_CHAPTERS = [
    {"canonical": "A00-B99", "aliases": ["chapter-1", "infectious"]},
    {"canonical": "C00-D49", "aliases": ["chapter-2", "neoplasms"]},
    {"canonical": "D50-D89", "aliases": ["chapter-3", "blood"]},
    {"canonical": "E00-E89", "aliases": ["chapter-4", "endocrine"]},
    {"canonical": "F01-F99", "aliases": ["chapter-5", "mental"]},
    {"canonical": "G00-G99", "aliases": ["chapter-6", "nervous-system"]},
    {"canonical": "H00-H59", "aliases": ["chapter-7", "eye"]},
    {"canonical": "H60-H95", "aliases": ["chapter-8", "ear"]},
    {"canonical": "I00-I99", "aliases": ["chapter-9", "circulatory"]},
    {"canonical": "J00-J99", "aliases": ["chapter-10", "respiratory"]},
    {"canonical": "K00-K95", "aliases": ["chapter-11", "digestive"]},
    {"canonical": "L00-L99", "aliases": ["chapter-12", "skin"]},
    {"canonical": "M00-M99", "aliases": ["chapter-13", "musculoskeletal"]},
    {"canonical": "N00-N99", "aliases": ["chapter-14", "genitourinary"]},
    {"canonical": "O00-O9A", "aliases": ["chapter-15", "pregnancy"]},
    {"canonical": "P00-P96", "aliases": ["chapter-16", "perinatal"]},
    {"canonical": "Q00-Q99", "aliases": ["chapter-17", "congenital"]},
    {"canonical": "R00-R99", "aliases": ["chapter-18", "symptoms-signs"]},
    {"canonical": "S00-T88", "aliases": ["chapter-19", "injury-poisoning"]},
    {"canonical": "V00-Y99", "aliases": ["chapter-20", "external-causes"]},
    {"canonical": "Z00-Z99", "aliases": ["chapter-21", "factors-influencing-health"]},
]


# Tiny GEMs sample (5 representative ICD-9 → ICD-10 mappings, exact).
# The full GEMs file (~24k forward + ~21k reverse rows) is covered by
# the values_registry pointer once a full ingest happens.
SAMPLE_GEMS = [
    {"from": "001.0", "to": "A00.0", "equivalence": "exact"},
    {"from": "008.45", "to": "A04.7", "equivalence": "exact"},
    {"from": "250.00", "to": "E11.9", "equivalence": "exact"},
    {"from": "401.9", "to": "I10", "equivalence": "exact"},
    {"from": "493.90", "to": "J45.909", "equivalence": "exact"},
]


def build_pack() -> dict:
    today = _dt.date.today().isoformat()
    pack = {
        "pack_name": "icd-10-cm",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Public-Domain",  # US public domain
            "source_url": "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
            "registry_license": "open",
            "attribution": "sha256:cms-icd-10-cm-notices",
        },
        "derives_from": {
            "standard": "ICD-10-CM",
            "version": "2024",
            "source_uri": (
                "https://www.cms.gov/files/zip/"
                "2024-icd-10-cm-code-files.zip"
            ),
        },
        "dimensions": {
            "icd_10_cm_code": {
                "description": (
                    "ICD-10-CM diagnosis code. The full registry has "
                    "~70k codes; the inline seed lists the 21 chapter "
                    "prefixes for documentation. The CMS-published "
                    "annual release at the values_registry pointer is "
                    "the authoritative source. Annual cutover is "
                    "October 1; pack maintainers bump derives_from."
                    "version + values_registry.version each cycle."
                ),
                "field_patterns": [
                    "icd_10",
                    "icd10",
                    "icd_10_cm",
                    "icd10cm",
                    "diagnosis_code",
                    "dx_code",
                    "primary_diagnosis",
                    "secondary_diagnosis",
                    "*_icd",
                    "*_icd10",
                    "*_diagnosis",
                ],
                "description_keywords": [
                    "icd-10",
                    "icd10",
                    "icd-10-cm",
                    "icd10cm",
                    "diagnosis code",
                    "clinical modification",
                ],
                "domains": ["healthcare"],
                "known_values": ICD_10_CM_CHAPTERS,
                "values_registry": {
                    "uri": (
                        "https://www.cms.gov/files/zip/"
                        "2024-icd-10-cm-code-files.zip"
                    ),
                    "hash": "placeholder:awaiting-ingest",
                    "version": "2024",
                },
            },
        },
        # Extension E: GEMs (General Equivalence Mappings) ICD-9 → ICD-10.
        # Five representative rows inline as documentation; the full
        # ~24k-row table will be a follow-on ingest into a dedicated
        # icd-9-cm-gems.yaml pack.
        "mappings": {
            "icd-9-cm": {
                "icd_9_cm_code": SAMPLE_GEMS,
            },
        },
    }
    return pack


def main() -> None:
    pack = build_pack()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
