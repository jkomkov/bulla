# Routed Inference Profile v0.1 — DRAFT

**Status:** draft, 2026-07-17. This is a provider-neutral workflow profile over
ActionReceipt v0.3. It does not change the ActionReceipt wire shape. It has local
golden traces, a zero-Bulla checker, and an offline handoff demonstration. It has no
live provider integration, settlement adapter, independent implementation, or
independently operated ActionReceipt witness.

## 1. Answerability Conservation Law

> No consequential transition may create an orphaned consequence or silently
> discharge an inherited binding. Every transition retains one or more attributable
> obligation-bearing principals, exact parent occurrences, the applicable terms, and
> conveyed recourse terms.

The conserved object is **answerability coverage**, not one scalar and not necessarily
one party. A v0.1 transition is covered when an authentic receipt binds at least one
principal, the exact `event` and `attestation` hashes of its parent, the unchanged slot
and term commitment, and the inherited recourse specification.

Version 0.1 never discharges a binding. `inference.delivery` records a delivery
assertion and `bulla.rely` records a reliance decision; neither is closure. Any
non-empty `action.discharges` is `DISCHARGE_UNSUPPORTED`, and the checker retains the
binding. Closure and novation require a later multi-party protocol.

## 2. Exact v0.1 boundary

This profile supports exactly one orderer, one router, one accepting provider, one
delivery assertion, and one relier:

```text
order -> route -> accept -> delivery -> rely
```

The term document MUST declare:

```json
{
  "route_topology": "single_route_single_provider",
  "term_disclosure": "full",
  "process_constraints": { "max_route_depth": 1 }
}
```

Every accepting principal receives and consents to the same canonical term document.
Exact `term_root` equality is the enforcement mechanism. This is a full-disclosure
profile: it does not preserve commercial confidentiality between hops. A Merkle proof
can later authenticate selective disclosure, but membership does not by itself prove
lawful attenuation or term transformation. Selective disclosure, general attenuation,
multi-hop linear routing, fan-out, hedging, fallback, and DAG accounting are outside
v0.1.

## 3. The epistemic divergence

Answerability coverage must survive every seam. Process grounding does not compose
that way: the chain's effective process grounding is the minimum over its necessary
process evidence. Adding an opaque hop can preserve or lower that minimum; a receipt or
witness cannot promote a self-asserted trace into verified execution.

A witness establishes that exact receipt bytes entered a witnessed history and may
expose omission or equivocation. An appraiser evaluates process evidence; an
adjudicator hears semantic disputes; an external rail executes financial remedies.
Those roles remain separate.

## 4. Ordinary ActionReceipts, specialized actions

Every node is an ordinary ActionReceipt with an open-vocabulary `action.type`:

1. `inference.order`
2. `inference.route`
3. `inference.accept`
4. `inference.delivery`
5. `bulla.rely`

The profile closes each action and subject container to its specified fields. Every
edge uses a two-hash `ReceiptRef`:

```json
{
  "event": "sha256:...",
  "attestation": "sha256:..."
}
```

The pair binds the exact observed occurrence (`event`) and vouched content plus
envelope (`attestation`). It does not independently prove that the actor's timestamp
is true. Arbitrary `evidence_refs` do not become dependency edges.

### Normative transition table

| Action | Cardinality | Required parent | Conserved or accepted fields |
|---|---:|---|---|
| `inference.order` | exactly 1 | none | slot, term root, request, ceiling, unit, remedy adapter, witness policy |
| `inference.route` | exactly 1 | exact order ref | slot, term root, permitted selection, router ledger |
| `inference.accept` | exactly 1 | exact route ref | route ref, selection, term root, remedy adapter, witness policy, provider ledger |
| `inference.delivery` | exactly 1 for `CONFORMS` | exact accept ref | slot, term root, accepted selection, artifact ref, resource evidence |
| `bulla.rely` | exactly 1 after delivery | exact delivery ref | relied-on ref, explicit policy, decision, diagnostic and evidence pins |

Absent delivery or reliance evidence can be `UNDETERMINED`; duplicated or conflicting
constrained transitions are objective violations.

## 5. Term document, consent, and recourse

`term_root = H(canonical(term_document))`. The term document commits to:

- request and artifact references without requiring plaintext prompts;
- the exact topology and disclosure mode;
- permitted providers and models, integer precision, hardware, randomness, route
  depth, and integer resource ceilings;
- minimum process grounding and an appraisal-policy reference;
- one integer settlement unit, disclosed-components pricing, and a ceiling;
- deadline, witness policy, remedy adapter, reliance policy, and named forum.

`inference.accept` signs the exact route reference and selection, inherited term root,
remedy adapter, witness policy, and provider ledger. A missing or substituted value is
not consent.

The checker distinguishes:

- `recourse_conveyance`: whether authentic receipts name and consistently inherit the
  recourse terms;
- `recourse_reachability`: whether the named forum or adapter is operational.

The local corpus can establish the first at identity depth. It reports
`recourse_reachability = UNVERIFIED`; a URL, adapter name, or signed promise does not
prove a live remedy.

## 6. Disclosed-components budget ledger

All amounts are non-negative safe integers in one pinned unit; v0.1 performs no FX
conversion. The one router and one provider declare:

```text
router.charge_to_upstream
  = router.charge_from_downstream + router.retained_amount
provider.charge_to_upstream
  = provider.charge_from_downstream + provider.retained_amount
router.charge_from_downstream
  = provider.charge_to_upstream
router.charge_to_upstream
  <= order.budget_ceiling
```

`ACCOUNTING_CONFORMS` establishes consistency of signed declarations. It can expose an
unbalanced ledger, a mismatched downstream amount, or an over-ceiling quote. It does
not prove actual resource consumption or payment. Only independently verified,
third-party-anchored rail evidence may reach `SETTLEMENT_CONFORMS`; otherwise the
checker reports `SETTLEMENT_UNVERIFIED`.

The portable executable subset permits strings, booleans, safe integers, `enum`,
`const`, integer bounds, and integer quantum. Floats, regex, implicit coercion, and
unsafe integers are forbidden.

## 7. Verdict and report dimensions

The checker returns:

- `CONFORMS` — every v0.1 predicate reachable at the reported depth passes;
- `VIOLATES` — an objective contradiction is present;
- `UNDETERMINED` — necessary identity, evidence, or ordering state is unavailable.

The report includes exact fault codes plus:

- `answerability_coverage`: `COVERED`, `BROKEN`, or `UNDETERMINED`;
- `binding_state`: `RETAINED` in v0.1;
- `recourse_conveyance`: `CONFORMS`, `VIOLATES`, or `UNDETERMINED`;
- `recourse_reachability`: `UNVERIFIED`;
- effective process grounding, accounting depth, settlement depth, and verification
  depth.

The standalone checker imports no Bulla code. Digest and executable predicates use
only the Python standard library. When PyNaCl is available it verifies did:key content,
authorization, and log-head signatures and reports identity depth. Without PyNaCl,
authority-dependent dimensions become `UNDETERMINED` at digest depth. Appraisal,
semantics, and operational recourse remain external.

Log equivocation is two authentic same-operator, same-size heads with different roots,
or a failed consistency proof. Different roots at different sizes are normal when
consistency holds. Unique prevention still requires a local host, quorum, or rail
compare-and-swap; the profile eliminates global consensus, not ordering.

## 8. Finite violation model and bounded completeness

The machine-readable `violation-taxonomy.json` maps every v0.1 transition predicate to
its fault codes and hostile mutations. Table-driven tests delete or substitute every
required field, alter each half of parent references, duplicate constrained actions,
exercise different-size heads, and remove optional cryptography.

Completeness means only this: for the finite action grammar, required containers, and
predicates specified here, every enumerated failure class has a checker decision and a
hostile regression. It is not completeness over arbitrary transports, real execution,
semantic disputes, undisclosed terms, future topologies, or adversarial cryptography.

## 9. Reproduction and bounded claims

The checker imports no Bulla code. Install PyNaCl to reproduce the canonical
identity-depth reports, then run all fourteen traces from a clean copy:

```sh
python3 -m pip install pynacl
python3 check.py
```

Without PyNaCl, digest and profile-predicate checks still run, but
authority-dependent outcomes correctly downgrade to `UNDETERMINED` and therefore do
not match the canonical expected reports.

Verify one bundle with machine-readable output:

```sh
python3 check.py verify 01-honest-balanced.json --json
```

Passing establishes conformance of deterministic fixtures at the reported verification
depth. It does not establish live provider behavior, actual settlement, complete
logging, operational recourse, selective disclosure, independent implementation,
witness plurality, or production readiness. The profile remains draft until an
external implementation reproduces it from this specification alone.

## 10. Deferred closure and novation

A future novation protocol must obtain authenticated, parent-linked consent from the
outgoing obligor, incoming obligor, and beneficiary; preserve applicable terms and
recourse; and discharge nothing until final consent. A reference string cannot perform
that work. No closure or novation action is accepted by v0.1.
