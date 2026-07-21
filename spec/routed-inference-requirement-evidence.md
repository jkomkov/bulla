# Routed-inference requirement-to-evidence matrix

**Profile:** `bulla.routed-inference/0.1-draft`

**As of:** 2026-07-17

**Claim status:** local specification, fixtures, and offline demonstration only

This matrix is the publication gate for routed-inference claims. A public surface may
use a sentence in the first column only while the evidence remains current and the
limitation remains adjacent or obvious.

| Public sentence or claim class | Normative source | Executable evidence | Required limitation |
|---|---|---|---|
| No v0.1 transition may orphan a consequence or discharge an inherited binding. | profile §§1, 4, 8 | checker; traces 13–14; mutation matrix | This is complete only relative to the finite single-router/single-provider grammar. |
| v0.1 supports one router and one provider with full term disclosure. | profile §2 | term validation; all vectors | It is not multi-hop, DAG, fallback, hedged, or selectively disclosed routing. |
| A `ReceiptRef` binds an exact observed occurrence and vouched envelope. | profile §4; `COMPATIBILITY.md` | parent mutations; reliance tests | It does not make the actor's timestamp independently truthful. |
| Provider acceptance covers the inherited terms, route, remedy adapter, and witness policy. | profile §5 | traces 01, 04, 05 | This proves signed consent at identity depth, not actual execution or meaningful legal assent. |
| Recourse conveyance can conform while recourse reachability remains unverified. | profile §§5, 7 | checker report dimensions; traces 01–14 | A named adapter or forum is not evidence that a remedy is live. |
| The disclosed ledger balances for one router and one provider and stays under the order ceiling. | profile §6 | traces 01, 06–08 | `ACCOUNTING_CONFORMS` is consistency of signed declarations, not actual usage, fair pricing, or payment. |
| Independently anchored rail evidence is required for settlement conformance. | profile §§6–7 | settlement-depth branch; status says adapter absent | Canonical traces report settlement unverified. |
| Permitted provider selection can conform; an out-of-policy delivery substitution violates. | profile §§2, 5 | traces 02–03 | Only the pinned permitted set is decided. |
| Insufficient process grounding violates a pinned evidence floor. | profile §§3, 7 | trace 09 | Receipts and witness inclusion do not upgrade self-asserted execution. |
| Missing delivery and unavailable witness evidence are undetermined, not actor fault. | profile §§4, 7 | trace 10 | Censorship and absence remain distinct until stronger evidence exists. |
| Conflicting delivery assertions are detectable. | profile §§4, 7 | trace 11 | Delivery is not closure; prevention still requires a local host, quorum, or rail CAS. |
| Authentic same-size heads with different roots are equivocation evidence. | profile §7 | trace 12; different-size mutation | Different roots at different sizes are normal when consistency holds. |
| Digest predicates are zero-Bulla; the canonical identity-depth reports require PyNaCl. | profile §§7, 9 | clean-copy and `python -S` tests | Without PyNaCl, authority-dependent results become undetermined at digest depth and the run does not reproduce 14/14 expected reports. |
| The local corpus has fourteen canonical traces. | status record | `expected.json`; checker; Glyph facts gate | Local fixtures are not an independent implementation or production evidence. |
| Receipt sizes are measured over the local fixture corpus. | `size-report.json` | `measure.py`; size tests | Measurement is not a protocol size guarantee. |
| The offline handoff demo isolates the five actor roles and a stranger verifier. | demo README and runner | happy path and two injected-fault tests | It is not a live provider, settlement, witness, or network integration. |
| A deterministic reproduction bundle can be verified without importing Bulla. | bundle README and manifest | bundle build/freshness/clean-copy tests | Running the supplied checker is fixture reproduction, not independent implementation. |
| There is no live provider, settlement adapter, independent reproduction, or independent ActionReceipt witness. | status; operator state | generated Glyph evidence and facts gate | These zeros change only from canonical external evidence. |

## Exit-gate commands

```sh
python3 bulla/spec/vectors/independent_check.py bulla/spec/vectors
python3 bulla/spec/routed-inference-vectors/check.py
pytest -q bulla/tests/test_reliance.py bulla/tests/test_reliance_receipt.py \
  bulla/tests/test_routed_inference_profile.py
cd glyph && npm run check && npm run build
```

Passing establishes implementation, fixture, bundle, and copy consistency. It is not
an external security audit, an independent profile reproduction, or evidence of a live
provider, recourse adapter, settlement rail, or witness network.
