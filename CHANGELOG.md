# Changelog

## 0.43.0 — 2026-07-13

**The v0.2 receipt: one canonicalization rule, and the convention field.** The trunk story is now the minimal recomputable receipt a stranger verifies from the spec directory and a Python stdlib — no bulla install, no server, no trust in this repo's prose.

### Erratum (0.42.0)
The 0.42.0 entry below describes "One canonicalization rule (`CANON_VERSION` 1 → 2)" — including `bulla._canonical.canonical_json`, the legacy-acceptance fallback, `tests/test_canonicalization.py`, and a `WITNESS-CONTRACT.md` canonicalization section — as shipped. **It had not shipped**: 0.42.0's code still hashed the measurement layer with spaced separators and contained none of those artifacts. Parts of the same entry's CI paragraph (the `[test]` extra, the 90% coverage floor, the `ruff`/`mypy` lint job, full-SHA action pins, the publish-time test gate) also did not match the shipped tree. The canonicalization work ships **here**, in 0.43.0, with the publish-time test gate; the remaining CI items are open work, not shipped features. The changelog is a record, so the false entry is corrected rather than rewritten.

### Canonicalization (`CANON_VERSION` 2 — actually shipped)
- `bulla._canonical.canonical_json` (compact, key-sorted; RFC 8785-compatible with two documented deviations) single-sourced across the action-receipt, certificate/deed, envelope, refusal, and witness layers.
- Witness `receipt_hash` moves spaced → compact and stamps `canon_version: 2` inside the hash. `verify_receipt_integrity` accepts the legacy spaced form; new `receipt_integrity_report` names which form matched — a format change is a version difference, not tampering.
- Deed-layer and action-receipt hashes byte-unchanged (that layer was always compact); gated by `tests/test_canonicalization.py` (byte-exact vectors both layers, legacy path, releases corpus).
- Normative "Canonicalization" section added to `WITNESS-CONTRACT.md`; `Composition.canonical_hash()` / `Diagnostic.content_hash()` stay on their original serialization as pinned input identities (a dated CANON_VERSION-3 decision).

### ActionReceipt v0.2 (`spec/action-receipt-v0.2.md`, normative)
- **`conventions`** — rules two parties coin at the seam, committed inside the content hash (neither mutable nor strippable without breaking it). `kind` is the decidability boundary: `executable` (the closed `jsonschema+quantum/1` form; conformance recomputed by any verifier against `action.subject`) vs `semantic` (opaque, hash-pinned, forum required under the envelope's Pin-the-Root law; reported `pinned`, never "checked"). Per ADR-001 the global convention graph is emergent, never an operated product. Also available on `WitnessReceipt` (conditional-include, backward-compatible).
- **Evidence grounding classes** (`self_asserted` / `counterparty_signed` / `third_party_anchored` / `execution_verified`), required per evidence ref in v0.2; verifiers surface the **minimum over necessary evidence** — a digest-valid receipt on self-asserted evidence is attested testimony, nothing more.
- Any-log verification and standing recomputability written in as normative principles (ADR-001); tolerance semantics (`verification_semantics`) specified as a claim-level carrier that changes nothing about byte-equality at the receipt layer.
- v0.1 receipts verify forever with their own `schema_version`; the golden vectors gate this.

### Receipt-first defaults (NOT yet the diet)
- README and CLI re-led on the receipt: create → verify → coverage. The coherence fee is one optional diagnostic a receipt can carry, not the trunk story. **Honest label:** this release changes defaults and framing only — the fee/diagnostic mass (`coboundary`, `guard`, geometry, …) still ships in the core install, which is *larger* than 0.42.0, not smaller. The actual diet (extraction behind a `bulla[fee]`-style extra so the default install IS the receipt) remains owed, dated open (2026-07-13).
- `bulla receipt create` — the missing ergonomic: mint a signed-or-unsigned ActionReceipt from flags, no Python required.
- `bulla gate --require-fee` now defaults to **not** gating on fee (report, don't block) — removing the last default that treated the fee as an execution predictor (FALSIFICATIONS.md); `--require-fee N` remains the explicit opt-in.

### Vectors & the stranger test
- `spec/vectors/` adds a CANON-2 WitnessReceipt, a convention-carrying ActionReceipt (executable + semantic), and a legacy-v1 witness vector that must verify via the fallback.
- `spec/vectors/independent_check.py` extended to the new vectors — witness hashes (both canon forms), convention pins, and executable conformance recomputed with zero bulla imports.
- Release ceremony: `publish.yml` now runs the full suite on the exact tagged commit and mints the release receipt **at** release time (signed when a release key is configured, OTS-anchored best-effort) — closing the retroactive-receipt seam recorded in `releases/reconstruct.py`. Scope honesty: a receipt bulla mints, signs, and verifies for its own release is dogfooding (the operator vouching for the operator); the independence claim rests on `independent_check.py`, not on this receipt.

## 0.42.0 — 2026-07-08

**The integrity-of-the-integrity-tool release, and the relicense to Apache-2.0.** A world-class-systems-engineer audit found the craft strong but flagged a cluster of "the integrity tool has integrity gaps" defects — individually cheap, collectively credibility-defining. This release closes them and moves the project to a license fit for neutral infrastructure.

### License
- **Relicensed BSL 1.1 → Apache License 2.0** — permissive, OSI-approved, with an explicit patent grant, from day one. A transparency substrate others recompute and reimplement cannot sit behind a single gatekeeper, and the repo already called Glyph an "open standard." Added `NOTICE`; `pyproject` now carries the SPDX `License-Expression: Apache-2.0`. Contributions are inbound=outbound via a **DCO** sign-off (`git commit -s`) — no CLA.
- **Owner of record unified to John Komkov** across LICENSE, NOTICE, `pyproject` authors, `action.yml`, and the deprecated `seam-lint` shim; email normalized to `jk@gvt.ai`.

### Integrity & determinism
- **One canonicalization rule (`CANON_VERSION` 1 → 2).** Every hash is now taken over `bulla._canonical.canonical_json` (compact, key-sorted, ASCII), single-sourced and imported everywhere — no more copy-paste. The measurement layer previously hashed with *spaced* separators while the deed layer used *compact*, so a stranger following the spec could not reproduce a `WitnessReceipt` hash. Deed-layer hashes are byte-unchanged (releases, `spec/vectors`, certificates verify identically); the measurement layer moved to compact. **Survival preserved:** `verify_receipt_integrity` accepts the legacy v1 form, so every pre-0.42 receipt still verifies (a format change is a version difference, not tampering). Drift-guarded by `tests/test_canonicalization.py`. Normative "Canonicalization" section added to `WITNESS-CONTRACT.md`.
- **Single source of version truth.** `pyproject` version is now `dynamic`, read from `src/bulla/__init__.py`; `tests/test_version_single_source.py` asserts the built metadata equals `__version__` (version is stamped into every receipt's provenance, so drift was a provenance defect).

### CI, gates & supply chain
- **CI actually enforces now.** The `tests` job installs a new `[test]` extra so the signing/forgery/registry-authenticity suite *runs* (not ImportError) and the numpy instrument test is exercised; a **coverage floor** (90%) guards the crypto/receipt core; a new **`lint` job** runs `ruff` (blocking) and `mypy` on the core allowlist (config added — none existed).
- **Release path hardened.** `publish.yml` now runs the full suite on the exact tagged commit before a signed wheel can ship (no green, no ship), and **every third-party action in both workflows is pinned by full SHA** — including `gh-action-pypi-publish` in the attestation-minting job.
- **`SECURITY.md`** added (private disclosure, coordinated fix) — a security-attestation product finally has a disclosure channel.

### Governance
- Added `GOVERNANCE.md` (BDFL-now, with an explicit path to open governance of the *standard*), `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS` (naming the owner of each integrity-critical surface), issue/PR templates, and `dependabot.yml`.

## 0.41.0 — 2026-07-04

**ActionReceipt v0.1 — the receipt a bond slashes against.** Bulla's diagnostic layer answers "is this composition coherent?"; this release adds the object that answers the next question: *an agent changed the world — under whose authority, within what bounds, with what recomputable verdict, and how is it contested?*

- **`bulla.action_receipt`** — one `ActionReceipt` envelope (the single new abstraction). A release *is* a tool call, so `action.type` is open vocabulary (`package.release`, `github.create_file`), not a family of types. Builders `build_release_receipt` / `build_tool_call_receipt`. Fields: `action`, `mandate` (authority+bounds — ex ante legitimacy), `remedy` (challenge/forum/remedies — ex post contestation, reusing the `RecourseEnvelope` modality law), a `diagnostic_ref` that is **never bare null** (the recomputable verdict — the differentiator), `evidence_refs`, `retention` (the civic asymmetry as classes), a **reserved** `stake` slot (the bond), and four hashes each answering one question: `content` (recompute the verdict), `event` (which occurrence), `attestation` (who vouched), `log_leaf` (where logged, RFC 6962).
- **`bulla receipt verify <file>`** — one verifier over `{action_receipt, witness_receipt, certificate}` that reports `verified_to: digest | attestation | log_inclusion` rather than a lying boolean. A forged receipt whose hashes were recomputed still fails at `attestation` (the signature is over `content`).
- **`bulla coverage --anchor git`** — omission detection: coverage as a plain set difference against a declared anchor (never a bare, gameable percentage). Run on bulla's own history it finds a real gap: 0.37.0 shipped to PyPI with no git tag.
- **Wire spec + JSON Schema + golden vectors** (`bulla/spec/`) with a stdlib-only `independent_check.py` that imports no bulla — a second implementer can verify a receipt from the spec alone.
- **Cross-boundary bonded transaction demo** (`bulla/examples/cross-boundary-bond/`): a stateless agent posts a bond, acts, and vanishes; a bystander recomputes the verdict from pinned inputs and slashes — no oracle. The senior tranche slashes on the objective trigger (the fee); pricing severity is the junior tranche (deferred).
- **`ActionReceipt → EU AI Act Article 12`** field-level mapping (`bulla/docs/`), scoped as *traceability*, never *compliance*.

## 0.40.1 — 2026-07-03

Documentation and packaging patch — no library behavior changes. An external reviewer ran the 0.40.0 README against the wheel and found the flagship `Session` example did not execute: it called `add_tool(name, fields=…, conventions=…)` and `add_edge("a","b")`, but the real API is `add_tool(spec: ToolSpec)` / `add_edge(edge: Edge)`. The `translate` example used `to_convention="numeric"` (the registered convention is `iso-4217-numeric`) and read a non-existent `evidence.kind`. Fixed, and — taking the package's own medicine — the README is now gated as a composition: `tests/test_readme_examples.py` extracts every fenced `python` block and runs it as a standalone script against the installed wheel, so doc↔code drift fails CI. Blocks that genuinely need external state (a live MCP server, a framework extra, an object built in prose) opt out with an HTML-comment marker that is invisible in rendered Markdown but auditable in source — the gate can loudly skip a marked block, never silently pass a broken one. Three flagship blocks (`translate`, `Session`, the Python API) now run green.

### Fixed
- README `Session` example corrected to the real `ToolSpec` / `Edge` constructors.
- README `translate` example corrected to `to_convention="iso-4217-numeric"` and the real `TranslationEvidence` attributes (`from_convention`, `equivalence`).
- README Python-API example made self-contained (`load_composition(text=…)`) so it runs without an external file.

### Added
- `py.typed` marker — the package is annotated; consumers' type checkers now see it as typed (PEP 561).
- `tests/test_readme_examples.py` — the README-as-composition gate, wired into root CI.

### Changed
- The `g23-a3` optional extra is renamed to `interp-sae` (the old internal codename leaked into the public install surface). `pip install bulla[g23-a3]` still resolves for one release via a deprecated alias.

## 0.40.0 — 2026-07-03

The recourse-layer release. Where 0.37.0 measured compositional obstruction, 0.40.0 makes the record of it **contestable**: a signed, recomputable coherence deed that any party re-derives from pinned inputs (`deed = f(composition@h, algorithm@v)`), an append-only RFC-6962 registry whose omission is *checkable* and whose root cannot be forged by the host serving it, an in-proxy gate that refuses an unreceipted cross-owner side effect before it happens and names the cure, and — new in this release — a per-deed **recourse envelope** whose every remedy attaches to an artifact or a stake rather than to a vanished actor. Plus a GF(2) fast path that takes the full-registry audit from ~22 min to ~17 s with bit-identical fees, and the live MCP proxy hardened into a safety co-pilot. The keystone is unchanged and now shipped end to end: verification is cheap; recourse is scarce.

### New optional extras

- **`bulla[identity]`** (`PyNaCl`) — sign coherence certificates under an ed25519 / `did:key` identity the agent already holds. Bulla signs, never mints.
- **`bulla[scitt]`** (`cbor2`, `PyNaCl`) — serialize a deed as a COSE_Sign1 Signed Statement (RFC 9052) for SCITT-shaped transparency services. See `SCITT-MAPPING.md`.
- **`bulla[ots]`** (`opentimestamps-client`) — anchor a deed's attestation hash to the Bitcoin timechain (closes backdating).

### Added — the recourse stack

- **Signed coherence deeds + registry** (`bulla.identity`, `bulla.certificate`, `bulla.registry`, `bulla.http_registry`). A signed `CompositionCertificate` is a deed; `DeedLog` is an append-only RFC-6962 Merkle log with inclusion + consistency proofs. `bulla registry serve` exposes a read-only HTTP registry; `HttpRegistry` + `bulla verify --registry` consume it.
- **Pin-the-Root** — the omission rung made trustless against a malicious host: an inclusion proof is only honored against a root the consumer pins independently (own log / `--trusted-root` / an OTS-anchored root); a host-asserted root is refused, an equivocating root is refused.
- **`bulla__deed_{emit,verify,lookup}`** in-loop MCP meta-tools (`bulla.live_proxy`): sign+log the current composition's deed at machine speed; demand inclusion against an independently-trusted root; look up deeds by composition hash.
- **The recourse gate** (`bulla.recourse_gate`, `bulla gate`) — OBSERVE → ENFORCE. Refuses a cross-owner `tools/call` whose counterparty deed is not authentic, included under a pinned root, and certifying `fee = 0`, emitting a signed, recomputable `RefusalCertificate` that names the disclosure cure — before the backend is ever touched.
- **Deed v0.2 — the recourse envelope** (`bulla.envelope`): optional `authority` (delegation chain to a surviving principal + `policy@hash`), `bounds` (scope / expiry / rollback), and `recourse` (challenge window, Pin-the-Root forum, and remedies) fields, plus `retention_class` / `disclosure_class` stubs. Committed inside the attestation hash, excluded from the content hash — so a v0.1 deed hashes byte-identically and the recomputable core stays pure. Every remedy is `{rung, verifier, anchor}` on the ladder recompute → challenge → cure → revert → slash → escalate; a remedy that names no stateful anchor cannot be constructed, and a hash-correct envelope that violates that rule is refused on read.
- **Gateway modes** (`bulla proxy --shadow` / `--enforce`, `bulla.side_effects`) — side-effect classification (read/notify/write/commit from MCP `ToolAnnotations`, conservative default: unknown ⇒ write). Shadow mode emits a signed per-call deed with a recourse envelope for every side-effecting call and never blocks; enforce mode gates side-effecting calls only (`--gate-reads` to gate everything).
- **Recourse-conformance v0** (`bulla.conformance`, `python -m bulla.conformance.scenarios`) — 24 named scenarios (recompute / log / appeal / cure / gate) a relying party can run against a host-controlling adversary.
- **`bulla certify-cost`** — a Coherence Cost Certificate: the coherence floor (the fee) and its witness fields, and with `--observed-cost` the portion of an intermediary's charge not required by coherence itself.

### Added — live proxy, diagnostics, and refinement types

- **Live MCP proxy** (`bulla proxy`) as an agent safety co-pilot: injects `bulla__{fee,blind_spots,bridge,should_proceed,why}`; `bulla__why` returns Aristotle-verified formal provenance. Unified `tools/call` telemetry (JSON-Lines, credential-redacted) and an adversarial consequence-analysis pipeline.
- **Per-dimension fee decomposition** with the additivity theorem, surfaced through `bulla__blind_spots`.
- **`bulla.lambda_nabla`** — the λ_∇ cohomological-refinement-types elaborator (research surface for the B3 paper).

### Performance

- **GF(2) fast rank** where the coboundary is totally unimodular: the full-registry coherence audit drops from ~22 min to ~17 s, with fees bit-identical to the exact-rational path.

### Fixed

- Reap the whole MCP-server process tree on proxy/scan shutdown (no orphaned backends).
- Bundle the showcase MCP manifests into the wheel so `bulla showcase` runs from a `pip install`.
- Registry read-side self-auditability, verified-submission-boundary scoping, and borrowed-inclusion binding (an inclusion proof for deed A can no longer authenticate deed B).

### Consistency

- `LICENSE` "Licensed Work" version synced to the release (was lagging at v0.34.0).
- `scitt` added to the `all` extra.

## 0.37.0 — 2026-05-03

Consolidates seven post-0.36.0 sprints into a single publishing event. Headline additions: `bulla.translate` (typed runtime translators), `bulla.Session` (online incremental composition), `bulla.LiveSession` (online MCP proxy), native `bulla.langgraph` and `bulla.crewai` adapters with callback handlers, narrative `bulla scan` with a 39-entry dimension explanation registry and auto-detect across seven hosts (Cursor, Claude Code, Claude Desktop, Cline, Windsurf, Zed, Codex), `bulla certify` per-composition certificate including the v1.0 schema with claim-structured assertions and a stable `certificate_content_hash`, the composition-only obstruction demo (3-tool synthetic where every pairwise certificate certifies fee=0 yet the global certificate finds fee=1), and the Dimension Pack Enhancement (all 11 fetchable open packs on real SHA-256, classifier corpus 880 → 27,651 rows, 10 → 28 firing dimensions). 124 new tests for framework adapters, 46 for translators, 21 for `LiveSession`, 18 + 29 + 8 + 2 for certificates and the obstruction demo, 6 corpus-growth gates, 5 provenance invariants, plus the load-bearing 10,000-seed property test that pins `Session` to bitwise equality with a from-scratch `witness_gram` recompute.

The detailed sprint logs follow as seven subsections.

### Sprint 15: Composition-Only Obstruction Demo

A 3-tool deterministic synthetic where every pairwise v1.0 certificate
certifies `coherence_fee == 0` AND `exact_disclosure_equivalence` is
certified, while the global certificate (with parent hashes pinned to
the pairwise content hashes) discovers `coherence_fee == 1` and
retracts the disclosure-equivalence claim. **Local witnesses are
evidence, not proof of global validity.**

### Added

- **`bulla.certificate.certify(parent_certificate_hashes=...)`** — Sprint 15
  extension to populate the v1.0 reserved slot. Adding parents changes
  the certificate's `certificate_content_hash` (per the discipline
  sentence: hash changes under parent changes), structurally binding
  parentage to identity. Default `()` preserves Sprint 14 behavior.
- **Sprint 15 demo fixture + runner** at
  `papers/composition-doctrine/sprint15_demo/`:
  - `fixture.py` — canonical 3-tool hub-and-spoke (DFD-respecting):
    A.p observable; B.p, C.p hidden; edges A→B and A→C with
    `from_field=p, to_field=p`.
  - `runner.py` — deterministic non-LLM CLI script. Builds 3 pairwise
    certs, then 1 global cert with parent hashes set to the pairwise
    content hashes. Prints terminal receipt; writes `output.json`.
    Exit 0 iff trigger AND discipline checks fire.
  - `report.md` — one-page report. Tagline: *"Local witnesses certified
    zero fee; the global witness found obstruction."* Mechanism:
    *"Projection can collapse distinct global obligations into identical
    local observations."*
- **Phase 0 search script** at
  `bulla/calibration/scripts/sprint15_search.py`. Synthetic + 80-triple
  registry scan. Synthetic hub-and-spoke triggers for k ∈ {2..5} spokes;
  registry triples did not naturally trigger (real-MCP triples have
  nonzero pairwise fees). Top-pick was 3-tool 2-spoke (smallest, with
  `pair_exact_conservative=True` bonus).
- **8 regression tests** in `bulla/tests/test_sprint15_demo.py`
  including the rank-story regression (`rank_obs=1, rank_internal=2,
  fee=1`), trigger condition, parent-hash chain, anti-overclaim guard
  against `bundle_composes_globally` / `global_validity_implied_by_parents`,
  and end-to-end runner CI gate.
- **2 unit tests** added to `bulla/tests/test_certificate.py` for the
  new `parent_certificate_hashes` kwarg (slot population +
  content-hash sensitivity).

### What this sprint does NOT do

- ❌ No `WitnessBundle` class, no incremental recertification, no
  LiveSession integration (Sprint 16+ work).
- ❌ No signing or anchoring of certificates.
- ❌ No `bulla repair` command.
- ❌ No claim asserts that local certificates imply global validity. The
  `bundle_composes_globally` claim slot is reserved but explicitly NOT
  emitted in v1.0; its semantics depend on Sprint 16+ bundle-merge logic.
- ❌ No new theory, no Lean modules, no new corpora.
- ❌ Real-MCP triples did not produce the trigger in Phase 0's 80-triple
  scan — this is a synthetic demo of the principle, not a real-MCP
  artifact.

### Sprint 14: Witness-Ready Certificate Schema (v1.0)

Bumps the per-composition certificate schema to v1.0 with structured
top-level blocks, claim-structured assertions, a stable
`certificate_content_hash`, and reserved slots (`parent_certificate_hashes`,
`issuer`, `signature`, `supersedes`, `attestation_hash`, `receipt_hash`)
for future incremental-bundle / signing / supersession infrastructure. **This is a deliberate schema
bump; v0 byte-shape from Sprint 13 is intentionally NOT preserved.**

### Schema v1.0 layout

Eighteen top-level keys in canonical order: `certificate_schema_version`
(literal "1.0"), `subject`, `method`, `regime`, `diagnostic`, `claims`,
`scope`, `parent_certificate_hashes`, `issuer`, `signature`,
`supersedes`, `violations`, `display`, `timestamp`, `bulla_version`,
`certificate_content_hash`, `attestation_hash`, `receipt_hash`.

### Claims block (six structured assertions)

Each claim is `{value, status, licensed_by[, not_licensed]}`. `status`
∈ `{"certified", "candidate", "not_certified", "not_applicable"}`.

- `schema_shape_valid` — Sprint 9 structural predicate.
- `fee_is_nonnegative`, `fee_is_interpretable` — Sprint 8 measured rank
  predicate (kept as separate claims for forward compat; identical
  derivation in v1.0).
- `exact_disclosure_equivalence` — Sprint 11/12 theorem regime claim.
  Refined during implementation: requires BOTH `is_well_formed_for_fee`
  AND `is_exact_regime_conservative` (the structural-conservative
  predicates alone aren't enough — an ill-formed composition can satisfy
  DFD+CHP yet have negative fee).
- `repair_basis_status` — bridge to Sprint 15+ repair logic. When
  `candidate`, the `not_licensed` field explicitly carries
  `exact_disclosure_equivalence` to make the regime gap visible.
- `subject_bound` — internal-consistency claim.

### `certificate_content_hash` discipline

- Format: `"sha256:<64 hex chars>"`. Prefix mandatory.
- SHA-256 over `json.dumps(canonical_dict, sort_keys=True, separators=(',',':'))`.
- Excluded from preimage: `timestamp`, `signature`, `certificate_content_hash` itself, `attestation_hash`, `receipt_hash`, and `display` (UI-only — wording edits must not invalidate parent-cert hashes).
- Determinism gate: two `certify()` calls on the same composition produce
  identical hashes.
- Sensitivity gate: different compositions produce different hashes.
- Signature-invariance gate: changing `signature` post-hoc does NOT change
  the hash.

### What's reserved but not implemented

`parent_certificate_hashes`: empty list (Sprint 15+ incremental bundles).
`issuer.id`, `signature`: null (future signing). `supersedes`: null
(future supersession). `pack_stack_sha256`, `manifest_hashes`: null/[]
(future pack-aware certify).

### Tests

29 tests in `bulla/tests/test_certificate.py` (schema version, hash
determinism + sensitivity + signature-invariance, claim coverage across
all 4 status enum values for the regime lattice, display back-compat,
method versioning, JSON round-trip, multi-server cross-decomposition,
witness-geometry on/off, schema-shape violation propagation, Sprint
11/12 discipline anti-overclaim). Plus 3-test canonical seed-set
regression in `bulla/tests/test_sprint13_seed_certificates.py`,
regenerated under v1.0.

### Out of scope

No signing implementation. No issuer DID / key infrastructure. No
supersession logic. No parent-certificate computation, bundle merging,
or `LiveSession` integration. No `bundle_composes_globally` claim
(deferred — pairwise validity does NOT imply global validity, and we
don't claim it does until Sprint 15+ implements bundle merging). No
`bulla repair` command, no demo, no composition-only obstruction
artifact, no Lean modules, no new corpora.

### Awareness-Gap Sprint (`bulla scan` narrative output)

The framework integration sprint and the indispensability push made
the substrate mature. This sprint flips the default `bulla scan`
output from JSON-receipt to plain prose so a first-time user reads
the diagnosis in under 10 seconds.

### Changed — `bulla scan` defaults to narrative output

- New `--format narrative` (default for `bulla scan`) renders a
  prose block: header naming the config and servers, headline fee,
  per-blind-spot explanations from a 39-entry dimension registry,
  and the pairwise-vs-global comparison block.
- `--json` is a shortcut for `--format json` (the prior programmatic-
  consumer path).
- The legacy mathematician-grade `text` format with β₁/H¹/δ₀ rank is
  preserved under `--format text` for power users.
- `--no-pairwise` skips the pairwise comparison; defaults to running
  it for compositions of 2–8 servers.

### Added — `bulla.explanations`

- 39-entry plain-language registry covering every dimension that
  `Diagnostic.blind_spots[i].dimension` can carry. Each entry is one
  sentence of explanation plus one sentence of failure mode, written
  with concrete examples.
- `tests/test_explanations.py` locks the registry to the dimension
  universe: every name in `src/bulla/packs/{seed,community}/*.yaml`
  plus every hardcoded pattern in `bulla.infer.classifier` must have
  an entry. CI fails when a new pack dimension is added without an
  explanation.
- `explain(dimension)` falls back gracefully on unknown names and
  strips the `_match` suffix that `compose_multi` appends to edge-
  inferred dimension names.

### Added — `bulla.scan_format`

- `format_scan_narrative(diagnostic, server_names, ...)` is a pure
  string formatter over a `Diagnostic`. It runs no I/O.
- The pairwise-vs-global block (the moat case) fires only when
  `global_fee > 0 AND max(pairwise_fees) == 0` — the exact regime
  where pairwise type-checking literally cannot find what bulla
  finds. Suppressing the block in any other case keeps the output
  focused.
- Cross-server seam filtering. With multiple servers, narrative
  output prioritizes blind spots whose endpoints span two distinct
  server prefixes (the `<server>__<tool>` convention). Within-server
  drift is real but isn't the awareness-gap story.
- Display cap of 8 blind spots per scan with a footer pointing at
  `--json` for the full list. Real compositions can produce 50–100+;
  truncation keeps the prose readable.

### Fixed — Claude Code auto-detect

- The Claude Code host detector now checks `~/.claude.json` (the
  canonical user-scoped config in current builds), with fallbacks to
  `~/.claude/settings.json` (older builds), `<cwd>/.mcp.json`
  (project-scoped canonical), and `<cwd>/.claude/settings.json`
  (workspace overrides).
- The parser walks `projects[<cwd-prefix>].mcpServers` so a scan
  run inside a project subdirectory finds the project's servers,
  not just the (often empty) top-level `mcpServers` block. Longest
  matching prefix wins.

### Added — Awareness-gap demo

- New `examples/awareness-gap-demo/` bundle: `repro.py`,
  `manifests/{filesystem,github}.json`, `README.md`,
  `requirements.txt`.
- `repro.py` runs deterministically without LLM, npm, or live MCP
  servers. It simulates GitHub's path-validation failure with the
  filesystem server's absolute-path output, runs `bulla.compose_multi`
  on the canned manifests, and demonstrates the bridge runtime fix.
- Anyone who clones the repo can run the script and see the same
  fee, the same blind-spot list, the same translation receipts. The
  re-runnability is the moat against "you cherry-picked."

### Fixed — Two rounds of post-sprint review

Round 1 (`016a8dc`):
- `path_convention` translator registered in `bulla.bridges`.
  Three-tier root resolution (`BULLA_REPO_ROOT` → `git rev-parse
  --show-toplevel` → `os.getcwd()` fallback). Demo Step 3 now
  closes the loop on the dimension it diagnosed in Step 2 instead
  of punting to a `currency_code` illustration.
- Headline reframe: scan output now reads `"Coherence fee: 22
  (across 1 convention dimension)"` so the actionable count is
  visible alongside the rank-of-H¹ math.
- `bulla scan` with no args triggers auto-detect via registered
  hosts and produces a structured no-config error that
  distinguishes three states (host configs unrecognized as MCP /
  parsed but empty / none found anywhere).

Round 2 (`878a30e` + `9dcd204`):
- Three structural bugs in `_cmd_scan` flagged in the second
  review:
  1. `server_names` was the flattened tool name list
     (`["filesystem__read_file", ...]`), so the narrative header
     printed every tool as a server. Now `sorted(server_tools.keys())`
     from the per-server dict.
  2. Pairwise reconstruction used `inputSchema={}`. Empty schemas
     made `compute_pairwise_fees` always return 0, firing the
     moat-case block on every fee>0 composition. Now the original
     per-server tool dicts (with real schemas) are passed straight
     through.
  3. The auto-detect path never set `config_source` or computed
     pairwise because the gates checked `args.commands` (always
     empty on auto-detect) instead of the local `commands` /
     `chosen_path` variables.
- Refactored `_cmd_scan` into `_resolve_scan_targets`,
  `_scan_explicit_commands`, `_scan_named_entries`,
  `_flatten_for_guard`, `_render_scan_narrative`. The three input
  branches (positional commands / `--config` / auto-detect)
  produce the same `(server_tools, config_source)` shape so
  downstream rendering is unconditional.
- New regression test pins Bug 2: `compute_pairwise_fees` on the
  canonical-demo manifests must return fee>0 on filesystem×github,
  proving the empty-schema reconstruction is gone.

### Native LangGraph + CrewAI Runtime Integration Sprint

The previous sprint shipped `bulla.translate` and `bulla.Session`. This
sprint puts the substrate inside the agent runtimes where users
actually compose tools — LangGraph and CrewAI. Two adapters, two
callback handlers, ~600 LOC, 124 new tests (50-seed property tests
each plus 12–13 unit tests, all gated by `pytest.importorskip`).

### Added — `bulla.langgraph`

- **`bulla.langgraph.bind(graph) -> Session`** — snapshots a live
  `langgraph.graph.StateGraph` (compiled or not) into a
  `bulla.Session`. Walks `nodes`, `edges`, `branches`, `channels` and
  builds a Composition whose `composition_hash` is deterministic
  with respect to the order LangGraph happened to add things
  internally. Pre-execution `session.fee` is immediately available;
  `session.diagnose()` returns a full `WitnessReceipt`.
- **`BullaCallbackHandler`** — a `langchain_core.callbacks.BaseCallbackHandler`
  subclass. Records actual tool invocations (`on_tool_start` /
  `on_tool_end`) into the Session's receipt chain during
  `graph.invoke()` / `.stream()`. Emits a terminal `WitnessReceipt`
  on outermost `on_chain_end`.
- Conditional edges declared without a `path_map` default to
  conservative fan-out (matching LangGraph's runtime behavior); the
  `on_unknown_branch="skip"` kwarg opts out.
- LOAD-BEARING property test: 50 seeded random graph constructions
  assert that `bind()` is order-independent — `bind(g_a)` and
  `bind(g_b)` on graphs built from the same nodes/edges in different
  orders produce identical `composition_hash`.

### Added — `bulla.crewai`

- **`bulla.crewai.bind(crew) -> Session`** — same shape, walks
  `crew.agents`, `crew.tasks`, `task.context`, `task.tools`,
  `agent.tools`, and `crew.process`. Sequential mode emits edges
  between consecutive tasks' tools; `task.context` emits explicit
  dependency edges; hierarchical mode emits manager-to-worker edges.
- **`BullaCrewCallback`** — wraps CrewAI's `step_callback` and
  `task_callback` plumbing. `handler.finalize()` after `crew.kickoff()`
  emits the terminal receipt.
- Tool names are namespaced as `"{agent.role}.{tool.name}"` so two
  agents sharing a tool name (e.g. both have `search`) are kept
  distinct in the composition.
- Same 50-seed order-independence property test as the LangGraph
  adapter.

### Added — Output-schema policy (both adapters)

- LangChain's `BaseTool.args_schema` and CrewAI's `BaseTool.args_schema`
  cover input fields but no standardized output schema exists.
- Both adapters accept an `output_schemas={tool_name: jsonschema}`
  kwarg on `bind()`. When supplied, output fields land on the
  `ToolSpec.observable_schema` so output-side fee detection works.
- When omitted, a one-line warning per unschematized node logs the
  gap, and the adapter falls back to input-only fee detection.
  Honest under-reporting beats silent magic.

### Naming and import discipline

- New public symbols: 4 (`bind` ×2, callback class ×2). New files: 6
  (two adapters, two shims, two test files). New CLI commands: 0.
- `import bulla.langgraph` and `import bulla.crewai` succeed even
  without the framework extras installed; symbols only fail at call
  time when the framework itself isn't importable.
- `bulla.__init__` does NOT auto-export framework symbols. Users
  import them explicitly via `from bulla.langgraph import ...` to
  keep the optional-dep boundary visible.
- Static AST adapters (`bulla.frameworks.langgraph`, `crewai`) are
  unchanged. Source-file scanning and live-object snapshots cohabit;
  the runtime adapters live in `bulla.frameworks.{langgraph,crewai}_runtime`.

### pyproject.toml

- `langgraph` extra now pins `["langchain-core>=0.3", "langgraph>=1.1"]`
  (added the framework itself).
- `crewai` extra unchanged at `["crewai>=0.80"]`.

### Sprint 13: Composition Certification Suite

Per-composition regime certificate as a first-class user-surface artifact.
Closes the gap between `bulla regime` (lattice predicates only) and `bulla
diagnose` (fee + blind spots) by bundling both with a fee-interpretation
label and repair semantics into one structured JSON object.

### Added

- **`bulla.certificate`** — new module with `CompositionCertificate`
  dataclass + `certify(comp, source_path=..., include_witness_geometry=...)`
  + `to_dict` / `to_json` serializers. Composition certificate has 5
  layers: identity, regime (Sprint 8/9/11), diagnostic (fee + blind
  spots + bridges), cross-server decomposition (multi-server compositions
  only), witness geometry (fee > 0 only). Plus `fee_interpretation` and
  `repair_semantics` short labels, regime violations from
  `validate_regime`.
- **`bulla certify` CLI subcommand** — emits per-composition certificate(s)
  in text or JSON format. Accepts file paths, directories, or
  `--seed-set` for the canonical Sprint 13 10-composition seed set.
  Optional `--output FILE` to write rather than print. Reuses existing
  `_load_with_regime_warning` helper (Sprint 11/12) for parser + regime
  warning emission.
- **Sprint 13 seed set** — 10 compositions covering the regime lattice
  (registry pairs `filesystem+github` + `github+notion`, 4 curated YAMLs,
  2 regime-break fixtures, A_{3,4} cycle family, and a malformed
  negative control). Canonical certificate output committed at
  `papers/composition-doctrine/sprint13_seed_certificates.json`.
- **Canonical-output regression test**
  (`test_sprint13_seed_certificates.py`) — re-generates the seed set
  in-memory and asserts byte-identity with the committed fixture, modulo
  `timestamp` + `bulla_version`. Same gate pattern as Sprint 12's
  `test_diagnose_default_json_regression`.
- **18 unit tests** (`test_certificate.py`) covering all five
  `fee_interpretation` lookup-table branches, all four `repair_semantics`
  branches, JSON round-trip stability, witness-geometry on/off, multi-
  server cross-decomposition detection, deterministic SHA-256, and the
  malformed negative control.
- **Sprint 13 figure** rendered as pure SVG (no matplotlib dep) at
  `papers/composition-doctrine/sprint13_certification_suite_figure.svg`.
  Reuses Sprint 11 lattice-audit data; one chart, regime predicate rates
  by corpus.
- **One-page report** at `papers/composition-doctrine/sprint13_certification_suite.md`
  with TL;DR, schema, lookup table, seed-set table, BABEL relation,
  explicit anti-overreach scope.

### Changed

- **`bulla/docs/REGIME.md`** — new section linking the regime guide to
  `bulla certify` for users who want the full bundle (regime + fee +
  interpretation) in one artifact.

### Out of scope (intentional non-goals)

- No new theory, no new Lean modules, no new diagnostics.
- No task-level validation correlating fee with real failure rates
  (deferred to Sprint 14+; would require failure-data infrastructure
  the project does not yet have).
- No predictive-validity claims about fee.
- No leaderboard / scoring / ranking — certificates attest, they do not
  compare.
- No BABEL integration beyond a single cross-reference paragraph.

### Indispensability Push Sprint

Three coupled deliverables that move Bulla from **diagnostic** (here is
your fee) to **prescriptive** (here is the typed translator that fixes
it) to **online** (here is the live delta as you compose).

### Added — Phase A: `bulla.translate` runtime

- **`bulla.bridges`** — new module exposing typed value translators on
  top of the existing Extension E `mappings:` substrate. Public verb is
  `bulla.translate(dimension, value=..., to_convention=...)` returning
  a `TranslationResult{value, evidence, receipt}`. Reuses the existing
  `WitnessReceipt` (no new model class) so chaining via
  `parent_receipt_hashes`, content-addressing, and signature work for
  free.
- **Five canonical translators** ship at registration time:
  `currency_code` (ISO-4217 ↔ Stripe-lower / numeric), `country_code`
  (alpha-2 ↔ alpha-3 ↔ numeric), `language_code` (ISO-639-1 ↔ -3 ↔
  BCP-47), `temporal_format` (ISO-8601 ↔ Unix seconds / millis), and
  `fhir_resource_type` (R4 ↔ R5 capitalization edges).
- **Pluggable registry** via the `@bulla.bridges.register` decorator;
  closed-by-default so the canonical translators are deterministic.
- **Mapping-derived path** — when no hand-written translator exists,
  the runtime walks the active pack stack's `mappings:` blocks
  (substrate: `bulla.mappings.translate`).
- **Restricted-pack invariant**: licensed values never surface raw —
  when the only resolver is a `restricted` / `research-only` pack, the
  runtime raises `TranslationUnavailable(license_required=...)` instead
  of returning the value.
- **Naming discipline** — the function is `translate` (not `bridge`)
  to avoid the case-collision with the diagnostic `Bridge` dataclass
  (`from bulla import Bridge` vs `from bulla import bridge` is exactly
  the import that breaks once and ships). The diagnostic-side `Bridge`
  is unchanged.
- **CLI**: new `bulla translate --dimension X --value V --to T`
  subcommand. The existing `bulla bridge` (diagnostic-bridge YAML
  rewriter) is untouched — different operation, different command.
- **Tests** — 46 in `tests/test_translate.py` covering all five
  canonical translators, registry mechanics, mapping-derived path,
  restricted-pack redaction, receipt chaining, and the naming-
  discipline import surface.

### Added — Phase B: `bulla.Session` for online incremental composition

- **`bulla.Session`** — new long-lived class for building a composition
  tool by tool. `add_tool` / `add_edge` / `add_tools_and_edges` mutate
  the session and return an `AddToolResult{delta_fee, fee_after,
  new_hidden_fields, ...}`. `checkpoint()` emits a mini-receipt;
  `diagnose()` runs the full witness pipeline.
- **`IncrementalDiagnostic.extend(new_tools, new_edges)`** — new
  method on the existing rank-1-Schur incremental class. Validates
  that tool names are unique and edge endpoints reference known
  tools, then refreshes K and the hidden basis by recomputing
  `witness_gram` on the extended composition. The API is forward-
  compatible with a future block-update implementation; the property
  test pins the externally-observable behavior so the swap is safe.
- **Receipt chaining**: every `add_tool`, `translate(...)`,
  `checkpoint()`, and `diagnose()` extends an internal
  `parent_receipt_hashes` chain, so a session's full history is
  reconstructable from the terminal receipt.
- **Full P_O generality** — `Session` handles tool additions that
  introduce new observables, not just new hidden fields.
- **LOAD-BEARING TEST**: `tests/test_session.py::test_session_bitwise_equals_full_rebuild`
  — 10,000 seeded random tool/edge sequences, each step asserts
  bitwise equality between the session's accumulated state and a
  from-scratch `witness_gram` computation. **All 10,000 seeds
  pass.** Without this proof, the incrementality claim is unverified.
- **Tests** — 15 additional unit tests covering add-validation,
  receipt chaining, disposition tracking, `diagnose()` on real
  compositions, and empty-session edge cases.

### Added — Phase C: Glyph live-fee widget

- **`/bulla/live-fee`** — new Glyph page where users paste two MCP
  server tool descriptions and watch the coherence fee, blind spots,
  and disposition compute in real time. Two paste-ready samples
  (`fs vs github`, `fs vs stripe`).
- **`glyph/api/bulla_diagnose.py`** — Vercel Python serverless
  function that calls `bulla.compose_multi(...)` directly and returns
  fee + blind-spots + disposition + receipt-hash JSON.
  `glyph/api/requirements.txt` pins `bulla>=0.36.0`.
- **`glyph/src/app/api/bulla/diagnose/route.ts`** — Next.js TS route
  that proxies to the Vercel Python function (when
  `BULLA_BACKEND_URL` env var is set) or falls back to a canned demo
  response. The `mode: "live" | "demo"` field on the response lets
  the widget surface which path executed.
- **`glyph/src/components/LiveFeeWidget.tsx`** — `'use client'`
  interactive widget with debounced live-update on textarea edits,
  per-pane parse-error display, and a structured result panel
  (fee, boundary fee, bridges-required, disposition, blind-spot
  list, active packs, receipt-hash preview).

### Added — Phase D: `bulla.LiveSession` online proxy

- **`bulla.LiveSession`** — unified online composition proxy that
  combines `Session` (incremental fee tracking) with
  `BullaProxySession` (call tracing). `add_server()` registers MCP
  servers dynamically and returns `AddServerResult{delta_fee,
  fee_after, new_tools, new_edges, new_blind_spots}`.
  `record_call()` traces live tool invocations with flow-conflict
  detection. `translate()`, `checkpoint()`, and `diagnose()` delegate
  to the underlying Session for receipt chaining.
- **`LiveSession.from_server_tools()`** — convenience constructor that
  adds all servers at once from a `dict[str, list[dict]]`.
- Mathematical invariant: `live.fee` after all `add_server` calls
  equals `compose_multi(all_server_tools).diagnostic.coherence_fee`.
  Tested with fee-independence-of-server-ordering property test.
- 21 tests covering: fee invariant (1/2/3 servers), delta correctness,
  call tracing with flow conflicts, receipt chaining, translation,
  replay trace, proxy rebuild on server addition, ordering invariance.

### Naming discipline

| Function | Module | Purpose |
|---|---|---|
| `bulla.translate(...)` | `bulla.bridges` | Runtime value translation across conventions |
| `bulla.Bridge` (dataclass) | `bulla.diagnostic` | Field-keyed structural metadata for blind-spot repair |
| `bulla.bridges.register` | `bulla.bridges` | Decorator to register a typed translator |
| `bulla.bridge` (CLI subcommand) | `bulla.cli` | Existing diagnostic-bridge YAML rewriter, untouched |
| `bulla.translate` (CLI subcommand) | `bulla.cli` | New runtime translation subcommand |

The function is **not** named `bridge` — the case-collision with
`Bridge` is an API design problem, not a doc problem.

### Dimension Pack Enhancement Sprint

The four-phase sprint moves the seed corpus from "good seed baseline"
to a stronger production footing.

### Added — Phase 1: closed open-registry hash gaps

- **All 11 fetchable open packs now carry real SHA-256 registry hashes**
  (UCUM, NAICS 2022, ISO 639, IANA Media Types, FHIR R4, FHIR R5,
  FIX 4.4, FIX 5.0, GS1, UN-EDIFACT, ICD-10-CM). Previously 5 of 11
  open packs (FIX 4.4, FIX 5.0, GS1, UN-EDIFACT, ICD-10-CM) shipped
  on the `placeholder:awaiting-ingest` sentinel because their fetch
  URLs had drifted or required browser-shaped UAs.
- **`scripts/standards-ingest/compute_real_hashes.py`** rewritten:
  per-target header overrides, exponential-backoff retries, browser-
  UA fallback for hosts that gate non-browser UAs (CMS, GS1).
  Eleven targets, all OK in a clean rebuild.
- **`scripts/standards-ingest/_hash_lookup.py` plumbed through every
  generator**. The five generators that hadn't been wired
  (`build_fix.py`, `build_gs1.py`, `build_un_edifact.py`,
  `build_icd_10_cm.py`) now share the same
  `_hash_for(pack, dimension, version)` lookup as the pre-existing
  six.
- **Updated registry URIs** to the live machine-readable artifacts
  the hash table now binds to:
  - FIX 4.4/5.0 → `quickfix/quickfix` C++ repo (the `quickfix-j`
    Java fork no longer carries the dictionaries at the historical
    path).
  - GS1 → `https://ref.gs1.org/ai/` JSON catalogue (the legacy
    `gs1.org` PDF returned 403 to non-browser UAs).
  - ICD-10-CM → `<year>-code-descriptions-tabular-order.zip` URL
    pattern (the legacy `<year>-icd-10-cm-code-files.zip` pattern
    was retired; switched to FY2026 release).

### Added — Phase 2: real-coverage corpus expansion

- **`scripts/standards-ingest/_external_fetcher.py`** — new fetcher +
  parser for external OpenAPI/GraphQL specs. Content-addressed
  cache under `calibration/data/api-registry/_cache/`, lenient YAML
  loader (handles malformed timestamps, the `tag:yaml.org,2002:value`
  tag, and non-printable scalars seen in real upstream specs),
  graceful per-URL skip on parse failure.
- **35 curated real public OpenAPI/GraphQL specs added to the
  Phase 7 build** (vendor repos: GitHub, Stripe, Slack, Twilio,
  Discord, Spotify, Box, Intercom, Zoom, Plaid, Asana, weather.gov,
  FHIR R4/R5; APIs.guru directory: OpenAI, Notion, Square, SendGrid,
  DigitalOcean, Linode, Atlassian Jira, CircleCI, Wikimedia,
  Mailchimp, Google Calendar/Gmail/Drive/Sheets/YouTube; plus
  duplicate APIs.guru entries for Stripe, Slack, Twilio, Spotify,
  Plaid, Zoom). Captured corpus grows from **65 sources (57 MCP +
  8 synthetic) to 100 sources (57 MCP + 35 external + 8 synthetic)**
  with the synthetic fixtures preserved as validation-only.
- **`coverage.json` carries explicit real-vs-synthetic breakdown**:
  `n_real_mcp`, `n_real_external`, `n_synthetic_fixtures`, `n_real`,
  `n_synthetic`, `n_external_skipped`. Consumers no longer have to
  reconstruct the split from `source_id` heuristics.
- Classifier corpus grows from ~880 rows to **27,651 rows**;
  distinct dimensions firing grows from 10 to 28.
- **`BULLA_NO_NETWORK=1`** environment toggle for deterministic
  offline builds (uses the on-disk cache for any URL it has already
  seen and skips the rest).

### Added — Phase 3: provenance hardening

- **`derives_from.source_hash` propagated to every fetchable open
  pack**. When `derives_from.source_uri == values_registry.uri`,
  `source_hash` and `values_registry.hash` are sourced from the
  same `_hash_for` lookup (so they bind to the same fetched
  artifact byte-for-byte).
- **`derives_from.source_uri` aligned to the registry artifact**
  for IANA Media Types and FHIR R4/R5 (previously `source_uri`
  pointed at the human-readable landing page; the human pointer is
  now exposed via `license.source_url` instead).
- **`tests/test_pack_provenance_invariants.py`** new — 5 invariants
  tested across the seed corpus and `registry-hashes.json`:
  1. `source_hash` agrees with `values_registry.hash` whenever
     `source_uri == values_registry.uri`.
  2. Every ok entry in `registry-hashes.json` round-trips into the
     corresponding seed pack.
  3. No open pack remains on `placeholder:awaiting-ingest` (Phase 1
     gap closure invariant).
  4. Every restricted pack uses `placeholder:awaiting-license` —
     real hashes never appear on a restricted pointer.
  5. Loaded `PackRef.derives_from.source_hash` round-trips byte-
     identical from the pack file.

### Added — Phase 4: tightened CI quality gates

- **`tests/test_phase7_index.py::TestSprintGrowthGates`** — 6 new
  gates pinning the post-sprint deliverables (≥ 20 real external
  sources, ≥ 75 real total, synthetic count == 8, ≥ 10000 corpus
  rows, ≥ 15 distinct dimensions firing, ≥ 8 seed-pack dimensions
  firing). Each threshold sits a few sources below the actual ship
  to leave headroom for transient upstream-host failures.
- **Tightened minimum-real-hashes threshold** from 4 to 10 in
  `test_placeholder_sentinel.py::TestSeedCorpusHashFormatInvariant::test_at_least_some_real_hashes_present`.
- The Phase 3 invariant tests (5 of them) all double as CI gates:
  any future PR that introduces a placeholder on an open pack, or
  drifts `source_hash` out of step with `values_registry.hash`, or
  silently puts inline values on a restricted pointer, fails CI.

## 0.36.0

### Added — Standards Ingestion sprint output

- **Five architectural extensions to the pack format** (Extensions A–E):
  - **A. License metadata** (`license: { spdx_id, source_url, registry_license, attribution }`) at the pack level. `registry_license` is one of `open` / `research-only` / `restricted` and describes the upstream registry's license posture, not the pack's own (the pack is always our own openly-published metadata). New `RegistryAccessError` with code `LICENSE_REQUIRED` raised when a consumer hits a registry needing a credential they haven't configured. New optional `WitnessReceipt.pack_attributions` carries hash-references to NOTICES.md entries that standards bodies require crediting.
  - **B. `values_registry`** (`{ uri, hash, version, license_id }`) on a dimension — pointer to an external content-addressed registry that owns the canonical set of values. The pack hash includes the pointer object but **not** the registry contents (avoids 3–5 MB JSON-blob hashing on every load). Inline `known_values` + `values_registry` may coexist for `open` packs (inline acts as documentation, stripped from the canonical hash); for `restricted` / `research-only` packs the validator REJECTS the coexistence — licensed values must remain behind the registry pointer, not be redistributed in the pack file. New `bulla pack verify`, `bulla pack status`, `bulla pack lint` CLI commands.
  - **C. `derives_from`** standard-version provenance on `PackRef` (`StandardProvenance` dataclass: `{ standard, version, source_uri, source_hash }`). Multi-pack receipts naturally carry per-standard provenance because every active pack records its own `derives_from`. Receipt hash binds the underlying-standard revision transitively.
  - **D. Alias-form `known_values`**: items widen from `string` to `string | { canonical, aliases: [string], source_codes: { standard: code } }`. Strictly additive — legacy string-only packs keep working. Classifier collapses canonical + aliases + source-code values into one normalized set per dimension, so a field whose enum lists `"840"` (ISO-4217 numeric) classifies under the same dimension as a field listing `"USD"`.
  - **E. Passive `mappings:` block** in regular packs (`{ target_pack: { target_dimension: [{from, to, equivalence: exact|lossy_forward|lossy_bidirectional|contextual}] } }`) plus `bulla.mappings.translate` consumer helper. The coboundary uses dimension *names*, not values — so mappings are receipt-side translation tables, NOT measurement primitives. No new artifact type; embedded as data in regular packs.

- **Placeholder-sentinel hash format** for `values_registry.hash`: `placeholder:awaiting-ingest` (open registries we haven't fetched yet) and `placeholder:awaiting-license` (license-gated registries). New `RegistryAccessErrorCode.PLACEHOLDER_HASH`. Validator REJECTS literal `sha256:0...0` (a valid-shaped hash that the verifier would silently treat as "checked, mismatched" — worse than the explicit "not yet checkable" state). The verifier short-circuits placeholder pointers before any fetch and returns `status='placeholder'` distinct from `hash_mismatch`. Strict mode (`raise_on_placeholder=True`) raises `RegistryAccessError(PLACEHOLDER_HASH, ...)`. Caught and fixed a pre-existing verifier bug in the process: `actual_hash` was bare hex while `expected_hash` carried the `sha256:` prefix, which would always mis-compare; now normalized to the prefixed canonical form.

- **19 seed packs** at `src/bulla/packs/seed/`:
  - **14 open standards**: ISO 4217 (currencies, 178 entries with alpha/numeric aliases), ISO 8601 / RFC 3339 (date/time formats), ISO 3166-1 (250+ countries with alpha-2/alpha-3/numeric aliases), ISO 639-1/3 (49 most-localized languages, full ~7700-entry SIL registry behind `values_registry`), IANA Media Types (MIME types), NAICS 2022 (industry classification), UCUM (units of measure), FIX 4.4 + 5.0 SP2 (financial messaging), GS1 General Specifications (GTIN/GLN/SSCC + Application Identifiers), UN/EDIFACT D.21B (supply-chain messaging), HL7 FHIR R4 + R5 (healthcare resource types), ICD-10-CM (US diagnosis codes + ICD-9 GEMs in the passive `mappings:` block).
  - **5 restricted-source metadata-only packs**: WHO ICD-10 translations, SWIFT MT/MX, HL7 v2, UMLS Metathesaurus mappings, ISO 20022. Each carries `license.registry_license: research-only` or `restricted`, dimension metadata only, `values_registry` placeholder pointers — zero licensed content shipped in any pack file.
  - **6 of 12 open registries carry real SHA-256 hashes** from authoritative-source fetches (UCUM, NAICS 2022, ISO 639, IANA Media Types, FHIR R4, FHIR R5). The remaining 6 carry the `placeholder:awaiting-ingest` sentinel. All 5 restricted packs carry `placeholder:awaiting-license`.

- **30 reconstructed historical mismatch incidents** at `calibration/data/incidents/` (Mars Climate Orbiter, Drupal+Stripe JPY, Vancouver Stock Exchange, Patriot missile Dhahran, Ariane 5 Flight 501, Gimli Glider, LIBOR-SOFR transition, US T+1 settlement, Herstatt Risk, levothyroxine mg/mcg, ICD-9→ICD-10 transition, FHIR R4→R5 breaking changes, SNOMED↔ICD-10 double-coding, Boeing 737 MAX FAA ODA, PHE COVID XLS truncation, Shopify-QuickBooks tax, Stripe webhook duplicates, NV Energy meter channel, GCV/NCV gas billing, LNG price-formula disputes, GTIN check-digit miscoding, EDIFACT D.96A→D.21B drift, MT→ISO 20022 migration, NAICS/SIC classification dispute, country-code drift, language-tag drift, Überlingen TCAS/ATC, leap-second 2012 outages, digoxin tenfold transfer, Singapore lidocaine overdose). Each is a Bulla composition YAML with pre-labeled dimension edges encoded by the generator script.

- **`bulla.api_registry` schema-capture pipeline** (Phase 7): MCP / OpenAPI / GraphQL normalization → classification under the active pack stack → content-addressed capture record. Aggregations: per-source coverage map, classifier-training corpus (every `(field, dimension, confidence)` triple as a labeled row in JSONL), forward-compatible storage format for the deferred Part B equivalence detector. Pipeline + 65 indexed records (57 reprocessed real MCP manifests + 8 synthetic pipeline-validation fixtures: Stripe charges, Shopify Admin, GitHub v3, FHIR Patient, Slack Web, Twilio Messages, FIX Trading Orders, GS1 Traceability).

- **2 end-to-end demo compositions** at `calibration/data/demos/` proving the architecture works across the full stack:
  - `cross_pack_receipt_billing.yaml` — clinical_emr → billing_system → payer_gateway crossing ISO 4217 + FHIR R4 + ICD-10-CM seams, producing a single signed receipt with all three packs in `active_packs`, all three `derives_from` provenances, and `pack_attributions` resolving via NOTICES.md.
  - `restricted_pack_metadata_only.yaml` — composition referencing the umls-mappings restricted pack issues a valid receipt today without any consumer-side license; `bulla pack verify` on the same pack fails with `RegistryAccessError(PLACEHOLDER_HASH)`. The architectural separation between metadata-receipt-issuance and licensed-value-fetch is end-to-end provable.

### Corpus + verification

- **Seed-pack scale**: 14 open + 5 restricted = 19 seed packs, all validate clean. ISO 639 dropped from 656 KB inline-everything to 8.7 KB seed + `values_registry` (75× smaller) — the architectural-consistency fix the Extension B rationale was designed to enforce.
- **Real-hash fetches** (6 of 12 open): UCUM (`ucum-essence.xml`, 545 KB, sha256:b78e1fc5…), NAICS 2022 (`2-6_digit_2022_Codes.xlsx`, 82 KB, sha256:be12ba41…), ISO 639-3 SIL (178 KB, sha256:9697ac84…), IANA Media Types XHTML (946 KB, sha256:e6d05584…), FHIR R4 valueset-resource-types (2 KB, sha256:82ae2b62…), FHIR R5 valueset-resource-types (63 KB, sha256:6ed6a7d2…).
- **Phase 5 empirical metrics** (revised post-feedback to distinguish two distinct claims):
  - **Claim B (load-bearing — classifier discovery on unlabeled MCP schemas)**: 29.4% signal-density increase on the 57-manifest calibration corpus, target ≥25%, **PASS**. This is the key metric — the classifier identifying standards-dimensions in raw `inputSchema` properties.
  - **Claim A (baseline sanity — coboundary correctness on labeled graphs)**: 30/30 incident-corpus detection (100%), target ≥80%, **PASS**. Note: incident YAMLs encode pre-labeled dimension edges by construction; this validates the measurement layer (δ₀) on a known-good case but does NOT exercise the discovery layer.
  - **Auxiliary** (incident-corpus field-name classifier): 18.8% reduction on a 198-field cross-domain corpus, target ≥15%, **PASS**. The original wrong-shaped 50% claim was retired after honest accounting — ~70% of incident fields are structural identifiers (patient_id, claim_id, trade_id) that no standards pack should classify.
- **Zero-licensed-content audit**: `git grep` confirms zero licensed values in any pack file across the 5 restricted packs. The validator's metadata-only invariant is the single line of defense; CI re-checks.
- **Test count**: **1381** tests collected under `pytest tests/`; **1363 passing**, **18 skipped** (verified 2026-04-30). Note: `tests/test_hosts.py` fixtures create a `.cursor` directory under the temp workspace; environments that deny creation of that path (some sandboxed runners) may see **PermissionError** on those cases — rerun outside the sandbox or on macOS without that restriction.
- Sprint-level test additions include: placeholder-sentinel, demo compositions, values_registry, aliases, mappings, license, derives_from, tier A/B seeds, ISO 4217, Phase 4 restricted, incidents, Phase 5/7, api_registry, etc.

### Lean

No new Lean work ships **inside** the Bulla wheel this cycle — Standards Ingestion is Python data + pack machinery on the stable measurement layer. The **research-program** Lean ledger (witness geometry / fee rank / Laplacian–Schur chain) is documented separately and lists **56** theorems across 10 files, 0 `sorry`, per [`papers/sheaf/lean/LEAN-CLAIM-LEDGER.md`](../papers/sheaf/lean/LEAN-CLAIM-LEDGER.md). For the colimit-comparison thread see [`docs/COLIMIT-OBSTRUCTION-RESUMPTION-2026-04-26.md`](../docs/COLIMIT-OBSTRUCTION-RESUMPTION-2026-04-26.md) (research memo; not a PyPI dependency).

### Changed

- **`bulla audit` default text** is now a compact **instrument receipt** (new `bulla.audit_report` module): boundary fee leads, grouped cross-server finding cards (schema contrast + convention contrast + fix), collapsed within-server blind-spot counts, action-item footer. Default output avoids sheaf / rank notation; use `bulla diagnose`, `-v`, or `--format json` for depth. JSON adds an optional additive **`audit_report`** block without removing existing keys. When no MCP config is detected, stderr suggests a copy-paste **`bulla scan …`** command plus `bulla audit <path>`.
- **Path/URL seam classification**: `path_convention` name patterns now include `url` / `uri` in addition to file-path tokens. This surfaces browser-navigation versus filesystem/repository-locator seams as cross-server findings when schemas only expose unconstrained locator strings.
- **Pack-hash canonicalization** (Extension B): `_hash_pack` strips inline `known_values` from any dimension that also has a `values_registry` pointer before hashing. Authors can curate inline documentation without producing pack-hash drift; the registry pointer is the binding object. Dimensions WITHOUT `values_registry` keep their inline values in the hash exactly as before — preserving 0.35.0 behavior for all base/community/financial packs.
- **Phase 4 / restricted-pack verifier precedence**: when a `values_registry` pointer carries the `placeholder:` sentinel, the verifier short-circuits before the credential gate. A restricted pack with `placeholder:awaiting-license` returns `status='placeholder'`, NOT `status='license_required'`. This is the architecturally correct precedence — the placeholder is "structurally not yet checkable" which is deeper than the credential question. The credential gate still fires when a real `sha256:...` hash meets a missing credential.
- **CLI `bulla pack verify` rendering**: distinguishes placeholder hashes (yellow `PLACEHOLDER (awaiting-ingest)` / `PLACEHOLDER (awaiting-license)`) from real `sha256:...` hashes in the per-pointer summary line.

### Documentation

- New `docs/STANDARDS-INGEST-SOURCES.md` — RP-2 deliverable; canonical source URIs + license + update cadence for all 12 open + 5 restricted standards.
- New `docs/STANDARDS-PACK-MAINTENANCE.md` — Phase 6 deliverable; per-pack maintenance ownership, FTE-allocation per pack family, drift-handling protocol, hash-state vocabulary (real `sha256:...` vs `placeholder:...` sentinels), restricted-corpus governance, contribution checklist, quarterly maintenance rotation.
- New `docs/STANDARDS-INGEST-NOTICES.md` — attribution master file resolving `pack_attributions` hash-references to the standards-body credit strings.
- New `docs/API-REGISTRY-PIPELINE.md` — Phase 7 deliverable; pipeline architecture, source-kind normalization (MCP/OpenAPI/GraphQL), capture-record format, coverage map, classifier-training corpus, forward-compatibility contract for the deferred Part B equivalence detector.
- README updated with "Standards Ingestion (new in 0.36.0)" section listing the 19 seed packs + new pack subcommands.

## 0.35.0

### Added
- **Witness-geometry diagnostics** surfaced through the CLI. The math layer (`bulla.witness_geometry`, shipped earlier as an internal research module) is now wired into `bulla diagnose`, `bulla check`, and `bulla gauge` behind explicit flags. All quantities are exact rationals — leverage scores, N_eff, and effective resistances serialize as `"p/q"` strings in JSON, never floats.
- **`bulla diagnose --witness` / `bulla check --witness`**: emit a top-level `witness_geometry` JSON block (per-field leverage, N_eff concentration, coloops, loops, greedy minimum-cost disclosure basis) and a "Witness Geometry" text section. Only computed when `coherence_fee > 0`. Default output (flag absent) is byte-identical to 0.34.0, guarded by a golden-fixture regression test.
- **`bulla gauge --leverage`**: include the witness-geometry block in gauge output (per-field ℓ scores alongside the existing disclosure list).
- **`bulla gauge --substitutes TOOL FIELD`**: top-3 disclosure substitutes for a target hidden field, ranked by effective resistance in the Kron-reduced witness geometry. Takes two positional args (dot- and colon-safe in tool/field names). Emits exit code 1 + stderr message if the target is not a hidden field in the composition.
- **`bulla gauge --costs FILE`**: matroid-greedy minimum-cost disclosure (Edmonds 1971). YAML file maps `"<tool>:<field>"` → rational cost string; returns the cost-optimal basis with total cost.
- **`Diagnostic` model extended** with six optional witness-geometry fields (`hidden_basis`, `leverage_scores`, `n_effective`, `coloops`, `loops`, `disclosure_set`). Defaults are empty tuples / `None` so existing construction sites continue to work unchanged. `content_hash()` includes these fields only when populated, so receipts produced before 0.35.0 still hash identically.
- **15 new CLI tests** (`TestWitnessGeometry`) covering positive + regression paths for every new flag, plus exact-rational leverage conservation, the empty-witness-hash backward-compat guarantee, and the default-JSON-unchanged regression against a pinned 0.34.0 golden fixture (`tests/golden/diagnose_0.34_financial_pipeline.json`).

### Corpus + verification
- 703-composition real-schema corpus passes 703/703 structural identities (`rank K = fee`, `Σ ℓ = fee`, `0 ≤ ℓ ≤ 1`) and 240/240 Kron-reduction leverage predictions in exact rational arithmetic. Artifacts: `calibration/results/witness_geometry_703.summary.json`, `calibration/results/laplacian_collapse_verification.json`.

### Lean
- 2 new theorems (`witness_gram_rank_eq_fee`, `leverage_conservation`) Aristotle-verified (UUID `3c1a38f9-a823-4b80-ae5d-7e4dfaacad85`). Program total: **14 Lean-verified theorems, 0 `sorry`**.

### Changed
- **Anti-reflexivity test** (`tests/test_serve.py::TestAntiReflexivity::test_diagnostic_has_no_witness_imports`) now uses exact module-name matching rather than substring. The ban is specifically on `bulla.witness` (receipt/disposition layer, Law 1: measurement cannot depend on judgment); `bulla.witness_geometry` is pure linear algebra and is explicitly excluded.

### Documentation
- New README section "Witness-geometry diagnostics (new in 0.35.0)" with CLI examples.

## 0.34.0

### Added
- **Structural schema comparison**: `bulla.infer.structural` module performs pack-free, deterministic cross-tool field comparison using schema metadata (type, format, enum, range, pattern). Produces a `StructuralDiagnostic` parallel to the cohomological `Diagnostic`. The coboundary measures the cost of opacity (hidden conventions); the structural scan measures the cost of incompatibility (visible fields with disagreeing schemas). Together: total verification bill.
- **`SchemaOverlap` model**: Frozen dataclass for detected schema relationships between cross-tool fields. Covers agreements (micro-pack input), contradictions (diagnostic output), homonyms, and synonyms. `to_dict()`/`from_dict()` round-trip.
- **`SchemaContradiction` model**: Frozen dataclass for visible-but-incompatible field pairs. Records `mismatch_type` (type/format/enum/range/pattern), `severity` (0.0–1.0), and human-readable details.
- **`StructuralDiagnostic` model**: Container for all structural findings: `overlaps`, `contradictions`, `n_overlapping_fields`, `n_contradicted`, `contradiction_score` (sum of severities, rounded).
- **`scan_composition()` and `schema_similarity()`**: Public API functions exported from `bulla`. `scan_composition(tools_fields)` is the entry point; `schema_similarity(a, b)` is the weighted 5-component similarity metric.
- **`PROCEED_WITH_CAUTION` disposition**: New member of `Disposition` enum for compositions with schema contradictions but no opacity (fee=0, contradictions>0).
- **2D disposition reasoning**: `_resolve_disposition()` now reasons over a fee x contradiction_score surface. Four quadrants: fee=0/contradictions=0 → PROCEED, fee>0/contradictions=0 → BRIDGE/REFUSE, fee=0/contradictions>0 → CAUTION, fee>0/contradictions>0 → REFUSE. Priority chain expanded to 10 rules (was 8).
- **`PolicyProfile.max_structural_contradictions`**: New threshold (int, default -1). Follows the `max_unknown` pattern: -1 disables, 0 means strict.
- **`WitnessReceipt.structural_contradictions` and `WitnessReceipt.contradiction_score`**: Receipt fields for structural findings. Conditionally included in `_hash_input()` for backward compatibility.
- **CLI `--max-structural` flag**: `bulla audit --max-structural N` exits 1 if the structural contradiction score exceeds N. Mirrors `--max-fee`/`--max-contradictions`.
- Sprint 34 tests: `test_structural.py` (structural scan, similarity metric, classification, contradiction detection), expanded `test_witness.py` (2D disposition, caution path, structural receipt fields).

### Changed
- **Pack tightening**: Narrowed `id_offset` field patterns from `*_id/*_index` to `page/page_number/*_offset/*_position`. Narrowed `line_ending` field patterns from `*_text/*_content` to `*_newline/*_line_ending`. Narrowed `owner_convention` field patterns from `owner/*_owner` to `repo_owner/repository_owner`. Within-server blind spots drop from 16,801 to 4,125 (76% reduction); precision improves from 1% to 4%.
- **`id_offset` classifier regex**: Narrowed from `id|index|offset|position|ordinal|sequence|serial|number|num|page` to `offset|position|page|page_number`.
- **`--max-contradictions` help text**: Clarified as "convention contradictions" to distinguish from structural contradictions.

### Fixed
- **Dead code in `_resolve_disposition()`**: Removed redundant triple-check (blind_spots AND fee AND structural) that was a strict subset of (blind_spots AND fee) and never differentiated.

## 0.33.0

### Added
- **Community convention pack**: `community.yaml` ships with 3 curated dimensions (`sort_direction`, `state_filter`, `owner_convention`) discovered during the Tier 2 calibration study and generalized for cross-server applicability. Every `bulla audit` now evaluates 14 dimensions by default (11 base + 3 community).
- **Auto-bundled community pack**: `load_pack_stack()` automatically loads `community.yaml` between the base pack and user-supplied `--pack` overlays. Zero-config vocabulary enrichment — user packs still override everything.
- **Provenance metadata in packs**: Each community dimension carries `provenance` (discovered_by, server_affinity, independent_discoveries) to support future reputation scoring without building registry infrastructure.
- **CONTRIBUTING.md**: Dimension contribution guide with required fields, quality bar, pattern guidelines, and validation instructions. A PR to `community.yaml` is the submission format.

### Changed
- Default coherence fees increase on compositions involving `status`, `owner`, or `direction` fields, reflecting the newly recognized `state_filter`, `owner_convention`, and `sort_direction` dimensions.
- Test assertions updated for the expanded default vocabulary.

## 0.32.0

### Added
- **Compose SDK**: `compose(tools, *, policy, chain, name)` and `compose_multi(server_tools, *, policy, chain)` are the one-function entry points for agent framework integration. Each returns a `ComposeResult(receipt, diagnostic, decomposition)`. No guided discovery, no LLM calls -- pure structural diagnosis + policy enforcement + receipt issuance.
- **`compose()` auto-computes `unmet_obligations`**: When a `chain` receipt is provided with `boundary_obligations`, the SDK calls `check_obligations()` internally and sets `unmet_obligations = len(unmet)`. The caller never passes obligation counts.
- **`compose_multi()` auto-detects contradictions**: When a `chain` receipt contains `inline_dimensions`, the SDK calls `detect_contradictions()` and embeds any results in the receipt. Zero LLM cost, zero extra complexity.
- **`ComposeResult` frozen dataclass**: Bundles `WitnessReceipt`, `Diagnostic`, and optional `FeeDecomposition`. Calibration partners can access `diagnostic.coherence_fee` for time-series tracking and `decomposition.boundary_fee` for cross-server analysis without separate API calls.
- **`WitnessReceipt.unmet_obligations`**: `int` field (default 0) recording the number of unmet boundary obligations at witness time. Conditional-include in `_hash_input()` (only when > 0), consistent with `contradictions`, `boundary_obligations`, `inline_dimensions`. Pre-v0.32.0 receipts verify correctly.
- Sprint 32 tests: 29 new tests covering unmet_obligations receipt field (7), consistency fix (2), enforce_policy completeness (6), compose (5), compose_multi (5), backward compat (2), SDK imports (2).

### Fixed
- **`verify_receipt_consistency` disposition bug**: Now passes `unmet_obligations` and `contradiction_count` to `_resolve_disposition()`, so receipts with obligation/contradiction-driven refusals verify correctly.
- **`enforce_policy()` completeness**: Accepts and passes through all receipt fields: `inline_dimensions`, `boundary_obligations`, `parent_receipt_hash`, `parent_receipt_hashes`, `active_packs`, `unmet_obligations`.

### Changed
- **License**: Changed from MIT to Business Source License 1.1. Use grant: non-competing use + commercial use under 1,000 compositions/month. Change date: 2030-04-01. Change license: Apache 2.0.
- **WITNESS-CONTRACT.md**: Hash coverage section updated for `unmet_obligations`. New "SDK Surface" section documenting `compose()` and `compose_multi()`. Sprint 32 thesis updated from future to present tense.
- **SDK surface**: 3 symbols (`compose`, `compose_multi`, `ComposeResult`) exported from `bulla` alongside 65+ kernel symbols.

## 0.31.0

### Added
- **Policy enforcement**: `PolicyProfile` gains `max_unmet_obligations` (int, default -1) and `max_contradictions` (int, default -1). Both follow the `max_unknown` pattern: -1 disables, 0 means strict, N means tolerance.
- **Disposition priority rules 3 and 4**: `_resolve_disposition()` now refuses when `unmet_obligations > max_unmet_obligations` (rule 3) or `contradiction_count > max_contradictions` (rule 4), slotted between the existing `max_unknown` refuse and `require_bridge` rules.
- **`witness()` enforcement parameters**: `unmet_obligations: int = 0` and `contradiction_count: int = 0` are caller-attested integers passed through to `_resolve_disposition()`. When `contradictions` tuple is provided and `contradiction_count` is 0, the count auto-derives from `len(contradictions)`.
- **`BullaGuard.enforce_policy()`**: Single entry point that diagnoses, resolves disposition under a given policy (with obligation/contradiction counts), and issues a receipt.
- **CLI `--max-unmet` and `--max-contradictions`**: New threshold flags on `bulla audit` with exit-code semantics (exit 1 if exceeded). Mirrors the existing `--max-fee` / `--max-blind-spots` pattern.
- **CLI `--max-fee` on `bulla check`**: Previously only available on `gauge` and `audit`.
- Sprint 31 tests: 26 new tests covering policy serialization (3), disposition rules (8), witness with new params (4), enforce_policy (4), CLI exit codes (3), backward compatibility (4).

### Fixed
- **`detect_expected_value_contradictions` docstring**: Clarified that `sources` contains only `obligation.placeholder_tool`; the parent agent who set `expected_value` is unnamed because the obligation does not carry parent identity.

### Changed
- **WITNESS-CONTRACT.md**: Policy Semantics section updated with `max_unmet_obligations` and `max_contradictions` fields. Disposition priority chain expanded to 8 rules (was 6). Sprint 31 thesis updated from future to present tense.
- **PROTOCOL-NOTE.md**: Open question (b) on policy enforcement threshold semantics marked as resolved.

## 0.30.0

### Added
- **Contradiction detection**: `detect_contradictions(discovered_pack)` is a pure function from pack dict to `tuple[ContradictionReport, ...]`. Any dimension with 2+ distinct `known_values` produces a MISMATCH report. Values and sources are sorted alphabetically at construction for canonical ordering.
- **`ContradictionSeverity` enum**: Follows the `ObligationVerdict` pattern. Single member `MISMATCH`; `CONFLICT` reserved for future pack-level incompatibility rules.
- **`ContradictionReport` frozen dataclass**: `dimension`, `values` (sorted tuple), `sources` (sorted tuple), `severity` (enum). `to_dict()`/`from_dict()` round-trip. Hashable and serializable.
- **`detect_expected_value_contradictions(probes)`**: Intra-agent detection. Fires when a probe confirms a `convention_value` that differs from its obligation's `expected_value`. Closes the Sprint 28 `expected_value` loop.
- **`detect_contradictions_across(*convergence_results)`**: Inter-agent convenience wrapper. Merges `discovered_pack` from multiple convergence results, then delegates to `detect_contradictions()`.
- **`WitnessReceipt.contradictions` field**: `tuple[ContradictionReport, ...] | None`. Included in `_hash_input()` with conditional-include pattern (None = absent from hash, backward compatible with pre-v0.30.0 receipts).
- **`witness()` `contradictions` parameter**: Pass-through to `WitnessReceipt` constructor.
- **`expected_value` hydration in CLI**: `--chain` receipt's `inline_dimensions` are used to hydrate `BoundaryObligation.expected_value` during obligation loading. Resolves the Sprint 28/29 TODO.
- **Protocol note**: `PROTOCOL-NOTE.md` with fee theorem, convergence guarantee, contradiction detection, worked example, and five open questions.
- **Pre-computed v030 receipt**: `examples/canonical-demo/receipts/audit_receipt_v030.json` with embedded contradictions field. Original `audit_receipt.json` (v029 format) preserved as historical artifact.
- Sprint 30 tests: 23 new tests covering contradiction detection (6), expected-value contradictions (4), cross-convergence (2), serialization (2), receipt integration (2), backward compat with v029/v030 receipts (6), updated demo smoke test (1).

### Fixed
- **`discovered_pack` caching**: `ConvergenceResult.discovered_pack` now caches on first access via `object.__setattr__` (same pattern as `WitnessReceipt.receipt_hash`). Safe because the dataclass is frozen.
- **`--live` flag test coverage**: Added smoke test verifying `run_canonical_demo.py --help` exits cleanly and `--live` is registered.

### Changed
- **CLI mismatch display**: Replaced ad-hoc MISMATCH logic in `_audit_text()`/`_audit_json()` with structured `detect_contradictions()` calls. The CLI is now a consumer of the protocol, not an ad-hoc formatter. `_audit_json()` now includes `"contradictions": [...]` (list of dicts) alongside `"mismatches": N`.
- **Canonical demo**: Output now includes `Contradictions: 1` section showing `path_convention_match: absolute_local vs relative_repo (MISMATCH)`.
- **WITNESS-CONTRACT.md**: Sprint 30 thesis updated from future to present tense. `contradictions` added to hash coverage section. Contradiction detection semantics section added.

## 0.29.0

### Added
- **Canonical proof artifact**: `examples/canonical-demo/` runs the full Sprint 25-28 pipeline against real MCP server manifests (filesystem + GitHub). Two servers, one cross-server seam (`path_convention_match`), one convention mismatch (`absolute_local` vs `relative_repo`). Measurement, obligation extraction, guided discovery, value extraction, receipt with inline dimensions, and receipt integrity verification in a single demo script.
- **Convention mismatch display**: When `discovered_pack` contains a dimension with 2+ `known_values`, `_audit_text()` flags it as `MISMATCH` with per-source-tool breakdown. `_audit_json()` adds `"mismatches": N` to the `guided_repair` section.
- **`RealWorldMockAdapter`**: Deterministic adapter for the canonical demo that returns known convention values for real MCP servers. Parses obligation server group and dimension from the guided discovery prompt. `--live` flag on `run_canonical_demo.py` enables real LLM probing.
- **Pre-computed proof artifact**: `examples/canonical-demo/receipts/audit_receipt.json` is a checked-in receipt with `inline_dimensions` containing the discovered path convention mismatch. `verify_receipt_integrity()` works on it directly.
- Sprint 29 tests: convention mismatch formatting (MISMATCH in text, count in JSON, single-value no mismatch), real manifest audit (server tool counts, coherence_fee=30, boundary_fee=1, 3 obligations), canonical demo smoke test, pre-computed receipt integrity (exists, valid, has path_convention_match, has both values, has boundary obligations).

### Fixed
- **Package source resolution**: Tests now run against the workspace source (not standalone repo) after `pip install -e .` from the correct directory.

### Changed
- **Sprint 28 TODO**: Added `expected_value` hydration TODO comment in `cli.py` at `--chain` obligation loading, documenting deferred propagation to Sprint 30 (contradiction detection).
- **WITNESS-CONTRACT.md**: Sprint 29 thesis updated from future to present tense. Pivoted from contradiction detection to canonical proof artifact. Contradiction detection deferred to Sprint 30. Dependency diagram updated.

## 0.28.0

### Added
- **Convention value extraction**: `extract_pack_from_probes(probes, composition_hash)` transforms confirmed `ProbeResult` convention values into persistent micro-pack dimensions. Only CONFIRMED probes with non-empty `convention_value` produce entries. Multiple probes on the same dimension merge: `known_values` collects all distinct values (deduplicated), `source_tools` collects all tool names, `field_patterns` collects all fields (exact-match only). Output validated with `validate_pack()`.
- **`ConvergenceResult.discovered_pack` property**: Derives a micro-pack from all confirmed probes across all convergence rounds. Lazy and derived (cannot be a stored field on `frozen=True` dataclass). Calls `extract_pack_from_probes` with the final composition's hash prefix.
- **`BoundaryObligation.expected_value` field**: New optional field (default `""`) for convention values from upstream. When a parent receipt carries obligations with confirmed values, the downstream agent receives `expected_value` on each obligation. Included in `to_dict()` only when non-empty. Backward-compatible. Not yet propagated through `merge_receipt_obligations` (Sprint 29).
- **CLI discovered pack integration**: `bulla audit --converge` and `--guided-discover` now extract convention values from probes and embed them as `inline_dimensions` on the receipt. Newly discovered dimensions win over existing inline dimensions from `--chain` (later-wins precedence). Text output reports: `Discovered conventions: N dimension(s) with M value(s)` with per-dimension detail.
- **Value extraction demo** (`scripts/run_value_extraction_demo.py`): Two-agent demo. Agent A runs `coordination_step()` on a fee=2 composition, extracts specific convention values (pagination=zero_based, path_convention=absolute), witnesses with `inline_dimensions`. Agent B receives A's receipt via chain, inherits the enriched vocabulary.
- Sprint 28 tests: `extract_pack_from_probes` (empty/single/multiple probes, same/different dimensions, denied/uncertain/empty excluded, same-value deduplication, validate_pack), `ConvergenceResult.discovered_pack` (valid pack, multi-round aggregation, empty convergence), `BoundaryObligation.expected_value` (default, to_dict, backward-compat, merge unchanged), receipt integration (inline_dimensions, round-trip, merge precedence), Sprint 27 Issue 1 fix, demo smoke test.

### Fixed
- **Sprint 27 Issue 1**: Removed redundant `diagnose()` call in `coordination_step()`. Now uses `rounds[-1].repaired_fee` instead of re-diagnosing (the repaired_fee was already computed in `repair_step`).
- **Sprint 27 Issues 2+4**: Clarified `ConvergenceResult` docstring: `total_confirmed`/`total_denied`/`total_uncertain` count probe events across all rounds (not unique obligations). `converged` docs explain fixpoint-with-fee semantics.

### Changed
- `WITNESS-CONTRACT.md`: Sprint 28 thesis updated from future to present tense. New "Convention Value Extraction" section documenting `extract_pack_from_probes` semantics, pack format, receipt integration, and `expected_value` field.

## 0.27.0

### Added
- **Iterative convergence loop**: `coordination_step(comp, partition, tool_schemas, adapter, *, max_rounds=5, ...)` wraps `repair_step()` in a bounded loop with three exit paths: `fee_zero` (full resolution), `fixpoint` (no progress), `max_rounds` (budget exhausted). Obligation triage between rounds carries forward only UNCERTAIN probes; DENIED and CONFIRMED are excluded. Convergence is a theorem: fee is a non-negative integer that strictly decreases on each round with at least one confirmation.
- **`ConvergenceResult` dataclass**: Result of iterative repair: `rounds` (tuple of `RepairResult`), `converged`, `final_comp`, `final_fee`, `total_confirmed`/`total_denied`/`total_uncertain`, and `termination_reason` (`"fee_zero"`, `"fixpoint"`, `"max_rounds"`).
- **`bulla.repair` module**: Repair/coordination layer extracted from `diagnostic.py`. Contains `repair_composition`, `repair_step`, `RepairResult`, `coordination_step`, `ConvergenceResult`. The measurement layer (`diagnostic.py`) has zero imports from `repair.py`, preserving the anti-reflexivity law.
- **CLI `--converge` flag**: `bulla audit --converge` runs the iterative convergence loop. Reports: `Convergence: fee 3 -> 0 in 2 round(s) (3 confirmed, 0 denied, 0 uncertain) [fee_zero]`. `--max-rounds N` controls budget (default 5). `--guided-discover` remains for single-shot mode.
- **Convergence demo** (`scripts/run_convergence_demo.py`): Two-agent demo with fee=2 topology. Agent B converges in 2 rounds (fee 2→1→0) using a dimension-aware staged adapter. Agent C demonstrates trivial fixpoint (fee=0).
- Sprint 27 tests: `ConvergenceResult` fields, 1-round/2-round convergence, fixpoint (all denied), max_rounds cutoff, zero obligations, obligation carry-forward triage, monotonicity invariant, module split imports, Phase 0 cleanup fixes, demo smoke test.

### Changed
- **Module split**: `repair_composition`, `repair_step`, `RepairResult` moved from `diagnostic.py` to `repair.py`. All symbols re-exported from the `bulla` package for backward compatibility.
- **`_match_tool_for_obligation`**: Uses sorted iteration for deterministic prefix matching. Source_edge match still preferred.
- **Convention value filter**: `parse_guided_response` now accepts any non-empty `convention_value` (removed restrictive filter for "empty", "none", "n/a").
- **Demo disambiguation**: Guided discovery demo prints `placeholder_tool:dimension/field` for obligation display, disambiguating duplicate `(dimension, field)` pairs.
- **`MockAdapter` docstring**: Documents `last_prompt` as a test-only attribute.
- `WITNESS-CONTRACT.md`: Sprint 27 thesis updated from future to present tense. New "Iterative Convergence Loop" section documenting termination conditions, obligation triage, module structure.

## 0.26.0

### Added
- **Bridge-guided discovery**: `guided_discover(obligations, tool_schemas, adapter, pack_context)` probes obligations via a single batched LLM call with per-obligation verdicts (CONFIRMED / DENIED / UNCERTAIN). Uses numbered delimiters (`---BEGIN_VERDICT_N---` / `---END_VERDICT_N---`) for reliable multi-verdict parsing.
- **`ObligationVerdict` enum**: Three-way verdict for guided discovery probes: `CONFIRMED`, `DENIED`, `UNCERTAIN`.
- **`ProbeResult` dataclass**: Pairs a `BoundaryObligation` with its verdict, evidence string, and optional `convention_value` (populated when CONFIRMED).
- **`GuidedDiscoveryResult`**: Container for batched probe results with `n_confirmed`, `n_denied`, `n_uncertain` summary stats and `confirmed` property for filtering.
- **`repair_composition(comp, confirmed_probes)`**: Pure function that produces a new `Composition` with confirmed fields added to `observable_schema`. Immutable, idempotent, verifiable.
- **`RepairResult` dataclass**: Result of one repair round: `original_fee`, `repaired_fee`, `fee_delta`, probes, `repaired_comp`, `remaining_obligations`.
- **`repair_step(comp, partition, tool_schemas, adapter, ...)`**: Full one-round loop: diagnose -> obligations -> guided discover -> repair -> re-diagnose. Core coordination primitive for Sprint 27's `coordination_step()`.
- **`build_guided_prompt(obligations, tool_schemas, pack_context)`**: Batched prompt template evaluating all obligations in one LLM call with numbered verdict delimiters and known_values context from the active pack.
- **`parse_guided_response(raw, n_obligations)`**: Extracts all verdicts + evidence from a batched LLM response.
- **CLI `--guided-discover` flag**: `bulla audit --guided-discover` runs obligation-directed LLM repair after diagnosis. Reports delta: `Guided repair: fee 3 -> 2 (1 confirmed, 1 denied, 0 uncertain)`. Works with `--chain` for chained obligation repair.
- **Guided discovery demo** (`scripts/run_guided_discovery_demo.py`): Three-agent chain demonstrating guided repair with collective invariant assertion: fee strictly decreases after confirmed repairs. Uses `MockAdapter` for reproducibility.
- Sprint 26 tests covering guided prompt construction/parsing, guided discovery engine, repair composition purity/idempotency, collective invariant (fee drops), repair_step integration, and demo smoke test.

### Changed
- **Collective repair invariant**: If at least one obligation is confirmed and repaired, `fee(repaired) < fee(original)`. The reduction is at least 1 but may be less than the number of confirmed probes (overlapping linear dependencies). Demo and WITNESS-CONTRACT assert this collective invariant, not per-probe.
- `WITNESS-CONTRACT.md`: New "Bridge-Guided Discovery (v0.26.0)" section documenting guided discovery semantics, collective repair invariant, `ObligationVerdict` enum, `repair_step()` contract.

## 0.25.0

### Added
- **Boundary obligations on receipts**: `WitnessReceipt.boundary_obligations` (`tuple[BoundaryObligation, ...] | None`) carries requirements for downstream compositions. Conditionally included in hash and serialization only when not None, preserving backward compatibility with pre-v0.25.0 receipts.
- **`boundary_obligations_from_decomposition(comp, partition, diag)`**: Extracts boundary obligations from cross-partition blind spots. `placeholder_tool` is the server group name (from `__` prefix convention). Deduplicates on `(placeholder_tool, dimension, field)` with first `source_edge` kept.
- **`check_obligations(obligations, comp)`**: Three-way obligation classification: `met` (field observable), `unmet` (field in internal_state only), `irrelevant` (field absent). Returns `(met, unmet, irrelevant)` tuples.
- **`merge_receipt_obligations(receipts)`**: Additive obligation accumulation across parent receipts (NOT precedence). All parent obligations survive; duplicates deduplicated by `(placeholder_tool, dimension, field)`.
- **`BoundaryObligation.source_edge`**: New field (default `""`) recording the tool pair that surfaced the obligation (e.g. `"storage__read_file -> api__list_items"`). Informational provenance, not semantic identity.
- **`BoundaryObligation.to_dict()`**: Serialization method with conditional `source_edge` inclusion (omitted when empty).
- **CLI obligation output**: `bulla audit` text and JSON output now includes obligation sections when `boundary_fee > 0`. When `--chain` is used with a parent receipt carrying obligations, the obligation check report (met/unmet/irrelevant) is displayed.
- **CLI merge obligation output**: `bulla merge` text and JSON output now includes accumulated obligations from parent receipts.
- **Obligation lifecycle demo** (`scripts/run_obligation_demo.py`): Three-agent chain demonstrating obligation convergence: A emits obligations from boundary blind spots, B resolves A's and adds own, C resolves all remaining. Verifies receipt integrity and chain linkage.
- **`witness()` accepts `boundary_obligations`**: Optional parameter passed through to `WitnessReceipt`.
- Sprint 25 tests covering obligation computation, checking, propagation, merge accumulation, receipt integration, backward compatibility, and demo smoke test.

### Changed
- `BoundaryObligation` docstring updated to document dual interpretation of `placeholder_tool` (from `conditional_diagnose` vs `boundary_obligations_from_decomposition`).
- `_hash_input()` docstring expanded to include `boundary_obligations` in the backward-compatibility explanation.
- `WITNESS-CONTRACT.md`: New "Boundary Obligations (v0.25.0)" section documenting obligation semantics, three-way classification, propagation rule, accumulation vs precedence distinction, and receipt field. New "Future Directions" paragraph.

## 0.24.0

### Added
- **Receipt DAG**: `WitnessReceipt.parent_receipt_hashes` (tuple of strings) replaces the singular `parent_receipt_hash`. A single parent is a 1-tuple; multiple parents form a DAG. Tuple order IS precedence order (later entries override earlier, consistent with the pack stack).
- **`bulla merge` CLI command**: Vocabulary union from multiple receipts with overlap detection. Argument order IS precedence order. Does vocabulary merge only -- no audit, no fee calculation. Re-audit uses existing `bulla audit --chain`.
- **`bulla.merge` module**: `merge_receipt_vocabularies(receipts)` returns merged vocabulary and overlap reports. Overlap = non-empty intersection of `field_patterns` glob sets between dimensions from different source receipts. Purely informational.
- **`witness()` convenience API**: Accepts both `parent_receipt_hash` (single string, convenience) and `parent_receipt_hashes` (tuple, DAG). Providing both raises `ValueError`. Single parent is normalized to a 1-tuple on the receipt.
- **Diamond demo** (`scripts/run_diamond_demo.py`): Multi-agent vocabulary convergence with adversarial overlap. Agent A and Agent C discover dimensions independently with overlapping field_patterns; Agent D merges and re-audits. Proves DAG structure, overlap detection, and receipt integrity.
- Sprint 24 tests covering DAG receipts, mutual exclusion, backward compatibility, merge logic, overlap detection, and diamond demo smoke test.

### Changed
- `WitnessReceipt` field migration: `parent_receipt_hash` (singular) removed, replaced by `parent_receipt_hashes` (plural, `tuple[str, ...] | None`). Conditionally included in `_hash_input()` and `to_dict()` only when not None. Pre-v0.24.0 receipts with the old key verify correctly via `verify_receipt_integrity()` (key-name-agnostic).
- `_hash_input()` no longer unconditionally includes `parent_receipt_hash`. The old key is removed from the hash input entirely; new receipts use `parent_receipt_hashes`.
- MCP server schema updated: `parent_receipt_hash` replaced with `parent_receipt_hashes` (array of strings).

### Fixed
- `tempfile.mktemp()` replaced with `tempfile.mkdtemp()` in chain demo script (deprecated, race-condition-prone).
- Tautological `assert basis.discovered >= 0` replaced with two meaningful tests: `> 0` with micro-pack, `== 0` with base pack only.

## 0.23.0

### Added
- **`bulla audit --discover`**: Single-command coordination loop. Runs LLM convention discovery and audits with the enriched vocabulary in one step. Composable with `--receipt` and `--chain` flags. Additional flags: `--discover-provider` (openai, anthropic, openrouter, auto), `--output-discovered FILE`.
- **`bulla audit --receipt FILE`**: Produces a `WitnessReceipt` JSON after auditing, threading `witness_basis`, `active_packs`, and `inline_dimensions`.
- **`bulla audit --chain RECEIPT.json`**: Loads a prior receipt's embedded vocabulary and chains the new receipt to it via `parent_receipt_hash`. Enables deterministic CI: team lead runs `--discover` once, CI pipeline uses `--chain receipt.json` with no LLM call, no API key, no cost.
- **`WitnessReceipt.inline_dimensions`**: Optional field embedding discovered pack content directly in the receipt. Agents receiving a chained receipt can reconstruct the vocabulary without the original YAML file. Conditionally included in `_hash_input()` and `to_dict()` only when not None, preserving backward compatibility with pre-v0.23.0 receipts.
- **`WitnessBasis.discovered`**: New count distinguishing LLM-discovered dimensions from base-pack inferred dimensions. Defaults to 0 for backward compatibility. Included in `to_dict()` only when non-zero.
- **Most-specific-dimension-wins deduplication**: When a field matches both a child dimension (from a micro-pack) and its `refines` parent (from the base pack), the classifier returns only the child. Unrelated dimensions matching the same field are both preserved.
- **Two-agent chain demo** (`scripts/run_chain_demo.py`): Demonstrates vocabulary growth across two agents with overlapping server sets. Agent A discovers 4 dimensions, Agent B inherits them and discovers 2 more, producing chained receipts with tamper-evident hashes. Both mock and live LLM modes.
- 14 new tests covering inline_dimensions backward compatibility, refines specificity deduplication, WitnessBasis.discovered, end-to-end chain loop, and chain demo smoke test.

### Changed
- `classify_field_by_name()` now collects all matching dimensions before returning, enabling specificity deduplication via the `refines` hierarchy.
- `_audit_text()` and `_gauge_text()` now display the `discovered` count in the witness basis line when non-zero.
- `witness()` accepts optional `inline_dimensions` parameter (default None) passed through to `WitnessReceipt`.

### Fixed
- Shallow copy mutation bug in vocabulary merging during receipt chaining (deep copy required for nested dimension dicts).

## 0.22.0

### Added
- **`bulla discover` CLI command**: LLM-powered convention dimension discovery. Reads tool schemas from manifest directory, sends structured prompt to LLM, outputs validated micro-pack YAML. Saves raw LLM response alongside for diagnostics. Usage: `bulla discover --manifests DIR -o FILE [--provider openai|anthropic|auto]`.
- **Micro-pack format**: Convention packs now support two optional per-dimension fields:
  - `refines`: Parent dimension name for degradation hierarchy (Dublin Core Dumb-Down Principle). Example: `entity_namespace` refines `id_offset`.
  - `provenance`: Metadata dict for agent-invented dimensions (source, confidence, source_tools, boundary).
- **`bulla pack validate FILE`**: New CLI subcommand to validate convention pack YAML files. Checks required fields, type constraints, and structural integrity.
- **`validate_pack()` function**: Programmatic pack validation in `bulla.packs.validate`.
- **LLM adapter interface**: `DiscoverAdapter` Protocol with `OpenAIAdapter`, `AnthropicAdapter`, and `MockAdapter` implementations. Real LLM dependencies are optional: `pip install bulla[discover]`.
- **`[discover]` extras group**: Optional dependencies for LLM-powered discovery (`openai>=1.0`, `anthropic>=0.20`).
- **Discovery evidence**: 3 new dimensions discovered from 4-server manifests (`entity_namespace`, `content_transport`, `graph_operation_scope`). Boundary fee 1→5, total fee 30→45, active dimensions 2→5.
- 23 new tests: micro-pack validation (12), micro-pack loading (6), pack validate CLI (2), discover adapter/prompt/engine (18), full-loop integration (2).

### Changed
- **FINDINGS.md**: Updated with v0.22.0 discovery results, before/after comparison table, and four new discovered-dimension writeups.
- **WITNESS-CONTRACT.md**: Documents micro-pack format, `refines` semantics, `provenance` fields, discovery engine architecture, LLM adapter interface, prompt architecture, and SCPI readiness.

## 0.21.0

### Added
- **`--manifests DIR` flag for `bulla audit`**: Load pre-captured MCP manifest JSON files from a directory instead of scanning live servers. Enables deterministic CI without server runtime dependencies. Each `*.json` file is one server's `tools/list` response; filename stem becomes the server name.
- **GitHub Action v2**: `action.yml` upgraded with `mode` input supporting both `check` (composition YAMLs, backward compatible) and `audit` (MCP manifests or live scan). Audit mode outputs `coherence-fee` and `boundary-fee` as action outputs. SARIF upload supported in both modes.
- **`examples/github-action/`**: Workflow template (`coherence-audit.yml`) and README documenting setup, configuration, SARIF annotations, and manifest vs live scan trade-offs.
- 10 new tests: `_description` suppression (4), `--manifests` CLI (6).

### Changed
- **`_description` pseudo-field suppression**: Tool-level description keyword matches that produce `_description` pseudo-fields no longer generate edges or blind spots. Signal is preserved in witness basis for auditability. Blind spots drop from 273 to 244 in the 4-server audit. Fee drops from 31 to 30 (more accurate without spurious edges). Boundary fee preserved at 1.
- **Real-world audit findings updated**: FINDINGS.md updated with v0.19→v0.20→v0.21 progression table, dimension coverage table, and honest framing of 2/11 dimensions activated.
- **Observable schema derivation**: Only real schema fields (not `_description` pseudo-fields) are excluded from `observable_schema`, producing slightly more accurate coboundary matrices.

### Fixed
- `_description` pseudo-fields no longer inflate blind spot counts (29 spurious blind spots removed from 4-server audit)
- SARIF output no longer fails when using `--manifests` with a directory path

## 0.20.0

### Added
- **`path_convention` dimension**: New convention dimension in `base.yaml` with known values `absolute_local`, `relative_cwd`, `relative_repo`, `uri`. Detects `path`, `filepath`, `directory`, `dirname`, `folder` fields. Creates cross-server edges between filesystem and GitHub servers, producing the first **nonzero boundary fee** in real-world audit.
- **Temporal field patterns**: `since`, `after`, `before`, `until` added to `date_format` core patterns and `base.yaml` field_patterns. GitHub's `list_issues.since` now correctly classified as `date_format`.
- **Per-field description scanning**: 4th signal source in `classify_tool_rich`. Per-field descriptions (not just tool-level) scanned against pack keyword lists. Source type `field_description` — weak alone, promotes to `declared` when combined with name/schema signals.
- **Pack-driven description keywords**: `_DESCRIPTION_KEYWORDS` replaced with dynamic loading from merged pack taxonomy via `_get_description_keywords()`. Custom packs automatically enrich description matching. Financial pack keywords become active when loaded.

### Changed
- **Negative patterns for `id_offset`**: `per_page`, `page_size`, `page_count`, `limit`, `count`, `total`, `max_results`, `num_results`, `batch_size` excluded from `id_offset` via `_NEGATIVE_PATTERNS`. These are counts/limits, not indices.
- **Type-aware exclusion**: String-typed `*_id` fields (UUIDs, SHA hashes) excluded from `id_offset` when `schema_type="string"` is available. `commit_id` (string) no longer flagged.
- **`id_offset` description narrowed**: "Whether numeric indices and page numbers are zero-based or one-based" (was "identifiers and indices").
- **Real-world audit findings updated**: Fee 31 (was 17), 2 dimensions (was 1), boundary_fee=1 (was 0), 28 cross-server blind spots (was 0). FINDINGS.md rewritten with concrete agent failure scenario lede and before/after comparison table.
- **Base pack now has 11 dimensions** (was 10).

### Fixed
- `per_page` false positive eliminated from GitHub audit findings
- `commit_id` (string-typed) false positive eliminated
- Description keyword matching now extensible via pack YAML instead of hardcoded

## 0.19.0

### Added
- **`BullaGuard.from_tools_list()`**: New public classmethod for building a guard from an in-memory list of MCP tool dicts. This is the recommended entry point for programmatic multi-server audit, replacing direct use of the private `_composition_from_mcp_tools` helper.
- **SARIF output for `bulla audit`**: `--format sarif` produces SARIF v2.1.0 output with blind spots and bridge recommendations tied to the MCP config file path. Enables GitHub Code Scanning integration for audit results.
- **Server-name prefixed tool names**: In `bulla audit`, tools are now prefixed with their server name using `__` separator (e.g., `filesystem__read_file`). This makes tool-to-server mapping robust and self-documenting, eliminating the fragile index-based mapping from v0.18.0.
- **Real-world audit evidence**: Captured genuine `tools/list` responses from 4 live MCP reference servers (filesystem, github, memory, puppeteer — 56 tools total) with provenance metadata. First real-world cross-server audit found 17 blind spots in the GitHub server's `id_offset` conventions. See `examples/real_world_audit/FINDINGS.md`.
- **`examples/real_world_audit/`**: Reproducible audit demo with `run_audit.py` script and version-pinned server manifests in `manifests/`.
- **8 new tests**: `from_tools_list` API, server-prefixed tool names, SARIF output validation, real-world manifest smoke test.

### Fixed
- `_cmd_audit` no longer imports private `_composition_from_mcp_tools`; uses `BullaGuard.from_tools_list()` instead.
- Tool-to-server mapping in audit is now derived from prefixed tool names in the composition, not pre-predicted from raw tool dicts (eliminates invisible coupling).

## 0.18.0

### Added
- **`bulla audit`**: New CLI subcommand that reads MCP configuration files (Cursor/Claude Desktop format), scans all configured servers in parallel, builds a cross-server composition graph, and diagnoses the combined system. Features:
  - Auto-detection of MCP config in standard locations (`.cursor/mcp.json`, `~/.cursor/mcp.json`, Claude Desktop config)
  - Parallel scanning via `ThreadPoolExecutor` with per-server error isolation
  - Cross-server risk decomposition using `decompose_fee()` -- partitions blind spots into intra-server (within individual servers) vs boundary fee (between servers)
  - Text and JSON output formats, CI gating with `--max-fee` / `--max-blind-spots`, `--verbose` for detailed blind spot listing
  - `--skip-failed` / `--no-skip-failed` for controlling failure behavior
- **`scan_mcp_servers_parallel()`**: New parallel scanner in `scan.py` using `ThreadPoolExecutor`. Returns `list[ServerScanResult]` with per-server success/failure instead of aborting on first error.
- **`ServerScanResult`**: New dataclass in `scan.py` for structured scan results with `name`, `tools`, `error`, and `ok` property.
- **`bulla.config` module**: New module with `McpServerEntry`, `parse_mcp_config()`, and `find_mcp_config()` for parsing Cursor/Claude Desktop MCP configuration files. Supports stdio servers, skips HTTP/SSE transport with warnings.
- **`env` parameter on `scan_mcp_server()`**: Optional environment variable dict merged with `os.environ` before spawning, enabling API key passthrough from MCP configs.
- 12 new tests (561 total): config parser (5), parallel scan (2), audit CLI text/JSON/threshold/failed-server (5).

### Changed
- CLI quick-start help now shows `bulla audit` as the first command.

## 0.17.0

### Added
- **`bulla gauge`**: New CLI subcommand for prescriptive diagnosis of MCP servers and manifests. Accepts a manifest JSON file or `--mcp-server CMD` to diagnose a live server. Returns coherence fee, minimum disclosure set (exact fields to expose), and witness basis in a single command. Supports `--format text|json|sarif`, `--output-composition FILE` to save inferred YAML, CI gating flags `--max-fee N` / `--max-blind-spots N` (exit 1 on violation), and `--verbose` for full blind spot detail and bridge recommendations.
- **`prescriptive_disclosure()`**: New helper in `diagnostic.py` that encapsulates the lazy disclosure guard (skip coboundary construction when fee=0). Used by both the MCP surface (`serve.py`) and the CLI (`bulla gauge`), eliminating the duplicated `if fee > 0` pattern.
- 6 new tests (549 total): gauge text/JSON output, threshold pass/fail, blind spots threshold, composition round-trip.

### Fixed
- **`scan.py` clientInfo version**: Replaced hardcoded `"version": "0.7.0"` with `__version__` import. The MCP initialize handshake now reports the correct Bulla version.
- **`formatters.py` residual string parsing**: Replaced 4 `bs.edge.split(" → ")` calls in `format_text` and `format_sarif` with `bs.from_tool` / `bs.to_tool`, eliminating the same fragile pattern fixed in `diagnostic.py` in v0.16.
- **LangGraph demo dimension naming**: Renamed confusing dimension names `threshold_currency` → `amount_rounding` and `jurisdiction` → `regulatory_framework` to better represent the semantic conventions being measured.
- **Dead code**: Removed unused `from bulla.model import Diagnostic, WitnessBasis` imports from gauge formatter functions.

### Changed
- **README**: Added "Quick start with `bulla gauge`" section as the primary entry point, showing manifest and live-server usage patterns.
- **CLI help text**: Updated quick-start listing to feature `bulla gauge` first.

## 0.16.0

### Added
- **`BlindSpot.from_tool` / `BlindSpot.to_tool`**: Ergonomic fields storing source and target tool names directly on blind spot objects. Eliminates fragile `edge.split(" → ")` string parsing in `diagnose()` bridge generation and `conditional_diagnose()` obligation extraction. These fields are **excluded from `content_hash()`** — they are derivable from the already-hashed `edge` label and do not affect receipt verification against v0.15 receipts.
- **Lazy disclosure test**: `test_serve.py` now verifies that MCP `bulla.witness` returns `disclosure_set=[]` for fee=0 compositions (using `auth_pipeline.yaml`), covering the lazy disclosure guard added in v0.15.
- **LangGraph integration demo**: `examples/langgraph_demo.py` — a self-contained 4-tool trade pipeline that builds a LangGraph graph (schema-valid), extracts a Bulla `Composition` with manual annotation, and diagnoses hidden conventions invisible to the orchestrator. Frames `bulla gauge` (Sprint 17) as the automation target for the annotation step. LangGraph is not a project dependency.
- 1 new test (543 total).

### Changed
- **Paper draft** (`papers/hierarchical-fee/`): Abstract tightened from ~196 to ~153 words (submodularity detail removed). Non-negativity proof expanded with explicit projection lemma. 8-tool case study added to empirical table with fee/disclosure/bridge/boundary metrics. Conditional resolution section expanded with baseline → worst-case → resolved fee-drop numbers. Author affiliation added. Self-citations (`bridge`, `sheaf`, `scpi`) labeled as "Technical Report, Res Agentica" with repository URLs. LangGraph demo referenced in Related Work. Companion version updated to v0.16.
- **Self-citation provenance**: `bridge`, `sheaf`, `scpi` bibitems now carry "Technical Report, Res Agentica, 2026" labels with `\url{https://github.com/jkomkov/bulla}`.
- **Sync script tracked**: `scripts/sync-to-standalone.sh` added to version control.

## 0.15.0

### Added
- **Trace gap investigation**: Computationally verified that the Frobenius trace gap (`||delta_full||_F^2 - ||delta_obs||_F^2`) equals the total count of hidden-endpoint instances across blind spots. Closed as a non-informative weighted blind-spot count: it can be positive when the fee is zero (hidden columns in the span of observable columns) and adds no information beyond the existing blind-spot structure. Counterexample verified. Documented as a remark in the proof note.
- **Survey smoke test**: `tests/test_adversarial_survey.py` — imports core functions from the adversarial submodularity survey script and runs a minimal 10-composition smoke test to guard against silent regressions.
- **Trace gap test suite**: `tests/test_trace_gap.py` — verifies trace_gap == endpoint count for all 10 bundled compositions, fee > 0 implies trace_gap > 0, fee=0/trace_gap>0 counterexample, and same-fee-different-trace-gap distinguishability.
- 26 new tests (542 total): trace gap (22), survey smoke (4).

### Changed
- **Paper draft**: Proof note reorganized from theorem order to story order for submission. New sections: Introduction (opens with financial settlement failure narrative), Related Work (3 areas: contract-based design, sheaf cohomology, multi-agent orchestration), Conclusion (with explicit non-claim: "fee measures structural verifiability, not semantic correctness"). Case study expanded with "what could go wrong" failure scenario. Empirical table trimmed to 6 highlight rows. Bibliography expanded from 4 to 15 references. 831 lines (up from 660). Target venue: AAMAS 2027 or NeurIPS/ICML agent safety workshop.
- **Case study YAML annotations**: `financial_settlement_pipeline.yaml` now includes comment blocks explaining the semantic meaning of each edge's convention propagation (e.g., why `jurisdiction` maps to `risk_model_version`).
- **Lazy disclosure_set in MCP**: `_handle_witness` in `serve.py` now guards the `minimum_disclosure_set(comp)` call with `receipt.fee > 0`, skipping both coboundary matrix constructions when the fee is zero.

## 0.14.0

### Added
- **Submodularity disproved**: Adversarial survey of 10,000 random compositions (635,095 partition pairs) found 4,061 violations of `bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)`, with maximum violation magnitude 3. Minimal counterexample: 4 tools, 5 edges, where two partitions have bf=0 but their meet has bf=1. Individual `rho_full` and `rho_obs` are submodular (matroid rank on row sets), but their difference `bf = rho_full - rho_obs` is not.
- **8-tool case study**: `financial_settlement_pipeline.yaml` — realistic hierarchical financial settlement workflow with 8 tools, 8 edges, betti_1=1 (cycle via audit_log -> compliance_check). Fee=7, 8 blind spots, 15 bridges, 7-element minimum disclosure set (2.1x savings over bridges).
- **MCP `disclosure_set`**: `bulla.witness` now always returns a `disclosure_set` field — the minimum disclosure set as `[[tool, field], ...]`. Makes every witness call prescriptive by default.
- **MCP `partition` parameter**: `bulla.witness` accepts an optional `partition` parameter (array of arrays of tool name strings). When provided, the output includes a `decomposition` field with `total_fee`, `local_fees`, `boundary_fee`, `rho_obs`, `rho_full`, `boundary_edges`. Only present when partition is provided — existing consumers are unaffected.
- **Case study section in proof note**: 8-tool composition analysis with fee, disclosure set table, front/back-office partition decomposition, and conditional resolution round-trip.
- **Adversarial survey script**: `scripts/adversarial_submodularity_survey.py` — generates random compositions with random hidden/visible fields and checks submodularity across partition pairs.
- 7 new tests (516 total): submodularity counterexample (1), MCP disclosure_set and decomposition (6).

### Changed
- **`ConditionalDiagnostic.extended_comp`**: Type annotation fixed from `Composition = None # type: ignore[assignment]` to `Composition | None = None`.
- **Resolution monotonicity proof**: Strengthened from "internal states identical by construction" to "I_real ⊇ I_placeholder is a consequence of composition validity" (edge dimensions must reference existing internal_state fields).
- **Submodularity remark in proof note**: Upgraded from "computationally verified" to "disproved by adversarial counterexample" with formal analysis of why bf is not submodular (difference of submodular functions).
- **Bundled parametrized tests**: Partition sampling for compositions with > 50 binary partitions (8-tool composition has 254), keeping the test suite under 70 seconds.

### Empirical Results
- Submodularity disproved: 4,061/635,095 violations across 10,000 adversarial random compositions (0.64% violation rate). Bundled compositions (833 sampled pairs) still show zero violations — a topological accident of pipeline-like structure.
- 8-tool case study: fee=7, |S|=7=fee, |bridges|=15 >= 2*7=14. Front/back-office partition: local=(2,3), bf=2.
- Tower law verified: 2,778/2,778 sampled pairs across 10 bundled compositions.

## 0.13.0

### Added
- **`resolve_conditional`**: Resolve one or more placeholders in a conditional diagnostic. Rebuilds the composition with real tools swapped in, runs `diagnose`, and partitions obligations into met and remaining. Supports partial resolution (resolve some placeholders, leave others). Returns a `Resolution` dataclass with `resolved_diag`, `resolved_fee`, `fee_delta`, `met_obligations`, and `remaining_obligations`.
- **`Resolution` dataclass**: Result type for `resolve_conditional`. `fee_delta` is `worst_case_fee - resolved_fee` and is always non-negative (a real tool is at least as informative as a placeholder with empty observable schema).
- **`ConditionalDiagnostic.extended_comp`**: Stores the extended composition with placeholders, enabling `resolve_conditional` to work without the caller needing to reconstruct the composition.
- **Extremal boundary fee**: New proposition and tests for the all-hidden star topology. Partition `{Hub} | {S_1..S_n}` achieves `bf = total_fee = n` because all edges are cross-partition and both groups are internally edge-free. Grouping the hub with k spokes reduces `bf` by exactly k.
- **Submodularity survey**: Exhaustive survey across 333 partition pairs from all 9 bundled compositions confirms submodularity (`bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)`) with zero violations. Added helper functions `_partition_meet` and `_partition_join` for lattice operations.
- **Online resolution corollary**: Added to proof note — replacing a placeholder with a real tool can only decrease or maintain the coherence fee (resolution monotonicity).
- **Proof note updates**: Extremal cases section with theorem and landscape remark, submodularity remark, online resolution section with corollary and proof. Abstract and empirical results updated for v0.13.
- **`minimum_disclosure_set` documentation**: Non-uniqueness note in docstring and matroid rank submodularity comment on greedy loop.
- 19 new tests (493 total): `resolve_conditional` (8 unit + bundled parametrized), extremal star (11: hub-vs-spokes, mixed partition, singleton partition), submodularity survey (1 bundled parametrized across 9 compositions).

### Empirical Results
- `resolve_conditional` verified on 7 unit compositions (fee drop, obligation matching, partial resolution, round-trip with `minimum_disclosure_set`, from-scratch equivalence).
- Submodularity verified across 333 partition pairs (333/333).
- Extremal star: `bf = total_fee` for `{Hub}|{spokes}` partition verified for 2-5 spokes.

## 0.12.0

### Added
- **Minimum Disclosure Set** (`minimum_disclosure_set`): Given a composition, returns the smallest set of `(tool, field)` disclosures that reduces the coherence fee to zero. The cardinality always equals the fee — it is a basis for the quotient space `col(delta_full) / col(delta_obs)`. Greedy column selection finds one such basis. Removes at least 2x redundancy versus the existing bridges mechanism.
- **Valuation counterexample**: Computationally proved that the boundary fee is NOT a valuation on the partition lattice. For the A->B->C chain: `bf(P) + bf(Q) = 2` but `bf(P^Q) + bf(P v Q) = 1`. The same hidden convention at B causes boundary fee in both partitions, but resolving it once suffices.
- **Submodularity test**: Verified that the boundary fee satisfies submodularity (`bf(P^Q) + bf(P v Q) <= bf(P) + bf(Q)`) for the counterexample chain.
- **Two-step tower law induction test**: Hand-built 4-tool chain (A->B->C->D) verifying `bf(singletons) = bf(coarse) + bf(sub_AB) + bf(sub_CD)`. Also verified on bundled compositions with >= 4 tools.
- **Proof note update**: New "Minimum Disclosure Set" section with theorem (cardinality equals fee), proof, and bridges comparison remark. Non-valuation remark added to Tower Law section. Abstract and empirical results updated for v0.12.
- **`satisfies_obligations` docstring**: Documents that the function checks fields only — the caller filters obligations by placeholder name.
- 44 new tests (474 total): minimum disclosure set (5 unit + 27 bundled parametrized), valuation counterexample (1), submodularity (1), two-step tower law (1 unit + 9 bundled, 6 skipped for < 4 tools).

### Empirical Results
- `len(minimum_disclosure_set) == fee` verified across all 9 bundled compositions (9/9).
- `len(bridges) >= 2 * len(disclosures)` verified across all 9 bundled compositions (9/9).
- Applying disclosures reduces fee to 0 for all 9 bundled compositions.
- Removing any single disclosure from a minimal set leaves fee > 0 (minimality verified).
- Valuation property disproved; submodularity holds for tested cases.

## 0.11.0

### Added
- **Tower Law** (Theorem): The boundary fee is additive across levels of hierarchy. For a partition refined by sub-partitioning each group: `bf(refined) = bf(coarse) + sum(bf(sub_i))`. Proof is a 3-sentence telescoping argument from the decomposition theorem.
- **Monotonicity Corollary**: Refining a partition can only increase the boundary fee. The boundary fee defines a monotone function on the refinement lattice: 0 at the trivial partition, `total_fee` at singletons. Formalizes "every level of delegation adds non-negative hidden cost" as a theorem.
- **`satisfies_obligations`**: Checks whether a `ToolSpec` meets a set of `BoundaryObligation`s. Closes the conditional receipt loop: `conditional_diagnose` -> obligations -> candidate tool arrives -> `satisfies_obligations` -> recompute with real tool.
- **Proof note update**: Tower Law theorem, proof, Monotonicity corollary, and lattice remark added to `papers/hierarchical-fee/`. Empirical results updated with tower law verification data (264/264 pairs verified).
- **WITNESS-CONTRACT.md**: Tower Law and Monotonicity added as sub-properties of the hierarchical decomposition law.
- 25 new tests (430 total): tower law verification across all bundled compositions (9 tests), monotonicity under refinement (9 tests), obligation satisfaction checker (5 tests), edge cases for decompose_fee with 0 edges and shared-placeholder conditional diagnosis (2 tests).

### Changed
- **`_cross_rank_modulo_internal`**: Replaced fragile label string parsing (`split("→")`) with direct `Edge` iteration matching `_edge_basis` row ordering. Coupling comment documents the implicit contract between `diagnostic.py` and `coboundary.py`.
- **`conditional_diagnose` placeholder merging**: Replaced O(n) `tuple` membership check with `set` intermediate for deduplication.
- **LaTeX bibkeys**: Renamed `\bibitem{sheaf-paper}` to `\bibitem{sheaf}` for consistency.

### Empirical Results
- Tower law computationally verified across 264 coarse/refined partition pairs (264/264). Boundary fee survey unchanged: 64/70 (91%) of binary partitions have nonzero boundary fee.

## 0.10.0

### Added
- **Hierarchical fee decomposition** (`decompose_fee`): Takes a `Composition` and a partition of tool names, returns `FeeDecomposition` with per-group local fees, boundary fee, and the independent block-rank characterization (`rho_obs`, `rho_full`). The boundary fee is computed via `rho_full - rho_obs` (rank of cross-partition rows modulo internal rows) and verified against the remainder. Non-negativity proved via column-projection argument.
- **Conditional diagnosis** (`conditional_diagnose`): Diagnose partial compositions with open ports. Creates placeholder tools with empty observable schemas, runs existing `diagnose`, and returns `ConditionalDiagnostic` with worst-case fee, boundary obligations (fields placeholders must expose), and structural unknown count.
- **`FeeDecomposition` model**: Frozen dataclass with `total_fee`, `local_fees`, `boundary_fee`, `partition`, `boundary_edges`, `rho_obs`, `rho_full`.
- **`ConditionalDiagnostic` model**: Frozen dataclass with baseline/extended diagnostics, fee bounds, obligations, structural unknowns.
- **`OpenPort` model**: Describes an unconnected port in a partial composition for conditional diagnosis.
- **`BoundaryObligation` model**: Convention that an unspecified tool must declare observably.
- **WITNESS-CONTRACT.md**: Hierarchical Fee Decomposition law, structural vs epistemic unknown distinction.
- **Proof note**: `papers/hierarchical-fee/` — theorem (fee decomposition from block rank), counterexample, vanishing corollary, SCPI connection, empirical results.
- 37 new tests (405 total): counterexample chain, multi-dimension variant, full-disclosure vanishing, decompose_fee API tests, invariant tests across all bundled compositions (70 partitions), parametrized full-disclosure vanishing (chain + cycle), adversarial hidden interfaces (both-sides, star topology, one-side, mixed), conditional diagnosis (6 tests), empirical boundary fee survey.

### Empirical Results
- Boundary fee is nonzero in 64/70 (91%) of binary partitions across 9 bundled compositions. The hierarchical blind spot is the dominant regime, not a corner case. All 6 vanishing cases come from `auth_pipeline` (total fee = 0).

## 0.9.1

### Changed
- **`verify_receipt_integrity` is now forward-compatible**: Uses dict-exclusion (`to_dict()` minus `receipt_hash` and `anchor_ref`) instead of hardcoded field enumeration. Future field additions are automatically covered without code changes.
- **`WitnessReceipt._hash_input()`**: Single source of truth for the receipt's hashable content. `receipt_hash`, `to_dict()`, and `verify_receipt_integrity` all derive from this one method, eliminating field enumeration duplication.
- **`receipt_hash` is now cached**: Computed once on first access using lazy cache on the frozen dataclass. Eliminates redundant SHA-256 computation when `to_dict()` or receipt chaining access the hash multiple times.
- **`verify_receipt_consistency` now verifies disposition**: Recomputes `_resolve_disposition` from the diagnostic, policy, and unknown_dimensions, and compares to the receipt's claimed disposition. A receipt can no longer claim `proceed` when measurements would produce `refuse_pending_disclosure`.
- **`Diagnostic` is now frozen** with immutable `tuple` fields (`blind_spots`, `bridges`). Completes the immutability chain: `Composition`, `Diagnostic`, and `WitnessReceipt` are all frozen with tuple fields.
- **`Bridge.add_to` is now `tuple[str, ...]`** (was `list[str]`). Consistent with all other frozen dataclass fields.
- **Development Status**: Alpha → Beta. The kernel is feature-complete with 368 tests, verification functions, immutable constitutional objects, and a normative spec.

### Added
- Anti-reflexivity AST test: `diagnostic.py` has zero imports from `serve.py`. The measurement layer is now provably isolated from both witness and transport layers.

## 0.9.0

### Breaking Changes
- **`witness_basis.unknown` overrides `unknown_dimensions`**: When `witness_basis` is provided to `witness()`, `witness_basis.unknown` now determines both the receipt's `unknown_dimensions` field and the policy disposition. The explicit `unknown_dimensions` parameter becomes a fallback for non-attested cases only. This eliminates lying receipts where `witness_basis.unknown=5` could coexist with `unknown_dimensions=0` and a `proceed` disposition under `max_unknown=3`.
- **`Composition.tools` and `Composition.edges` are now `tuple`**: Previously `list`. The `frozen=True` dataclass was misleading when fields were mutable. All construction sites now use `tuple()`. Code that indexes or iterates is unaffected; code that calls `.append()` on these fields must change.

### Added
- **`verify_receipt_consistency(receipt, comp, diag)`**: Checks that a receipt's claimed hashes and counts match the given composition and diagnostic objects. Returns `(is_valid, violations)`.
- **`verify_receipt_integrity(receipt_dict)`**: Self-contained tamper detection from a serialized receipt dict. Recomputes the SHA-256 hash from the dict's fields and compares to the claimed `receipt_hash`. No kernel or original objects required.
- **Public API exports**: `PackRef`, `WitnessBasis`, `verify_receipt_consistency`, and `verify_receipt_integrity` are now exported from `bulla`.
- **MCP active_packs threading**: `bulla.witness` and `bulla.bridge` MCP handlers now pass configured pack refs to the witness kernel. Receipts emitted via MCP record the active lexical constitution.
- **`bulla.bridge` schema parity**: `unknown_dimensions` and `witness_basis` parameters added to `bulla.bridge` input schema, matching `bulla.witness`.
- **Mathematical invariant test suite**: `test_invariants.py` with 67 parametrized tests across all bundled compositions: coherence fee non-negativity, bridging monotonicity, basis/unknown consistency, verification round-trips, tamper detection, hash determinism, and pack order sensitivity.
- 74 new tests (366 total)

### Fixed
- **`bulla://taxonomy` resource**: Now returns the merged pack stack (via `load_pack_stack()`) instead of the raw `taxonomy.yaml` file, consistent with the pack system.
- **Bridge handler parity**: `_handle_bridge` now threads `unknown_dimensions`, `witness_basis`, and `active_packs` through both the original and patched witness calls.

## 0.8.0

### Added
- **Structured MCP output**: `bulla.witness` and `bulla.bridge` now return `structuredContent` (typed dict) alongside the `content` text fallback. Both tools declare `outputSchema` so agents can consume receipts as typed objects without JSON parsing.
- **Operative policy thresholds at MCP boundary**: policy input accepts a full object (`name`, `max_blind_spots`, `max_fee`, `max_unknown`, `require_bridge`) in addition to a bare string name. Custom thresholds now actually govern disposition — `max_unknown` is no longer dead code.
- **Receipt chaining**: `WitnessReceipt` gains `parent_receipt_hash`. When `bulla.bridge` produces a patched receipt, it links back to the original. Enables auditable chains: original → repair → patched.
- **Convention pack overlays**: `src/bulla/packs/` directory with layered, mergeable convention packs. Ships `base.yaml` (the 10 reference dimensions, moved from `taxonomy.yaml`) and `financial.yaml` (4 financial-specific dimensions: `day_count_convention`, `settlement_cycle`, `fee_basis`, `rounding_mode`). Later packs override earlier ones with `logger.warning` on dimension collisions.
- **`--pack` CLI flag**: `bulla diagnose`, `bulla check`, `bulla infer`, and `bulla scan` accept `--pack FILE` (repeatable) to load additional convention packs.
- **`PackRef` model**: ordered pack references (`name`, `version`, `hash`) stored on receipts. Order is semantics — `[base, financial]` and `[financial, base]` produce different receipt hashes.
- **`WitnessBasis` model**: epistemic provenance dataclass (`declared`, `inferred`, `unknown`). Accepted as a parameter on `witness()` — the kernel records what the caller attests, never fabricates provenance.
- **Provenance threading**: `BullaGuard.from_mcp_manifest()` and `BullaGuard.from_mcp_server()` now aggregate classifier confidence tags into a `WitnessBasis`, available via `guard.witness_basis`.
- **Pack-aware classifier**: `classifier.py` loads from a configurable pack stack via `configure_packs()` / `load_pack_stack()`. Content hashes are SHA-256 of parsed canonical JSON, not raw YAML bytes.
- 55 new tests (292 total)

### Changed
- `_resolve_disposition()` now enforces `max_unknown`: compositions exceeding the threshold receive `REFUSE_PENDING_DISCLOSURE`.
- MCP `bulla.witness` input schema accepts `unknown_dimensions` (integer) and `witness_basis` (object) parameters.
- MCP `bulla.bridge` handler sets `parent_receipt_hash` on the patched receipt.
- `WitnessReceipt.receipt_hash` computation includes `parent_receipt_hash`, `active_packs`, and `witness_basis`.
- `WitnessReceipt.to_dict()` includes `parent_receipt_hash`, `active_packs`, and `witness_basis`.

## 0.7.0

### Changed
- **Renamed from `seam-lint` to `bulla`** — package, CLI, Python imports, and all public API names.
- `SeamGuard` → `BullaGuard`, `SeamCheckError` → `BullaCheckError`
- `to_seam_patch()` → `to_bulla_patch()`, `seam_patch_version` → `bulla_patch_version`
- `seam_manifest` YAML key → `bulla_manifest` (parser accepts both for one version cycle)
- MCP server tools: `bulla.witness`, `bulla.bridge`; resource URI: `bulla://taxonomy`
- CLI entry point: `bulla` (was `seam-lint`)
- SARIF rule IDs: `bulla/blind-spot`, `bulla/bridge-recommendation`
- PyPI package: `pip install bulla`

## 0.6.0

### Added
- **Witness kernel** (`witness.py`): deterministic measurement → receipt pipeline with three-layer separation (measurement / binding / judgment)
- **Constitutional objects**: `Disposition` enum (5 levels), `BridgePatch` (frozen, Bulla Patch v0.1), `WitnessReceipt` (content-addressable, tamper-evident)
- **`bulla serve`** — MCP stdio server exposing 2 tools + 1 resource:
  - `bulla.witness`: composition YAML → WitnessReceipt (atomic measure-bind-judge)
  - `bulla.bridge`: composition YAML → patched composition + receipt + before/after metrics
  - `bulla.taxonomy` resource: convention taxonomy for agent inspection
- **`bulla bridge`** — auto-generate bridged composition YAML or Bulla Patches from diagnosed composition
- **`bulla witness`** — diagnose and emit WitnessReceipt as JSON
- **`Diagnostic.content_hash()`** — deterministic SHA-256 of measurement content (excludes timestamps)
- **`load_composition(text=)`** — parser accepts string input for MCP server use
- **Policy profile**: `witness()` and `_resolve_disposition()` accept named `policy_profile` parameter (default: `witness.default.v1`), recorded in receipt and receipt hash
- **Bulla Patch v0.1**: `BridgePatch.to_bulla_patch()` — explicitly typed patch format, not RFC 6902
- **Typed error vocabulary**: `WitnessErrorCode` enum (4 codes), `WitnessError` exception
- **Anti-reflexivity enforcement**: AST-level test proves `diagnostic.py` has zero imports from `witness.py` (Law 1); bounded recursion via `depth` parameter with `MAX_DEPTH=10` (Law 7)
- **Three-hash boundary**: `composition_hash` (what was proposed), `diagnostic_hash` (what was measured), `receipt_hash` (what was witnessed) — tested for independence
- 33 new tests (233 total)

### Fixed
- **Bridge generation bug**: when `from_field != to_field` and both sides hidden, destination tool received wrong field. Now generates separate Bridge per side with correct field.

### Changed
- `to_json_patch()` renamed to `to_bulla_patch()` with `bulla_patch_version: "0.1.0"` field
- `receipt_hash` docstring documents timestamp inclusion semantics (unique event identity vs deduplication via `diagnostic_hash`)
- Bridge response includes `original_composition_hash` for traceability

## 0.5.0

### Added
- **Three-tier confidence: "unknown" tier now live** — single description-keyword-only or weak schema signals (enum partial overlap, integer type inference) now correctly produce `unknown` instead of the dead-branch `inferred`
- **0-100 range disambiguation** — fields with `minimum: 0, maximum: 100` now check field name and description for rate/percent indicators before choosing `rate_scale` vs `score_range`
- **Domain-aware prioritization** — `classify_tool_rich()` accepts `domain_hint` (e.g. `"financial"`, `"ml"`) to boost domain-relevant dimensions from `unknown` → `inferred`
- **`_normalize_enum_value()` helper** — single source of truth for enum normalization (lowercase, strip hyphens/underscores), replacing duplicated inline logic
- **Real MCP validation suite** — 5 realistic tool definitions (Stripe, GitHub, Datadog, Slack, ML) with per-tool coverage assertions
- **End-to-end coverage test** — real MCP JSON → generate manifests → validate → assert ≥6/10 dimensions detected
- **Domain map API** — `_get_domain_map()` loads taxonomy `domains` metadata (previously defined but unused)
- 16 new tests covering unknown tier, range disambiguation, domain boosting, normalization, real-tool coverage, and E2E pipeline (178 total)

### Changed
- `_merge_signals()` accepts `domain_hint` parameter for confidence boosting
- `classify_tool_rich()` accepts `domain_hint` parameter (backward-compatible, defaults to `None`)
- Description-only signals now produce `unknown` confidence (was incorrectly `inferred`)

### Fixed
- Dead `else` branch in `_merge_signals()` — the "unknown" confidence tier was unreachable (all paths produced "inferred")
- Field name propagation for description hits in `_merge_signals()` — description hits now inherit field names from co-occurring name/schema hits
- Circular import between `classifier.py` and `mcp.py` now documented with inline comment
- False positive: `format: "uri"` / `"email"` / `"uri-reference"` no longer mapped to `encoding` dimension — these are string formats, not encoding conventions
- False positive: `count` removed from `id_offset` field name patterns — count is a quantity, not an index
- Text formatter now explains fee-vs-blind-spots divergence when fee = 0 but blind spots exist

## 0.4.0

### Added
- **Multi-signal convention inference**: classifier now uses three independent signal sources instead of field-name regex alone
  - Signal 1: Field name pattern matching (existing, now taxonomy-compiled)
  - Signal 2: Description keyword matching — detects conventions from tool/field descriptions (e.g. "amounts in cents", "ISO-8601 timestamps")
  - Signal 3: JSON Schema structural signals — `format`, `type`+range, `enum`, `pattern` metadata
- **Nested property extraction**: recursive extraction of fields from nested JSON Schema objects with dot-path naming (e.g. `invoice.total_amount`), depth limit 3
- **Taxonomy as single source of truth**: `field_patterns` from `taxonomy.yaml` now compile into classifier regex at load time; `known_values` drive enum matching
- **Three-tier confidence model**: `declared` (2+ independent signals agree), `inferred` (1 strong signal), `unknown` (weak/ambiguous) — replaces the binary high/medium system
- `FieldInfo` dataclass for rich field metadata (type, format, enum, min/max, pattern, description)
- `classify_tool_rich()` high-level API for full multi-signal classification of MCP tool definitions
- `classify_description()` for extracting dimension signals from tool descriptions
- `classify_schema_signal()` for extracting dimension signals from JSON Schema metadata
- `description_keywords` per dimension in taxonomy (v0.2)
- Currency codes (USD, EUR, GBP, JPY, CNY, BTC) added to `amount_unit` known_values
- `extract_field_infos()` public API for rich field extraction from tool schemas
- Manifest generation now uses multi-signal classifier with `sources` metadata in output
- 41 new tests covering all signal types, confidence tiers, and round-trip validation (162 total)

### Changed
- Confidence values in generated manifests are now directly `declared`/`inferred`/`unknown` — the `_CONFIDENCE_MAP` translation layer is removed
- `infer_from_manifest()` output now includes signal sources in review comments
- Taxonomy version bumped to 0.2

### Fixed
- Version string tests now use `__version__` import instead of hardcoded values

## 0.3.0

### Added
- `bulla manifest --publish` — anchor manifest commitment hash to Bitcoin timechain via OpenTimestamps
- `bulla manifest --verify` — verify OTS proof on a published manifest
- `bulla manifest --verify --upgrade` — upgrade pending proofs to confirmed after Bitcoin block inclusion
- Optional `[ots]` extra: `pip install bulla[ots]` (base install stays single-dependency)
- Commitment hash excludes OTS fields for deterministic verification after publish
- 11 new OTS tests (mocked calendars, no network required)

## 0.2.0

### Added
- `bulla manifest` — generate and validate Bulla Manifest files from MCP tool definitions
- `bulla manifest --from-json` — generate from MCP manifest JSON
- `bulla manifest --from-server` — generate from live MCP server
- `bulla manifest --validate` — validate existing manifest YAML
- `bulla manifest --examples` — generate example manifests to see the format
- `bulla scan` — scan live MCP server(s) via stdio and diagnose
- `bulla init` — interactive wizard to generate a composition YAML
- `bulla diagnose --brief` — one-line-per-file summary output
- `BullaGuard` Python API for programmatic composition analysis
- Convention taxonomy (10 dimensions) with field-pattern inference
- Auto-validation after manifest generation
- "Now what?" guidance in `check` output on failure
- Quickstart guide when running bare `bulla` with no subcommand
- SARIF output format for GitHub Code Scanning integration

### Fixed
- Confidence mapping: classifier internal grades (`high`/`medium`) now correctly map to manifest spec vocabulary (`declared`/`inferred`/`unknown`)
- `_examples_dir()` portability for installed packages

## 0.1.0

### Added
- `bulla diagnose` — full sheaf cohomology diagnostic with blind spot detection
- `bulla check` — CI/CD gate with configurable thresholds
- `bulla infer` — infer proto-composition from MCP manifest JSON
- Text, JSON, and SARIF output formats
- Exact rational arithmetic (no floating-point) via Python `Fraction`
- 9 bundled example compositions (financial, code review, ETL, RAG, auth, MCP)
- 107 tests, single dependency (PyYAML)
