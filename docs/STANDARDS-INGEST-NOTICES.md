# Standards Ingestion Attribution Master

> Resolves the `pack_attributions` hash-references that appear in
> `WitnessReceipt.pack_attributions` (Extension A). Every standards
> body or vocabulary registry that requires crediting in a published
> work has an entry below; the receipt records a hash reference, and
> downstream consumers resolve the reference here.
>
> The hash-reference rather than inline-text design prevents receipt
> bloat (a composition crossing many seams would otherwise carry many
> kilobytes of legal text per receipt). Receipts stay tamper-evident
> at the binding-strength of SHA-256.

## Conventions

Each entry has:
- A **hash reference** (left column) that pack files cite under
  `license.attribution`.
- A **standards body** and **canonical attribution string**.
- The **license** the upstream registry imposes (also recorded as
  `license.spdx_id` on the pack).
- **Notes** on commercial vs. research use distinctions.

The hash references below are short symbolic strings (e.g.
`sha256:iso-4217-notices`) rather than real SHA-256 of this file's
contents. A future iteration may bind to actual SHA-256 once this
document stabilizes; the symbolic form is sufficient for the
metadata-layer routing today.

## Open standards

### `sha256:iso-4217-notices`
**Standards body:** SIX (Maintenance Agency for ISO 4217)
**Attribution:** "ISO 4217 currency codes © ISO; reproduced under the
freely-redistributable convention for currency code usage in software."
**License:** Public domain in practice; see SIX terms.
**Pack:** `iso-4217.yaml`

### `sha256:iso-3166-notices`
**Standards body:** ISO (Maintenance Agency: ISO 3166/MA)
**Attribution:** "ISO 3166-1 country codes © ISO; codes themselves are
freely redistributable."
**Pack:** `iso-3166.yaml`

### `sha256:iso-639-notices`
**Standards body:** SIL International (registration authority for ISO 639-3)
**Attribution:** "ISO 639-3 language codes © SIL International; freely
redistributable per SIL terms."
**Pack:** `iso-639.yaml`

### `sha256:iana-media-types-notices`
**Standards body:** IANA
**Attribution:** "IANA Media Types registry; public domain."
**Pack:** `iana-media-types.yaml`

### `sha256:naics-2022-notices`
**Standards body:** US Census Bureau / Office of Management and Budget
**Attribution:** "NAICS 2022; US public domain."
**Pack:** `naics-2022.yaml`

### `sha256:ucum-notices`
**Standards body:** Regenstrief Institute / UCUM Organization
**Attribution:** "Unified Code for Units of Measure (UCUM)
© Regenstrief Institute; openly licensed for reference use."
**Pack:** `ucum.yaml`

### `sha256:fix-trading-community-notices`
**Standards body:** FIX Trading Community
**Attribution:** "FIX 4.4 / 5.0 SP2 message-type and field codes
© FIX Trading Community; freely usable under the FIX Protocol open
specification."
**Pack:** `fix-4.4.yaml`, `fix-5.0.yaml`

### `sha256:gs1-notices`
**Standards body:** GS1 Global Office
**Attribution:** "GS1 General Specifications © GS1; reference use of
Application Identifier codes is permitted under GS1 General Terms."
**Pack:** `gs1.yaml`

### `sha256:un-cefact-notices`
**Standards body:** UN/CEFACT
**Attribution:** "UN/EDIFACT directory codes © UN/CEFACT; public
domain."
**Pack:** `un-edifact.yaml`

### `sha256:hl7-fhir-notices`
**Standards body:** HL7 International
**Attribution:** "HL7 FHIR R4 / R5 specification © HL7 International,
Inc.; the FHIR specification is licensed under CC0-1.0 (normative
content) per the HL7 release notes."
**Pack:** `fhir-r4.yaml`, `fhir-r5.yaml`

### `sha256:cms-icd-10-cm-notices`
**Standards body:** CDC / NCHS (US) — clinical modification by CMS
**Attribution:** "ICD-10-CM © CDC NCHS; US public domain. CMS
publishes the annual code release."
**Pack:** `icd-10-cm.yaml`

## Restricted-source standards

The pack metadata is openly licensed (it's our own description of the
dimension structure). The licensed registries the packs point at are
the property of the listed bodies and require separate licenses to
fetch; consumers must obtain those licenses before
`bulla pack verify --fetch` will materialize the values.

### `sha256:who-icd-10-notices`
**Standards body:** World Health Organization
**Attribution:** "WHO International Classification of Diseases, 10th
Revision (ICD-10) © WHO. Translations are licensed individually;
reference English text is public access. The dimension metadata in
this pack is independently authored by Bulla; the licensed values
must be obtained from WHO."
**License:** CC-BY-NC-SA-3.0 (varies by translation)
**Pack:** `who-icd-10.yaml`

### `sha256:swift-notices`
**Standards body:** SWIFT (Society for Worldwide Interbank Financial Telecommunication)
**Attribution:** "SWIFT MT and MX message-type catalogs are SWIFT
membership-restricted property. The dimension metadata in this pack
is independently authored by Bulla; the licensed values must be
obtained via SWIFT membership."
**License:** Proprietary (membership-only redistribution)
**Pack:** `swift-mt-mx.yaml`

### `sha256:hl7-v2-notices`
**Standards body:** HL7 International
**Attribution:** "HL7 v2.x normative content © HL7 International,
Inc. Reproduction beyond personal use requires HL7 organizational
membership."
**License:** Proprietary (HL7 membership tier)
**Pack:** `hl7-v2.yaml`

### `sha256:nlm-umls-notices`
**Standards body:** US National Library of Medicine
**Attribution:** "UMLS Metathesaurus © National Library of Medicine.
The Metathesaurus is licensed under the NLM License Agreement;
redistribution requires user-of-record tracking. The mapping schema
in this pack is independently authored by Bulla; the licensed
mapping rows must be obtained via the UTS service under an active
NLM-UMLS license."
**License:** NLM License Agreement (annual; research-only by default)
**Pack:** `umls-mappings.yaml`

### `sha256:iso-20022-notices`
**Standards body:** ISO (Technical Committee TC 68/SC 9)
**Attribution:** "ISO 20022 message-definition catalog © ISO.
The full message catalog is paid; the dimension metadata in this
pack is independently authored by Bulla."
**License:** Proprietary (paid ISO standard)
**Pack:** `iso-20022.yaml`

## How a consumer resolves attributions

Given a `WitnessReceipt` with `pack_attributions: [..., "sha256:..."]`:

1. Read this file and look up each hash reference.
2. Surface the canonical attribution string somewhere visible in the
   downstream consumer's UI / report / publication. (Where exactly
   depends on the consumer — a paper might cite in a footnote; a
   user-facing dashboard might link to the standards body.)
3. If the receipt references a `restricted` pack, the consumer must
   either (a) hold the relevant license to materialize the registry
   values, or (b) explicitly note that the receipt is metadata-only
   verifiable.

A consumer that publishes work derived from a Bulla composition
should at minimum credit the standards bodies whose dimensions the
composition crossed. The receipt's `pack_attributions` makes this
machine-readable; the human-readable attribution lives in this file.

## Updating this file

When a new seed pack is added or a license changes:

1. Add (or update) the entry above.
2. Update the corresponding pack's `license.attribution` to the new
   hash reference (or reuse an existing one if the body is the same).
3. Re-run the per-pack integration test to confirm the receipt
   round-trip preserves the new attribution.
4. PR review confirms the license-text matches the upstream body's
   current terms.
