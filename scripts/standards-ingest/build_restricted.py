"""Generate the five restricted-source pack metadata files (Phase 4).

Each pack ships only dimension *metadata* (field patterns, structural
shape, vocabulary topology). The actual licensed values live behind
``values_registry`` pointers; consumers must obtain the relevant
license to fetch the registry contents. The metadata-only invariant
(Extension B validator) refuses any inline ``known_values`` on a
licensed dimension.

The five packs:
  - who-icd-10:    WHO international ICD-10 (translations are licensed)
  - swift-mt-mx:   SWIFT MT and ISO-20022-flavored MX message types
  - hl7-v2:        HL7 v2.x segments + value sets
  - umls-mappings: UMLS Metathesaurus cross-coding (SNOMED ↔ ICD-10 ↔
                   LOINC ↔ RxNorm), schema only — rows behind UMLS license
  - iso-20022:     ISO 20022 financial-messaging components

These packs validate today; downstream verification (fetching the
licensed registries) is gated on consumer-side credentials and will
fail with ``RegistryAccessError(LICENSE_REQUIRED, ...)`` until the
caller registers the appropriate license token.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml


def who_icd_10() -> dict:
    today = _dt.date.today().isoformat()
    return {
        "pack_name": "who-icd-10",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "CC-BY-NC-SA-3.0",  # varies by translation; placeholder
            "source_url": "https://icd.who.int/browse10/",
            "registry_license": "research-only",
            "attribution": "sha256:who-icd-10-notices",
        },
        "derives_from": {
            "standard": "WHO-ICD-10",
            "version": "2019",
            "source_uri": "https://icd.who.int/browse10/2019/en",
        },
        "dimensions": {
            "who_icd_10_code": {
                "description": (
                    "WHO international ICD-10 diagnosis code "
                    "(distinct from ICD-10-CM, the US clinical "
                    "modification). Translations are licensed; the "
                    "values_registry pointer requires a WHO ICD-10 "
                    "research credential to fetch."
                ),
                "field_patterns": [
                    "who_icd",
                    "who_icd_10",
                    "international_icd_10",
                    "*_who_icd",
                ],
                "description_keywords": [
                    "who icd-10",
                    "international classification of diseases",
                    "icd-10 international",
                ],
                "domains": ["healthcare"],
                "values_registry": {
                    "uri": "https://icd.who.int/browse10/2019/en",
                    "hash": "placeholder:awaiting-license",
                    "version": "2019",
                    "license_id": "WHO-ICD-10",
                },
            },
        },
    }


def swift_mt_mx() -> dict:
    today = _dt.date.today().isoformat()
    return {
        "pack_name": "swift-mt-mx",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Proprietary-SWIFT",
            "source_url": "https://developer.swift.com/",
            "registry_license": "restricted",
            "attribution": "sha256:swift-notices",
        },
        "derives_from": {
            "standard": "SWIFT-MT-MX",
            "version": f"snapshot-{today}",
            "source_uri": "https://developer.swift.com/",
        },
        "dimensions": {
            "swift_mt_message_type": {
                "description": (
                    "SWIFT MT message type (MT 103, MT 202, MT 940, "
                    "etc.). Membership-restricted; values_registry "
                    "requires a SWIFT credential."
                ),
                "field_patterns": [
                    "mt_type",
                    "mt_message_type",
                    "swift_mt",
                    "*_mt_type",
                    "*_swift_mt",
                ],
                "description_keywords": [
                    "swift mt",
                    "swift mt message",
                    "mt 103",
                    "mt 202",
                ],
                "domains": ["financial"],
                "values_registry": {
                    "uri": "https://developer.swift.com/messages-mt",
                    "hash": "placeholder:awaiting-license",
                    "version": "snapshot",
                    "license_id": "SWIFT-MEMBER",
                },
            },
            "swift_mx_message_type": {
                "description": (
                    "SWIFT MX (ISO 20022-flavored) message type "
                    "(pacs.008, pain.001, camt.053, etc.). Same "
                    "license gating as MT."
                ),
                "field_patterns": [
                    "mx_type",
                    "mx_message_type",
                    "swift_mx",
                    "*_mx_type",
                    "*_swift_mx",
                    "iso20022_message",
                ],
                "description_keywords": [
                    "swift mx",
                    "iso 20022 message",
                    "pacs.008",
                    "pain.001",
                    "camt.053",
                ],
                "domains": ["financial"],
                "values_registry": {
                    "uri": "https://developer.swift.com/messages-mx",
                    "hash": "placeholder:awaiting-license",
                    "version": "snapshot",
                    "license_id": "SWIFT-MEMBER",
                },
            },
        },
    }


def hl7_v2() -> dict:
    today = _dt.date.today().isoformat()
    return {
        "pack_name": "hl7-v2",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Proprietary-HL7",
            "source_url": "https://www.hl7.org/implement/standards/product_brief.cfm?product_id=185",
            "registry_license": "research-only",
            "attribution": "sha256:hl7-v2-notices",
        },
        "derives_from": {
            "standard": "HL7-v2",
            "version": "2.5.1",
            "source_uri": "https://www.hl7.org/implement/standards/",
        },
        "dimensions": {
            "hl7_v2_segment": {
                "description": (
                    "HL7 v2.x segment identifier (3-character code: "
                    "MSH, PID, OBR, OBX, ORC, NTE, etc.). Membership-"
                    "tier licensed; values_registry requires HL7 "
                    "credential to fetch the full segment dictionary."
                ),
                "field_patterns": [
                    "segment",
                    "segment_id",
                    "hl7_segment",
                    "*_segment",
                    "*_hl7_segment",
                ],
                "description_keywords": [
                    "hl7 v2 segment",
                    "hl7 segment",
                    "msh segment",
                    "pid segment",
                    "obx segment",
                ],
                "domains": ["healthcare"],
                "values_registry": {
                    "uri": "https://www.hl7.org/implement/standards/v2-segments",
                    "hash": "placeholder:awaiting-license",
                    "version": "2.5.1",
                    "license_id": "HL7-MEMBER",
                },
            },
            "hl7_v2_message_type": {
                "description": (
                    "HL7 v2.x message type (ADT, ORM, ORU, RDE, etc.) "
                    "with optional event triggers (ADT^A01)."
                ),
                "field_patterns": [
                    "message_type",
                    "msg_type",
                    "hl7_message_type",
                    "*_hl7_msg",
                ],
                "description_keywords": [
                    "hl7 v2 message",
                    "hl7 message type",
                    "adt message",
                    "oru message",
                ],
                "domains": ["healthcare"],
                "values_registry": {
                    "uri": "https://www.hl7.org/implement/standards/v2-messages",
                    "hash": "placeholder:awaiting-license",
                    "version": "2.5.1",
                    "license_id": "HL7-MEMBER",
                },
            },
        },
    }


def umls_mappings() -> dict:
    today = _dt.date.today().isoformat()
    return {
        "pack_name": "umls-mappings",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "NLM-UMLS",
            "source_url": "https://uts.nlm.nih.gov/uts/umls",
            "registry_license": "restricted",
            "attribution": "sha256:nlm-umls-notices",
        },
        "derives_from": {
            "standard": "UMLS-Metathesaurus",
            "version": "2024AB",
            "source_uri": "https://www.nlm.nih.gov/research/umls/licensedcontent/umlsknowledgesources.html",
        },
        "dimensions": {
            "umls_concept_id": {
                "description": (
                    "UMLS Concept Unique Identifier (CUI) — the spine "
                    "of cross-vocabulary terminology mapping. License-"
                    "gated; values_registry requires NLM-UMLS user-"
                    "of-record credential."
                ),
                "field_patterns": [
                    "cui",
                    "umls_cui",
                    "concept_id",
                    "umls_concept_id",
                    "*_cui",
                ],
                "description_keywords": [
                    "umls cui",
                    "umls concept",
                    "concept unique identifier",
                ],
                "domains": ["healthcare"],
                "values_registry": {
                    "uri": "https://uts.nlm.nih.gov/uts/umls/concepts",
                    "hash": "placeholder:awaiting-license",
                    "version": "2024AB",
                    "license_id": "NLM-UMLS",
                },
            },
        },
        # Mapping schema only — actual rows live in the licensed
        # registry. The schema makes downstream consumers' code
        # type-stable across credential boundaries: a tool can be
        # written against this pack's mapping topology before any
        # license is held, and only the values fetch needs the
        # credential.
        "mappings": {
            "snomed-ct": {
                "snomed_concept_id": [],  # rows fetched from registry
            },
            "icd-10-cm": {
                "icd_10_cm_code": [],  # rows fetched from registry
            },
            "loinc": {
                "loinc_code": [],  # rows fetched from registry
            },
            "rxnorm": {
                "rxnorm_cui": [],  # rows fetched from registry
            },
        },
    }


def iso_20022() -> dict:
    today = _dt.date.today().isoformat()
    return {
        "pack_name": "iso-20022",
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "Proprietary-ISO",
            "source_url": "https://www.iso20022.org/",
            "registry_license": "research-only",
            "attribution": "sha256:iso-20022-notices",
        },
        "derives_from": {
            "standard": "ISO-20022",
            "version": "2024",
            "source_uri": "https://www.iso20022.org/iso-20022-message-definitions",
        },
        "dimensions": {
            "iso_20022_message_type": {
                "description": (
                    "ISO 20022 message-definition identifier (e.g. "
                    "pacs.008.001.10, pain.001.001.11). Paid spec; "
                    "values_registry requires ISO-20022 license."
                ),
                "field_patterns": [
                    "iso20022_msg",
                    "iso_20022_message",
                    "message_definition",
                    "*_iso20022_msg",
                ],
                "description_keywords": [
                    "iso 20022 message",
                    "iso-20022 message",
                    "pacs.008",
                    "pain.001",
                    "camt.053",
                ],
                "domains": ["financial"],
                "values_registry": {
                    "uri": "https://www.iso20022.org/iso-20022-message-definitions",
                    "hash": "placeholder:awaiting-license",
                    "version": "2024",
                    "license_id": "ISO-20022",
                },
            },
        },
    }


PACKS = {
    "who-icd-10":    who_icd_10,
    "swift-mt-mx":   swift_mt_mx,
    "hl7-v2":        hl7_v2,
    "umls-mappings": umls_mappings,
    "iso-20022":     iso_20022,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in PACKS:
        print(f"Usage: build_restricted.py {{{'|'.join(PACKS.keys())}}}", file=sys.stderr)
        sys.exit(2)
    pack = PACKS[sys.argv[1]]()
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
