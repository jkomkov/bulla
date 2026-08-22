/** Dependency-free verifier for bulla.witness-covenant/0.1-experimental. */

export const PROFILE = "bulla.witness-covenant/0.1-experimental";
const CONTEXT_PROFILE = "bulla.witness-covenant-context/0.1-experimental";
const PREDICATE_PROFILE = "bulla.same-size-log-equivocation/1";
const UNIT = "USD_CENTS";
const BOND = 20000;
const MAX_REMEDY = 12500;
const RAIL = "test-ledger/1";
const HASH_RE = /^sha256:[0-9a-f]{64}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });

const ROLE_ACTIONS = {
  provider: "inference.delivery",
  witness_operator: "assurance.promise.accept",
  rail_observer: "assurance.collateral.bind",
  challenge_authority: "assurance.challenge.state",
  settlement_authority: "assurance.settlement.authorize",
  settlement_observer: "assurance.fixture-settlement.report",
};
const ROLES = Object.keys(ROLE_ACTIONS).sort();
const SUBJECT_FIELDS = {
  "inference.delivery": ["profile", "issuer_role", "covenant_id", "covenant_hash", "proposition_digest", "claim_class", "claim_value"],
  "assurance.promise.accept": ["profile", "issuer_role", "covenant_id", "covenant_hash"],
  "assurance.collateral.bind": ["profile", "issuer_role", "covenant_id", "covenant_hash", "binding_id", "unit", "locked_amount", "allocation_manifest", "reported_unspent", "checkpoint_ref", "rail_adapter"],
  "assurance.challenge.state": ["profile", "issuer_role", "covenant_id", "covenant_hash", "finding_ref", "finding_class", "challenge_state", "challenge_checkpoint", "observed_checkpoint", "prior_challenge_ref"],
  "assurance.settlement.authorize": ["profile", "issuer_role", "covenant_id", "covenant_hash", "finding_ref", "finding_class", "challenge_attestation", "consequence", "amount", "unit", "destination", "capital_binding_id", "challenge_state", "rail_checkpoint", "authority_grant"],
  "assurance.fixture-settlement.report": ["profile", "issuer_role", "covenant_id", "covenant_hash", "fixture_report_hash", "authorization_attestation", "amount", "unit", "destination", "status", "synthetic", "actual_funds"],
};
const RECEIPT_FIELDS = ["schema_version", "canonicalization", "kind", "action", "diagnostic_ref", "evidence_refs", "anchor_ref", "mandate", "remedy", "retention", "stake", "conventions", "signature", "occurrence", "authorization", "event_id", "claimed_at", "producer", "hashes"];
const PROOF_FIELDS = ["type", "purpose", "issuer", "verificationMethod", "proofValue"];
const CHECKPOINT_FIELDS = ["schema_version", "profile", "log_id", "operator", "tree_size", "root", "previous_checkpoint_hash", "ordering_domain", "position", "issued_at", "anchor_evidence", "checkpoint_hash", "proof"];

class CovenantFailure extends Error {}

class StrictJsonParser {
  constructor(raw, label) {
    if (!(raw instanceof Uint8Array) || raw.byteLength > 262144) throw new CovenantFailure(`${label} exceeds its byte limit`);
    this.text = decoder.decode(raw); this.label = label; this.index = 0; this.nodes = 0;
  }
  parse() { const value = this.value(1); this.space(); if (this.index !== this.text.length) throw new CovenantFailure(`${this.label} has trailing content`); return value; }
  space() { while (/[\t\n\r ]/.test(this.text[this.index] ?? "")) this.index += 1; }
  count(depth) { this.nodes += 1; if (this.nodes > 10000 || depth > 20) throw new CovenantFailure(`${this.label} exceeds structural limits`); }
  value(depth) {
    this.space(); this.count(depth); const character = this.text[this.index];
    if (character === "{") return this.object(depth);
    if (character === "[") return this.array(depth);
    if (character === '"') return this.string();
    if (this.text.startsWith("true", this.index)) { this.index += 4; return true; }
    if (this.text.startsWith("false", this.index)) { this.index += 5; return false; }
    if (this.text.startsWith("null", this.index)) { this.index += 4; return null; }
    return this.number();
  }
  object(depth) {
    this.index += 1; const result = Object.create(null); const keys = new Set(); this.space();
    if (this.text[this.index] === "}") { this.index += 1; return result; }
    while (true) {
      this.space(); if (this.text[this.index] !== '"') throw new CovenantFailure(`${this.label} has an invalid object`);
      const key = this.string(); this.count(depth + 1); if (keys.has(key)) throw new CovenantFailure(`${this.label} has duplicate member ${key}`); keys.add(key); this.space();
      if (this.text[this.index] !== ":") throw new CovenantFailure(`${this.label} has an invalid object`);
      this.index += 1; result[key] = this.value(depth + 1); this.space(); const separator = this.text[this.index++];
      if (separator === "}") return result; if (separator !== ",") throw new CovenantFailure(`${this.label} has an invalid object`);
    }
  }
  array(depth) { this.index += 1; const result = []; this.space(); if (this.text[this.index] === "]") { this.index += 1; return result; } while (true) { result.push(this.value(depth + 1)); this.space(); const separator = this.text[this.index++]; if (separator === "]") return result; if (separator !== ",") throw new CovenantFailure(`${this.label} has an invalid array`); } }
  string() {
    const start = this.index; this.index += 1; let escaped = false;
    while (this.index < this.text.length) {
      const code = this.text.charCodeAt(this.index);
      if (!escaped && code === 0x22) {
        this.index += 1; let value; try { value = JSON.parse(this.text.slice(start, this.index)); } catch { throw new CovenantFailure(`${this.label} has an invalid string`); }
        if (encoder.encode(value).byteLength > 16384 || [...value].length > 4096) throw new CovenantFailure(`${this.label} has an oversized string`);
        for (let i = 0; i < value.length; i += 1) { const unit = value.charCodeAt(i); if (unit >= 0xd800 && unit <= 0xdbff) { const next = value.charCodeAt(i + 1); if (!(next >= 0xdc00 && next <= 0xdfff)) throw new CovenantFailure(`${this.label} has a lone surrogate`); i += 1; } else if (unit >= 0xdc00 && unit <= 0xdfff) throw new CovenantFailure(`${this.label} has a lone surrogate`); }
        return value;
      }
      if (!escaped && code < 0x20) throw new CovenantFailure(`${this.label} has a control character`);
      if (escaped) escaped = false; else if (code === 0x5c) escaped = true; this.index += 1;
    }
    throw new CovenantFailure(`${this.label} has an unterminated string`);
  }
  number() { const match = /^-?(?:0|[1-9]\d*)/.exec(this.text.slice(this.index)); if (!match) throw new CovenantFailure(`${this.label} has an invalid number`); this.index += match[0].length; if (/[.eE]/.test(this.text[this.index] ?? "")) throw new CovenantFailure(`${this.label} contains a non-integer`); const value = Number(match[0]); if (!Number.isSafeInteger(value)) throw new CovenantFailure(`${this.label} contains an unsafe integer`); return value; }
}

function bytes(value) { return value instanceof Uint8Array ? value : encoder.encode(value); }
function parse(raw, label) { return new StrictJsonParser(bytes(raw), label).parse(); }
function exact(value, fields, label) { if (value === null || typeof value !== "object" || Array.isArray(value)) throw new CovenantFailure(`${label} must be an object`); const actual = Object.keys(value).sort(); const expected = [...fields].sort(); if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new CovenantFailure(`${label} has unexpected fields`); return value; }
function canonical(value) { if (value === null) return "null"; if (value === true) return "true"; if (value === false) return "false"; if (typeof value === "number") { if (!Number.isSafeInteger(value)) throw new CovenantFailure("unsafe canonical integer"); return String(value); } if (typeof value === "string") return JSON.stringify(value); if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`; if (typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${canonical(key)}:${canonical(value[key])}`).join(",")}}`; throw new CovenantFailure("unsupported canonical value"); }
function concat(...values) { const size = values.reduce((total, value) => total + value.byteLength, 0); const out = new Uint8Array(size); let offset = 0; for (const value of values) { out.set(value, offset); offset += value.byteLength; } return out; }
function hex(raw) { return [...raw].map((value) => value.toString(16).padStart(2, "0")).join(""); }
function fromHex(value) { if (!HASH_RE.test(value)) throw new CovenantFailure("invalid SHA-256 digest"); return Uint8Array.from(value.slice(7).match(/../g).map((part) => Number.parseInt(part, 16))); }
async function digest(raw) { return new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes(raw))); }
async function hashBytes(raw) { return `sha256:${hex(await digest(raw))}`; }
async function hashJson(value) { return hashBytes(encoder.encode(canonical(value))); }
async function nodeHash(left, right) { return digest(concat(Uint8Array.of(1), left, right)); }
async function deedLeaf(receipt) { return hashBytes(concat(Uint8Array.of(0), encoder.encode(canonical({ issuer: receipt.signature.issuer, content_hash: receipt.hashes.content, attestation_hash: receipt.hashes.attestation })))); }
async function verifyInclusion(record, expectedLeaf, trustedRoot) {
  exact(record, ["attestation", "index", "tree_size", "leaf", "proof", "root"], "model claim inclusion");
  if (record.leaf !== expectedLeaf || record.root !== trustedRoot || !Number.isSafeInteger(record.index) || !Number.isSafeInteger(record.tree_size) || record.index < 0 || record.index >= record.tree_size || !Array.isArray(record.proof)) return false;
  let fn = record.index; let sn = record.tree_size - 1; let computed = fromHex(record.leaf);
  for (const item of record.proof) { if (sn === 0) return false; const proof = fromHex(item); if ((fn & 1) || fn === sn) { computed = await nodeHash(proof, computed); if (!(fn & 1)) while (!(fn & 1) && fn !== 0) { fn >>= 1; sn >>= 1; } } else computed = await nodeHash(computed, proof); fn >>= 1; sn >>= 1; }
  return sn === 0 && `sha256:${hex(computed)}` === trustedRoot;
}
function base64(value) { const raw = atob(value); return Uint8Array.from(raw, (character) => character.charCodeAt(0)); }
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function didKey(value) { if (typeof value !== "string" || !value.startsWith("did:key:z")) throw new CovenantFailure("issuer is not did:key"); const input = value.slice(9); let number = 0n; for (const character of input) { const index = B58.indexOf(character); if (index < 0) throw new CovenantFailure("invalid did:key"); number = number * 58n + BigInt(index); } const reversed = []; while (number > 0n) { reversed.push(Number(number & 255n)); number >>= 8n; } reversed.reverse(); const leading = input.length - input.replace(/^1+/, "").length; const raw = Uint8Array.from([...new Array(leading).fill(0), ...reversed]); if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) throw new CovenantFailure("did:key is not Ed25519"); return raw.slice(2); }
async function verifyProof(proof, purpose, digestValue, issuer, schema = "0.4") { exact(proof, PROOF_FIELDS, `${purpose} proof`); if (proof.type !== "bulla/ed25519-2026" || proof.purpose !== purpose || proof.issuer !== issuer || proof.verificationMethod !== issuer) throw new CovenantFailure(`${purpose} proof identity mismatch`); const signature = base64(proof.proofValue); if (signature.length !== 64) throw new CovenantFailure(`${purpose} proof length mismatch`); const key = await globalThis.crypto.subtle.importKey("raw", didKey(issuer), { name: "Ed25519" }, false, ["verify"]); const message = encoder.encode(canonical({ context: "bulla-proof", schema, purpose, digest: digestValue })); if (!(await globalThis.crypto.subtle.verify("Ed25519", key, signature, message))) throw new CovenantFailure(`${purpose} proof signature failed`); }

function recourseEnvelope(receipt) { const result = { deed_schema: receipt.mandate.deed_schema || "0.2" }; if (receipt.mandate.authority) result.authority = receipt.mandate.authority; if (receipt.mandate.bounds) result.bounds = receipt.mandate.bounds; if (Object.keys(receipt.remedy).length) result.recourse = receipt.remedy; if (receipt.retention.record) result.retention_class = receipt.retention.record; if (receipt.retention.disclosure) result.disclosure_class = receipt.retention.disclosure; return result; }
async function receiptHashes(receipt) { const preimage = { schema_version: "0.4", canonicalization: "bulla-jcs-int/1", kind: "action_receipt", action: receipt.action, diagnostic_ref: receipt.diagnostic_ref, evidence_refs: receipt.evidence_refs, anchor_ref: receipt.anchor_ref }; if (receipt.conventions.length) preimage.conventions = receipt.conventions; const content = await hashJson(preimage); const event = await hashJson({ content_hash: content, event_id: receipt.event_id, claimed_at: receipt.claimed_at }); const envelope = recourseEnvelope(receipt); const authorization = await hashJson({ event_hash: event, envelope_hash: await hashJson(envelope) }); const attestation = await hashJson({ content_hash: content, signature: receipt.signature, event_hash: event, occurrence: receipt.occurrence, recourse_envelope: envelope, authorization: receipt.authorization }); const log_leaf = await hashBytes(concat(Uint8Array.of(0), encoder.encode(attestation))); return { content, event, attestation, authorization, log_leaf }; }

async function validateReceipt(raw, manifest, core, context) {
  const receipt = exact(parse(raw, manifest.path), RECEIPT_FIELDS, manifest.path);
  if (receipt.schema_version !== "0.4" || receipt.canonicalization !== "bulla-jcs-int/1" || receipt.kind !== "action_receipt") throw new CovenantFailure(`${manifest.path} is not ActionReceipt v0.4`);
  exact(receipt.action, ["type", "subject"], `${manifest.path}.action`);
  const diagnostic = exact(receipt.diagnostic_ref, ["status"], `${manifest.path}.diagnostic_ref`);
  if (diagnostic.status !== "not_applicable" || !Array.isArray(receipt.evidence_refs) || receipt.evidence_refs.length !== 0 || Object.keys(exact(receipt.anchor_ref, [], `${manifest.path}.anchor_ref`)).length !== 0 || !Array.isArray(receipt.conventions) || receipt.conventions.length !== 0 || receipt.stake !== null) throw new CovenantFailure(`${manifest.path} uses unsupported reserved fields`);
  const mandate = exact(receipt.mandate, ["authority", "bounds"], `${manifest.path}.mandate`); const authority = exact(mandate.authority, ["principal", "policy", "delegation"], `${manifest.path}.authority`); const bounds = exact(mandate.bounds, ["scope"], `${manifest.path}.bounds`); const remedy = exact(receipt.remedy, ["challenge_window", "forum", "remedies"], `${manifest.path}.remedy`); const forum = exact(remedy.forum, ["log_endpoint", "trusted_root_ref"], `${manifest.path}.forum`); const retention = exact(receipt.retention, ["disclosure", "record"], `${manifest.path}.retention`);
  if (!Array.isArray(authority.delegation) || authority.delegation.length !== 0 || authority.policy !== `policy://witness-covenant/${receipt.action.type}` || bounds.scope !== `profile:${PROFILE}` || remedy.challenge_window !== "checkpoint:witness-covenant-5" || forum.log_endpoint !== "https://glyphstandard.com/bulla/answerable-computing" || !HASH_RE.test(forum.trusted_root_ref) || !Array.isArray(remedy.remedies) || remedy.remedies.length !== 1 || canonical(exact(remedy.remedies[0], ["anchor", "rung", "verifier"], `${manifest.path}.remedy item`)) !== canonical({ anchor: "forum:witness-covenant", rung: "challenge", verifier: "verify the covenant dossier under separately supplied trust roots" }) || retention.disclosure !== "public" || retention.record !== "operational") throw new CovenantFailure(`${manifest.path} has an unsupported covenant envelope`);
  if (!UUID_RE.test(receipt.event_id) || typeof receipt.claimed_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(receipt.claimed_at)) throw new CovenantFailure(`${manifest.path} has malformed event metadata`);
  const action = receipt.action.type; const role = manifest.role;
  if (action !== manifest.action_type || ROLE_ACTIONS[role] !== action) throw new CovenantFailure(`${manifest.path} action/role mismatch`);
  const subject = exact(receipt.action.subject, SUBJECT_FIELDS[action], `${manifest.path}.subject`);
  if (subject.profile !== PROFILE || subject.issuer_role !== role || subject.covenant_id !== core.covenant_id || subject.covenant_hash !== core.covenant.covenant_hash) throw new CovenantFailure(`${manifest.path} covenant lineage mismatch`);
  const issuer = receipt.signature.issuer;
  if (issuer !== core.role_issuers[role] || !context.accepted_issuers_by_role[role].includes(issuer)) throw new CovenantFailure(`${manifest.path} issuer is not accepted`);
  if (authority.principal !== issuer) throw new CovenantFailure(`${manifest.path} authority is not signer-bound`);
  const computed = await receiptHashes(receipt);
  exact(receipt.hashes, ["content", "event", "attestation", "log_leaf"], `${manifest.path}.hashes`);
  for (const name of ["content", "event", "attestation", "log_leaf"]) if (receipt.hashes[name] !== computed[name]) throw new CovenantFailure(`${manifest.path} ${name} hash mismatch`);
  if (manifest.event !== computed.event || manifest.attestation !== computed.attestation) throw new CovenantFailure(`${manifest.path} manifest reference mismatch`);
  await verifyProof(receipt.signature, "content", computed.content, issuer);
  await verifyProof(receipt.occurrence, "occurrence", computed.event, issuer);
  await verifyProof(receipt.authorization, "authorization", computed.authorization, issuer);
  return receipt;
}

async function validateCheckpoint(value, covenant) {
  exact(value, CHECKPOINT_FIELDS, "witness checkpoint");
  const unsigned = Object.fromEntries(CHECKPOINT_FIELDS.filter((key) => !["checkpoint_hash", "proof"].includes(key)).map((key) => [key, value[key]]));
  if (value.schema_version !== "0.1-draft" || value.profile !== "bulla.witness-checkpoint/0.1-draft" || typeof value.operator !== "string" || !value.operator.trim() || typeof value.log_id !== "string" || !value.log_id.trim() || !Number.isSafeInteger(value.tree_size) || value.tree_size < 0 || value.position !== value.tree_size || !HASH_RE.test(value.root) || (value.previous_checkpoint_hash !== null && !HASH_RE.test(value.previous_checkpoint_hash)) || (value.issued_at !== null && typeof value.issued_at !== "string") || value.checkpoint_hash !== await hashJson(unsigned)) throw new CovenantFailure("witness checkpoint binding failed");
  if (value.ordering_domain !== await hashJson({ profile: value.profile, log_id: value.log_id, operator: value.operator })) throw new CovenantFailure("witness ordering domain failed");
  if (value.operator !== covenant.operator || value.log_id !== covenant.log_id || canonical(value.anchor_evidence) !== canonical({ authority_epoch: covenant.authority_epoch, covenant_hash: covenant.covenant_hash })) throw new CovenantFailure("witness checkpoint is outside the covenant");
  await verifyProof(value.proof, "witness-checkpoint", value.checkpoint_hash, value.operator, "0.3");
}

/** Verify one signed ActionReceipt v0.4 for a composing source profile. */
export async function verifyComposedActionReceipt(raw, acceptedIssuer, expectedProfile, transactionId, expectedEvidenceRefs = []) {
  const receipt = exact(parse(raw, "composed ActionReceipt"), RECEIPT_FIELDS, "composed ActionReceipt");
  if (receipt.schema_version !== "0.4" || receipt.canonicalization !== "bulla-jcs-int/1" || receipt.kind !== "action_receipt") throw new CovenantFailure("composed receipt is not ActionReceipt v0.4");
  exact(receipt.action, ["type", "subject"], "composed receipt action");
  if (!UUID_RE.test(receipt.event_id) || typeof receipt.claimed_at !== "string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/.test(receipt.claimed_at)) throw new CovenantFailure("composed receipt metadata is malformed");
  if (canonical(exact(receipt.diagnostic_ref, ["status"], "diagnostic_ref")) !== canonical({ status: "not_applicable" }) || !Array.isArray(receipt.evidence_refs) || !Array.isArray(expectedEvidenceRefs) || canonical(receipt.evidence_refs) !== canonical(expectedEvidenceRefs) || receipt.evidence_refs.some((item) => { exact(item, ["name", "hash", "grounding"], "composed evidence reference"); return typeof item.name !== "string" || !HASH_RE.test(item.hash) || typeof item.grounding !== "string"; }) || Object.keys(exact(receipt.anchor_ref, [], "anchor_ref")).length || !Array.isArray(receipt.conventions) || receipt.conventions.length || receipt.stake !== null) throw new CovenantFailure("composed receipt uses unsupported reserved fields");
  const mandate = exact(receipt.mandate, ["authority", "bounds"], "mandate"); const authority = exact(mandate.authority, ["principal", "policy", "delegation"], "authority"); const bounds = exact(mandate.bounds, ["scope"], "bounds");
  const remedy = exact(receipt.remedy, ["challenge_window", "forum", "remedies"], "remedy"); const forum = exact(remedy.forum, ["log_endpoint", "trusted_root_ref"], "forum");
  const retention = exact(receipt.retention, ["disclosure", "record"], "retention");
  const expectedAction = receipt.action.type;
  const expectedRemedy = [{ anchor: "forum:answerability-network", rung: "challenge", verifier: "verify the retained network under supplied context" }];
  if (typeof expectedProfile !== "string" || typeof transactionId !== "string" || !Array.isArray(authority.delegation) || authority.delegation.length || authority.principal !== acceptedIssuer || authority.policy !== `policy://answerability-network/${expectedAction}` || receipt.signature?.issuer !== acceptedIssuer || bounds.scope !== `profile:${expectedProfile};transaction:${transactionId}` || remedy.challenge_window !== "checkpoint:answerability-network-5" || forum.log_endpoint !== "https://glyphstandard.com/bulla/answerable-computing" || !HASH_RE.test(forum.trusted_root_ref) || canonical(remedy.remedies) !== canonical(expectedRemedy) || canonical(retention) !== canonical({ disclosure: "public", record: "operational" })) throw new CovenantFailure("composed receipt authority is not accepted");
  if (receipt.producer === null || typeof receipt.producer !== "object" || Array.isArray(receipt.producer)) throw new CovenantFailure("composed receipt producer is malformed");
  const computed = await receiptHashes(receipt); exact(receipt.hashes, ["content", "event", "attestation", "log_leaf"], "hashes");
  for (const name of ["content", "event", "attestation", "log_leaf"]) if (receipt.hashes[name] !== computed[name]) throw new CovenantFailure(`composed receipt ${name} hash mismatch`);
  await verifyProof(receipt.signature, "content", computed.content, acceptedIssuer); await verifyProof(receipt.occurrence, "occurrence", computed.event, acceptedIssuer); await verifyProof(receipt.authorization, "authorization", computed.authorization, acceptedIssuer);
  return receipt;
}

export async function verifyComposedReceiptInclusion(record, receipt, trustedHead) {
  const inclusion = exact(record, ["attestation", "index", "tree_size", "leaf", "proof", "root"], "composed receipt inclusion");
  return inclusion.attestation === receipt.hashes.attestation && inclusion.root === trustedHead.root && inclusion.tree_size === trustedHead.tree_size && await verifyInclusion(inclusion, await deedLeaf(receipt), trustedHead.root);
}

async function verifyConsistency(record, oldCheckpoint, newCheckpoint) {
  exact(record, ["old_size", "new_size", "old_root", "new_root", "proof"], "consistency record");
  const m = record.old_size; const n = record.new_size;
  if (!Number.isSafeInteger(m) || !Number.isSafeInteger(n) || m < 0 || n < 0 || m !== oldCheckpoint.tree_size || n !== newCheckpoint.tree_size || record.old_root !== oldCheckpoint.root || record.new_root !== newCheckpoint.root || m > n || !Array.isArray(record.proof)) return false;
  if (m === n) return record.proof.length === 0 && record.old_root === record.new_root;
  if (m === 0) return record.proof.length === 0;
  if (!record.proof.length) return false;
  let node = m - 1; let last = n - 1; while (node & 1) { node >>= 1; last >>= 1; }
  let h1; let h2; let rest;
  if (node > 0) { h1 = fromHex(record.proof[0]); h2 = fromHex(record.proof[0]); rest = record.proof.slice(1); } else { h1 = fromHex(record.old_root); h2 = fromHex(record.old_root); rest = record.proof; }
  for (const item of rest) { if (last === 0) return false; const proof = fromHex(item); if ((node & 1) || node === last) { h1 = await nodeHash(proof, h1); h2 = await nodeHash(proof, h2); while (!(node & 1) && node !== 0) { node >>= 1; last >>= 1; } } else h2 = await nodeHash(h2, proof); node >>= 1; last >>= 1; }
  return last === 0 && `sha256:${hex(h1)}` === record.old_root && `sha256:${hex(h2)}` === record.new_root;
}

export async function verifyComposedCheckpointExtension(prefix, head, record, covenant) {
  await validateCheckpoint(prefix, covenant);
  return head.previous_checkpoint_hash === prefix.checkpoint_hash && head.operator === prefix.operator && head.log_id === prefix.log_id && head.ordering_domain === prefix.ordering_domain && head.tree_size >= prefix.tree_size && await verifyConsistency(record, prefix, head);
}

export async function composedCanonicalHash(value) { return hashJson(value); }
export function parseComposedJson(raw, label = "composed JSON") { return parse(raw, label); }
export async function composedBytesHash(raw) { return hashBytes(raw); }

function baseReport(scenario, error) {
  return { profile: PROFILE, scenario, dossier_integrity: "FAILED", receipt_integrity: "NOT_COMPUTED", signature_status: "NOT_COMPUTED", issuer_role_status: "NOT_COMPUTED", covenant_binding: "NOT_COMPUTED", checkpoint_authenticity: "NOT_COMPUTED", joint_observation: "NOT_COMPUTED", claim_inclusion: "NOT_COMPUTED", history_consistency: "NOT_COMPUTED", same_size_equivocation: "NOT_COMPUTED", witness_trust: "NOT_COMPUTED", witness_control: "PROJECT_OPERATED", capital_lock: "NOT_COMPUTED", capital_allocation: "NOT_COMPUTED", challenge: "NOT_COMPUTED", witness_remedy: "NOT_COMPUTED", settlement_authorization: "NOT_COMPUTED", test_ledger_attempt: "NOT_COMPUTED", substantive_truth: "NOT_ESTABLISHED", occurrence: "NOT_ESTABLISHED", deterrence: "NOT_COMPUTED", custody: "NOT_COMPUTED", collectibility: "NOT_COMPUTED", actual_funds: "NOT_ESTABLISHED", suppressed_conclusions: ["all protected conclusions"], errors: [String(error)], warnings: [], exit_code: 1 };
}

/** Verify a closed covenant. bundle maps normalized relative paths to bytes. */
export async function verifyWitnessCovenant(bundle, contextRaw) {
  let scenario = "UNKNOWN";
  try {
    if (!(bundle instanceof Map) || !bundle.has("covenant-core.json") || bundle.size > 64) throw new CovenantFailure("dossier membership is invalid");
    let total = 0; for (const [path, raw] of bundle) { if (!path || path.startsWith("/") || path.includes("\\") || path.split("/").some((part) => ["", ".", ".."].includes(part))) throw new CovenantFailure("dossier path is unsafe"); if (bytes(raw).byteLength > 262144) throw new CovenantFailure(`${path} exceeds the per-member byte limit`); total += bytes(raw).byteLength; if (total > 4194304) throw new CovenantFailure("dossier exceeds aggregate limits"); }
    const core = exact(parse(bundle.get("covenant-core.json"), "covenant core"), ["profile", "revision", "scenario", "covenant_id", "covenant", "role_issuers", "receipt_manifest", "artifact_manifest"], "covenant core");
    scenario = core.scenario;
    if (core.profile !== PROFILE || core.revision !== 1) throw new CovenantFailure("unsupported covenant profile");
    const context = exact(parse(contextRaw, "verification context"), ["profile", "accepted_operator", "accepted_log", "authority_epoch", "accepted_covenant_hash", "accepted_issuers_by_role", "accepted_authority_grants", "accepted_rail_adapters", "accepted_capital_checkpoint", "controlled_roles"], "verification context");
    if (context.profile !== CONTEXT_PROFILE || canonical(context.accepted_rail_adapters) !== canonical([RAIL]) || canonical([...context.controlled_roles].sort()) !== canonical(ROLES) || !HASH_RE.test(context.accepted_capital_checkpoint) || !Array.isArray(context.accepted_authority_grants) || !context.accepted_authority_grants.length || context.accepted_authority_grants.some((item) => !HASH_RE.test(item))) throw new CovenantFailure("unsupported verification context");
    exact(context.accepted_issuers_by_role, ROLES, "accepted issuers");
    for (const role of ROLES) if (!Array.isArray(context.accepted_issuers_by_role[role]) || context.accepted_issuers_by_role[role].length !== 1) throw new CovenantFailure("each role needs one accepted issuer");
    const covenant = exact(core.covenant, ["covenant_hash", "operator", "key_identifier", "log_id", "authority_epoch", "checkpoint_profile", "covered_duty", "fault_predicate", "challenge_checkpoint", "correction_path", "beneficiary", "settlement_authority", "destination", "maximum_remedy", "capital"], "covenant");
    const covenantBody = Object.fromEntries(Object.entries(covenant).filter(([key]) => key !== "covenant_hash"));
    const capitalPolicy = exact(covenant.capital, ["binding_id", "unit", "required_allocation", "binding_mode", "rail_adapter"], "capital policy");
    const challengeCheckpoint = exact(covenant.challenge_checkpoint, ["domain", "value"], "challenge checkpoint");
    if (covenant.covenant_hash !== await hashJson(covenantBody) || covenant.fault_predicate !== PREDICATE_PROFILE || covenant.covered_duty !== "sign no different roots for one log, epoch, and tree size" || covenant.checkpoint_profile !== "bulla.witness-checkpoint/0.1-draft" || covenant.key_identifier !== covenant.operator || covenant.maximum_remedy !== MAX_REMEDY || challengeCheckpoint.domain !== "accepted-checkpoint-height" || challengeCheckpoint.value !== 5 || capitalPolicy.unit !== UNIT || capitalPolicy.required_allocation !== BOND || capitalPolicy.binding_mode !== "DEDICATED" || capitalPolicy.rail_adapter !== RAIL) throw new CovenantFailure("covenant differs from the closed profile");
    if (covenant.operator !== context.accepted_operator || covenant.log_id !== context.accepted_log || covenant.authority_epoch !== context.authority_epoch || covenant.covenant_hash !== context.accepted_covenant_hash) throw new CovenantFailure("context does not accept this covenant");
    exact(core.role_issuers, ROLES, "role issuers"); if (new Set(Object.values(core.role_issuers)).size !== ROLES.length) throw new CovenantFailure("protected roles reuse one issuer"); if (ROLES.some((role) => canonical(context.accepted_issuers_by_role[role]) !== canonical([core.role_issuers[role]]))) throw new CovenantFailure("external context does not accept every named role issuer"); if (covenant.settlement_authority !== core.role_issuers.settlement_authority) throw new CovenantFailure("named settlement authority is not accepted");
    const declared = new Set();
    for (const [kind, manifest] of [["receipt", core.receipt_manifest], ["artifact", core.artifact_manifest]]) {
      if (!Array.isArray(manifest) || !manifest.length) throw new CovenantFailure(`${kind} manifest is empty`);
      for (const item of manifest) { exact(item, kind === "receipt" ? ["path", "sha256", "byte_length", "media_type", "role", "action_type", "event", "attestation"] : ["path", "sha256", "byte_length", "media_type"], `${kind} manifest item`); if (item.media_type !== "application/json") throw new CovenantFailure("the closed covenant permits JSON members only"); if (declared.has(item.path) || item.path === "covenant-core.json") throw new CovenantFailure("duplicate or circular manifest path"); declared.add(item.path); const raw = bundle.get(item.path); if (!raw || item.byte_length !== bytes(raw).byteLength || item.sha256 !== await hashBytes(raw)) throw new CovenantFailure(`artifact commitment mismatch ${item.path}`); parse(raw, item.path); }
    }
    if (declared.size + 1 !== bundle.size || [...declared].some((path) => !bundle.has(path))) throw new CovenantFailure("dossier has undeclared or missing files");
    const receipts = [];
    for (const item of core.receipt_manifest) receipts.push(await validateReceipt(bundle.get(item.path), item, core, context));
    const byRole = (role) => receipts.filter((item) => item.action.subject.issuer_role === role);
    const acceptances = byRole("witness_operator"); const capitalReceipts = byRole("rail_observer");
    if (acceptances.length !== 1 || capitalReceipts.length > 1) throw new CovenantFailure("one acceptance and at most one capital binding are allowed");
    if (acceptances[0].signature.issuer !== covenant.operator) throw new CovenantFailure("the accepted operator did not sign the covenant");
    const allocation = capitalReceipts.length ? capitalReceipts[0].action.subject : null;
    const expectedAllocation = [{ covenant_hash: covenant.covenant_hash, amount: BOND, active: true }];
    const capitalAdequate = allocation !== null;
    if (allocation && (allocation.binding_id !== capitalPolicy.binding_id || allocation.unit !== UNIT || allocation.locked_amount !== BOND || canonical(allocation.allocation_manifest) !== canonical(expectedAllocation) || allocation.reported_unspent !== true || allocation.checkpoint_ref !== context.accepted_capital_checkpoint || allocation.rail_adapter !== RAIL)) throw new CovenantFailure("dedicated allocation is inadequate or misbound");
    const headA = parse(bundle.get("evidence/head-a.json"), "head A"); const headB = parse(bundle.get("evidence/head-b.json"), "head B");
    await validateCheckpoint(headA, covenant); await validateCheckpoint(headB, covenant);
    if (headA.operator !== headB.operator || headA.log_id !== headB.log_id || canonical(headA.anchor_evidence) !== canonical(headB.anchor_evidence) || headA.tree_size !== headB.tree_size) throw new CovenantFailure("checkpoint views are not comparable");
    const equivocation = headA.root !== headB.root;
    const equivocationFindingRef = await hashJson({ predicate: PREDICATE_PROFILE, head_a: headA.checkpoint_hash, head_b: headB.checkpoint_hash });
    const providerClaims = byRole("provider"); if (providerClaims.length > 1) throw new CovenantFailure("at most one provider claim is supported");
    let claimInclusion = "NOT_APPLICABLE"; let claimFindingRef = null;
    if (providerClaims.length) { const claim = providerClaims[0]; const subject = claim.action.subject; if (subject.claim_class !== "MODEL_IDENTITY_P3") throw new CovenantFailure("unsupported provider claim class"); const inclusion = parse(bundle.get("evidence/model-claim-inclusion.json"), "model claim inclusion"); if (inclusion.attestation !== claim.hashes.attestation || inclusion.tree_size !== headA.tree_size || inclusion.root !== headA.root || !(await verifyInclusion(inclusion, await deedLeaf(claim), headA.root))) throw new CovenantFailure("provider model claim lacks leaf-bound inclusion"); claimInclusion = "VERIFIED"; claimFindingRef = await hashJson({ claim_attestation: claim.hashes.attestation, proposition_digest: subject.proposition_digest, claim_class: subject.claim_class }); }
    const challenges = byRole("challenge_authority"); if (challenges.length > 2) throw new CovenantFailure("too many challenge transitions");
    let challenge = "NOT_APPLICABLE"; let challengeAttestation = null; let findingClass = "SAME_SIZE_LOG_EQUIVOCATION"; let initialFinding = null; let prior = null;
    for (let index = 0; index < challenges.length; index += 1) { const receipt = challenges[index]; const subject = receipt.action.subject; const expectedFindingRef = subject.finding_class === "SAME_SIZE_LOG_EQUIVOCATION" ? equivocationFindingRef : subject.finding_class === "MODEL_IDENTITY_P3" && claimFindingRef ? claimFindingRef : null; if (!expectedFindingRef) throw new CovenantFailure("unsupported or ungrounded finding class"); if (subject.finding_ref !== expectedFindingRef || canonical(subject.challenge_checkpoint) !== canonical(covenant.challenge_checkpoint) || subject.prior_challenge_ref !== prior) throw new CovenantFailure("challenge chronology is misbound"); if (!Number.isSafeInteger(subject.observed_checkpoint) || subject.observed_checkpoint < 0) throw new CovenantFailure("observed checkpoint must be a non-negative safe integer"); if (initialFinding === null) initialFinding = subject.finding_class; else if (subject.finding_class !== initialFinding) throw new CovenantFailure("challenge changes finding class"); if (index === 0 && subject.challenge_state !== "OPEN") throw new CovenantFailure("first challenge must be OPEN"); if (index === 1 && subject.challenge_state !== "EXPIRED") throw new CovenantFailure("second challenge must be EXPIRED"); if (subject.challenge_state === "OPEN" && subject.observed_checkpoint >= covenant.challenge_checkpoint.value) throw new CovenantFailure("OPEN challenge is stale"); if (subject.challenge_state === "EXPIRED" && subject.observed_checkpoint < covenant.challenge_checkpoint.value) throw new CovenantFailure("EXPIRED challenge is premature"); findingClass = subject.finding_class; prior = receipt.hashes.attestation; challengeAttestation = prior; challenge = subject.challenge_state; }
    if (!equivocation && findingClass === "SAME_SIZE_LOG_EQUIVOCATION") challenge = "NOT_APPLICABLE"; else if (findingClass !== "SAME_SIZE_LOG_EQUIVOCATION") challenge = "CHALLENGE_REQUIRED";
    let remedy = "INELIGIBLE"; if (findingClass !== "SAME_SIZE_LOG_EQUIVOCATION") remedy = "CHALLENGE_REQUIRED"; else if (equivocation && challenge === "OPEN") remedy = "CHALLENGE_REQUIRED"; else if (equivocation && challenge === "EXPIRED" && capitalAdequate) remedy = "ELIGIBLE";
    const authorizations = byRole("settlement_authority"); let authorizationStatus = "NOT_ISSUED"; let authorizationAttestation = null;
    if (authorizations.length) { if (authorizations.length !== 1 || remedy !== "ELIGIBLE" || !allocation) throw new CovenantFailure("settlement authorization is not permitted"); const receipt = authorizations[0]; const subject = receipt.action.subject; if (subject.finding_ref !== equivocationFindingRef || subject.finding_class !== "SAME_SIZE_LOG_EQUIVOCATION" || subject.challenge_attestation !== challengeAttestation || subject.consequence !== "TRANSFER_COLLATERAL" || subject.amount !== MAX_REMEDY || subject.unit !== UNIT || subject.destination !== covenant.destination || subject.capital_binding_id !== capitalPolicy.binding_id || subject.challenge_state !== "EXPIRED" || subject.rail_checkpoint !== allocation.checkpoint_ref || !context.accepted_authority_grants.includes(subject.authority_grant)) throw new CovenantFailure("settlement authorization is misbound"); authorizationStatus = "VERIFIED"; authorizationAttestation = receipt.hashes.attestation; }
    const observers = byRole("settlement_observer"); let attempt = "NOT_REPORTED";
    if (observers.length) { if (observers.length !== 1 || authorizationStatus !== "VERIFIED") throw new CovenantFailure("test-ledger report lacks authorization"); const raw = bundle.get("settlement/test-ledger-report.json"); const subject = observers[0].action.subject; const fixture = exact(parse(raw, "test-ledger report"), ["profile", "covenant_hash", "authorization_attestation", "status", "amount", "unit", "destination", "synthetic", "actual_funds"], "test-ledger report"); const expectedFixture = { profile: "bulla.test-ledger/1", covenant_hash: covenant.covenant_hash, authorization_attestation: authorizationAttestation, status: "ATTEMPT_REPORTED", amount: MAX_REMEDY, unit: UNIT, destination: covenant.destination, synthetic: true, actual_funds: false }; if (!raw || subject.fixture_report_hash !== await hashBytes(raw) || subject.authorization_attestation !== authorizationAttestation || subject.amount !== MAX_REMEDY || subject.unit !== UNIT || subject.destination !== covenant.destination || subject.status !== "ATTEMPT_REPORTED" || subject.synthetic !== true || subject.actual_funds !== false || canonical(fixture) !== canonical(expectedFixture)) throw new CovenantFailure("test-ledger report is misbound"); attempt = "REPORTED"; }
    const derivedScenario = findingClass === "MODEL_IDENTITY_P3" ? "model-dispute" : !equivocation ? "consistent" : challenge === "OPEN" ? "fork-open" : challenge === "EXPIRED" && authorizationStatus === "VERIFIED" && attempt === "REPORTED" ? "fork-authorized" : challenge === "EXPIRED" && !capitalAdequate ? "fork-closed-no-bond" : challenge === "EXPIRED" ? "fork-closed" : "UNRESOLVED";
    if (scenario !== derivedScenario) throw new CovenantFailure("scenario label differs from authenticated facts");
    const suppressed = []; if (remedy !== "ELIGIBLE") suppressed.push("automatic witness remedy"); if (authorizationStatus !== "VERIFIED") suppressed.push("settlement authorization");
    return { profile: PROFILE, scenario, dossier_integrity: "VERIFIED", receipt_integrity: "VERIFIED", signature_status: "VERIFIED", issuer_role_status: "VERIFIED", covenant_binding: "VERIFIED", checkpoint_authenticity: "VERIFIED", joint_observation: "VERIFIED", claim_inclusion: claimInclusion, history_consistency: equivocation ? "EQUIVOCATION_ESTABLISHED" : "CONSISTENT_AT_COMPARED_SIZE", same_size_equivocation: equivocation ? "ESTABLISHED" : "NOT_ESTABLISHED", witness_trust: "SUPPLIED_CONTEXT_ACCEPTED", witness_control: "PROJECT_OPERATED", capital_lock: capitalAdequate ? "REPORTED_UNSPENT" : "ABSENT", capital_allocation: capitalAdequate ? "ADEQUATE_DEDICATED" : "INADEQUATE", challenge, witness_remedy: remedy, settlement_authorization: authorizationStatus, test_ledger_attempt: attempt, substantive_truth: "NOT_ESTABLISHED", occurrence: "NOT_ESTABLISHED", deterrence: "NOT_COMPUTED", custody: "NOT_COMPUTED", collectibility: "NOT_COMPUTED", actual_funds: "NOT_ESTABLISHED", suppressed_conclusions: suppressed, errors: [], warnings: ["bond amount is an authored term, not a risk estimate", "checkpoint signatures establish operator statements, not worldly truth"], exit_code: 0 };
  } catch (error) { return baseReport(scenario, error instanceof Error ? error.message : error); }
}
