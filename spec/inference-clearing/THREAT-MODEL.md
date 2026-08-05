# Inference Clearing Alpha Threat Model

The protected claim is narrow: retained bytes and an external verification
context are sufficient to recompute whether the canonical transaction meets the
declared buyer policy. The alpha does not protect an open prompt or production
service.

| Threat | Mechanism | Regression | Residual limitation |
|---|---|---|---|
| Receipt or artifact mutation | Exact byte digests, v0.4 signatures, closed manifests | Content, model, input, output, and receipt mutations | Issuers can still make false self-assertions |
| Attacker re-signing or role substitution | External role allowlists and purpose-separated signatures | Wrong provider, receiver, witness, and settlement keys | All fixture roles remain team-controlled |
| Cross-order replay | Transaction, term root, and exact parent event/attestation lineage | Cross-order receipt graft | No global replay registry is claimed |
| Borrowed witness inclusion | External root, accepted operator, and exact expected leaf | Unrelated-leaf proof | Team witness does not increment W or I |
| Direct effect bypass | Receiver signs the exact denominator hash and counts; reliance binds that checkpoint | `effect-bypass-001` and publisher-shortened signed hostile case | Receiver can self-shorten its own record |
| Post-hoc model substitution | Expected model hash is bound in terms before provider acceptance | A coherent alternative model returns the same `BACKUP` bytes but remains `UNBOUND` | The term-bound relation does not establish historical provider execution |
| Publisher rewrites a provider statement | Provider acceptance binds the execution-report hash and closed process claim | Publisher flips `self_asserted` to `absent` without a new provider receipt | A provider signature authenticates the statement, not its truth |
| Evidence amplification | Relation-specific recomputation and non-amplification rules | Provider claims, guarantees, collateral, and witness additions leave historical execution `NOT_ESTABLISHED` | Recomputed bytes do not prove model quality |
| Premature or contradictory payment | Separate reliance, authorization, and rail records; rail receipt binds the exact settlement and rail-evidence hashes | Settlement before coverage and signed settlement contradiction | Rail report is still an observer claim |
| Cross-language integer drift | Exact integer arithmetic with every multiplication and accumulation constrained to the safe range | Cancellation after an unsafe intermediate product | The finite model is intentionally small and fixed |
| Trust bootstrap | Context is outside the bundle | Bundle-carried key/root substitution | Context distribution is a relying-party responsibility |
| Unsafe archive or parser input | Strict byte parser, bounded JSON, normalized paths, no symlinks | Duplicate members, Unicode, size, depth, traversal | Parser correctness is tested, not formally proved |
| Provider disappearance | Role processes emit the retained v0.4 chain; offline check consumes only the assembled bundle and external context | Confirm runtime bundles differ from frozen fixtures, terminate providers, then run Python and Node | Retention availability is not guaranteed externally |
| Crash after effect | No eligibility before receiver occurrence, witnessing, and coverage reconciliation | Three typed process-stop injections | The alpha does not prove production recovery or exactly-once delivery |

The deterministic model uses fixed safe-integer arithmetic. Reproduction
establishes that the retained, term-bound model maps the retained input to the
returned bytes. It does not establish which model the provider historically ran
or the quality, provenance, or real-world fitness of that model.
