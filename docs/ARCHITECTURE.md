# Bulla Architecture

## The Mathematical Design

Bulla measures a quantity — the *coherence fee* — that tells you how many independent dimensions of semantic mismatch are invisible to pairwise verification of a tool composition. The measurement is exact (rational arithmetic, no floating-point), deterministic (same composition always produces the same fee), and constructive (it tells you exactly which fields to disclose to eliminate the fee).

The architecture follows from the mathematics. Understanding why the code is organized the way it is requires understanding what the mathematics demands.

---

## The Three Layers

```
┌─────────────────────────────────────────────────┐
│  Judgment      witness.py                        │
│  Maps diagnostics → disposition (proceed/refuse) │
│  Produces tamper-evident WitnessReceipt          │
├─────────────────────────────────────────────────┤
│  Measurement   diagnostic.py + coboundary.py     │
│  Computes fee, blind spots, bridges, geometry    │
│  Pure functions on Composition → Diagnostic      │
├─────────────────────────────────────────────────┤
│  Geometry      witness_geometry.py               │
│  Witness Gram K(G), leverage, greedy repair      │
│  Deeper analysis when fee > 0                    │
└─────────────────────────────────────────────────┘
```

**The critical invariant:** Measurement has zero imports from Judgment. This is not an aesthetic preference — it reflects a mathematical fact. The coherence fee is a property of the composition graph, independent of what any policy decides to do about it. Measurement is a theorem; Judgment is a decision. Coupling them would make the measurement policy-dependent, which it must never be.

Geometry is lazily imported by Measurement (only when `include_witness_geometry=True` and fee > 0). This keeps the default diagnostic path fast and dependency-light while making the deeper analysis available on demand.

---

## The Coboundary Operator

The core mathematical object is the **coboundary matrix** δ₀: C⁰ → C¹, constructed in `coboundary.py`.

### What it represents

A composition has tools (vertices) and edges (data flows between tools). Each tool has fields. Each edge has semantic dimensions that connect fields across tools.

The coboundary matrix encodes this structure:
- **Rows** correspond to (edge, dimension) pairs — each semantic dimension on each edge
- **Columns** correspond to (tool, field) pairs — each field on each tool
- **Entries** are -1 (source tool), +1 (target tool), or 0

The sign convention is the standard one from algebraic topology: if an edge flows from tool A to tool B along dimension d, the coboundary row for (edge, d) has -1 in the column for (A, field_from) and +1 in the column for (B, field_to). This encodes the *oriented boundary* of the data flow.

### Why two matrices

Bulla builds TWO coboundary matrices for every composition:

- **δ_obs** uses only observable fields (the ones each tool exposes in its schema)
- **δ_full** uses all fields, including internal state that tools don't expose

The rank difference tells you the fee:

```
fee = rank(δ_full) - rank(δ_obs)
    = h¹(obs) - h¹(full)
```

where h¹ = dim(C¹) - rank(δ) is the first cohomology (the number of independent cycles that the coboundary cannot resolve).

The fee counts dimensions where the full internal structure would resolve a cycle but the observable structure cannot — because the relevant fields are hidden. These are the *blind spots*: semantic mismatches that pairwise verification will miss.

### Why exact arithmetic

The coboundary matrix has entries in {-1, 0, +1}. It is *totally unimodular*: every square submatrix has determinant in {-1, 0, +1}. This means:
- All ranks are integers
- All derived quantities (the Gram matrix, leverage scores, effective resistances) are rational
- Floating-point arithmetic would introduce spurious numerical error in a problem that has none

Bulla uses `fractions.Fraction` throughout. The fee is always an exact integer. Leverage scores are always exact rationals. There is no tolerance, no epsilon, no numerical stability concern.

---

## The Witness Gram Matrix

When fee > 0, `witness_geometry.py` computes the *witness Gram matrix*:

```
K(G) = Hᵀ (I - P_O) H
```

where:
- H is the hidden-column block of δ_full (columns corresponding to hidden fields)
- P_O is the orthogonal projector onto range(δ_obs) (the space spanned by observable fields)
- (I - P_O) is the residual projector — what's left after removing the observable contribution

### The four canonical invariants

K(G) is a symmetric positive semidefinite rational matrix. It carries four invariants that completely characterize the witness matroid M/O:

1. **rank(K) = fee(G)**: The backbone theorem. The fee equals the rank of the Gram matrix. This is the central identity connecting the cohomological definition (rank difference of coboundary matrices) to the geometric object (rank of a single Gram matrix).

2. **Leverage scores** lⱼ = (K⁺K)ⱼⱼ: Per-field indispensability. A leverage score of 1 means the field is a *coloop* — it must be disclosed in every repair. A leverage score of 0 means the field is a *loop* — it's already redundant. The sum of all leverage scores equals the fee.

3. **N_effective** = (Σlⱼ)² / Σlⱼ²: Concentration index. Ranges from 1 (fee concentrated in a single must-disclose field) to fee (fee spread uniformly across interchangeable fields). Tells you whether the repair is forced or flexible.

4. **Greedy minimum-cost basis**: The matroid greedy algorithm (Edmonds 1971) on M/O with cost weights gives a globally optimal repair — not a heuristic approximation but a provably minimum-cost set of field disclosures that eliminates the fee entirely.

### Why the Gram matrix, not just the rank

The rank tells you the fee. The Gram matrix tells you *where* the fee lives and *how* to fix it. A composition with fee=5 might have:
- Five coloops (leverage=1 each): every hidden field is essential, no flexibility
- One coloop and four interchangeable fields: one mandatory disclosure plus choice among four
- Five equally-leveraged fields: maximum repair flexibility

The Gram matrix makes this distinction precise and actionable.

---

## The Diagnostic Pipeline

`diagnostic.py` orchestrates the analysis:

1. **Build** δ_obs and δ_full via `coboundary.py`
2. **Compute** ranks and fee
3. **Enumerate** blind spots (edges where a dimension has hidden endpoints)
4. **Propose** bridges (field disclosures that would eliminate blind spots)
5. **Simulate** bridging (rebuild δ with proposed disclosures, recompute rank)
6. **Optionally** compute witness geometry (K, leverage, repair) via `witness_geometry.py`

The output is a frozen `Diagnostic` dataclass — immutable, content-addressable, suitable for hashing into a witness receipt.

### Fee decomposition

For multi-server compositions, `decompose_fee()` partitions the fee into local and boundary components:

```
total_fee = Σ local_fees + boundary_fee
```

The boundary fee is the fee that exists *between* servers but not *within* any single server. This is the fee that cross-server auditing targets. The decomposition uses the rank of cross-partition edge rows modulo internal edge rows — a well-defined quantity that satisfies a block-diagonal rank formula.

### Conditional diagnosis

For compositions that aren't yet fully specified (some tools are placeholders), `conditional_diagnose()` computes:
- **baseline_fee**: fee of the known subgraph alone
- **worst_case_fee**: fee if placeholders disclose nothing
- **obligations**: which fields each placeholder must expose for the fee to drop

This supports incremental composition: you can diagnose a partial pipeline and know what to demand of the next tool before you select it.

---

## The Witness Layer

`witness.py` produces `WitnessReceipt` objects that bind:
1. A hash of the Composition (the input)
2. A hash of the Diagnostic (the measurement)
3. A hash of the Receipt itself (the judgment)

Receipts chain via parent hashes, forming a DAG. Verification is deterministic: `verify_receipt_integrity(receipt.to_dict())` requires only the serialized dict, no kernel objects.

The witness layer applies policy: given a diagnostic (fee, blind spots, leverage), what disposition? Proceed (fee=0), refuse (fee exceeds threshold), or bridge (propose disclosures). The policy is configurable but the measurement is not — the same composition always produces the same diagnostic regardless of policy.

---

## Performance Characteristics

The coboundary matrix has dimensions (|edges| × |dims|) by (|tools| × |fields|). For typical MCP compositions:
- 2-10 tools, 5-30 fields per tool, 1-20 edges, 1-5 dimensions per edge
- δ is roughly 20×50 to 100×300
- Gaussian elimination on these sizes: microseconds

The exact-arithmetic overhead (Fraction vs float) is ~10-50x per operation, but on matrices this small the absolute time is negligible. A typical diagnosis completes in <5ms.

The witness geometry (Gram matrix, leverage scores, greedy repair) involves additional matrix operations but remains fast for compositions under ~100 hidden fields. Beyond that, the O(n³) Gaussian elimination in `matrix_rank` and `_solve_square` dominates.

No composition in the 703-corpus calibration or the 932-instance BABEL benchmark has exceeded 50 tools. The current architecture is well within its performance envelope for all known real-world compositions.

---

## Module Map

```
src/bulla/
├── model.py              # Data classes: Composition, Diagnostic, ToolSpec, Edge, etc.
├── coboundary.py          # δ₀ construction + exact rational rank computation
├── diagnostic.py          # Fee computation, blind spots, bridges, decomposition
├── witness_geometry.py    # Gram matrix K(G), leverage, repair, effective resistance
├── witness.py             # WitnessReceipt production, verification, policy
├── guard.py               # BullaGuard: fluent API for constructing analyses
├── parser.py              # YAML composition file parser
├── cli.py                 # CLI entry points (14 subcommands)
├── serve.py               # MCP stdio server
├── scan.py                # MCP server auto-detection and schema extraction
├── merge.py               # Multi-composition merging
├── repair.py              # Contradiction detection and coordination
├── sdk.py                 # compose() / compose_multi() SDK entry points
├── proxy.py               # Session proxy: flow tracking, RepairGeometry, EpistemicReceipt
├── lifecycle.py           # Receipt validation, diffing, round-trip
├── formatters.py          # Output formatting (text, JSON, SARIF)
├── config.py              # Convention packs and configuration
├── manifest.py            # Server manifest generation
├── init.py                # Composition scaffolding
├── infer/                 # LLM-assisted dimension inference (optional)
└── ots.py                 # OpenTimestamps anchoring (optional)
```

The dependency graph flows downward: `cli.py` → `guard.py` → `diagnostic.py` → `coboundary.py` → `model.py`. The witness layer (`witness.py`) imports from `diagnostic.py` but `diagnostic.py` never imports from `witness.py`.
