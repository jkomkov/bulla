# ActionReceipt v0.2 — wire spec (normative)

**Status:** normative, 2026-07-13. Supersedes `action-receipt-v0.1.md` for
producers; v0.1 remains valid for verification (a v0.1 receipt recomputes with
its own `schema_version` forever). Design authority:
`ADR-001-standing-model.md`. Everything that changes canon ships in this one
revision, so canon migrates exactly once.

A receipt records **one consequential agent action**: what was done, under
whose authority, within what bounds, with what recomputable verdict, on what
grounding, under which coined conventions, and how it is contested. This
document is sufficient to verify a receipt **without the bulla source** — the
golden vectors in `vectors/` pin every hash and verdict below, and
`vectors/independent_check.py` (Python stdlib only, zero bulla imports) is the
executable form of this claim.

## 1. Canonicalization (`CANON_VERSION` 2 — the one rule)

Every hash is over the **canonical JSON** of a value:

```
canonical(x) = json.dumps(x, sort_keys=True, separators=(",", ":"))   # UTF-8
H(x)         = "sha256:" + hex(sha256(canonical(x)))
```

Object keys are sorted; arrays keep their given order; no insignificant
whitespace. Every hash carries the `sha256:` prefix so an algorithm bump is
detectable. This is the rule for **every layer** — the action receipt, the
certificate/deed layer, and the WitnessReceipt measurement layer (which, prior
to CANON_VERSION 2, hashed with spaced separators; see §1.2).

### 1.1 Relationship to RFC 8785 (JCS)

CANON_VERSION 2 is JCS-compatible in structure (sorted members, no
whitespace) with two **deliberate deviations**, chosen so every existing
deed-layer hash stays byte-valid:

1. **Non-ASCII escaping.** Characters outside ASCII are emitted as `\uXXXX`
   escapes (Python `ensure_ascii=True`), where JCS §3.2.2.2 emits raw UTF-8.
2. **Numbers.** Hashed material SHOULD restrict numbers to integers (all
   quantities in minor units / quanta — see §5). Where floats occur, Python
   `repr` formatting applies rather than the ES6 rules of JCS §3.2.2.3.
   Producers MUST NOT rely on cross-language float serialization agreement.

Additionally, key sorting is by Unicode code point; JCS sorts by UTF-16 code
unit. These agree on all BMP keys, so object keys in hashed material MUST NOT
contain non-BMP characters.

### 1.2 Versioning and the legacy form

`CANON_VERSION` is `2`. WitnessReceipts mint their `receipt_hash` over the
canonical form and stamp `canon_version: 2` inside the hashed content. A
verifier encountering a pre-v2 WitnessReceipt (no `canon_version` stamp, hash
minted over `json.dumps(x, sort_keys=True)` — spaced) MUST attempt the
canonical form first, then the legacy spaced form, and report which matched.
**A format change is a version difference, not tampering.** Deed-layer and
action-receipt hashes are byte-identical under v1 and v2 (that layer was
always compact).

## 2. Document shape

```jsonc
{
  "schema_version": "0.2",
  "kind": "action_receipt",
  "action":   { "type": "<open vocab>", "subject": { ... } },   // e.g. "package.release", "github.create_file"
  "diagnostic_ref": { "status": "reference"|"not_applicable"|"deferred", "ref"?: "sha256:…" },
  "evidence_refs": [ { "name": "<str>", "hash": "sha256:…", "grounding": "<class>" }, ... ],
  "anchor_ref": { ... },                 // {"kind":"git"|"pypi"|…, "ref":"…", "root_of_trust"?:{…}}
  "mandate":   { "authority": {…}, "bounds": {…} },     // ex ante legitimacy
  "remedy":    { "challenge_window": "…", "forum": {…}, "remedies": [ … ] },  // ex post contestation
  "retention": { "record": "<class>", "disclosure": "<class>" },
  "stake": null,                         // RESERVED (the bond slot) — always null in v0.2
  "conventions": [ … ],                  // coined-at-the-seam rules — §5; may be empty
  "signature": { … } | null,             // detached proof over hashes.content
  "timestamp": "<ISO-8601>",
  "producer":  { "bulla_version": "…", ... },   // provenance, NOT identity
  "hashes": { "content": "sha256:…", "event": "sha256:…", "attestation": "sha256:…", "log_leaf": "sha256:…" }
}
```

`mandate` / `remedy` / `retention` are **named views** over one recourse
envelope (§4); the *envelope* is what the attestation hash commits.

Changes from v0.1: `schema_version` is `"0.2"`; every `evidence_refs` entry
carries a required `grounding` (§3); the `conventions` list exists (§5) and
enters the content hash whenever non-empty.

## 3. Evidence grounding classes

`grounding` ∈:

| Class | Meaning | Canonical example |
|---|---|---|
| `self_asserted` | produced by the acting party; testimony with a timestamp | the actor's own execution log |
| `counterparty_signed` | bears the signature of the party on the other side of the act | a countersigned mandate, a signed delivery acknowledgment |
| `third_party_anchored` | held or countersigned by an independent system the actor does not control | a rail's transaction record, a git commit on a remote with protected history the actor does not administer |
| `execution_verified` | re-derivable by re-running a pinned computation from pinned inputs | a deterministic recomputation, a replayed transformation |

**Relativity note:** classes rank relative to the challenging party — a
counterparty signature grounds the record only against its signer; against a
stranger to the transaction (a regulator, an insurer, a third party harmed),
bilateral collusion makes independent third-party anchors the stronger class.
The table order above (weakest first: `self_asserted`, `counterparty_signed`,
`third_party_anchored`, `execution_verified`) is the default,
stranger-relative ranking used by the display rule.

**Display rule (normative).** A receipt's effective grounding is the
**minimum** class over its *necessary* evidence — the set without which the
verdict does not recompute (v0.2 reference verifiers treat all carried
evidence as necessary). Verifiers MUST surface effective grounding alongside
the verdict; a `digest`-valid receipt whose necessary evidence is
`self_asserted` MUST NOT be presented as more than attested testimony.

**Rationale:** the receipt proves the claim and the authority; the world
enters through the anchors, and the record inherits the grounding of its
weakest necessary anchor. Making grounding first-class prevents the elision
("recomputable" heard as "true") that this field's absence invites.

## 4. The four hashes and the recourse envelope

Unchanged from v0.1 except where noted; restated normatively.

1. **`content`** — *"recompute the verdict."* Preimage:
   ```
   { "schema_version", "kind", "action", "diagnostic_ref", "evidence_refs",
     "anchor_ref", "conventions"? }
   ```
   `schema_version` is the receipt's **own** stored value. `conventions` is
   present in the preimage **iff** the list is non-empty — so a coined rule
   can be neither altered nor silently stripped without breaking the hash.
   Envelope-free, time-free, signature-free: identical on any machine, any
   producer version, forever.

2. **`event`** — *"which occurrence."* `H({ "content_hash": <content>, "timestamp": <timestamp> })`.

3. **`attestation`** — *"who vouched."*
   `H({ "content_hash": <content>, "signature": <signature or null>, "recourse_envelope": <envelope> })`
   where `<envelope>` is reconstructed from the views exactly as in v0.1:
   ```
   recourse_envelope = {
     "deed_schema": "0.2",
     "authority":  mandate.authority,          // omit if absent
     "bounds":     mandate.bounds,             // omit if absent
     "recourse":   remedy,                     // omit if empty
     "retention_class":  retention.record,     // omit if absent
     "disclosure_class": retention.disclosure  // omit if absent
   }
   ```

4. **`log_leaf`** — *"where logged."* RFC 6962 leaf:
   `"sha256:" + hex(sha256(0x00 ‖ utf8(<attestation_hash string>)))`.

**The modality law (a verifier MUST enforce it).** Recourse has no stateful
respondent — the actor is gone at contest time. Therefore: every remedy names
a non-empty `verifier` **and** a non-empty `anchor`; an `escalate` remedy
requires `authority`; `forum.trusted_root_ref` is required (a forum that
verifies against the host's own served root is self-consistency, not
recourse); remedy `rung` ∈ `{recompute, challenge, cure, revert, slash,
escalate}`.

## 5. Conventions — predicate invention, auditable

Agents coordinating across a seam coin rules the schema never anticipated —
in-line, on-the-fly DDL. v0.2 makes the coined rule a first-class, hashed
part of the record. Each entry:

```jsonc
{
  "name": "<str>",                   // required, non-empty
  "scope": "<str>",                  // required — the seam the rule binds, e.g. "seam:caller->payments.charge"
  "kind": "executable" | "semantic", // the DECIDABILITY BOUNDARY — see below
  "definition": …,                   // required for executable; optional opaque string for semantic
  "definition_hash": "sha256:…",     // required — the pin (§5.3)
  "forum": { "log_endpoint", "trusted_root_ref" }   // required for semantic
}
```

The `kind` discriminator IS the decidability boundary:

* **`executable`** — the definition is written in a small **declared form**
  (§5.1), not a general language, and any verifier **recomputes conformance**
  of the act's declared subject against it from the receipt alone.
  Enforcement = recompute.
* **`semantic`** — the definition is opaque (natural language, an external
  document), pinned by `definition_hash`. No verifier can decide it;
  enforcement is **recourse**, so `forum` is required and obeys the same
  Pin-the-Root law as the envelope's forum (§4). Verifiers report such
  entries as `pinned`, never as conforming.

### 5.1 The executable form: `jsonschema+quantum/1`

```jsonc
"definition": {
  "form": "jsonschema+quantum/1",
  "schema": {                    // constraint over action.subject
    "type": "object",            // (optional; "object" is the only value)
    "required": [ "<field>", … ],
    "additionalProperties": true|false,
    "properties": {
      "<field>": { "type": "string"|"integer"|"number"|"boolean",
                   "enum": […], "const": …, "minimum": n, "maximum": n,
                   "pattern": "<regex>" }
    }
  },
  "quantum": {                   // unit/quantum declaration (optional)
    "<field>": { "unit": "<str>", "multipleOf": <positive integer> }
  }
}
```

The keyword vocabulary above is **closed**. A definition using any other
keyword is malformed; a verifier MUST fail closed (refuse the receipt at
construction/parse), never skip the unknown constraint. Quantized fields MUST
be integers in minor units (`multipleOf` a positive integer) — decidable
arithmetic, no float ties. `pattern` is an (unanchored) regular-expression
search.

**Conformance** of a receipt's act against an executable convention is
evaluated over `action.subject`: every `required` field present; declared
types, `enum`/`const`, bounds, and patterns satisfied; every quantum field an
integer multiple of its `multipleOf`. The verdict is `conforms` or
`violates`, with reasons.

### 5.2 Conformance is surfaced, not folded in

A convention violation is a verdict about the **act**; hash integrity is a
verdict about the **record**. A receipt recording a non-conforming act is
still a valid record. Verifiers MUST surface per-convention status
(`conforms` / `violates` / `pinned`) alongside `verified_to`, and MUST NOT
fail hash verification on a conformance violation — nor present a `pinned`
semantic convention as checked.

### 5.3 The pin

* structured (executable) definition: `definition_hash = H(definition)`
  (canonical JSON, §1);
* opaque (semantic) definition string: `definition_hash = "sha256:" +
  hex(sha256(utf8(definition)))`; the string itself MAY be omitted from the
  receipt — the hash alone pins an externally held text.

A present definition whose hash does not match `definition_hash` makes the
receipt invalid (that is forgery at the entry level, distinct from the
content hash which covers the whole list).

### 5.4 The convention graph is emergent (ADR-001)

The global graph of coined conventions is the transitive closure of
referenced definitions across published receipts. It is **emergent — never an
operated product**. No conformant implementation may gate convention
resolution behind a proprietary registry; a convention resolves from its
in-line definition or its hash-pinned external text, wherever served
(any-log, §6).

## 6. Any-log verification

The verification path — signature check, content recomputation, inclusion
proof — MUST NOT depend on which operator's log served the receipt. A receipt
carried by any log verifies identically. Log identifiers are informative,
never authoritative; consistency is checked against anchored/gossiped
checkpoints (hook: `registry.py:527`). The reference implementation's
single-operator scope (`registry.py:57`) is a limitation of the
implementation, not a property of the format. Design target: Certificate
Transparency's plurality structure.

## 7. Standing recomputability (normative principle)

Any standing, score, or reputation derived from receipts MUST be recomputable
from published records using a published algorithm. An implementation or
operator offering standing that the public record cannot reproduce is
non-conformant. (ADR-001 §1; the receipts analog of "sell the receipt, never
the score.")

## 8. Tolerance semantics — `verification_semantics` (claim-level)

The receipt layer is rightly binary: hashes either recompute or they do not,
and byte equality governs §1. But claims *inside* receipts increasingly
attest recomputations of ML workloads that are not bit-exact across hardware.
Where an attested recomputation claim carries a tolerance, the accept/reject
semantics live **in the signed record**, not verifier-side policy:

```jsonc
"verification_semantics": {
  "comparison_fn": "<namespaced ref, e.g. toploc-lsh-v1 | difr-token-v1 | exact>",
  "threshold": "…",
  "seed_regime": { "sampling_seeds": "verifier-supplied", "seed_digest": "sha256:…" },
  "environment_class": { "kernels": "…", "hw_class": "…" }
}
```

This block rides inside the claim it qualifies (e.g. within
`action.subject` or an evidence descriptor) and is therefore covered by the
content hash. **Layering precision:** it changes nothing about
canonicalization or chain verification — byte equality still governs the
receipt; declared tolerance governs only the attested recomputation claim.
This spec standardizes the carrier, not the comparison function.

## 9. Verification levels (`verified_to`) — honest about depth

A verifier reports the **highest** rung it reached, never a lying boolean:

- **`digest`** — the four hashes recompute and match; the envelope
  re-validates (modality law); every `evidence_ref` has a name, hash, and (in
  v0.2) a valid grounding; `diagnostic_ref` has a valid non-null status;
  every convention entry validates (§5). Zero dependencies.
- **`attestation`** — additionally, the detached `signature` over
  `hashes.content` verifies (ed25519 / did:key or COSE). Skipped, not failed,
  when the receipt is unsigned.
- **`log_inclusion`** — additionally, an external inclusion proof (Rekor / an
  RFC 6962 registry) binds the receipt to a public log. v0.2 carries no
  inline proof; this rung is the `bulla[sigstore]` follow-up. **Named, never
  faked.**

Alongside the rung, a verifier MUST surface: effective grounding (§3) and
per-convention conformance (§5.2).

A receipt whose content was altered and whose hashes were *recomputed* by an
adversary still fails at `attestation`: the signature is over `content`, so
any change invalidates it and it cannot be re-forged without the key.

## 10. Genesis / root of trust

A `package.release` receipt for the release that *introduces* receipts cannot
be its own root of trust. Its `anchor_ref.root_of_trust`
(`{scheme:"sigstore-pep740", rekor_log_index, attestation_bundle_sha256}`)
points at the **external** PEP 740 / Sigstore attestation; bulla binds to
that public log, it does not replace it. Verifying that binding is the
`log_inclusion` rung.

## Open items (dated)

- Gossip transport for checkpoint comparison: unspecified (2026-07-11).
- Registration-refusal mitigation beyond open-spec/run-your-own-log: policy
  only (2026-07-11) — registration criteria must be published and
  content-neutral.
- A second executable-definition form (beyond `jsonschema+quantum/1`):
  deliberately not specified (2026-07-13); any addition must keep the closed
  keyword vocabulary and stdlib-decidable evaluation.
- Inline inclusion proofs (`log_inclusion` reachable from the receipt alone):
  the `bulla[sigstore]` follow-up (2026-07-04).
