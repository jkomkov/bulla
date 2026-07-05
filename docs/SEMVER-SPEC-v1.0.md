# Semantic SemVer Specification v1.0

Last updated: 2026-05-11  
Status: Draft (G26 implementation track)

## Purpose

Semantic SemVer classifies interface updates by **witness-rank delta** rather than
schema-only shape changes.

Conventional versioning can miss semantically-breaking changes that keep JSON
schemas valid. Semantic SemVer provides a machine-checkable discipline for these
cases.

## Core record

`bulla certify-update OLD.yaml NEW.yaml --format json` emits:

```json
{
  "old_fee": 2,
  "new_fee": 4,
  "delta_r": 2,
  "coherence_preserving": false,
  "update_kind": "semantic-major",
  "minimum_bridge_delta": 2
}
```

## Field definitions

- `old_fee`: coherence fee on baseline composition.
- `new_fee`: coherence fee on updated composition.
- `delta_r`: `new_fee - old_fee`.
- `coherence_preserving`: `true` iff `delta_r <= 0`.
- `update_kind`:
  - `semantic-patch`: `delta_r <= 0`
  - `semantic-minor`: `delta_r == 1`
  - `semantic-major`: `delta_r >= 2`
- `minimum_bridge_delta`: lower-bound cardinality of additional bridge receipts
  needed to restore prior coherence class.

## CI usage

Use GitHub Action mode `semver`:

```yaml
- uses: ./bulla
  with:
    mode: semver
    old-path: compositions/pipeline_old.yaml
    new-path: compositions/pipeline_new.yaml
    fail-on-major: true
```

Outputs:

- `delta-r`
- `update-kind`
- `coherence-preserving`
- `old-fee`
- `new-fee`
- `minimum-bridge-delta`

Policy inputs:

- `fail-on-major`: fail workflow when `update-kind == semantic-major`
- `fail-on-minor`: fail workflow when `update-kind in {semantic-minor, semantic-major}`
- `max-delta-r`: integer threshold for hard fail when `delta-r` exceeds limit

Contract freeze for CI plugin hardening:

- Action outputs and CLI JSON fields must remain synchronized.
- Policy enforcement is first-class in the plugin surface (no custom shell
  parsing required for major/minor gates).
- CI failure semantics are explicit: "assessment succeeded" is distinct from
  "policy allows merge."

## Policy recommendations

- Block release when `update_kind == semantic-major` unless bridge receipts are attached.
- Require explicit reviewer signoff for `semantic-minor`.
- Allow `semantic-patch` updates under normal CI flow.

## Backward compatibility note

This v1.0 spec is additive to existing `check`, `audit`, and `certify` workflows.
It does not alter legacy command semantics.
