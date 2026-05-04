# Update Protocol — `bulla diff` + `bulla repair`

**Status:** Design (Sprint 2 Day 0 deliverable; implementation gated by §9.5 proof completion).

The update protocol is Bulla's wire-protocol artifact for **coherence-preserving interface evolution**. When two MCP server manifests differ — a SEP-1400 minor-version bump, a SEP-1575 compatibility class change, an enum narrowing, a field rename — `bulla diff` decides whether the difference preserves witness rank (i.e., whether existing receipts remain valid without re-derivation), and `bulla repair` outputs the minimum patch to make a non-preserving update preserving.

This is the operational realization of **Conjecture 9.5 (Coherence-Preserving Update)** from the Composition Doctrine paper, paired with **§6.2 Repair Duality**. The math is sketched at `papers/composition-doctrine/notes/9-5-attack-phase-{a,b,c}.md`; this document is the engineering specification.

## §1. Why this exists

The MCP ecosystem ships server upgrades constantly. Each upgrade is potentially:

- **Coherence-preserving** — schema changed but cohomologically equivalent; receipts transport without re-derivation
- **Coherence-changing** — schema changed in a way that breaks the seam complex; existing receipts are invalid

Today this is determined by hand (or not at all): operators read SEP changelogs and *guess*. The cost of guessing wrong is silent semantic drift across the composition graph — exactly the problem Bulla exists to detect.

`bulla diff` makes the determination automatic and machine-verifiable. `bulla repair` makes it actionable: when the upgrade is coherence-changing, it outputs the minimum disclosure patch making it coherence-preserving via post-composition.

## §2. The pair: diff and repair

The CLI is two commands working together:

```bash
# 1. Diagnose
bulla diff old.json new.json
# → coherence-preserving: true | false
# → on false: list of failing cocycles + minimum repair size

# 2. Patch (only when diff said false)
bulla repair old.json new.json
# → reads same diff data
# → outputs patched-new.json with minimum disclosures added
# → outputs receipt witnessing the repair

# 3. Verify (idempotency check)
bulla diff old.json patched-new.json
# → coherence-preserving: true (must hold by construction)
```

**The pairing is not incidental.** §6.2 of the doctrine paper proves that the minimum-disclosure repair has cardinality exactly `r(G)` (the witness rank). Specialized to updates: the failing cocycles output by the diff procedure are exactly the minimum-disclosure repair set. So the same computation that *detects* non-preservation also *constructs* the patch.

This is the **operational form of repair duality**, applied at the update layer. The empirical claim Bulla makes:

> Every coherence-changing MCP update has a minimum repair patch of cardinality `r(G_diff)`, computable from the diff data in polynomial time.

## §3. Theoretical basis

### 3.1 Theorem 9.5-A (rank invariance under chain-homotopy)

If `f: G → G'` induces a chain-homotopy equivalence `f^•: C^•(G) → C^•(G')`, then `r(G) = r(G')` and any valid receipt for `G` transports to a valid receipt for `G'` via the explicit chain-homotopy data `(f^•, g^•, h, h')`.

**`bulla diff` checks chain-homotopy equivalence** via the mapping-cone acyclicity test (Phase E §E of the math notes):

```
mapping_cone = cone(f^•)
preserves := H^0(mapping_cone) = 0 ∧ H^1(mapping_cone) = 0
```

When `preserves = true`, the algorithm extracts the chain homotopy `h` via standard kernel/image splitting (Moore–Penrose pseudoinverse). This `h` is the transport certificate.

### 3.2 Theorem 9.5-B (decision procedure)

The chain-homotopy test is decidable in `O((|G| + |u|)^ω)` over ℚ, where `ω < 2.373` is the matrix-multiplication exponent. Concretely: build coboundary matrices `D, D'`, build the chain map `f^•` from the schema-diff data, build the mapping cone matrix, run a rank computation. All steps are exact rational arithmetic.

### 3.3 §6.2 Repair Duality (operational specialization)

For any complex `G` with `r(G) > 0`, the minimum-disclosure repair has cardinality exactly `r(G)`. Specialized:

> If `bulla diff` returns `coherence-preserving: false` with failing cocycle basis `(c_1, …, c_r)`, then the minimum patch promoting these cocycles to coboundaries (via post-composition disclosure) has cardinality exactly `r`. The patch is constructible from the cocycle basis by Moore–Penrose disclosure assignment.

`bulla repair` outputs this minimum patch.

## §4. CLI spec

### 4.1 `bulla diff`

```
USAGE:
  bulla diff <old> <new> [OPTIONS]

ARGS:
  <old>  Path to old manifest (JSON, MCP tools/list response)
  <new>  Path to new manifest (JSON, MCP tools/list response)

OPTIONS:
  --format text|json|sarif    Output format (default: text)
  --linear-data <FILE>         Optional explicit chain-map data (resolves logic-drift cases)
  --policy <PROFILE>           Policy profile for receipt emission
  --output <FILE>              Write certificate JSON to file (default: stdout)

EXIT CODES:
  0  Coherence-preserving (receipts transport)
  1  Coherence-changing (run `bulla repair` to construct patch)
  2  Requires linear data (textual diff ambiguous; supply --linear-data)
  3  Input error (malformed manifest)
```

### 4.2 `bulla repair`

```
USAGE:
  bulla repair <old> <new> [OPTIONS]

ARGS:
  <old>  Path to old manifest
  <new>  Path to new manifest

OPTIONS:
  --output <FILE>              Output patched manifest (default: <new>.patched.json)
  --certificate <FILE>         Output repair receipt (default: <new>.receipt.json)
  --linear-data <FILE>         Same as bulla diff
  --max-disclosures <N>        Refuse repair if minimum patch exceeds N disclosures

EXIT CODES:
  0  Repair succeeded; patched manifest + receipt written
  1  Repair impossible (composition not in exact regime; surrogate-regime conjecture territory)
  2  Repair exceeded --max-disclosures
  3  Input error or coherence-preserving (no repair needed)
```

## §5. Certificate format

A `bulla diff` coherence-preservation certificate is a JSON object:

```json
{
  "schema": "bulla.update-certificate.v1",
  "old_manifest_hash": "sha256:...",
  "new_manifest_hash": "sha256:...",
  "preserves_coherence": true,
  "witness_rank_old": 3,
  "witness_rank_new": 3,
  "chain_homotopy": {
    "f_dot": "<base64-encoded matrix>",
    "g_dot": "<base64-encoded matrix>",
    "h": "<base64-encoded matrix>",
    "h_prime": "<base64-encoded matrix>"
  },
  "decision_procedure": "bulla.mapping-cone-acyclicity@v0.37.0",
  "issued_at": "2026-05-01T00:00:00Z",
  "issuer_signature": "<sig>"
}
```

When `preserves_coherence: false`, the chain-homotopy field is replaced with:

```json
  "preserves_coherence": false,
  "failing_cocycles": [
    {"basis_vector": "<base64>", "context": "<MCP server.tool>", "field": "..."},
    ...
  ],
  "minimum_repair_cardinality": 2
```

A `bulla repair` receipt extends with:

```json
  "repair_disclosures": [
    {"disclosure_W": "<base64>", "context": "...", "field": "..."},
    ...
  ],
  "repair_certificate_chain": ["<parent_hash>", "..."]
```

## §6. Integration with Bulla architecture

### 6.1 New Python module: `bulla.update`

```python
# bulla/src/bulla/update.py
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional

from bulla.coboundary import matrix_rank
from bulla.model import Composition, ToolSpec, WitnessReceipt

@dataclass(frozen=True)
class ChainHomotopy:
    f_dot: list[list[Fraction]]
    g_dot: list[list[Fraction]]
    h: list[list[Fraction]]
    h_prime: list[list[Fraction]]

@dataclass(frozen=True)
class Cocycle:
    basis_vector: list[Fraction]
    context: str
    field: str

@dataclass(frozen=True)
class CoherencePreservationCertificate:
    old_hash: str
    new_hash: str
    preserves_coherence: bool
    witness_rank_old: int
    witness_rank_new: int
    chain_homotopy: Optional[ChainHomotopy]
    failing_cocycles: list[Cocycle]
    minimum_repair_cardinality: int

@dataclass(frozen=True)
class RepairCertificate(CoherencePreservationCertificate):
    repair_disclosures: list[Cocycle]
    parent_receipt_hashes: list[str]

def diff_classify(old: Composition, new: Composition) -> CoherencePreservationCertificate:
    """Decide whether `new` is a coherence-preserving update of `old`.
    
    Implementation follows the mapping-cone acyclicity test. See
    `papers/composition-doctrine/notes/9-5-attack-phase-b.md` §E.
    """
    ...

def repair(old: Composition, new: Composition) -> tuple[Composition, RepairCertificate]:
    """Construct the minimum-disclosure patch making `new` coherence-preserving.
    
    Implementation extracts the failing-cocycle basis from `diff_classify`
    and applies repair-duality (§6.2 of doctrine paper) to get the minimum
    disclosure assignment.
    """
    ...
```

### 6.2 CLI integration

Add subcommands to `bulla/src/bulla/cli.py`:

```python
# bulla/src/bulla/cli.py (additions)
def cmd_diff(args):
    """bulla diff <old> <new>"""
    old = load_composition(args.old)
    new = load_composition(args.new)
    cert = update.diff_classify(old, new)
    emit(cert, format=args.format)
    sys.exit(0 if cert.preserves_coherence else 1)

def cmd_repair(args):
    """bulla repair <old> <new>"""
    old = load_composition(args.old)
    new = load_composition(args.new)
    patched, cert = update.repair(old, new)
    save_composition(patched, args.output)
    save_certificate(cert, args.certificate)
    sys.exit(0)
```

### 6.3 Receipt chain extension

Coherence-preservation certificates extend Bulla's existing receipt-chain mechanism:

```python
# Existing receipt:
WitnessReceipt(
    composition_hash="...",
    diagnostic_hash="...",
    receipt_hash="...",
    parent_receipt_hashes=["..."],  # ← new chain link
)

# After bulla diff with preserves_coherence: true:
new_receipt = old_receipt.with_parent(coherence_certificate.hash)

# Validity propagates: a receipt for `old` is also a receipt for `new`,
# witnessed by the chain `old_receipt → preservation_certificate → new`.
```

This makes Bulla receipts **monotone across coherence-preserving updates**: no re-derivation needed.

### 6.4 Glyph integration

Glyph manifests already publish under SEP-1400 / SEP-1575. Adding `bulla diff` to the manifest publication CI:

```yaml
# Glyph CI (concept)
- name: Coherence preservation check
  run: |
    bulla diff manifests/old.json manifests/new.json
    if [ $? -eq 1 ]; then
      bulla repair manifests/old.json manifests/new.json
      echo "::warning::Update is coherence-changing; repair required."
    fi
- uses: actions/upload-artifact@v4
  with:
    name: coherence-certificate
    path: certificate.json
```

## §7. Empirical validation: 703-composition retro-classification

After shipping, run on the existing Bulla 703-composition corpus:

```bash
for composition in corpus/*.json; do
  bulla diff $composition.v1 $composition.v2 --format json >> classification.jsonl
done

# Aggregate:
# - Distribution of preserving vs. changing
# - Average minimum-repair cardinality on changing updates
# - Correlation with SEP type (1400 minor vs major; 1575 compatible vs incompatible)
```

This gives the first empirical distribution of "what fraction of real MCP updates are coherence-preserving" — a publishable result on its own.

## §8. Risk and fallback

- **Risk:** Mapping-cone implementation produces false negatives (claims non-preserving when actually preserving). 
  *Mitigation:* Cross-check with explicit chain-homotopy verification: given returned `h`, verify `f^•∘g^• − id = δ∘h + h∘δ` (a finite linear-algebra check).

- **Risk:** §6.2 repair duality not actually valid at the update level (i.e., minimum-disclosure repair of an update is NOT the failing cocycle basis).
  *Mitigation:* Repair-duality is proved at the *complex* level in §6.2; the update-level specialization needs a separate proof. If it doesn't hold cleanly, ship `bulla diff` only and treat `bulla repair` as a separate 0.38.0 feature requiring its own theorem.

- **Risk:** Rational-arithmetic overflow on large compositions.
  *Mitigation:* Bulla already uses `fractions.Fraction` for exact arithmetic; the new module inherits this. Performance: composition with `n` tools has matrix sizes ~`n × n`, so `O(n^ω)` over ℚ is `O(n^{2.373} · L^2)` where `L` is bit-length of largest rational. Empirically `L = O(\log n)`, so total `O(n^{2.373} \log^2 n)`. Acceptable up to `n ≈ 10^4`.

- **Risk:** Logic drift in textual diffs.
  *Mitigation:* `--linear-data` flag for SEP authors to disambiguate; `bulla diff` returns exit code 2 with a clear "ambiguous rename" error when needed.

## §9. Roadmap

| Sprint | Deliverable |
|---|---|
| Sprint 2 (in progress) | This design doc + Lean refinement of `IsCoherencePreserving` |
| Sprint 2 day 4 | Aristotle submission for `update_preserves_witness_rank` |
| Sprint 3 | `bulla.update` Python module implementation |
| Sprint 3 | `bulla diff` CLI subcommand |
| Sprint 4 | `bulla repair` CLI subcommand (gated by repair-duality at update level) |
| Sprint 4 | 703-corpus retro-classification |
| Sprint 5 | SEP-1400 / SEP-1575 ecosystem advocacy: certificates as standard |

## §10. Open architectural questions

1. **Should certificates be content-addressed?** Currently Bulla receipts are content-addressed (SHA-256 of canonical-JSON serialization). Coherence-preservation certificates should follow the same convention; the `chain_homotopy` field's serialization needs canonical encoding.

2. **Multi-server diff?** Currently one old/new manifest pair; what about diffing a whole composition (multiple tools updating simultaneously)? Likely: factor into pairwise diffs + transitive closure over composition graph. Future work.

3. **Persistent certificates?** Should we maintain a database of "all coherence-preservation certificates issued by this Bulla instance"? Useful for retro-classification but raises storage / privacy questions. Defer to Sprint 5 advocacy phase.

4. **Cross-version receipts?** A receipt for `manifest_v1` is valid for `manifest_v2` *if* there's a chain of preservation certificates `v1 → v2`. What about `v1 → v3` skipping `v2`? Likely: certificates compose, so the chain `v1 → v2` and `v2 → v3` give `v1 → v3` automatically (via category composition Definition I.2 of the math notes). Worth verifying.

---

**Bottom line:** `bulla diff` + `bulla repair` is the operational kernel that converts Conjecture 9.5 from a paper theorem into an MCP-ecosystem protocol feature. The math is mature; the architecture gap is what Sprint 2 closes.
