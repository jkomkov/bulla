# Standards Ingestion — Canonical Source URIs (RP-2)

> Status: living reference, started 2026-04-26. Updated as new packs land.
>
> Each row is the source-of-truth URI for a standard's authoritative
> machine-readable artifact. Pack YAML files use these as
> `derives_from.source_uri`, `derives_from.source_hash`, and
> `values_registry.uri` values.

| Standard | Phase | Format | Authoritative source | Update cadence |
|---|---|---|---|---|
| **ISO 4217** (currency codes) | 2A | XML/CSV | https://www.six-group.com/en/products-services/financial-information/data-standards.html | Irregular; SIX is the official maintenance agency |
| **ISO 8601 / RFC 3339** (date/time) | 2B | Prose / RFC | https://www.rfc-editor.org/rfc/rfc3339 | RFC frozen; ISO 8601-1:2019 is the current ISO base |
| **ISO 3166-1/2** (country / subdivision) | 2C | CSV / JSON | https://www.iso.org/iso-3166-country-codes.html (canonical); https://datahub.io/core/country-list (mirror) | Annual or as needed |
| **ISO 639** (languages) | 2D | TSV (SIL) | https://iso639-3.sil.org/code_tables/639/data | Annual |
| **IANA Media Types (MIME)** | 2E | CSV | https://www.iana.org/assignments/media-types/media-types.xhtml | Continuous (additions land monthly) |
| **NAICS 2022** (US industry codes) | 2F | CSV / Excel | https://www.census.gov/naics/?68967 | 5-year revision cycle |
| **UCUM** (units of measure) | 3A | XML (`ucum-essence.xml`) | https://ucum.org/ucum-essence.xml | Stable; minor errata |
| **FIX 4.4** (financial messaging) | 3B | XML reference dictionary | https://www.fixtrading.org/standards/fix-4-4/ | Frozen |
| **FIX 5.0 SP2** | 3B | XML reference dictionary | https://www.fixtrading.org/standards/fix-5-0-sp-2/ | Engineering drafts published; spec frozen |
| **GS1 General Specifications** (GTIN/GLN/SSCC, AIs) | 3C | PDF + JSON | https://www.gs1.org/standards/barcodes-epcrfid-id-keys/general-specifications | Annual |
| **UN/EDIFACT D.21B** | 3D | TXT (UN/CEFACT) | https://service.unece.org/trade/untdid/d21b/d21b.htm | Biannual |
| **HL7 FHIR R4** | 3E | JSON / XML (Definitions package) | https://hl7.org/fhir/R4/downloads.html | Frozen at R4; R4B is patches |
| **HL7 FHIR R5** | 3E | JSON / XML | https://hl7.org/fhir/R5/downloads.html | Active; minor releases |
| **ICD-10-CM** (US clinical modification) | 3F | XML / CSV | https://www.cms.gov/medicare/coding-billing/icd-10-codes | Annual cutover October 1 |

## Restricted (Phase 4 — gated on RP-1 license acquisition)

| Standard | Phase | License | Notes |
|---|---|---|---|
| WHO ICD-10 (translations) | 4A | CC-BY-NC-SA-3.0 (varies by translation) | English text is mostly open; non-English translations have local licensing |
| SWIFT MT/MX message types | 4B | SWIFT membership / spec license | Modern API sandbox: https://developer.swift.com/ |
| HL7 v2.x segments + value sets | 4C | HL7 organizational membership | v2.5.1 widely deployed |
| UMLS Metathesaurus mappings | 4D | NLM License Agreement (annual) | https://uts.nlm.nih.gov/uts/umls — free for research with user-of-record tracking |
| ISO 20022 financial messaging | 4E | Paid ISO standard | https://www.iso20022.org/iso-20022-message-definitions |

## Cross-standard mappings (RP-4)

| Mapping | Source | License | Phase |
|---|---|---|---|
| ICD-9 ↔ ICD-10 (CMS GEMs) | https://www.cms.gov/medicare/coding-billing/icd-10-codes | Public domain (US) | 3F via passive `mappings:` block |
| ICD-10 ↔ SNOMED CT | UMLS Metathesaurus | NLM-UMLS license | 4D mapping schema only (rows behind license) |
| FHIR ConceptMaps (R4 ↔ R5) | https://hl7.org/fhir/conceptmaps.html | Open | 3E `mappings:` block |
| ISO 4217 alpha ↔ numeric | Canonical source above | Open | 2A internal aliases (Extension D form) |

## Existing community ingests (RP-3 — adapt, don't reinvent)

| Standard | Reusable source | License | Status |
|---|---|---|---|
| ICD-10-CM | https://github.com/openhandsfoundation/icd-10-cm | MIT | Per-revision tags; usable for CSV→pack pipeline |
| FIX dictionaries | https://github.com/connamara/quickfixengine (`spec/`) | Apache-2.0 | Canonical XML field dictionaries per FIX version |
| ISO codes (4217, 3166, 639, 15924) | https://salsa.debian.org/iso-codes-team/iso-codes | LGPL-2.1 | JSON + translations; long-maintained |
| HL7 FHIR Definitions | Official `definitions.json.zip` per release | HL7 (CC0 for normative content) | Direct download, no community wrapper needed |
| MIME types | https://www.iana.org/assignments/media-types/media-types.csv | IANA — open | Direct CSV; no wrapper needed |

## Notes on update cadence handling

- **Annual or scheduled** (ICD-10-CM Oct 1, NAICS every 5 years): pack maintainers bump `derives_from.version` + `values_registry.hash`/`version` and re-publish a new pack version. Old packs continue to verify against their original standard revisions.
- **Continuous** (IANA Media Types, FHIR R5 patches): polled monthly; updates land as minor pack revisions.
- **Frozen** (FIX 4.4, ISO 8601): no maintenance burden once ingested.

## Pending work

- [ ] Capture `source_hash` SHA-256 for each artifact at ingest time (recorded in pack `derives_from.source_hash` per Extension C).
- [ ] Identify mirror/archive URLs for each authoritative source so a pack remains verifiable if the upstream URL drifts.
- [ ] Confirm exact license terms for each restricted corpus (RP-1 deliverable; gates Phase 4).
