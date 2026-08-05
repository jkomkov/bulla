#!/usr/bin/env node
/** Standalone Node verifier for bulla.inference-clearing/0.1-experimental. */

import { createHash, createPublicKey, verify as verifySignature } from "node:crypto";
import {
  closeSync, constants as fsConstants, fstatSync, lstatSync, openSync,
  readFileSync, readdirSync,
} from "node:fs";
import { relative, resolve, sep } from "node:path";

const PROFILE = "bulla.inference-clearing/0.1-experimental";
const EXECUTION_PROFILE = "bulla.inference-execution-evidence/0.1-experimental";
const COVERAGE_PROFILE = "bulla.inference-receiver-coverage/0.1-experimental";
const CAPITAL_PROFILE = "bulla.inference-capital-allocation/0.1-experimental";
const SETTLEMENT_PROFILE = "bulla.inference-settlement-report/0.1-experimental";
const CORE = "clearing-core.json";
const PUBLISH = "publish-receipt.json";
const HASH_RE = /^sha256:[0-9a-f]{64}$/;
const MAX_FILE_BYTES = 262144;
const MAX_TOTAL_BYTES = 4194304;
const MAX_FILES = 64;
const MAX_DEPTH = 24;
const MAX_NODES = 25000;
const MAX_STRING_BYTES = 16384;
const RECEIPT_FIELDS = new Set([
  "schema_version", "canonicalization", "kind", "action", "diagnostic_ref",
  "evidence_refs", "anchor_ref", "mandate", "remedy", "retention", "stake",
  "conventions", "signature", "occurrence", "authorization", "event_id",
  "claimed_at", "producer", "hashes",
]);
const PROOF_FIELDS = new Set(["type", "purpose", "issuer", "verificationMethod", "proofValue"]);
const ACTION_ROLES = {
  "inference.order": "buyer",
  "inference.route": "router",
  "inference.accept": "provider",
  "inference.delivery": "receiver",
  "inference.coverage.checkpoint": "receiver",
  "assurance.collateral.bind": "rail_observer",
  "assurance.guarantee.issue": "guarantor",
  "bulla.rely": "relier",
  "assurance.settlement.authorize": "settlement_authority",
  "rail.settlement.report": "rail_observer",
  "inference.clearing.publish": "publisher",
};
const ACTION_SUBJECT_FIELDS = {
  "inference.order": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "input_hash", "price"],
  "inference.route": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "provider_kind"],
  "inference.accept": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "accepted_model_hash", "execution_report_hash", "provider_process_claim"],
  "inference.delivery": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "effect_id", "output_hash", "receiver_anchor"],
  "inference.coverage.checkpoint": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "coverage_hash", "anchor_id", "denominator_count", "receipted_count"],
  "assurance.collateral.bind": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "capital_hash"],
  "assurance.guarantee.issue": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "guaranteed_claim", "exposure", "unit", "capital_hash"],
  "bulla.rely": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "policy_hash", "decision", "unmet_requirements", "named_consequence", "witness_root", "coverage_hash", "coverage_ref"],
  "assurance.settlement.authorize": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "eligibility", "destination", "amount", "unit", "rail_adapter"],
  "rail.settlement.report": ["profile", "issuer_role", "transaction_id", "term_root", "comparison_group", "parent_ref", "status", "authorization_ref", "amount", "unit", "rail_adapter", "settlement_hash"],
  "inference.clearing.publish": ["profile", "issuer_role", "transaction_id", "clearing_core_hash", "component_hashes"],
};
const COMPONENT_NAMES = new Set(["terms", "receipts", "execution", "receiver", "witness", "assurance", "settlement"]);
const DECLARED_ROLE_NAMES = new Set(["buyer", "router", "provider_opaque", "provider_reproducible", "receiver", "witness", "relier", "settlement_authority", "rail_observer", "guarantor", "publisher"]);
const CONTEXT_ROLE_NAMES = new Set(["buyer", "router", "provider", "receiver", "witness", "relier", "settlement_authority", "rail_observer", "guarantor", "publisher"]);
const BASE_SEQUENCE = [
  "inference.order", "inference.route", "inference.accept", "inference.delivery",
  "assurance.collateral.bind", "inference.coverage.checkpoint", "bulla.rely",
];
const ELIGIBLE_SEQUENCE = [
  ...BASE_SEQUENCE, "assurance.settlement.authorize", "rail.settlement.report",
];
const GUARANTEE_SEQUENCE = [...BASE_SEQUENCE.slice(0, 5), "assurance.guarantee.issue", "inference.coverage.checkpoint", "bulla.rely"];
const REQUIRED_EVIDENCE = {
  output_binding: "VERIFIED",
  model_binding: "TERM_BOUND",
  relation_reproduction: "REPRODUCED",
  provider_execution_occurrence: "NOT_REQUIRED",
};

class Malformed extends Error {}
class VerificationFailure extends Error {}

class StrictJsonParser {
  constructor(text, label) {
    this.text = text; this.label = label; this.index = 0; this.nodes = 0;
  }
  parse() {
    const value = this.value(1); this.space();
    if (this.index !== this.text.length) throw new Malformed(`invalid JSON in ${this.label}: trailing content`);
    return value;
  }
  space() { while (/[\t\n\r ]/.test(this.text[this.index] ?? "")) this.index += 1; }
  count(depth) {
    this.nodes += 1;
    if (this.nodes > MAX_NODES || depth > MAX_DEPTH) throw new Malformed(`${this.label} exceeds JSON resource limits`);
  }
  value(depth) {
    this.space(); this.count(depth);
    const character = this.text[this.index];
    if (character === "{") return this.object(depth);
    if (character === "[") return this.array(depth);
    if (character === '"') return this.string();
    if (this.text.startsWith("true", this.index)) { this.index += 4; return true; }
    if (this.text.startsWith("false", this.index)) { this.index += 5; return false; }
    if (this.text.startsWith("null", this.index)) { this.index += 4; return null; }
    return this.number();
  }
  object(depth) {
    this.index += 1; const result = {}; const keys = new Set(); this.space();
    if (this.text[this.index] === "}") { this.index += 1; return result; }
    while (true) {
      this.space(); this.count(depth + 1);
      if (this.text[this.index] !== '"') throw new Malformed(`invalid JSON object in ${this.label}`);
      const key = this.string();
      if (keys.has(key)) throw new Malformed(`duplicate JSON member ${JSON.stringify(key)}`);
      keys.add(key); this.space();
      if (this.text[this.index] !== ":") throw new Malformed(`invalid JSON object in ${this.label}`);
      this.index += 1; result[key] = this.value(depth + 1); this.space();
      const separator = this.text[this.index++];
      if (separator === "}") return result;
      if (separator !== ",") throw new Malformed(`invalid JSON object in ${this.label}`);
    }
  }
  array(depth) {
    this.index += 1; const result = []; this.space();
    if (this.text[this.index] === "]") { this.index += 1; return result; }
    while (true) {
      result.push(this.value(depth + 1)); this.space();
      const separator = this.text[this.index++];
      if (separator === "]") return result;
      if (separator !== ",") throw new Malformed(`invalid JSON array in ${this.label}`);
    }
  }
  string() {
    const start = this.index; this.index += 1; let escaped = false;
    while (this.index < this.text.length) {
      const code = this.text.charCodeAt(this.index);
      if (!escaped && code === 0x22) {
        this.index += 1; let value;
        try { value = JSON.parse(this.text.slice(start, this.index)); }
        catch (error) { throw new Malformed(`invalid JSON string in ${this.label}: ${error.message}`); }
        for (let i = 0; i < value.length; i += 1) {
          const unit = value.charCodeAt(i);
          if (unit >= 0xd800 && unit <= 0xdbff) {
            const next = value.charCodeAt(i + 1);
            if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Malformed(`${this.label} contains a lone Unicode surrogate`);
            i += 1;
          } else if (unit >= 0xdc00 && unit <= 0xdfff) throw new Malformed(`${this.label} contains a lone Unicode surrogate`);
        }
        if (Buffer.byteLength(value, "utf8") > MAX_STRING_BYTES) throw new Malformed(`${this.label} contains an oversized string`);
        return value;
      }
      if (!escaped && code < 0x20) throw new Malformed(`invalid control character in ${this.label}`);
      if (escaped) escaped = false; else if (code === 0x5c) escaped = true;
      this.index += 1;
    }
    throw new Malformed(`unterminated JSON string in ${this.label}`);
  }
  number() {
    const match = /^-?(?:0|[1-9]\d*)/.exec(this.text.slice(this.index));
    if (!match) throw new Malformed(`invalid JSON number in ${this.label}`);
    this.index += match[0].length;
    if (/[.eE]/.test(this.text[this.index] ?? "")) throw new Malformed(`${this.label} contains a non-integer number`);
    const value = Number(match[0]);
    if (!Number.isSafeInteger(value)) throw new Malformed(`${this.label} contains an unsafe integer`);
    return value;
  }
}

function loadJson(raw, label) {
  if (raw.length > MAX_FILE_BYTES) throw new Malformed(`${label} exceeds ${MAX_FILE_BYTES} bytes`);
  return new StrictJsonParser(new TextDecoder("utf-8", { fatal: true }).decode(raw), label).parse();
}
function exact(value, fields, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Malformed(`${label} must be an object`);
  const missing = [...fields].filter((key) => !Object.hasOwn(value, key));
  const extra = Object.keys(value).filter((key) => !fields.has(key));
  if (missing.length || extra.length) throw new Malformed(`${label} fields mismatch; missing=${missing.sort()} extra=${extra.sort()}`);
  return value;
}
function hash(value, label) {
  if (typeof value !== "string" || !HASH_RE.test(value)) throw new Malformed(`${label} must be sha256:<64 lowercase hex>`);
  return value;
}
function amount(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) throw new Malformed(`${label} must be a non-negative safe integer`);
  return value;
}
function stringList(value, label) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item) || new Set(value).size !== value.length) throw new Malformed(`${label} must be unique non-empty strings`);
  return value;
}
function safePath(value, label) {
  if (typeof value !== "string" || !value || value.includes("\\") || value.includes("\0") || value.startsWith("/")
    || value.split("/").some((part) => ["", ".", ".."].includes(part) || part.includes(":"))) {
    throw new Malformed(`${label} must be a normalized relative POSIX path`);
  }
  return value;
}
function readBundle(rootValue) {
  const root = resolve(rootValue); const rootStat = lstatSync(root);
  if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) throw new Malformed("bundle root must be a non-symlink directory");
  const files = new Map(); let total = 0;
  function walk(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const absolute = resolve(directory, entry.name); const before = lstatSync(absolute);
      const local = relative(root, absolute).split(sep).join("/"); safePath(local, "bundle member");
      if (before.isSymbolicLink()) throw new Malformed(`bundle contains symlink ${local}`);
      if (before.isDirectory()) walk(absolute);
      else if (before.isFile()) {
        if (files.size >= MAX_FILES || before.size > MAX_FILE_BYTES) throw new Malformed("bundle exceeds file limits");
        let descriptor;
        try {
          descriptor = openSync(absolute, fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0));
          const opened = fstatSync(descriptor);
          if (!opened.isFile() || opened.dev !== before.dev || opened.ino !== before.ino) throw new Malformed(`${local} changed before open`);
          const raw = readFileSync(descriptor); const after = fstatSync(descriptor);
          if (raw.length !== opened.size || after.ino !== opened.ino || after.dev !== opened.dev) throw new Malformed(`${local} changed while read`);
          total += raw.length; if (total > MAX_TOTAL_BYTES) throw new Malformed("bundle exceeds total-byte limit");
          files.set(local, raw);
        } finally { if (descriptor !== undefined) closeSync(descriptor); }
      } else throw new Malformed(`bundle contains non-regular member ${local}`);
    }
  }
  walk(root); return files;
}
function canonical(value) {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "number") { if (!Number.isSafeInteger(value)) throw new Malformed("canonical number is unsafe"); return String(value); }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${canonical(key)}:${canonical(value[key])}`).join(",")}}`;
  throw new Malformed("unsupported canonical value");
}
function hashBytes(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }
function hashJson(value) { return hashBytes(Buffer.from(canonical(value), "utf8")); }
function isHash(value) { return typeof value === "string" && HASH_RE.test(value); }

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function base58(value) {
  let number = 0n;
  for (const character of value) { const index = B58.indexOf(character); if (index < 0) throw new Malformed("invalid did:key"); number = number * 58n + BigInt(index); }
  const octets = []; while (number > 0n) { octets.push(Number(number & 255n)); number >>= 8n; } octets.reverse();
  return Buffer.from([...new Array(value.length - value.replace(/^1+/, "").length).fill(0), ...octets]);
}
function didKeyBytes(value) {
  if (typeof value !== "string" || !value.startsWith("did:key:z")) throw new Malformed("issuer must be did:key");
  const raw = base58(value.slice(9));
  if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) throw new Malformed("did:key is not Ed25519");
  return raw.subarray(2);
}
function validateProof(proof, purpose, digest, issuer, schema = "0.4") {
  exact(proof, PROOF_FIELDS, `${purpose} proof`);
  if (proof.type !== "bulla/ed25519-2026" || proof.purpose !== purpose || proof.issuer !== issuer || proof.verificationMethod !== issuer) throw new VerificationFailure(`${purpose} proof identity mismatch`);
  const signature = Buffer.from(proof.proofValue, "base64");
  if (signature.length !== 64 || signature.toString("base64") !== proof.proofValue) throw new Malformed(`${purpose} proof is not canonical base64`);
  const key = createPublicKey({ key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), didKeyBytes(issuer)]), type: "spki", format: "der" });
  const message = Buffer.from(canonical({ context: "bulla-proof", schema, purpose, digest }), "utf8");
  if (!verifySignature(null, message, key, signature)) throw new VerificationFailure(`${purpose} proof signature failed`);
}
function envelope(receipt) {
  const result = { deed_schema: receipt.mandate.deed_schema || "0.2" };
  if (receipt.mandate.authority) result.authority = receipt.mandate.authority;
  if (receipt.mandate.bounds) result.bounds = receipt.mandate.bounds;
  if (Object.keys(receipt.remedy).length) result.recourse = receipt.remedy;
  if (receipt.retention.record) result.retention_class = receipt.retention.record;
  if (receipt.retention.disclosure) result.disclosure_class = receipt.retention.disclosure;
  return result;
}
function receiptHashes(receipt) {
  const preimage = {
    schema_version: "0.4", canonicalization: "bulla-jcs-int/1", kind: "action_receipt",
    action: receipt.action, diagnostic_ref: receipt.diagnostic_ref,
    evidence_refs: receipt.evidence_refs, anchor_ref: receipt.anchor_ref,
  };
  if (receipt.conventions.length) preimage.conventions = receipt.conventions;
  const content = hashJson(preimage);
  const event = hashJson({ content_hash: content, event_id: receipt.event_id, claimed_at: receipt.claimed_at });
  const recourseEnvelope = envelope(receipt);
  const authorization = hashJson({ event_hash: event, envelope_hash: hashJson(recourseEnvelope) });
  const attestation = hashJson({
    content_hash: content, signature: receipt.signature, event_hash: event,
    occurrence: receipt.occurrence, recourse_envelope: recourseEnvelope,
    authorization: receipt.authorization,
  });
  return { content, event, authorization, attestation, log_leaf: hashBytes(Buffer.concat([Buffer.from([0]), Buffer.from(attestation)])) };
}
function accepted(context, role, issuer) { return (context.accepted_issuers_by_role[role] ?? []).includes(issuer); }
function validateSubject(subject, action, role, label) {
  const fields = ACTION_SUBJECT_FIELDS[action];
  if (!fields) throw new Malformed(`${label} uses an unsupported action`);
  exact(subject, new Set(fields), `${label}.action.subject`);
  if (subject.profile !== PROFILE || subject.issuer_role !== role || typeof subject.transaction_id !== "string" || !subject.transaction_id) throw new Malformed(`${label} subject profile, role, or transaction is invalid`);
  for (const key of ["term_root", "comparison_group", "input_hash", "accepted_model_hash", "execution_report_hash", "output_hash", "capital_hash", "coverage_hash", "policy_hash", "witness_root", "settlement_hash", "clearing_core_hash"]) {
    if (Object.hasOwn(subject, key)) hash(subject[key], `${label}.${key}`);
  }
  for (const key of ["parent_ref", "authorization_ref", "coverage_ref"]) {
    if (!Object.hasOwn(subject, key)) continue;
    exact(subject[key], new Set(["event", "attestation"]), `${label}.${key}`);
    hash(subject[key].event, `${label}.${key}.event`); hash(subject[key].attestation, `${label}.${key}.attestation`);
  }
  if (Object.hasOwn(subject, "price")) {
    exact(subject.price, new Set(["amount", "unit"]), `${label}.price`);
    amount(subject.price.amount, `${label}.price.amount`);
    if (typeof subject.price.unit !== "string" || !subject.price.unit) throw new Malformed(`${label}.price.unit must be non-empty`);
  }
  if (Object.hasOwn(subject, "amount")) amount(subject.amount, `${label}.amount`);
  if (Object.hasOwn(subject, "exposure")) amount(subject.exposure, `${label}.exposure`);
  for (const key of ["denominator_count", "receipted_count"]) if (Object.hasOwn(subject, key)) amount(subject[key], `${label}.${key}`);
  if (Object.hasOwn(subject, "unmet_requirements") && (!Array.isArray(subject.unmet_requirements) || new Set(subject.unmet_requirements).size !== subject.unmet_requirements.length || subject.unmet_requirements.some((value) => typeof value !== "string" || !value))) throw new Malformed(`${label}.unmet_requirements must be unique non-empty strings`);
  if (Object.hasOwn(subject, "provider_process_claim") && !["self_asserted", "absent"].includes(subject.provider_process_claim)) throw new Malformed(`${label}.provider_process_claim is outside the closed vocabulary`);
}
function validateReceipt(raw, label, context, expectedAction, expectedRole) {
  const receipt = exact(loadJson(raw, label), RECEIPT_FIELDS, label);
  if (receipt.schema_version !== "0.4" || receipt.canonicalization !== "bulla-jcs-int/1" || receipt.kind !== "action_receipt") throw new Malformed(`${label} is not ActionReceipt v0.4`);
  exact(receipt.action, new Set(["type", "subject"]), `${label}.action`);
  if (receipt.action.type !== expectedAction || ACTION_ROLES[expectedAction] !== expectedRole) throw new Malformed(`${label} action or role mismatch`);
  validateSubject(receipt.action.subject, expectedAction, expectedRole, label);
  const issuer = receipt.signature?.issuer;
  if (!accepted(context, expectedRole, issuer)) throw new VerificationFailure(`${label} issuer is not accepted for ${expectedRole}`);
  if ((receipt.mandate?.deed_schema ?? "0.2") !== "0.2" || receipt.mandate?.authority?.principal !== issuer || receipt.mandate.authority.delegation?.length !== 0) throw new VerificationFailure(`${label} authority is not direct and signer-bound`);
  const hashes = receiptHashes(receipt);
  exact(receipt.hashes, new Set(["content", "event", "attestation", "log_leaf"]), `${label}.hashes`);
  for (const key of ["content", "event", "attestation", "log_leaf"]) if (receipt.hashes[key] !== hashes[key]) throw new VerificationFailure(`${label} ${key} hash mismatch`);
  validateProof(receipt.signature, "content", hashes.content, issuer);
  validateProof(receipt.occurrence, "occurrence", hashes.event, issuer);
  validateProof(receipt.authorization, "authorization", hashes.authorization, issuer);
  return receipt;
}
function runModel(model, inputs) {
  exact(model, new Set(["profile", "input_width", "hidden_weights", "hidden_bias", "output_weights", "output_bias", "labels"]), "model");
  if (
    model.profile !== "bulla.int8-mlp/1"
    || model.input_width !== 8
    || !Array.isArray(inputs)
    || inputs.length !== 8
    || !Array.isArray(model.hidden_weights)
    || !model.hidden_weights.length
    || !Array.isArray(model.hidden_bias)
    || model.hidden_weights.length !== model.hidden_bias.length
    || model.hidden_weights.some((row) => !Array.isArray(row) || row.length !== 8)
    || !Array.isArray(model.output_weights)
    || model.output_weights.length !== 2
    || model.output_weights.some((row) => !Array.isArray(row) || row.length !== model.hidden_weights.length)
    || !Array.isArray(model.output_bias)
    || model.output_bias.length !== 2
    || canonical(model.labels) !== canonical(["PRIMARY", "BACKUP"])
  ) throw new Malformed("deterministic model dimensions are invalid");
  const numbers = [...inputs, ...model.hidden_bias, ...model.output_bias, ...model.hidden_weights.flat(), ...model.output_weights.flat()];
  if (numbers.some((value) => !Number.isSafeInteger(value) || Math.abs(value) > 1_000_000)) throw new Malformed("deterministic model uses unsupported integer values");
  const safeDot = (row, values, bias) => {
    let total = 0n;
    for (let index = 0; index < row.length; index += 1) {
      const product = BigInt(row[index]) * BigInt(values[index]);
      if (product > BigInt(Number.MAX_SAFE_INTEGER) || product < BigInt(Number.MIN_SAFE_INTEGER)) throw new Malformed("model multiplication exceeds safe integers");
      total += product;
      if (total > BigInt(Number.MAX_SAFE_INTEGER) || total < BigInt(Number.MIN_SAFE_INTEGER)) throw new Malformed("model accumulation exceeds safe integers");
    }
    total += BigInt(bias);
    if (total > BigInt(Number.MAX_SAFE_INTEGER) || total < BigInt(Number.MIN_SAFE_INTEGER)) throw new Malformed("model biased sum exceeds safe integers");
    return Number(total);
  };
  const hidden = model.hidden_weights.map((row, index) => Math.max(0, safeDot(row, inputs, model.hidden_bias[index])));
  const scores = model.output_weights.map((row, index) => safeDot(row, hidden, model.output_bias[index]));
  const selected = scores[1] >= scores[0] ? 1 : 0; const output = model.labels[selected];
  return [output, { hidden, scores, selected_index: selected, output }];
}
function nodeHash(left, right) { return createHash("sha256").update(Buffer.concat([Buffer.from([1]), left, right])).digest(); }
function verifyInclusion(record, expectedLeaf, trustedRoot) {
  if (record.leaf !== expectedLeaf || record.root !== trustedRoot || !Number.isSafeInteger(record.index) || !Number.isSafeInteger(record.tree_size) || record.index < 0 || record.index >= record.tree_size) return false;
  let fn = record.index; let sn = record.tree_size - 1; let computed = Buffer.from(record.leaf.slice(7), "hex");
  for (const item of record.proof) {
    if (sn === 0) return false;
    const proof = Buffer.from(item.replace(/^sha256:/, ""), "hex");
    if ((fn & 1) || fn === sn) {
      computed = nodeHash(proof, computed);
      if (!(fn & 1)) while (!(fn & 1) && fn !== 0) { fn >>= 1; sn >>= 1; }
    } else computed = nodeHash(computed, proof);
    fn >>= 1; sn >>= 1;
  }
  return sn === 0 && `sha256:${computed.toString("hex")}` === trustedRoot;
}
function deedLeaf(receipt) {
  const deed = canonical({
    issuer: receipt.signature.issuer,
    content_hash: receipt.hashes.content,
    attestation_hash: receipt.hashes.attestation,
  });
  return hashBytes(Buffer.concat([Buffer.from([0]), Buffer.from(deed)]));
}
function validateCheckpoint(value, context) {
  const fields = new Set(["schema_version", "profile", "log_id", "operator", "tree_size", "root", "previous_checkpoint_hash", "ordering_domain", "position", "issued_at", "anchor_evidence", "checkpoint_hash", "proof"]);
  exact(value, fields, "witness checkpoint");
  const unsigned = Object.fromEntries([...fields].filter((key) => !["checkpoint_hash", "proof"].includes(key)).map((key) => [key, value[key]]));
  if (value.schema_version !== "0.1-draft" || value.profile !== "bulla.witness-checkpoint/0.1-draft" || value.position !== value.tree_size || value.checkpoint_hash !== hashJson(unsigned)) throw new VerificationFailure("witness checkpoint binding failed");
  if (value.ordering_domain !== hashJson({ profile: value.profile, log_id: value.log_id, operator: value.operator })) throw new VerificationFailure("witness ordering domain failed");
  if (!accepted(context, "witness", value.operator) || !context.trusted_witness_roots.includes(value.root)) throw new VerificationFailure("witness root or operator is not accepted");
  validateProof(value.proof, "witness-checkpoint", value.checkpoint_hash, value.operator, "0.3");
}
function componentHashes(core) {
  const names = [...COMPONENT_NAMES];
  return Object.fromEntries(names.map((name) => [name, hashJson(core.artifacts.filter((item) => item.path.split("/", 1)[0] === name))]));
}

function verify(rootValue, contextPath) {
  const files = readBundle(rootValue); const context = loadJson(readFileSync(contextPath), "verification context");
  exact(context, new Set(["accepted_issuers_by_role", "accepted_execution_adapters", "accepted_rail_adapters", "accepted_policy_hash", "accepted_evidence_requirements", "trusted_witness_roots", "team_controlled_roles"]), "verification context");
  hash(context.accepted_policy_hash, "accepted policy hash");
  exact(context.accepted_evidence_requirements, new Set(Object.keys(REQUIRED_EVIDENCE)), "accepted evidence requirements");
  if (canonical(context.accepted_evidence_requirements) !== canonical(REQUIRED_EVIDENCE)) throw new Malformed("accepted evidence requirements contain unsupported values");
  for (const key of ["accepted_execution_adapters", "accepted_rail_adapters", "trusted_witness_roots", "team_controlled_roles"]) stringList(context[key], key);
  if (!context.accepted_execution_adapters.length || !context.accepted_rail_adapters.length || !context.trusted_witness_roots.length) throw new Malformed("accepted adapters and trusted witness roots must be non-empty");
  if (context.team_controlled_roles.some((role) => !CONTEXT_ROLE_NAMES.has(role))) throw new Malformed("team-controlled roles are outside the closed context role set");
  for (const root of context.trusted_witness_roots) hash(root, "trusted witness root");
  if (context.accepted_issuers_by_role === null || typeof context.accepted_issuers_by_role !== "object" || Array.isArray(context.accepted_issuers_by_role)) throw new Malformed("accepted issuers must be an object");
  if (canonical(Object.keys(context.accepted_issuers_by_role).sort()) !== canonical([...CONTEXT_ROLE_NAMES].sort())) throw new Malformed("accepted issuers must contain the exact closed role set");
  const acceptedIssuerSet = new Set();
  for (const [role, issuers] of Object.entries(context.accepted_issuers_by_role)) {
    stringList(issuers, `accepted issuers for ${role}`);
    if (!issuers.length) throw new Malformed(`accepted issuers for ${role} must be non-empty`);
    for (const issuer of issuers) {
      if (acceptedIssuerSet.has(issuer)) throw new Malformed("one accepted issuer cannot occupy multiple roles");
      acceptedIssuerSet.add(issuer);
    }
  }
  const core = loadJson(files.get(CORE), CORE);
  exact(core, new Set(["profile", "transaction_id", "revision", "provider_kind", "comparison_group", "role_issuers", "term_root", "ordered_receipts", "artifacts"]), "clearing core");
  if (core.profile !== PROFILE || core.revision !== 1 || !["OPAQUE", "REPRODUCIBLE"].includes(core.provider_kind)) throw new Malformed("unsupported core profile");
  if (!Array.isArray(core.ordered_receipts) || !core.ordered_receipts.length || !Array.isArray(core.artifacts) || !core.artifacts.length) throw new Malformed("core manifests must be non-empty arrays");
  if (core.role_issuers === null || typeof core.role_issuers !== "object" || Array.isArray(core.role_issuers) || Object.values(core.role_issuers).some((value) => typeof value !== "string") || new Set(Object.values(core.role_issuers)).size !== Object.keys(core.role_issuers).length || canonical(Object.keys(core.role_issuers).sort()) !== canonical([...DECLARED_ROLE_NAMES].sort())) throw new Malformed("role issuers are malformed, reused, or incomplete");
  hash(core.comparison_group, "core comparison group"); hash(core.term_root, "core term root");
  const declared = new Set([CORE, PUBLISH, ...core.artifacts.map((item) => safePath(item.path, "artifact path"))]);
  if (declared.size !== core.artifacts.length + 2 || declared.size !== files.size || [...declared].some((path) => !files.has(path))) throw new Malformed("bundle membership mismatch");
  for (const item of core.artifacts) {
    exact(item, new Set(["path", "media_type", "byte_length", "sha256"]), "artifact");
    if (!COMPONENT_NAMES.has(item.path.split("/", 1)[0])) throw new Malformed(`${item.path} is outside the closed bundle layout`);
    const expectedMediaType = item.path.endsWith(".json") ? "application/json" : "application/octet-stream";
    if (item.media_type !== expectedMediaType) throw new Malformed(`${item.path} has the wrong media type`);
    const raw = files.get(item.path);
    if (raw.length !== item.byte_length || hashBytes(raw) !== item.sha256) throw new VerificationFailure(`artifact commitment mismatch ${item.path}`);
  }
  const publish = validateReceipt(files.get(PUBLISH), PUBLISH, context, "inference.clearing.publish", "publisher");
  const expectedPublish = { profile: PROFILE, issuer_role: "publisher", transaction_id: core.transaction_id, clearing_core_hash: hashJson(core), component_hashes: componentHashes(core) };
  if (publish.signature.issuer !== core.role_issuers.publisher || canonical(publish.action.subject) !== canonical(expectedPublish)) throw new VerificationFailure("publish receipt does not bind core, components, and declared publisher");

  const receipts = core.ordered_receipts.map((item) => {
    exact(item, new Set(["action_type", "role", "path", "event", "attestation"]), "ordered receipt");
    const receipt = validateReceipt(files.get(item.path), item.path, context, item.action_type, item.role);
    const declaredRole = item.role === "provider" ? (core.provider_kind === "OPAQUE" ? "provider_opaque" : "provider_reproducible") : item.role;
    if (receipt.signature.issuer !== core.role_issuers[declaredRole]) throw new VerificationFailure(`receipt signer differs from declared ${declaredRole}`);
    if (receipt.hashes.event !== item.event || receipt.hashes.attestation !== item.attestation) throw new VerificationFailure(`ordered receipt hash mismatch ${item.path}`);
    if (receipt.action.subject.transaction_id !== core.transaction_id) throw new VerificationFailure(`cross-transaction receipt ${item.path}`);
    return [item, receipt, receipt.action.subject];
  });
  for (const key of ["path", "event", "attestation"]) if (new Set(core.ordered_receipts.map((item) => item[key])).size !== core.ordered_receipts.length) throw new Malformed(`ordered receipt ${key} values are not unique`);
  const sequence = receipts.map(([item]) => item.action_type);
  if (![BASE_SEQUENCE, ELIGIBLE_SEQUENCE, GUARANTEE_SEQUENCE, [...GUARANTEE_SEQUENCE, "assurance.settlement.authorize", "rail.settlement.report"]].some((allowed) => canonical(sequence) === canonical(allowed))) throw new Malformed("unsupported receipt sequence");
  receipts.forEach(([, receipt, subject], index) => {
    if (index === 0) { if (Object.hasOwn(subject, "parent_ref")) throw new Malformed("order carries parent_ref"); }
    else {
      const previous = receipts[index - 1][1];
      if (canonical(subject.parent_ref) !== canonical({ event: previous.hashes.event, attestation: previous.hashes.attestation })) throw new VerificationFailure("receipt lineage mismatch");
    }
    if (subject.term_root !== core.term_root || subject.comparison_group !== core.comparison_group) throw new VerificationFailure("receipt term or comparison-group mismatch");
  });

  const term = loadJson(files.get("terms/term-document.json"), "term document");
  exact(term, new Set(["profile", "transaction_id", "comparison_group", "input_hash", "expected_model_hash", "output_schema", "required_evidence", "policy_hash", "price", "capital_requirement", "coverage_anchor", "witness_policy", "recourse"]), "term document");
  for (const key of ["comparison_group", "input_hash", "expected_model_hash", "policy_hash"]) hash(term[key], `term.${key}`);
  for (const key of ["price", "capital_requirement"]) {
    exact(term[key], new Set(["amount", "unit"]), `term.${key}`); amount(term[key].amount, `term.${key}.amount`);
    if (term[key].unit !== "sat") throw new Malformed(`term.${key}.unit must be sat`);
  }
  exact(term.output_schema, new Set(["type", "values"]), "term.output_schema");
  if (term.output_schema.type !== "enum" || canonical(term.output_schema.values) !== canonical(["PRIMARY", "BACKUP"])) throw new Malformed("term output schema is unsupported");
  exact(term.required_evidence, new Set(Object.keys(REQUIRED_EVIDENCE)), "term.required_evidence");
  exact(term.recourse, new Set(["challenge_window", "forum"]), "term.recourse");
  if (term.profile !== PROFILE || canonical(term.required_evidence) !== canonical(context.accepted_evidence_requirements) || term.witness_policy !== "accepted-signed-checkpoint-and-inclusion" || typeof term.coverage_anchor !== "string" || !term.coverage_anchor || Object.values(term.recourse).some((value) => typeof value !== "string" || !value) || hashJson(term) !== core.term_root || term.transaction_id !== core.transaction_id || term.comparison_group !== core.comparison_group || term.policy_hash !== context.accepted_policy_hash) throw new VerificationFailure("term binding failed");
  const execution = loadJson(files.get("execution/report.json"), "execution report");
  const outputBytes = files.get("execution/output.bin");
  if (!Buffer.isBuffer(outputBytes)) throw new Malformed("execution output bytes are missing");
  exact(execution, new Set(["profile", "adapter", "model_id", "model_hash", "model_available", "input", "input_hash", "output", "output_hash", "trace", "trace_hash", "provider_process_claim"]), "execution report");
  for (const key of ["model_hash", "input_hash", "output_hash", "trace_hash"]) hash(execution[key], `execution.${key}`);
  if (execution.profile !== EXECUTION_PROFILE || typeof execution.adapter !== "string" || !execution.adapter || typeof execution.model_id !== "string" || !execution.model_id || typeof execution.model_available !== "boolean" || !Array.isArray(execution.input) || execution.input.some((value) => !Number.isSafeInteger(value)) || !["PRIMARY", "BACKUP"].includes(execution.output) || !["self_asserted", "absent"].includes(execution.provider_process_claim)) throw new Malformed("execution structure failed");
  if (hashJson(execution.input) !== execution.input_hash || execution.input_hash !== term.input_hash || hashJson(execution.trace) !== execution.trace_hash || !outputBytes.equals(Buffer.from(`${execution.output}\n`)) || hashBytes(outputBytes) !== execution.output_hash) throw new VerificationFailure("execution binding failed");
  const modelPresent = files.has("execution/model.json");
  if (execution.model_available !== modelPresent) throw new Malformed("execution model availability differs from retained artifacts");
  const expectedAdapter = core.provider_kind === "OPAQUE" ? "provider-assertion/1" : "bulla.int8-mlp-recompute/1";
  if (execution.adapter !== expectedAdapter) throw new Malformed("execution adapter differs from provider kind");
  let traceBinding = "NOT_APPLICABLE"; let modelAvailability = "UNAVAILABLE";
  let modelBinding = "UNAVAILABLE"; let relationReproduction = "UNAVAILABLE";
  if (execution.adapter === "bulla.int8-mlp-recompute/1") {
    if (!modelPresent) throw new Malformed("recomputation adapter requires execution/model.json");
    const model = loadJson(files.get("execution/model.json"), "execution model");
    const computedModelHash = hashJson(model);
    if (computedModelHash !== execution.model_hash) throw new VerificationFailure("model hash mismatch");
    const [output, trace] = runModel(model, execution.input);
    traceBinding = output === execution.output && hashJson(trace) === execution.trace_hash && canonical(trace) === canonical(execution.trace) ? "VERIFIED" : "FAILED";
    modelAvailability = "AVAILABLE";
    modelBinding = computedModelHash === execution.model_hash && execution.model_hash === term.expected_model_hash ? "TERM_BOUND" : "UNBOUND";
    relationReproduction = modelBinding === "TERM_BOUND" && traceBinding === "VERIFIED" ? "REPRODUCED" : "FAILED";
  } else if (execution.adapter !== "provider-assertion/1" || modelPresent) throw new Malformed("unsupported execution adapter or provider assertion");
  const adapterAccepted = context.accepted_execution_adapters.includes(execution.adapter);
  const acceptSubject = receipts.find(([item]) => item.action_type === "inference.accept")[2];
  const deliverySubject = receipts.find(([item]) => item.action_type === "inference.delivery")[2];
  const routeSubject = receipts.find(([item]) => item.action_type === "inference.route")[2];
  const orderSubject = receipts.find(([item]) => item.action_type === "inference.order")[2];
  if (
    orderSubject.input_hash !== term.input_hash
    || canonical(orderSubject.price) !== canonical(term.price)
    || routeSubject.provider_kind !== core.provider_kind
    || acceptSubject.accepted_model_hash !== term.expected_model_hash
    || acceptSubject.execution_report_hash !== hashJson(execution)
    || acceptSubject.provider_process_claim !== execution.provider_process_claim
    || deliverySubject.output_hash !== execution.output_hash
    || deliverySubject.receiver_anchor !== term.coverage_anchor
  ) throw new VerificationFailure("order, route, acceptance, or delivery evidence binding failed");

  const coverage = loadJson(files.get("receiver/coverage.json"), "receiver coverage");
  exact(coverage, new Set(["profile", "transaction_id", "anchor_id", "provenance", "denominator_ids", "receipted_ids"]), "receiver coverage");
  stringList(coverage.denominator_ids, "coverage denominator ids"); stringList(coverage.receipted_ids, "coverage receipted ids");
  if (coverage.profile !== COVERAGE_PROFILE || coverage.transaction_id !== core.transaction_id || coverage.anchor_id !== term.coverage_anchor || coverage.provenance !== "PATH_SEPARATE_TEAM_CONTROLLED") throw new Malformed("coverage structure failed");
  const phantom = coverage.receipted_ids.filter((item) => !coverage.denominator_ids.includes(item));
  if (phantom.length) throw new Malformed("coverage contains phantom receipts");
  const uncovered = coverage.denominator_ids.filter((item) => !coverage.receipted_ids.includes(item));
  const coverageStatus = uncovered.length ? "UNCOVERED" : "COVERED";
  if (!coverage.receipted_ids.includes(deliverySubject.effect_id)) throw new Malformed("delivery is absent from receipted effects");
  const coverageEntry = receipts.find(([item]) => item.action_type === "inference.coverage.checkpoint");
  if (!coverageEntry || coverageEntry[2].coverage_hash !== hashJson(coverage) || coverageEntry[2].anchor_id !== coverage.anchor_id || coverageEntry[2].denominator_count !== coverage.denominator_ids.length || coverageEntry[2].receipted_count !== coverage.receipted_ids.length) throw new VerificationFailure("receiver coverage checkpoint does not bind the exact denominator");

  const checkpoint = loadJson(files.get("witness/decision-checkpoint.json"), "witness checkpoint"); validateCheckpoint(checkpoint, context);
  if (checkpoint.operator !== core.role_issuers.witness) throw new VerificationFailure("witness checkpoint operator differs from the declared role");
  const inclusions = loadJson(files.get("witness/decision-inclusions.json"), "witness inclusions");
  if (!Array.isArray(inclusions) || inclusions.some((item) => item === null || typeof item !== "object" || Array.isArray(item)) || new Set(inclusions.map((item) => item.attestation)).size !== inclusions.length) throw new Malformed("decision inclusions are malformed or duplicated");
  const byAttestation = new Map(inclusions.map((item) => [item.attestation, item]));
  const preRelianceReceipts = receipts.filter(([item]) => !["bulla.rely", "assurance.settlement.authorize", "rail.settlement.report"].includes(item.action_type));
  if (checkpoint.tree_size !== preRelianceReceipts.length) throw new VerificationFailure("decision checkpoint tree size differs from pre-reliance receipt count");
  const decisionAttestations = new Set(preRelianceReceipts.map(([, receipt]) => receipt.hashes.attestation));
  if (byAttestation.size !== decisionAttestations.size || [...byAttestation.keys()].some((item) => !decisionAttestations.has(item))) throw new VerificationFailure("decision inclusions do not exactly cover pre-reliance receipts");
  for (const [, receipt] of preRelianceReceipts) {
    const inclusion = byAttestation.get(receipt.hashes.attestation);
    if (!inclusion || inclusion.tree_size !== checkpoint.tree_size || !verifyInclusion(inclusion, deedLeaf(receipt), checkpoint.root)) throw new VerificationFailure("witness inclusion failed");
  }
  const finalCheckpoint = loadJson(files.get("witness/final-checkpoint.json"), "final witness checkpoint");
  validateCheckpoint(finalCheckpoint, context);
  if (
    finalCheckpoint.operator !== checkpoint.operator
    || finalCheckpoint.previous_checkpoint_hash !== checkpoint.checkpoint_hash
    || finalCheckpoint.tree_size !== receipts.length
  ) throw new VerificationFailure("final witness checkpoint does not extend the decision checkpoint");
  const finalInclusions = loadJson(files.get("witness/final-inclusions.json"), "final witness inclusions");
  if (!Array.isArray(finalInclusions) || finalInclusions.some((item) => item === null || typeof item !== "object" || Array.isArray(item)) || new Set(finalInclusions.map((item) => item.attestation)).size !== finalInclusions.length) throw new Malformed("final inclusions are malformed or duplicated");
  const finalByAttestation = new Map(finalInclusions.map((item) => [item.attestation, item]));
  const finalAttestations = new Set(receipts.map(([, receipt]) => receipt.hashes.attestation));
  if (finalByAttestation.size !== finalAttestations.size || [...finalByAttestation.keys()].some((item) => !finalAttestations.has(item))) throw new VerificationFailure("final inclusions do not exactly cover retained receipts");
  for (const [, receipt] of receipts) {
    const inclusion = finalByAttestation.get(receipt.hashes.attestation);
    if (!inclusion || inclusion.tree_size !== finalCheckpoint.tree_size || !verifyInclusion(inclusion, deedLeaf(receipt), finalCheckpoint.root)) throw new VerificationFailure("final witness inclusion failed");
  }
  const capital = loadJson(files.get("assurance/capital.json"), "capital allocation");
  exact(capital, new Set(["profile", "transaction_id", "binding_id", "unit", "locked_amount", "allocated_amount", "external_encumbrance", "rail_adapter", "rail_evidence_hash"]), "capital allocation");
  amount(capital.locked_amount, "capital locked amount"); amount(capital.allocated_amount, "capital allocated amount");
  if (capital.external_encumbrance !== "NOT_COMPUTED") throw new Malformed("external encumbrance must remain NOT_COMPUTED");
  const capitalSubject = receipts.find(([item]) => item.action_type === "assurance.collateral.bind")[2];
  if (capitalSubject.capital_hash !== hashJson(capital)) throw new VerificationFailure("capital receipt does not bind capital allocation");
  const railEvidence = loadJson(files.get("settlement/rail-evidence.json"), "rail evidence");
  exact(railEvidence, new Set(["profile", "binding_id", "locked_amount", "unit", "reported_execution", "synthetic"]), "rail evidence");
  amount(railEvidence.locked_amount, "rail evidence locked amount");
  if (railEvidence.profile !== "bulla.fixture-escrow-evidence/1" || typeof railEvidence.binding_id !== "string" || !railEvidence.binding_id || railEvidence.unit !== "sat" || !["NOT_ATTEMPTED", "EXECUTED", "FAILED"].includes(railEvidence.reported_execution) || railEvidence.synthetic !== true) throw new Malformed("rail evidence is outside the closed fixture profile");
  if (capital.rail_evidence_hash !== hashJson(railEvidence)) throw new VerificationFailure("capital allocation does not bind retained rail evidence");
  let capitalStatus = "ALLOCATION_ADEQUATE";
  if (capital.profile !== CAPITAL_PROFILE || capital.transaction_id !== core.transaction_id || capital.unit !== term.capital_requirement.unit) capitalStatus = "UNIT_MISMATCH";
  else if (capital.allocated_amount > capital.locked_amount || capital.allocated_amount < term.capital_requirement.amount) capitalStatus = "ALLOCATION_SHORTFALL";
  else if (!context.accepted_rail_adapters.includes(capital.rail_adapter)) capitalStatus = "UNACCEPTED_RAIL";

  const negatives = [];
  if ("VERIFIED" !== context.accepted_evidence_requirements.output_binding) negatives.push("required output binding was not established");
  if (modelBinding !== context.accepted_evidence_requirements.model_binding) negatives.push("required term-bound model was not established");
  if (relationReproduction !== context.accepted_evidence_requirements.relation_reproduction) negatives.push("required computational relation was not reproduced");
  if (context.accepted_evidence_requirements.provider_execution_occurrence !== "NOT_REQUIRED") negatives.push("historical provider execution is not established");
  if (!adapterAccepted) negatives.push("execution adapter was not accepted");
  if (coverageStatus !== "COVERED") negatives.push("required receiver coverage was not complete");
  if (capitalStatus !== "ALLOCATION_ADEQUATE") negatives.push("required capital allocation was not established");
  const reliance = negatives.length ? "REFUSE" : "RELY"; const payment = negatives.length ? "INELIGIBLE" : "ELIGIBLE";
  const rely = receipts.find(([item]) => item.action_type === "bulla.rely")[2];
  if (rely.policy_hash !== context.accepted_policy_hash || rely.decision !== reliance || rely.witness_root !== checkpoint.root || rely.named_consequence !== "RELEASE_PAYMENT" || rely.coverage_hash !== hashJson(coverage) || canonical(rely.coverage_ref) !== canonical({ event: coverageEntry[1].hashes.event, attestation: coverageEntry[1].hashes.attestation }) || canonical(rely.unmet_requirements) !== canonical(negatives)) throw new VerificationFailure("reliance receipt differs from recomputation");
  const authorize = receipts.filter(([item]) => item.action_type === "assurance.settlement.authorize");
  const rails = receipts.filter(([item]) => item.action_type === "rail.settlement.report");
  const authorizationRef = authorize.length === 1 ? { event: authorize[0][1].hashes.event, attestation: authorize[0][1].hashes.attestation } : null;
  const settlement = loadJson(files.get("settlement/report.json"), "settlement report");
  exact(settlement, new Set(["profile", "transaction_id", "rail_adapter", "status", "amount", "unit", "synthetic", "authorization_ref", "rail_evidence_hash"]), "settlement report");
  amount(settlement.amount, "settlement amount");
  const authorizationMatches = payment === "ELIGIBLE" && authorize.length === 1 && authorize[0][2].eligibility === "ELIGIBLE" && authorize[0][2].amount === term.price.amount && authorize[0][2].unit === term.price.unit && authorize[0][2].rail_adapter === settlement.rail_adapter && typeof authorize[0][2].destination === "string" && !!authorize[0][2].destination;
  const settlementAuthorization = authorize.length === 0 ? "NOT_ISSUED" : (authorizationMatches ? "AUTHORIZED" : "INVALID");
  const railMatches = settlementAuthorization === "AUTHORIZED" && rails.length === 1 && rails[0][2].status === "EXECUTED" && canonical(rails[0][2].authorization_ref) === canonical(authorizationRef) && rails[0][2].amount === term.price.amount && rails[0][2].unit === term.price.unit && rails[0][2].rail_adapter === settlement.rail_adapter && rails[0][2].settlement_hash === hashJson(settlement);
  const settlementExecution = rails.length === 0 ? "NOT_ATTEMPTED" : (railMatches ? "EXECUTED" : "INVALID");
  if (settlementAuthorization === "INVALID" || settlementExecution === "INVALID") throw new VerificationFailure("settlement state is unsupported by the computed eligibility and authorization");
  if (settlement.profile !== SETTLEMENT_PROFILE || settlement.transaction_id !== core.transaction_id || settlement.status !== settlementExecution || settlement.amount !== term.price.amount || settlement.unit !== term.price.unit || settlement.synthetic !== true || settlement.rail_evidence_hash !== hashJson(railEvidence) || canonical(settlement.authorization_ref) !== canonical(authorizationRef) || !context.accepted_rail_adapters.includes(settlement.rail_adapter)) throw new VerificationFailure("settlement report mismatch");

  return {
    profile: PROFILE, transaction_id: core.transaction_id, provider_kind: core.provider_kind,
    output: execution.output, bundle_integrity: "VERIFIED", receipt_integrity: "VERIFIED",
    signature_status: "VERIFIED", issuer_role_status: "ACCEPTED", term_binding: "VERIFIED",
    lineage_integrity: "VERIFIED", occurrence_binding: "VERIFIED",
    relation_evidence: {
      adapter: execution.adapter, adapter_acceptance: adapterAccepted ? "ACCEPTED" : "UNACCEPTED",
      model_availability: modelAvailability, input_binding: "VERIFIED", output_binding: "VERIFIED",
      trace_binding: traceBinding, model_binding: modelBinding,
      relation_reproduction: relationReproduction,
      provider_process_claim: execution.provider_process_claim.toUpperCase(),
      provider_execution_occurrence: "NOT_ESTABLISHED", reasons: [],
    },
    receiver_coverage: coverageStatus, receiver_total: coverage.denominator_ids.length,
    receiver_receipted: coverage.receipted_ids.length, uncovered_effects: uncovered,
    witness_inclusion: "VERIFIED", witness_root_trust: "TRUSTED", capital_allocation: capitalStatus,
    recourse_conveyance: receipts.some(([item]) => item.action_type === "assurance.guarantee.issue") ? "GUARANTEE_NAMED" : "NAMED", recourse_reachability: "NOT_COMPUTED",
    reliance_decision: reliance, payment_eligibility: payment,
    settlement_authorization: settlementAuthorization, settlement_execution: settlementExecution,
    denominator_completeness: "NOT_ESTABLISHED", custody: "NOT_COMPUTED",
    collectibility: "NOT_COMPUTED", worldly_truth: "NOT_COMPUTED",
    suppressed_conclusions: [], errors: [], warnings: [
      "coverage is relative to the supplied receiver record",
      "team-operated role separation does not establish organizational independence",
    ], exit_code: 0,
  };
}

function main() {
  const args = process.argv.slice(2); const contextIndex = args.indexOf("--context");
  if (args.length !== 3 || contextIndex < 0 || contextIndex === 2) {
    process.stderr.write("usage: node check.mjs BUNDLE --context CONTEXT\n"); return 2;
  }
  const bundle = args[contextIndex === 0 ? 2 : 0]; const context = args[contextIndex + 1];
  try { process.stdout.write(`${JSON.stringify(verify(bundle, context), null, 2)}\n`); return 0; }
  catch (error) {
    const status = error instanceof VerificationFailure ? 1 : 2;
    process.stdout.write(`${JSON.stringify({ error: error.message, exit_code: status })}\n`); return status;
  }
}

process.exitCode = main();
