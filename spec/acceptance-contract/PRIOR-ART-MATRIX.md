# Acceptance Contract prior-art boundary

| Area | Established capability | Role in this alpha |
|---|---|---|
| Policy engines | Evaluate declared facts against rules | Bulla does not claim to invent policy evaluation. The alpha fixes a small closed policy before delivery. |
| Authorization systems | Decide whether an authenticated principal may perform an action | The alpha keeps eligibility separate from authorization and execution. |
| Supply-chain attestations | Bind build, test, and provenance claims to artifacts | Such attestations can supply evidence through an explicitly accepted adapter. |
| Workflow gates | Hold or reject a release when checks are missing or negative | The alpha produces the same operational states, but retains the exact counterparty transaction and request lineage. |
| Provenance systems | Authenticate and retain evidence about events and artifacts | ActionReceipts provide the portable transaction records used by this profile. |
| Counterfactual repair | Search for changes that alter a decision | The alpha does not expose an open repair engine. It returns conditional requests only from the contract's declared finite catalog. |

The differentiated composition is a counterparty-retained transaction record,
evidence and authority bound to accepted terms, buyer-local policy fixed before
delivery, a machine-readable conditional request for absent evidence, separate
eligibility/authorization/execution stages, and offline reevaluation after the
provider is unavailable.
