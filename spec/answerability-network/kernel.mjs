/** Browser-safe composition kernel for bulla.answerability-network/0.1-experimental. */

import { computeRelianceMap } from "../reliance-map/kernel.mjs";
import {
  composedBytesHash, composedCanonicalHash, parseComposedJson,
  verifyComposedActionReceipt, verifyComposedCheckpointExtension,
  verifyComposedReceiptInclusion, verifyWitnessCovenant,
} from "../witness-covenant/kernel.mjs";

export const PROFILE = "bulla.answerability-network/0.1-experimental";
export const REPORT_PROFILE = "bulla.answerability-network-report/0.1-experimental";
const CONTEXT_PROFILE = "bulla.answerability-network-context/0.1-experimental";
const NETWORK_ID = "answerability-network:one-job-one-fork-10000";
const TRANSACTION_ID = "inference-job-001";
const TASK_DIGEST = "sha256:7f345516c404c8bc5143ae7d88de620a088fb115442a752dd17efffee3e59d18";
const ARTIFACT_DIGEST = "sha256:57320e94c7670afda62ca39aa76bf96ddfe646a5b960bd750d7e90da24ef7de3";
const EVIDENCE_PROFILE = "bulla.fixed-sha256-delivery/1";
const EVIDENCE_CLASS = "SUPPLIED_BYTE_PREIMAGE";
const WITNESS_COVENANT_HASH = "sha256:f45645e91c063000e8120effa3af63b8238f459c22aab26bdb6bf29d5fb067f5";
const PRICE = 12500;
const UNIT = "USD_CENTS";
const HASH_RE = /^sha256:[0-9a-f]{64}$/;
const STAGES = new Set(["job", "published", "fork-open", "fork-closed", "fork-authorized", "model-dispute"]);
const ACTIONS = { buyer: "inference.promise.accept", "provider-a": "inference.delivery", "provider-b": "inference.delivery", "buyer-selection": "inference.selection" };
const decoder = new TextDecoder("utf-8", { fatal: true });

class NetworkFailure extends Error {}
function exact(value, keys, label) { if (value === null || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) throw new NetworkFailure(`${label} has the wrong fields`); return value; }
function base(stage, error) { return { profile: REPORT_PROFILE, stage, network_integrity: "FAILED", procurement: "NOT_COMPUTED", byte_equality: "NOT_COMPUTED", buyer_policy: "NOT_COMPUTED", selected_provider: null, selected_receipt: "NOT_COMPUTED", receipt_inclusion: "NOT_COMPUTED", history_extension: "NOT_COMPUTED", history_consistency: "NOT_COMPUTED", same_size_equivocation: "NOT_COMPUTED", challenge: "NOT_COMPUTED", witness_remedy: "NOT_COMPUTED", settlement_authorization: "NOT_COMPUTED", test_ledger_attempt: "NOT_COMPUTED", model_identity_control: "NOT_COMPUTED", reliance: null, substantive_truth: "NOT_ESTABLISHED", actual_funds: "NOT_ESTABLISHED", errors: [String(error)], warnings: [], exit_code: 1 }; }
function subBundle(bundle, prefix) { const result = new Map(); for (const [path, raw] of bundle) if (path.startsWith(prefix)) result.set(path.slice(prefix.length), raw); return result; }

export async function verifyAnswerabilityNetwork(bundle, contextRaw) {
  let stage = "UNREAD";
  try {
    if (!(bundle instanceof Map) || !bundle.has("network-core.json") || bundle.size > 160) throw new NetworkFailure("network bundle is missing or oversized");
    let total = 0; for (const [path, raw] of bundle) { if (typeof path !== "string" || path.includes("\\") || path.startsWith("/") || path.split("/").some((part) => !part || part === "." || part === "..")) throw new NetworkFailure("unsafe network path"); if (!(raw instanceof Uint8Array) || raw.byteLength > 4 * 1024 * 1024) throw new NetworkFailure("network bundle member is oversized"); total += raw.byteLength; } if (total > 32 * 1024 * 1024) throw new NetworkFailure("network bundle is oversized");
    const core = exact(parseComposedJson(bundle.get("network-core.json"), "network core"), ["profile", "revision", "stage", "network_id", "transaction_id", "artifact_manifest"], "network core");
    stage = core.stage;
    if (core.profile !== PROFILE || core.revision !== 1 || core.network_id !== NETWORK_ID || core.transaction_id !== TRANSACTION_ID || !STAGES.has(stage)) throw new NetworkFailure("unsupported network core");
    const context = exact(parseComposedJson(contextRaw, "network context"), ["profile", "accepted_core_hash", "accepted_issuers", "accepted_evidence_verifier", "accepted_witness_covenant_hash", "witness_context", "reliance_context"], "network context");
    if (context.profile !== CONTEXT_PROFILE || !HASH_RE.test(context.accepted_core_hash) || context.accepted_core_hash !== await composedCanonicalHash(core)) throw new NetworkFailure("network core is not externally accepted");
    if (context.accepted_evidence_verifier !== EVIDENCE_PROFILE || context.accepted_witness_covenant_hash !== WITNESS_COVENANT_HASH) throw new NetworkFailure("closed evidence or covenant context is not accepted");
    exact(context.accepted_issuers, Object.keys(ACTIONS), "accepted procurement issuers");
    if (Object.values(context.accepted_issuers).some((issuer) => typeof issuer !== "string" || !issuer) || new Set(Object.values(context.accepted_issuers)).size !== Object.keys(ACTIONS).length) throw new NetworkFailure("accepted procurement issuers are malformed");
    if ((context.witness_context !== null && (typeof context.witness_context !== "object" || Array.isArray(context.witness_context))) || (context.reliance_context !== null && (typeof context.reliance_context !== "object" || Array.isArray(context.reliance_context)))) throw new NetworkFailure("nested verification context is malformed");
    if (!Array.isArray(core.artifact_manifest) || !core.artifact_manifest.length) throw new NetworkFailure("network manifest is empty");
    const declared = new Set();
    for (const item of core.artifact_manifest) {
      exact(item, ["path", "sha256", "byte_length", "media_type"], "network manifest item"); const raw = bundle.get(item.path);
      const expectedMedia = item.path === "procurement/provider-a-delivery.bin" ? "application/octet-stream" : "application/json";
      if (typeof item.path !== "string" || declared.has(item.path) || !raw || item.byte_length !== raw.byteLength || item.sha256 !== await composedBytesHash(raw) || item.media_type !== expectedMedia) throw new NetworkFailure(`network artifact commitment mismatch: ${item.path}`);
      if (expectedMedia === "application/json" && item.path !== "reliance/graph.json") parseComposedJson(raw, item.path);
      declared.add(item.path);
    }
    if (declared.size + 1 !== bundle.size || [...declared].some((path) => !bundle.has(path))) throw new NetworkFailure("network bundle has undeclared members");
    const deliveryAssertionRaw = bundle.get("procurement/provider-a-delivery-assertion.json");
    const providerAEvidenceRefs = [
      { name: "delivery_report", hash: await composedBytesHash(deliveryAssertionRaw), grounding: "self_asserted" },
      { name: "delivered_bytes", hash: ARTIFACT_DIGEST, grounding: "execution_verified" },
    ];
    const receipt = async (path, role) => { const evidenceRefs = role === "provider-a" ? providerAEvidenceRefs : []; const value = await verifyComposedActionReceipt(bundle.get(path), context.accepted_issuers[role], PROFILE, TRANSACTION_ID, evidenceRefs); if (value.action.type !== ACTIONS[role]) throw new NetworkFailure(`${role} action is not accepted`); return value; };
    const buyer = await receipt("procurement/promise.json", "buyer"); const providerA = await receipt("procurement/provider-a.json", "provider-a"); const providerB = await receipt("procurement/provider-b.json", "provider-b"); const selection = await receipt("procurement/selection.json", "buyer-selection");
    const promise = exact(buyer.action.subject, ["profile", "transaction_id", "task_digest", "expected_artifact_digest", "accepted_evidence_classes", "price", "unit", "witness_covenant_hash"], "promise subject");
    const deliveryKeys = ["profile", "transaction_id", "provider_id", "artifact_digest", "evidence_class", "evidence_digest", "model_identity"];
    const a = exact(providerA.action.subject, deliveryKeys, "provider A subject"); const b = exact(providerB.action.subject, deliveryKeys, "provider B subject");
    const selected = exact(selection.action.subject, ["profile", "transaction_id", "selected_provider", "selected_attestation", "policy_digest"], "selection subject");
    if ([promise, a, b, selected].some((subject) => subject.profile !== PROFILE || subject.transaction_id !== core.transaction_id)) throw new NetworkFailure("procurement lineage mismatch");
    if (promise.task_digest !== TASK_DIGEST || promise.expected_artifact_digest !== ARTIFACT_DIGEST || promise.price !== PRICE || promise.unit !== UNIT || promise.witness_covenant_hash !== context.accepted_witness_covenant_hash || a.provider_id !== "provider-a" || b.provider_id !== "provider-b" || a.model_identity !== "SELF_ASSERTED" || b.model_identity !== "SELF_ASSERTED" || !HASH_RE.test(a.evidence_digest) || !HASH_RE.test(b.evidence_digest)) throw new NetworkFailure("procurement terms differ from the closed job");
    if (a.artifact_digest !== b.artifact_digest || a.artifact_digest !== promise.expected_artifact_digest) throw new NetworkFailure("provider-reported artifact digests differ from the promise");
    if (JSON.stringify(promise.accepted_evidence_classes) !== JSON.stringify([EVIDENCE_CLASS]) || a.evidence_class !== EVIDENCE_CLASS || b.evidence_class !== "SELF_ASSERTED") throw new NetworkFailure("buyer evidence policy differs");
    const evidenceRaw = bundle.get("procurement/provider-a-evidence.json"); const deliveryRaw = bundle.get("procurement/provider-a-delivery.bin");
    const deliveryAssertion = exact(parseComposedJson(deliveryAssertionRaw, "provider A delivery assertion"), ["profile", "transaction_id", "provider_id", "artifact_digest", "claim"], "provider A delivery assertion");
    const evidence = exact(parseComposedJson(evidenceRaw, "provider A evidence"), ["profile", "transaction_id", "provider_id", "artifact_digest", "artifact_path"], "provider A evidence");
    const expectedDeliveryAssertion = { profile: PROFILE, transaction_id: TRANSACTION_ID, provider_id: "provider-a", artifact_digest: ARTIFACT_DIGEST, claim: "HISTORICAL_DELIVERY_REPORTED" };
    if (await composedCanonicalHash(deliveryAssertion) !== await composedCanonicalHash(expectedDeliveryAssertion) || evidence.profile !== context.accepted_evidence_verifier || evidence.transaction_id !== TRANSACTION_ID || evidence.provider_id !== "provider-a" || evidence.artifact_digest !== ARTIFACT_DIGEST || evidence.artifact_path !== "procurement/provider-a-delivery.bin" || a.evidence_digest !== await composedBytesHash(evidenceRaw) || await composedBytesHash(deliveryRaw) !== ARTIFACT_DIGEST) throw new NetworkFailure("provider A evidence does not satisfy the accepted byte-preimage rule");
    if (await composedCanonicalHash(providerA.evidence_refs) !== await composedCanonicalHash(providerAEvidenceRefs) || providerB.evidence_refs.length !== 0) throw new NetworkFailure("procurement evidence references are not accepted");
    if (selected.selected_provider !== a.provider_id || selected.selected_attestation !== providerA.hashes.attestation || selected.policy_digest !== await composedCanonicalHash({ accepted_evidence_classes: promise.accepted_evidence_classes, rule: "REQUIRE_SHA256_PREIMAGE/1" })) throw new NetworkFailure("buyer selection is misbound");
    let witness = null; let inclusion = "NOT_PRESENTED"; let extension = "NOT_PRESENTED";
    const witnessBundle = subBundle(bundle, "witness-covenant/");
    if (witnessBundle.size) {
      witness = await verifyWitnessCovenant(witnessBundle, new TextEncoder().encode(JSON.stringify(context.witness_context)));
      if (witness.exit_code !== 0) throw new NetworkFailure("embedded witness covenant did not verify");
      if (witnessBundle.has("evidence/external-receipt-inclusion.json")) {
        const head = parseComposedJson(witnessBundle.get("evidence/head-a.json"), "witness head"); const record = parseComposedJson(witnessBundle.get("evidence/external-receipt-inclusion.json"), "receipt inclusion");
        if (!(await verifyComposedReceiptInclusion(record, providerA, head))) throw new NetworkFailure("selected receipt inclusion proof failed");
        const prefix = parseComposedJson(witnessBundle.get("evidence/external-receipt-checkpoint.json"), "receipt checkpoint"); const consistency = parseComposedJson(witnessBundle.get("evidence/external-receipt-consistency.json"), "receipt consistency"); const covenantCore = parseComposedJson(witnessBundle.get("covenant-core.json"), "covenant core");
        const selectedLeaf = record.leaf;
        if (prefix.tree_size !== 1 || prefix.root !== selectedLeaf || !(await verifyComposedCheckpointExtension(prefix, head, consistency, covenantCore.covenant))) throw new NetworkFailure("selected receipt history extension failed"); inclusion = "VERIFIED"; extension = "VERIFIED";
      }
      const covenantCore = parseComposedJson(witnessBundle.get("covenant-core.json"), "covenant core"); if (promise.witness_covenant_hash !== covenantCore.covenant.covenant_hash) throw new NetworkFailure("promise does not bind the witness covenant");
    }
    let reliance = null;
    if (bundle.has("reliance/graph.json")) {
      reliance = await computeRelianceMap(bundle.get("reliance/graph.json"), bundle.get("reliance/correction-ledger.json"), new TextEncoder().encode(JSON.stringify(context.reliance_context)));
      const graph = JSON.parse(decoder.decode(bundle.get("reliance/graph.json"))); const ledger = JSON.parse(decoder.decode(bundle.get("reliance/correction-ledger.json"))); const checkpointSource = graph.nodes.find((item) => item.node_id === "source-00"); const head = parseComposedJson(witnessBundle.get("evidence/head-a.json"), "accepted witness head"); const otherHead = parseComposedJson(witnessBundle.get("evidence/head-b.json"), "conflicting witness head");
      if (!Array.isArray(ledger.corrections) || ledger.corrections.length !== 1) throw new NetworkFailure("reliance recall must contain one accepted notice");
      const correction = ledger.corrections[0].action.subject; const finding = await composedCanonicalHash({ predicate: "bulla.same-size-log-equivocation/1", head_a: head.checkpoint_hash, head_b: otherHead.checkpoint_hash });
      if (checkpointSource?.artifact_digest !== head.checkpoint_hash || correction.target_digest !== head.checkpoint_hash || correction.reason_digest !== finding || correction.replacement_digest !== await composedCanonicalHash({ recheck_notice: finding }) || JSON.stringify(reliance.summary) !== JSON.stringify({ declared_decisions: 10000, graph_nodes: 10004, graph_edges: 10000, affected: 2500, not_affected: 5000, undetermined: 2500 })) throw new NetworkFailure("reliance binding or tri-state closure changed");
    }
    let derived = "job"; if (witness) { derived = witness.scenario === "consistent" ? "published" : witness.scenario; if (derived !== "model-dispute" && (inclusion !== "VERIFIED" || extension !== "VERIFIED")) throw new NetworkFailure("selected receipt lacks its authenticated append-only history"); } if (stage !== derived || (stage.startsWith("fork-") && !reliance)) throw new NetworkFailure("stage label differs from verified facts");
    let relianceReport = null;
    if (reliance) { const selectedResult = reliance.results.find((item) => item.node_id === "decision-09996"); const selectedPath = ["receipt:provider-a", ...selectedResult.paths[0].nodes]; const head = parseComposedJson(witnessBundle.get("evidence/head-a.json"), "accepted witness head"); if (selectedResult.status !== "AFFECTED" || selectedPath.length !== 14) throw new NetworkFailure("selected 13-hop trace changed"); relianceReport = { graph_digest: reliance.graph_digest, report_digest: reliance.report_digest, summary: reliance.summary, selected_receipt_attestation: providerA.hashes.attestation, selected_checkpoint: head.checkpoint_hash, selected_status: selectedResult.status, selected_path: selectedPath }; }
    const report = { profile: REPORT_PROFILE, stage, network_integrity: "VERIFIED", procurement: "VERIFIED", byte_equality: "PROVIDER_DIGESTS_MATCH_SUPPLIED_BYTES", buyer_policy: "SELECTED_PROVIDER_A", selected_provider: a.provider_id, selected_receipt: "VERIFIED", receipt_inclusion: inclusion, history_extension: extension, history_consistency: witness?.history_consistency ?? "NOT_PRESENTED", same_size_equivocation: witness?.same_size_equivocation ?? "NOT_PRESENTED", challenge: witness?.challenge ?? "NOT_PRESENTED", witness_remedy: witness?.witness_remedy ?? "NOT_PRESENTED", settlement_authorization: witness?.settlement_authorization ?? "NOT_PRESENTED", test_ledger_attempt: witness?.test_ledger_attempt ?? "NOT_PRESENTED", model_identity_control: stage === "model-dispute" ? "CHALLENGE_REQUIRED" : "NOT_RUN", reliance: relianceReport, substantive_truth: "NOT_ESTABLISHED", actual_funds: "NOT_ESTABLISHED", errors: [], warnings: ["the remaining 9,999 decisions are synthetic declarations, not transactions", "a witness fork triggers recheck and bounded recourse; it does not make the provider result false"], exit_code: 0 };
    return { ...report, report_digest: await composedCanonicalHash(report) };
  } catch (error) { return base(stage, error instanceof Error ? error.message : error); }
}

export async function inspectAnswerabilityDecision(bundle, contextRaw, nodeId) {
  if (typeof nodeId !== "string" || !/^decision-[0-9]{5}$/.test(nodeId)) throw new NetworkFailure("decision identifier is invalid");
  const report = await verifyAnswerabilityNetwork(bundle, contextRaw);
  if (report.exit_code !== 0 || report.reliance === null) throw new NetworkFailure("no verified reliance result is available");
  const context = parseComposedJson(contextRaw, "network context");
  const reliance = await computeRelianceMap(
    bundle.get("reliance/graph.json"),
    bundle.get("reliance/correction-ledger.json"),
    new TextEncoder().encode(JSON.stringify(context.reliance_context)),
  );
  if (reliance.report_digest !== report.reliance.report_digest || await composedCanonicalHash(reliance.summary) !== await composedCanonicalHash(report.reliance.summary)) throw new NetworkFailure("inspection differs from the protected reliance report");
  const result = reliance.results.find((item) => item.node_id === nodeId);
  if (!result) throw new NetworkFailure("decision is absent from the accepted graph");
  const path = result.paths.length ? ["receipt:provider-a", ...result.paths[0].nodes] : [];
  return {
    node_id: result.node_id,
    status: result.status,
    conditional_action: result.conditional_action,
    path,
  };
}

export async function inspectAnswerabilityDecisions(bundle, contextRaw, verifiedReport = null) {
  const report = verifiedReport ?? await verifyAnswerabilityNetwork(bundle, contextRaw);
  if (report.exit_code !== 0 || report.reliance === null) throw new NetworkFailure("no verified reliance result is available");
  const context = parseComposedJson(contextRaw, "network context");
  const reliance = await computeRelianceMap(
    bundle.get("reliance/graph.json"),
    bundle.get("reliance/correction-ledger.json"),
    new TextEncoder().encode(JSON.stringify(context.reliance_context)),
  );
  if (reliance.report_digest !== report.reliance.report_digest || await composedCanonicalHash(reliance.summary) !== await composedCanonicalHash(report.reliance.summary)) throw new NetworkFailure("inspection differs from the protected reliance report");
  return reliance.results.map((item) => ({ node_id: item.node_id, status: item.status }));
}
