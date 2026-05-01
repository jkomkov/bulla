# Standards Pack Maintenance Guide

> Maintenance contract for the seed packs landed in the Standards
> Ingestion sprint (Phases 2–4). This document is the source of truth
> for who owns what, how drift is handled, and what regenerating each
> pack costs in operator time.
>
> Companion to [`STANDARDS-INGEST-SOURCES.md`](./STANDARDS-INGEST-SOURCES.md)
> (the canonical-source reference).

## Hash-state vocabulary

Every `values_registry.hash` is one of two forms:

- **`sha256:<64-hex>`** — a real content hash from an actual fetch.
  The verifier compares fetched content against this hash. Today,
  6 open packs carry real hashes (UCUM, NAICS 2022, ISO 639,
  IANA Media Types, FHIR R4, FHIR R5).
- **`placeholder:<reason>`** — a sentinel indicating the pack is
  structurally ready to verify but no real ingest has happened yet.
  Two reasons in use today:
  - `placeholder:awaiting-ingest` — open registry, hash will be
    computed by `compute_real_hashes.py` once the URL is fetchable
    in the build environment.
  - `placeholder:awaiting-license` — license-gated registry; a
    consumer holding the license will substitute the real hash
    after performing their own ingest.

The validator REJECTS literal `sha256:0...0` (a valid-shaped hash
that the verifier would silently treat as "checked, mismatched").
Use the placeholder sentinel instead.

Refresh the real-hash subset by running:

```bash
python scripts/standards-ingest/compute_real_hashes.py
# Then re-run any affected build_*.py and validate
for f in src/bulla/packs/seed/*.yaml; do python -m bulla pack validate "$f"; done
```

## What lives where

| Path | Role |
|---|---|
| `src/bulla/packs/seed/*.yaml` | Seed packs (committed artifacts) |
| `scripts/standards-ingest/build_*.py` | Generators (re-run to regenerate the YAML) |
| `calibration/data/incidents/*.yaml` | 30 historical-mismatch fixtures |
| `calibration/data/standards-ingest-results.json` | Phase 5 empirical-validation output |
| `tests/test_seed_*.py` | Per-tier pack acceptance tests |
| `tests/test_phase5_validation.py` | Headline-metric acceptance |

Every seed pack must:

- pass `bulla pack validate <path>` (schema clean)
- pass `bulla pack verify <path>` (registry pointer well-formed)
- have a generator script in `scripts/standards-ingest/`
- be referenced by at least one integration test
- carry `derives_from` provenance and a `license` block

## Per-pack maintenance ownership

The plan budgeted ≈1.0 FTE/year for the open set; this table calibrates
to the actual operational cost per pack family observed during ingest.

| Pack | Update cadence | FTE/year | Owner role | Notes |
|---|---|---|---|---|
| iso-4217 | Irregular (SIX as MA) | 0.02 | Maintainer | Add new currencies (e.g. ZWG 2024); deprecate withdrawn ones. |
| iso-8601 | Frozen (ISO 8601-1:2019) | 0.01 | Maintainer | RFC 3339 amendments rare. |
| iso-3166 | Annual (rare changes) | 0.02 | Maintainer | Country additions/withdrawals (~1/year typical). |
| iso-639 | Annual (SIL) | 0.02 | Maintainer | Mostly minor adjustments. |
| iana-media-types | Continuous (monthly additions) | 0.05 | Maintainer | Re-fetch CSV monthly; bump registry hash + version. |
| naics-2022 | 5-year (next 2027) | 0.01 | Maintainer | Heavy lift on revision boundary; near-zero between. |
| ucum | Stable (errata only) | 0.02 | Maintainer | Track ucum.org errata. |
| fix-4.4 | Frozen | 0.00 | Maintainer | No expected drift. |
| fix-5.0 | Engineering drafts | 0.05 | Domain reviewer | FIX EP releases on QA cadence. |
| gs1 | Annual (General Specs revision) | 0.05 | Maintainer | New AIs added each year. |
| un-edifact | Biannual (D.X[A/B] cycle) | 0.05 | Maintainer | Two new directory versions per year. |
| fhir-r4 | Frozen at R4; R4B patches | 0.05 | Domain reviewer | Errata + R4B patch releases. |
| fhir-r5 | Active minor releases | 0.30 | Domain reviewer | Most ongoing FHIR work happens here. |
| icd-10-cm | Annual (Oct 1 cutover) | 0.10 | Domain reviewer | Hard deadline; both old and new must verify. |
| who-icd-10 | Stable (ICD-11 emerging) | 0.02 | Domain reviewer | License-gated; coordinate with WHO licensee. |
| swift-mt-mx | Continuous (MT→MX migration) | 0.10 | Domain reviewer | License-gated; track Swift 2025 freeze. |
| hl7-v2 | Frozen at 2.5.1; v2.9 emerging | 0.05 | Domain reviewer | License-gated. |
| umls-mappings | Two releases/year (AA / AB) | 0.10 | Domain reviewer | License-gated; renews annually. |
| iso-20022 | Annual maintenance release | 0.05 | Domain reviewer | License-gated. |
| **TOTAL** | | **≈1.0 FTE/year** | | (≈0.5 open + ≈0.5 restricted) |

Two roles:
- **Maintainer** — engineer who runs the generator script, bumps
  versions, fixes failing tests. No domain knowledge required for the
  open set.
- **Domain reviewer** — signs off on semantic changes (FHIR resource
  rename, ICD-10-CM annual additions, FIX field-list deltas). Required
  for healthcare and finance packs because the changes touch
  vocabularies the maintainer can't audit alone.

## Drift-handling protocol

When an upstream standard publishes a new version:

1. **Bump the generator script** to the new source URI / version.
2. **Re-run the generator**: `python scripts/standards-ingest/build_<pack>.py > src/bulla/packs/seed/<pack>.yaml`
3. **Update the `derives_from.version`** to match the upstream label.
4. **Update `values_registry.hash`** if a real registry hash is available; placeholder otherwise.
5. **Run `bulla pack validate` and `bulla pack verify`** locally.
6. **Run `bulla pack status`** to confirm `derives_from` reads correctly.
7. **Run the per-pack integration tests** (`pytest tests/test_seed_<pack>.py`).
8. **Run the full `test_phase5_validation.py`** — confirms the new pack version still produces ≥80% incident detection.
9. **Bump the pack's own `pack_version`** (semver-ish: 0.1.0 → 0.1.1 for additive value adds; 0.2.0 for renaming, deprecation, or schema changes).
10. **Open a PR.** Two-eyes review required for any pack with `registry_license: research-only` or `restricted` (defense-in-depth on the metadata-only invariant).

Old receipts continue to verify against their original pack revision
because `PackRef.hash` binds the exact pack content; new receipts use
the new pack hash. **No receipts are invalidated by a pack update —
they refer to the pack revision that was active when they were
issued.**

## What `bulla pack status` surfaces at load time

After every PR that touches a seed pack, an operator should run:

```bash
for p in src/bulla/packs/seed/*.yaml; do
  python -m bulla pack status "$p"
done
```

This reports per-pack:

- `pack_name`, `pack_version`
- `derives_from.standard` and `version`
- `license.spdx_id`, `license.registry_license`
- registry pointer count + license_id per pointer
- mappings table count + row counts

Drift surfaces as either: (a) `derives_from.version` not matching the
authoritative source URL, or (b) `registry_license` flipping from
`open` to `restricted`. Either is a PR-review red flag.

## Restricted-corpus governance (Phase 4)

The five restricted-source packs (`who-icd-10`, `swift-mt-mx`,
`hl7-v2`, `umls-mappings`, `iso-20022`) ship as **metadata-only** —
the dimension structure, field patterns, and license metadata are
public; the actual licensed values stay behind the
`values_registry` pointer.

The metadata-only invariant is enforced at three layers:

1. **Validator** (`packs/validate.py`): a pack with
   `registry_license: research-only` or `restricted` cannot ship
   inline `known_values` on a dimension that also has a
   `values_registry` pointer.
2. **CI gate**: every PR runs `tests/test_seed_phase4_restricted.py`
   which re-checks the audit at the file level (`git grep`-equivalent).
3. **Pre-release audit**: a one-line check that all 5 restricted
   packs have zero inline values across all licensed dimensions.

To add a new restricted pack:

1. Decide `registry_license`: `research-only` if non-commercial use is
   permitted with a credential; `restricted` if any access requires a
   paid license.
2. Set a unique `license_id` on every `values_registry` pointer (e.g.
   `WHO-ICD-10`, `NLM-UMLS`, `SWIFT-MEMBER`).
3. Add a generator function in `build_restricted.py` (or a new file).
4. Add an integration test parameterized by pack name in
   `test_seed_phase4_restricted.py`.
5. Update this document's per-pack ownership table.

## Pack-contribution governance

External contributions land via PR. Contribution checklist:

- [ ] Generator script in `scripts/standards-ingest/`.
- [ ] Pack YAML in `src/bulla/packs/seed/`.
- [ ] Integration test in `tests/test_seed_*.py`.
- [ ] `derives_from` provenance pointing at the authoritative source.
- [ ] `license` block with the correct `registry_license`.
- [ ] One-paragraph PR description explaining: which standard, why
      this pack, what dimensions it adds, what historical incidents
      (if any) it makes detectable.
- [ ] If `registry_license` is `research-only` or `restricted`: zero
      inline values on licensed dimensions; two-eyes review.

Maintainer-only changes (no review burden):
- Bumping `pack_version` after re-running an existing generator.
- Updating `derives_from.version` after an upstream revision.
- Adding new aliases or `source_codes` entries within an existing
  alias-form `known_values` item.

Reviewer-required changes:
- Adding a new dimension to a pack.
- Renaming a canonical value (breaks classifier signals downstream).
- Changing `registry_license` from `open` to anything else.
- Modifying the validator (`packs/validate.py`).

## Quarterly maintenance rotation

Suggested cadence for the maintainer role (aggregate ≈0.5 FTE on the
open set):

- **Month 1 (annual ICD-10-CM cutover)**: regenerate `icd-10-cm.yaml` for
  the new fiscal year; re-run incident detection; update
  `STANDARDS-INGEST-SOURCES.md`.
- **Months 2, 5, 8, 11**: poll IANA Media Types CSV; re-run
  `build_iana_mime.py` if changed.
- **Months 3, 9**: regenerate FIX 5.0 for new EP releases; FHIR R5
  patch releases.
- **Months 4, 10**: GS1 General Specifications annual revision check.
- **Months 6, 12 (D.21A / D.21B / D.22A...)**: UN/EDIFACT directory
  refresh.
- **Month 7**: ISO 3166 / ISO 4217 / ISO 639 minor refresh.

## Anti-CDM commitment (deferred — see plan)

The deferred Part B (self-learning canonical pack via cohomological
MDL on aggregated receipts) is **not** in this sprint's scope. The
seed packs ship as themselves; there is no merged "canon" published
by Phase 6. The anti-CDM-commitment-in-writing document belongs to
the follow-on plan and is gated on (a) Hans's resolution of the
colimit-obstruction question and (b) ≥1000 real composition receipts
accumulating.

This Phase 6 governance documents Part A's pragmatic posture:
**maintainer authors seed packs, community contributes seed packs
with provenance, the fee math evaluates them empirically.**

## Verification

Every claim in this document is testable; the relevant tests:

- Per-pack integration: `tests/test_seed_iso_4217.py`, `test_seed_tier_a.py`, `test_seed_tier_b.py`, `test_seed_phase4_restricted.py`
- Incident detection: `tests/test_seed_incidents.py`
- Phase 5 acceptance: `tests/test_phase5_validation.py`
- Validator invariants (license, values_registry, metadata-only): `tests/test_pack_license.py`, `test_pack_values_registry.py`, `test_pack_derives_from.py`, `test_pack_aliases.py`, `test_pack_mappings.py`

Running the full sprint test set:

```bash
python -m pytest tests/test_pack_license.py \
    tests/test_pack_values_registry.py \
    tests/test_pack_derives_from.py \
    tests/test_pack_aliases.py \
    tests/test_pack_mappings.py \
    tests/test_seed_iso_4217.py \
    tests/test_seed_tier_a.py \
    tests/test_seed_tier_b.py \
    tests/test_seed_phase4_restricted.py \
    tests/test_seed_incidents.py \
    tests/test_phase5_validation.py
```

Expected count: 250+ tests, all green.
