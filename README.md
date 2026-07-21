# Bulla

**Portable, recomputable receipts for consequential agent actions.**

Bulla creates portable, recomputable receipts for consequential agent actions.
Each receipt records what happened, who authorized it, what bounds applied, what
evidence is carried, what a relying party decided, and where challenge or remedy
goes. Verification reports what is proven and what remains unresolved; it does
not turn a signed record into worldly truth.

A *bulla* was the clay envelope sealed around a record so it could survive the
absence of the parties who made it. Bulla applies that discipline to agent
actions: the action may finish in milliseconds, but its authority, evidence,
limits, and challenge path remain available to the next system or institution.

- **Glyph** is the open ActionReceipt format and verification contract.
- **Bulla** is the Apache-2.0 Python reference implementation.
- **Res Agentica** is the research program behind the experimental semantic and
  institutional profiles.

Core installation has no heavy numerical or model dependency. It requires
Python 3.10+ and PyYAML.

## Create and verify one receipt

```bash
python -m pip install bulla

bulla receipt create \
  --type demo.write \
  --subject path=/tmp/example.txt \
  --principal did:web:example.invalid:agent \
  --policy policy://demo-v1 \
  --scope path=/tmp/example.txt \
  --evidence diff=sha256:1111:self_asserted \
  --forum-endpoint https://example.invalid/challenge \
  --forum-root fixture:independently-pinned-root \
  --out receipt.json

bulla receipt verify receipt.json --format json
```

The verifier reports independent dimensions rather than collapsing them into a
misleading Boolean:

```text
integrity            VERIFIED
authenticity         UNVERIFIED
authority            UNAUTHENTICATED
scope                 NOT_APPLICABLE
grounding             SELF_ASSERTED
recourse              NAMED
reachability          UNVERIFIED
reliance_decision     NOT_COMPUTED
```

This output is pinned by a checked
[CLI fixture](https://github.com/jkomkov/bulla/blob/main/docs/fixtures/unsigned-self-asserted-answerability.json).
The exact values depend on the receipt. An unsigned example can have verified
hash integrity while authenticity and authority remain unverified. A named
forum can be present while its operational reachability remains unverified.
Those distinctions are the point.

The same stable boundary is available from Python:

```python
from bulla.action_receipt import build_action_receipt, verify_receipt
from bulla.envelope import Authority, Bounds, Forum, Recourse, RecourseEnvelope, Remedy

receipt = build_action_receipt(
    action={"type": "demo.write", "subject": {"path": "/tmp/example.txt"}},
    diagnostic_ref={"status": "deferred"},
    envelope=RecourseEnvelope(
        authority=Authority(
            principal="did:web:example.invalid:agent",
            policy="policy://demo-v1",
        ),
        bounds=Bounds(scope="path=/tmp/example.txt"),
        recourse=Recourse(
            challenge_window="P30D",
            forum=Forum(
                log_endpoint="https://example.invalid/challenge",
                trusted_root_ref="fixture:independently-pinned-root",
            ),
            remedies=(Remedy(
                rung="recompute",
                verifier="bulla receipt verify",
                anchor="hashes.content",
            ),),
        ),
    ),
    evidence_refs=({
        "name": "diff",
        "hash": "sha256:1111",
        "grounding": "self_asserted",
    },),
    timestamp="2026-07-20T00:00:00Z",
)

result = verify_receipt(receipt.to_dict())
assert result.ok
assert result.authority_authentic == "unauthenticated"
assert result.effective_grounding == "self_asserted"
```

The final assertions are deliberate: recomputable integrity did not upgrade an
unsigned authority envelope or self-asserted evidence into stronger claims.

The implementation-independent checker needs no Bulla import:

```bash
python spec/vectors/independent_check.py
```

It recomputes the frozen ActionReceipt vectors from the normative specification
and fails closed on structural tampering.

## What 0.44.0 contains

| Surface | Maturity | Availability | What it establishes |
|---|---|---|---|
| ActionReceipt v0.2 | Stable and normative | PyPI 0.44.0 | Canonical action records, four hash preimages, evidence references, and recourse envelopes |
| ActionReceipt v0.3 authority binding | Opt-in released draft | PyPI 0.44.0 | The content signer signed the exact authority, bounds, and recourse envelope |
| Delegation and bounds conformance | Opt-in released draft | PyPI 0.44.0 | Separate chain, principal, policy, scope, time, revocation, and action-bounds dimensions |
| Reliance receipts | Released implementation | PyPI 0.44.0 | A relying party records and recomputes its selected reliance policy and decision |
| Release coverage | Released implementation | PyPI 0.44.0 | Published-package actions missing contemporaneous receipts relative to the PyPI anchor |
| Routed inference | Experimental profile | Source and fixtures | Retention of declared bindings through one router and one provider; no live-provider claim |
| Semantic invention and finality | Experimental research | Source/research only | Checked finite predicates, partial safe regions, typed abstention, and staged finality under declared closure |
| Claim Flow and precedent | Experimental research | Source/research only | Typed appraisal, forum, precedent, applicability, and settlement transitions; no external legal-validity claim |
| Independent witness plurality | Blocked | Not available | Local checkpoint mechanics exist; independently operated witnesses do not |

The canonical, generated status table is
[What Exists Today](https://glyphstandard.com/status). It distinguishes released
code, released drafts, experimental mechanisms, research results, and external
gaps.

## The answerability flow

```text
record  ->  verify  ->  rely  ->  retain  ->  challenge
```

1. **Record.** Capture the action, authority, bounds, evidence, and recourse.
2. **Verify.** Recompute the canonical hashes and every supported verification
   dimension from pinned inputs.
3. **Rely.** Apply an explicit reliance policy and receipt the relying party's
   own decision.
4. **Retain.** Bind the record to an independently obtained root or other
   declared persistence mechanism when occurrence coverage matters.
5. **Challenge.** Preserve the forum, window, and remedy ladder needed to
   contest or correct the action.

Bulla's security foundation as four separate requirements is deliberate:
authenticity, inclusion under a root obtained independently of the issuer,
independently persistent witnessing where occurrence coverage is required, and
an executable recourse path. A deployment must report which requirements it
actually establishes; one does not stand in for another.

The format is intentionally open. A second implementation can produce or verify
the same receipt without importing this package.

## What verification does not prove

A valid receipt is evidence about a record and the checks actually performed.
It does not by itself prove:

- that the described event occurred in the world;
- that carried evidence is truthful or complete;
- that an authority policy is legally or institutionally sufficient;
- that every relevant action received a receipt;
- that a named forum or remedy is operationally reachable;
- that a local registry is independently witnessed;
- that an experimental finite model is complete in an open world.

Callers should read the named dimensions or use an authored reliance policy.
`ReceiptVerification` and the other multidimensional verdict objects reject
Boolean coercion so `if verify_receipt(...):` cannot silently accept an
ambiguous result.

## Documentation

- [Five-minute quickstart](https://glyphstandard.com/bulla/quickstart)
- [Bulla documentation](https://glyphstandard.com/bulla)
- [ActionReceipt specification](https://glyphstandard.com/spec)
- [What exists today](https://glyphstandard.com/status)
- [Complete capability reference](https://github.com/jkomkov/bulla/blob/main/docs/CAPABILITIES.md)
- [Experimental profiles](https://glyphstandard.com/bulla/experimental)
- [Research program](https://www.resagentica.com/research)
- [Changelog](https://github.com/jkomkov/bulla/blob/main/CHANGELOG.md)
- [Security policy](https://github.com/jkomkov/bulla/security/policy)

## Legacy composition diagnostics

Bulla still includes its original tool-composition diagnostics, convention
packs, bridges, translators, MCP scanner, and witness-geometry utilities. Their
*coherence fee* is retained as a model-relative disclosure/omission measure: it
counts convention dimensions hidden from a declared observable seam. Current
execution-derived evidence does **not** support treating it as a mismatch,
runtime-failure, or safety oracle, and the default enforcement path does not do
so.

Legacy theorem and run provenance can make a scoped disclosure recommendation
recomputable; it does not turn that recommendation into a safety proof or an
execution-failure prediction.

See [Legacy composition diagnostics](https://github.com/jkomkov/bulla/blob/main/docs/LEGACY-COMPOSITION-DIAGNOSTICS.md)
and [Falsifications](https://github.com/jkomkov/bulla/blob/main/FALSIFICATIONS.md)
for the surviving scope and the claims that were withdrawn. SEAM remains part
of the program's research lineage; it is not the product's current trust root.

## Research frontier

The repository also carries experimental profiles for semantic invention,
partial RELY/REFUSE envelopes, Semantic Finality, correct abstention, typed
Claim Flow, and reason-bearing precedent. They reuse ordinary ActionReceipts
but are not stable package APIs and have only internal, model-relative evidence
unless their status page says otherwise.

The research-program ledger currently records 56 Aristotle-verified theorems
with no `sorry` across its named formal abstractions. That is theorem-checking
provenance, not independent validation or a proof of the Python implementation;
the PyPI package does not vendor Lean.

The public research records are:

- [Interpolant Envelope](https://www.resagentica.com/research/interpolant-envelope)
- [The Golden Gate](https://www.resagentica.com/research/golden-gate)
- [No Free Precedent](https://www.resagentica.com/research/no-free-precedent)

## License and security

Bulla is licensed under the
[Apache License 2.0](https://github.com/jkomkov/bulla/blob/main/LICENSE).
Report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/jkomkov/bulla/security/advisories/new)
or by following the [security policy](https://github.com/jkomkov/bulla/blob/main/SECURITY.md).
