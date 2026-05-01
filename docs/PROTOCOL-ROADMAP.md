# Bulla Protocol Roadmap

> Strategic direction for the Bulla protocol.
> Revised April 2026 after the 2D Disposition Sprint.

---

## Current state

Bulla has two independent diagnostic axes, both calibrated on the same
38-server corpus:

**Axis 1 -- Coherence fee (opacity).** The coboundary algebra measures
conventions hidden from schemas. `fee(G) = rank(delta_full) - rank(delta_obs)`.
Boundary fee = 0 has zero false negatives across 678 compositions.

**Axis 2 -- Contradiction score (incompatibility).** The structural scan
compares every cross-tool field pair by schema similarity. 959 contradictions,
58,802 agreements across 703 compositions. Requires no convention packs.

The disposition logic now reasons over a **2D risk surface** instead of
flattening these axes via addition:

| Fee | Contradictions | Meaning | Disposition |
|-----|---------------|---------|-------------|
| 0 | 0 | Clean | PROCEED |
| >0 | 0 | Opacity risk | PROCEED_WITH_BRIDGE / REFUSE |
| 0 | >0 | Incompatibility risk | PROCEED_WITH_CAUTION |
| >0 | >0 | Both | REFUSE |

The receipt carries `fee` and `contradiction_score` as independent fields.
`PolicyProfile` has separate thresholds: `max_contradictions` (convention)
and `max_structural_contradictions` (schema).

---

## Rejected: Micro-pack feedback loop

The original Option 1 proposed feeding structural agreements back into the
classifier as transient convention dimensions, re-running the coboundary
with the enriched vocabulary.

**This is architecturally wrong.** Three reasons:

1. **Circular measurement.** The structural scan finds "these fields have
   the same schema shape." Promoting that to a convention dimension means
   the coboundary measures "opacity relative to what the structural scan
   discovered." But the structural scan already measured that. Running the
   same signal through two instruments and calling the second reading
   independent is invalid.

2. **False promotion.** Two fields with `type: string, format: date-time`
   are structurally similar. That does NOT mean they represent the same
   convention. `created_at` and `expires_at` have identical schema shape
   but different semantic concepts. Promoting shape-agreement to
   semantic-identity introduces false positives into the algebra.

3. **The claim is already true.** The structural scan runs without packs,
   produces a `StructuralDiagnostic`, feeds `contradiction_score` into
   the receipt, and drives disposition via `PROCEED_WITH_CAUTION`. Fee = 0
   with zero packs is the correct answer: there are no declared conventions
   to be opaque about. The instrument is reporting accurately.

**The principle:** Don't feed Axis 2 into Axis 1. The two diagnostics are
independent and should remain so. Their independence is what makes the
measurements trustworthy.

---

## Phase 1: 2D risk surface (COMPLETED)

Refactored `_resolve_disposition()` to reason over fee and contradiction_score
as independent axes. Added `Disposition.PROCEED_WITH_CAUTION` for the
(fee=0, contradictions>0) quadrant. Added `max_structural_contradictions`
to `PolicyProfile`. Removed the flattening that summed convention and
structural contradictions into a single threshold check.

---

## Phase 2: Composition-aware session proxy (NEXT)

Build a BullaProxy that sits between an agent and its MCP servers. The
proxy's value is not runtime schema validation (table stakes -- any JSON
Schema validator does that). The value is **composition-aware session state**.

When an agent calls tool A and gets a result, then calls tool B with
derived arguments, the proxy knows:
- Which tools have been called in this session
- Which field values have flowed between them
- What the accumulated composition risk looks like

```
Agent --> BullaProxy --> Target MCP Server
              |
              +-- On tools/list: compose(), cache diagnostic
              |
              +-- On tools/call:
              |   1. Schema-validate arguments (table stakes)
              |   2. Track which output fields feed which input fields
              |   3. Update running composition receipt
              |   4. If contradiction detected between source -> target:
              |      return structured error with bridge suggestion
              |
              +-- Session state: running WitnessReceipt
                  (updated after each call, DAG-chained)
```

The structured error is not "this argument is invalid" but "this argument
is invalid because it came from tool A's output and tool B's schema
disagrees on the sort enum, and here's the bridge."

The receipt's `parent_receipt_hashes` already support DAG chaining. Each
`tools/call` produces a child receipt that records "this call was made in
the context of this composition state." The running receipt IS the audit
trail.

---

## Phase 3: Framework-level receipt gating (ENDGAME)

The endgame is NOT tool-level gating (`requires_witness: true` on
individual tools). That breaks Bulla's best property: it works without
tool developer cooperation.

The endgame is **framework-level receipt gating**: the agent framework
(Claude, LangChain, CrewAI, etc.) says "I won't execute a multi-tool
workflow unless the composition receipt says PROCEED." This requires
zero changes to individual tools. It's a policy on the orchestration
layer, not a protocol extension on the tool layer.

The receipt already carries everything the framework needs to make this
decision. The framework just needs to call `compose()` before execution
and respect the disposition.

**The key insight:** Bulla shouldn't ask tools to check receipts. Bulla
should be the infrastructure that makes receipts unavoidable. The notary
didn't ask merchants to verify their own documents. The notary sat at the
table and the transaction went through her. The proxy IS the notary.

---

## Sequence

1. **Phase 1** (done) -- 2D risk surface, four-quadrant disposition
2. **Phase 2** (next) -- Composition-aware proxy with session state
3. **Phase 3** (when proxy is proven) -- Framework integrations

Each phase is independently valuable. Each makes the next one more
powerful.
