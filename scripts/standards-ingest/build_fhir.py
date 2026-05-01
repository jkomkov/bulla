"""Generate the HL7 FHIR R4 + R5 packs.

FHIR (Fast Healthcare Interoperability Resources) is HL7's modern
healthcare-exchange standard. Unlike traditional HL7 v2, FHIR is JSON/
XML-native and has a proper resource model.

The dimensions captured here are the *resourceType* enum (~150 entries
in R4, ~160 in R5) — the discriminator at the top of every FHIR
resource. Terminology bindings (SNOMED, LOINC, ICD-10, RxNorm, etc.)
do not live in this pack; they live in the umls-mappings restricted
pack via passive `mappings:` blocks.

R4 and R5 ship as separate seed files because production systems
often run both in parallel during transition periods, and R4→R5 has
real breaking changes that drove documented incidents.
"""

from __future__ import annotations

import datetime as _dt
import sys

import yaml

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from _hash_lookup import lookup as _hash_for  # noqa: E402


# Most-common FHIR resource types — both R4 and R5 share these.
COMMON_RESOURCE_TYPES = [
    "Patient", "Practitioner", "PractitionerRole",
    "Organization", "Location",
    "Encounter", "Observation", "Condition", "DiagnosticReport",
    "Procedure", "MedicationRequest", "Medication",
    "MedicationStatement", "MedicationAdministration",
    "AllergyIntolerance", "Immunization", "FamilyMemberHistory",
    "CarePlan", "Goal", "Task",
    "Coverage", "Claim", "ExplanationOfBenefit",
    "Composition", "Bundle", "DocumentReference",
    "Specimen", "ImagingStudy", "ServiceRequest",
    "Appointment", "AppointmentResponse", "Schedule", "Slot",
    "Consent", "Provenance", "AuditEvent",
    "RelatedPerson", "Group", "List",
    "Subscription", "Communication",
]


def build_pack(version: str) -> dict:
    today = _dt.date.today().isoformat()
    pack_name = f"fhir-{version.lower()}"
    pack = {
        "pack_name": pack_name,
        "pack_version": "0.1.0",
        "license": {
            "spdx_id": "CC0-1.0",
            "source_url": f"https://hl7.org/fhir/{version}/",
            "registry_license": "open",
            "attribution": "sha256:hl7-fhir-notices",
        },
        "derives_from": {
            "standard": "HL7-FHIR",
            "version": version,
            "source_uri": f"https://hl7.org/fhir/{version}/downloads.html",
        },
        "dimensions": {
            "fhir_resource_type": {
                "description": (
                    f"FHIR {version} resourceType discriminator. Inline "
                    f"seed of ~40 most-recurrent resources; the "
                    f"authoritative ~150 entries live at the "
                    f"values_registry pointer. R4→R5 introduces "
                    f"breaking changes on a subset of resources, so "
                    f"R4 and R5 packs are separate."
                ),
                "field_patterns": [
                    "resourceType",
                    "resource_type",
                    "resource",
                    "fhir_type",
                    "*_resource_type",
                ],
                "description_keywords": [
                    "fhir resource",
                    "fhir resource type",
                    "fhir resourcetype",
                    f"fhir {version}",
                ],
                "domains": ["healthcare"],
                "known_values": COMMON_RESOURCE_TYPES,
                "values_registry": {
                    "uri": (
                        f"https://hl7.org/fhir/{version}/"
                        "valueset-resource-types.json"
                    ),
                    "hash": _hash_for(
                        f"fhir-{version.lower()}",
                        "fhir_resource_type",
                        version,
                    ),
                    "version": version,
                },
            },
        },
    }
    # R4↔R5 mapping demonstrates Extension E for cross-version
    # translation. Most resources stay the same; a handful renamed
    # (e.g. R4 ImagingManifest → R5 ImagingSelection).
    if version == "R5":
        pack["mappings"] = {
            "fhir-r4": {
                "fhir_resource_type": [
                    {
                        "from": "ImagingSelection",
                        "to": "ImagingManifest",
                        "equivalence": "lossy_bidirectional",
                        "note": "R5 ImagingSelection replaces R4 ImagingManifest with field-level differences",
                    },
                ],
            },
        }
    return pack


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"R4", "R5"}:
        print("Usage: build_fhir.py {R4|R5}", file=sys.stderr)
        sys.exit(2)
    pack = build_pack(sys.argv[1])
    yaml.dump(pack, sys.stdout, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
