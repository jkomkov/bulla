# Bulla

**Portable, recomputable receipts for consequential agent actions.**

Bulla adds portable, independently verifiable receipts to consequential agent
actions. Bulla is the Python reference implementation of the Glyph
ActionReceipt standard.

- **Glyph** is the open ActionReceipt format and verification contract.
- **Bulla** is the Apache-2.0 Python reference implementation.
- **Res Agentica** develops the research and experimental profiles.

An ActionReceipt records the action, authority, bounds, evidence, and recourse.
Verification reports integrity, authenticity, authority, bounds, evidence
grounding, inclusion, recourse, and reliance as separate dimensions. Core
receipt creation and digest verification require Python 3.10+ and no hosted
service.

## Verify the canonical payment

```bash
python -m pip install bulla

curl -fsSLo payment-authorization-v0.2.json \
  https://glyphstandard.com/examples/payment-authorization-v0.2.json
bulla receipt verify payment-authorization-v0.2.json --format json
```

The same receipt ships in the repository at `spec/vectors/payment-authorization.json`,
so the check runs offline from a clone.

The canonical receipt records a USD 125.00 charge under a structured USD 200.00
maximum. The checked result is:

```text
integrity            VERIFIED
authenticity         UNVERIFIED
authority            UNAUTHENTICATED
scope                 CONFORMS
grounding             SELF_ASSERTED
recourse              NAMED
reachability          UNVERIFIED
reliance_decision     NOT_COMPUTED
```

The unsigned receipt reaches the digest verification rung. Digest integrity
does not authenticate the authority, strengthen self-asserted evidence, or
compute a reliance decision.

These verdicts are pinned in `spec/vectors/expected.json`, which the
implementation-independent checker recomputes without importing Bulla. The block
above is not a transcript someone typed; it is checked against that file in CI.

Change `amount_minor` from `12500` to `12501` without recomputing the hashes,
then run the same command. The verifier returns nonzero, reports a content hash
mismatch, and suppresses content-dependent conclusions.

## Create a receipt

```bash
bulla receipt create \
  --type demo.write \
  --subject path=/tmp/example.txt \
  --forum-endpoint https://example.invalid/challenge \
  --forum-root fixture:independently-pinned-root \
  --out receipt.json
bulla receipt verify receipt.json --format json
```

## The Python API

Building a receipt directly shows what the CLI is filling in: the authority that
acted, the bounds it acted under, the evidence carried and how it is grounded, and
where a challenge goes.

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

The three assertions are the point. The receipt verifies, and it still reports that
nobody authenticated the authority and that the evidence is the actor's own word.
Verification separates what was established from what was merely recorded.

Every fenced `python` block in this file is executed in CI by
`scripts/check_readme_examples.py`, which fails if a block raises and also fails if
the blocks disappear.

The [verification-first quickstart](https://glyphstandard.com/bulla/quickstart)
provides the checked output, browser verifier, and integration paths. The
implementation-independent checker reproduces the normative vector corpus without
importing Bulla:

```bash
python spec/vectors/independent_check.py
```

## What 0.44.2 contains

| Surface | Maturity | Availability | What it establishes |
|---|---|---|---|
| ActionReceipt v0.2 | Stable and normative | PyPI 0.44.2; default creation format | Canonical action records, four hash preimages, evidence references, and recourse envelopes |
| Strict byte ingestion | Released implementation | PyPI 0.44.2 | Duplicate members, non-finite values, malformed Unicode, unknown closed fields, and declared resource-limit violations fail before verification |
| Receipt verification and event coverage | Released implementation | PyPI 0.44.2 | Verification dimensions remain separate; coverage reports missing and phantom receipts against a supplied independent action denominator |
| Action-boundary helpers | Released implementation | PyPI 0.44.2 | `wrap_action`, `operational_envelope`, and `receipt_for` add receipts at selected Python action boundaries |
| ActionReceipt v0.3 and v0.4 | Opt-in released drafts | PyPI 0.44.2 | v0.3 binds authority; v0.4 adds occurrence, mandate, action-type, parent, and timeline commitments without changing the v0.2 default |
| Delegation, bounds, and reliance | Released implementation | PyPI 0.44.2 | Authority-chain checks and relying-party decisions remain explicit, recomputable dimensions |
| Routed inference | Experimental profile | Source and fixtures | Retention of declared bindings through one router and one provider; no live-provider claim |
| Existing experimental modules and Golden F13 | Released experimental code | PyPI 0.44.2 under `bulla.experimental` | Finite research checkers remain available without promotion to stable package interfaces |
| Incident, control-plane, witnessing, challenge, answerability, and generalization profiles | Experimental source | GitHub source only; excluded from wheel and sdist | Repository-local experiments remain inspectable without becoming installed package or CLI surfaces |
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
