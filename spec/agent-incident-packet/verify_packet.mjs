#!/usr/bin/env node
/**
 * No-Bulla Node verifier for glyph.agent-incident-packet/0.1-draft.
 *
 * This is separately implemented from the Python/Bulla verifier. It uses only
 * Node built-ins, preserves the packet's byte boundary, verifies v0.4 did:key
 * Ed25519 proofs, and recomputes per-anchor coverage.
 */

import {
  createHash,
  createPublicKey,
  verify as verifySignature,
} from "node:crypto";
import {
  closeSync,
  constants as fsConstants,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { relative, resolve, sep } from "node:path";

const PROFILE = "glyph.agent-incident-packet/0.1-draft";
const CORE_FILE = "packet-core.json";
const PUBLISH_FILE = "publish-receipt.json";
const MAX_FILE_BYTES = 2_097_152;
const MAX_TOTAL_BYTES = 16_777_216;
const MAX_FILES = 512;
const MAX_DEPTH = 40;
const MAX_NODES = 100_000;
const MAX_STRING_BYTES = 524_288;
const HASH_RE = /^sha256:[0-9a-f]{64}$/;
const COMPONENTS = [
  "roles", "timeline", "artifacts", "denominators", "coverage",
  "redactions", "statements", "witnesses", "corrections",
];
const CORE_FIELDS = new Set([
  "profile", "packet_id", "incident_id", "revision", "classification",
  "supersedes_core_hash", ...COMPONENTS,
]);
const RECEIPT_FIELDS = new Set([
  "schema_version", "canonicalization", "kind", "action", "diagnostic_ref",
  "evidence_refs", "anchor_ref", "mandate", "remedy", "retention", "stake",
  "conventions", "signature", "occurrence", "authorization", "event_id",
  "claimed_at", "producer", "hashes",
]);
const PROOF_FIELDS = new Set([
  "type", "purpose", "issuer", "verificationMethod", "proofValue",
]);
const OBSERVATION_FIELDS = new Set([
  "anchor_id", "observation_id", "run_id", "protocol", "operation_ref",
  "phase", "evidence_hash",
]);
const ACTION_ALLOWED_ROLES = {
  "eval.run.authorize": new Set(["evaluation_authority"]),
  "capability.decide": new Set(["gateway", "boundary"]),
  "capability.observe": new Set(["gateway", "boundary", "target"]),
  "trajectory.decide": new Set(["trajectory_monitor"]),
  "incident.statement": null,
  "incident.handoff": new Set(["incident_commander"]),
  "incident.packet.publish": new Set(["publisher"]),
  "incident.correct": new Set(["correction_authority"]),
};
const PROTOCOLS = new Set(["http", "mcp"]);
const DECISIONS = new Set(["PERMIT", "REFUSE"]);
const OBSERVATION_CLASSES = new Set([
  "EFFECT_OBSERVED", "NO_EFFECT_OBSERVED", "OUTCOME_UNKNOWN",
]);
const TRAJECTORY_DECISIONS = new Set([
  "PROCEED", "ESCALATE", "REFUSE_AND_FREEZE",
]);
const EPISTEMIC_STATUSES = new Set([
  "observed", "inferred", "counterparty_confirmed", "unresolved",
]);
const GROUNDING_CLASSES = new Set([
  "self_asserted", "counterparty_signed", "third_party_anchored",
  "execution_verified",
]);
const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const UTC_INSTANT_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$/;
const ACTION_FIELDS = {
  "eval.run.authorize": new Set([
    "profile", "issuer_role", "run_id", "model_id", "harness_id",
    "safeguards_id", "authorized_targets", "budgets", "prohibitions",
    "valid_from", "valid_until", "policy_digest", "role_issuers",
  ]),
  "capability.decide": new Set([
    "profile", "issuer_role", "run_id", "request_id", "decision_event_id",
    "request_hash", "mandate_ref", "policy_digest", "decision",
    "rationale_codes", "anchor_id", "protocol", "evidence_hash",
  ]),
  "capability.observe": new Set([
    "profile", "issuer_role", "run_id", "request_id",
    "decision_attestation", "observation_id", "anchor_id", "protocol",
    "effect_hash", "evidence_refs", "observation_class",
    "transport_status", "mandate_ref", "evidence_hash",
  ]),
  "trajectory.decide": new Set([
    "profile", "issuer_role", "run_id", "mandate_ref", "ordered_lineage",
    "policy_digest", "decision", "rationale_codes", "requested_controls",
  ]),
  "incident.statement": new Set([
    "profile", "issuer_role", "statement_id", "incident_id", "topic",
    "claim", "epistemic_status", "evidence_refs",
  ]),
  "incident.handoff": new Set([
    "profile", "issuer_role", "incident_id", "recipient", "mandate_ref",
    "timeline_hash", "coverage_hash", "statement_refs", "parent_refs",
    "requested_actions", "disclosure_conditions", "correction_channel",
    "challenge_channel",
  ]),
  "incident.packet.publish": new Set([
    "profile", "issuer_role", "packet_id", "packet_core_hash",
    "component_hashes",
  ]),
  "incident.correct": new Set([
    "profile", "issuer_role", "correction_id", "supersedes_kind",
    "supersedes_ref", "replacement_ref", "reason", "correction_channel",
  ]),
};

class Malformed extends Error {}

class StrictJsonParser {
  constructor(text, label) {
    this.text = text;
    this.label = label;
    this.index = 0;
    this.nodes = 0;
  }

  parse() {
    const value = this.value(1);
    this.space();
    if (this.index !== this.text.length) {
      throw new Malformed(`invalid JSON in ${this.label}: trailing content`);
    }
    return value;
  }

  space() {
    while (/[\t\n\r ]/.test(this.text[this.index] ?? "")) this.index += 1;
  }

  count(depth) {
    this.nodes += 1;
    if (this.nodes > MAX_NODES) {
      throw new Malformed(`${this.label} exceeds ${MAX_NODES} aggregate JSON nodes`);
    }
    if (depth > MAX_DEPTH) {
      throw new Malformed(`${this.label} exceeds maximum JSON depth ${MAX_DEPTH}`);
    }
  }

  value(depth) {
    this.space();
    this.count(depth);
    const character = this.text[this.index];
    if (character === "{") return this.object(depth);
    if (character === "[") return this.array(depth);
    if (character === '"') return this.string();
    if (this.text.startsWith("true", this.index)) {
      this.index += 4;
      return true;
    }
    if (this.text.startsWith("false", this.index)) {
      this.index += 5;
      return false;
    }
    if (this.text.startsWith("null", this.index)) {
      this.index += 4;
      return null;
    }
    return this.number();
  }

  object(depth) {
    this.index += 1;
    const result = {};
    const keys = new Set();
    this.space();
    if (this.text[this.index] === "}") {
      this.index += 1;
      return result;
    }
    while (true) {
      this.space();
      if (this.text[this.index] !== '"') {
        throw new Malformed(`invalid JSON in ${this.label}: object key required`);
      }
      this.count(depth + 1);
      const key = this.string();
      if (keys.has(key)) {
        throw new Malformed(`duplicate JSON member ${JSON.stringify(key)}`);
      }
      keys.add(key);
      this.space();
      if (this.text[this.index] !== ":") {
        throw new Malformed(`invalid JSON in ${this.label}: ':' required`);
      }
      this.index += 1;
      result[key] = this.value(depth + 1);
      this.space();
      const separator = this.text[this.index++];
      if (separator === "}") return result;
      if (separator !== ",") {
        throw new Malformed(`invalid JSON in ${this.label}: ',' or '}' required`);
      }
    }
  }

  array(depth) {
    this.index += 1;
    const result = [];
    this.space();
    if (this.text[this.index] === "]") {
      this.index += 1;
      return result;
    }
    while (true) {
      result.push(this.value(depth + 1));
      this.space();
      const separator = this.text[this.index++];
      if (separator === "]") return result;
      if (separator !== ",") {
        throw new Malformed(`invalid JSON in ${this.label}: ',' or ']' required`);
      }
    }
  }

  string() {
    const start = this.index;
    this.index += 1;
    let escaped = false;
    while (this.index < this.text.length) {
      const code = this.text.charCodeAt(this.index);
      if (!escaped && code === 0x22) {
        this.index += 1;
        let value;
        try {
          value = JSON.parse(this.text.slice(start, this.index));
        } catch (error) {
          throw new Malformed(`invalid JSON string in ${this.label}: ${error.message}`);
        }
        for (let i = 0; i < value.length; i += 1) {
          const unit = value.charCodeAt(i);
          if (unit >= 0xd800 && unit <= 0xdbff) {
            const next = value.charCodeAt(i + 1);
            if (!(next >= 0xdc00 && next <= 0xdfff)) {
              throw new Malformed(`${this.label} contains a lone Unicode surrogate`);
            }
            i += 1;
          } else if (unit >= 0xdc00 && unit <= 0xdfff) {
            throw new Malformed(`${this.label} contains a lone Unicode surrogate`);
          }
        }
        if (Buffer.byteLength(value, "utf8") > MAX_STRING_BYTES) {
          throw new Malformed(
            `${this.label} contains a string over ${MAX_STRING_BYTES} UTF-8 bytes`,
          );
        }
        return value;
      }
      if (!escaped && code < 0x20) {
        throw new Malformed(`invalid control character in ${this.label}`);
      }
      if (escaped) {
        escaped = false;
      } else if (code === 0x5c) {
        escaped = true;
      }
      this.index += 1;
    }
    throw new Malformed(`unterminated JSON string in ${this.label}`);
  }

  number() {
    const source = this.text.slice(this.index);
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(source);
    if (!match) {
      throw new Malformed(`invalid JSON value in ${this.label}`);
    }
    this.index += match[0].length;
    const value = Number(match[0]);
    if (!Number.isFinite(value)) {
      throw new Malformed(`non-finite JSON number is not permitted`);
    }
    return value;
  }
}

function loadJson(raw, label) {
  if (raw.length > MAX_FILE_BYTES) {
    throw new Malformed(`${label} exceeds ${MAX_FILE_BYTES} bytes`);
  }
  const text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
  return new StrictJsonParser(text, label).parse();
}

function exact(value, fields, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Malformed(`${label} must be an object`);
  }
  const keys = Object.keys(value);
  const missing = [...fields].filter((key) => !Object.hasOwn(value, key));
  const unknown = keys.filter((key) => !fields.has(key));
  if (missing.length) throw new Malformed(`${label} is missing ${missing.sort()}`);
  if (unknown.length) throw new Malformed(`${label} has unknown fields ${unknown.sort()}`);
  return value;
}

function closed(value, fields, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Malformed(`${label} must be an object`);
  }
  const unknown = Object.keys(value).filter((key) => !fields.has(key));
  if (unknown.length) throw new Malformed(`${label} has unknown fields ${unknown.sort()}`);
  return value;
}

function string(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Malformed(`${label} must be a non-empty string`);
  }
  return value;
}

function digest(value, label, nullable = false) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !HASH_RE.test(value)) {
    throw new Malformed(`${label} must be a lowercase sha256 digest`);
  }
  return value;
}

function integer(value, label, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum) {
    throw new Malformed(`${label} must be an integer >= ${minimum}`);
  }
  return value;
}

function objects(value, label) {
  if (!Array.isArray(value)) throw new Malformed(`${label} must be an array`);
  value.forEach((item, index) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) {
      throw new Malformed(`${label}[${index}] must be an object`);
    }
  });
  return value;
}

function strings(value, label, unique = false) {
  if (!Array.isArray(value)) throw new Malformed(`${label} must be an array`);
  const result = value.map((item, index) => string(item, `${label}[${index}]`));
  if (unique && new Set(result).size !== result.length) {
    throw new Malformed(`${label} must not contain duplicates`);
  }
  return result;
}

function instant(value, label) {
  const text = string(value, label);
  const match = UTC_INSTANT_RE.exec(text);
  if (!match) {
    throw new Malformed(`${label} must be an RFC 3339 UTC instant ending in Z`);
  }
  const [, year, month, day, hour, minute, second, fraction = ""] = match;
  const millis = Number((fraction + "000").slice(0, 3));
  const timestamp = Date.UTC(
    Number(year), Number(month) - 1, Number(day), Number(hour),
    Number(minute), Number(second), millis,
  );
  const parsed = new Date(timestamp);
  if (
    parsed.getUTCFullYear() !== Number(year)
    || parsed.getUTCMonth() !== Number(month) - 1
    || parsed.getUTCDate() !== Number(day)
    || parsed.getUTCHours() !== Number(hour)
    || parsed.getUTCMinutes() !== Number(minute)
    || parsed.getUTCSeconds() !== Number(second)
  ) {
    throw new Malformed(`${label} is not a valid RFC 3339 instant`);
  }
  return timestamp;
}

function safePath(value, label) {
  string(value, label);
  if (
    value.includes("\\")
    || value.includes("\0")
    || value.split("/").some((part) => part.includes(":"))
    || value.startsWith("/")
    || value.split("/").some((part) => ["", ".", ".."].includes(part))
  ) {
    throw new Malformed(`${label} must be a normalized relative POSIX path`);
  }
  return value;
}

function readPacket(rootValue) {
  const root = resolve(rootValue);
  const rootStat = lstatSync(root);
  if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) {
    throw new Malformed("packet root must be a non-symlink directory");
  }
  const files = new Map();
  let total = 0;
  function walk(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const absolute = resolve(directory, entry.name);
      const stat = lstatSync(absolute);
      const local = relative(root, absolute).split(sep).join("/");
      safePath(local, "packet file");
      if (stat.isSymbolicLink()) {
        throw new Malformed(`packet contains a symlink: ${local}`);
      }
      if (stat.isDirectory()) {
        walk(absolute);
      } else if (stat.isFile()) {
        if (files.size >= MAX_FILES) throw new Malformed(`packet exceeds ${MAX_FILES} files`);
        if (stat.size > MAX_FILE_BYTES) {
          throw new Malformed(`${local} exceeds ${MAX_FILE_BYTES} bytes`);
        }
        let descriptor;
        try {
          descriptor = openSync(
            absolute,
            fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0),
          );
          const opened = fstatSync(descriptor);
          if (
            !opened.isFile()
            || opened.dev !== stat.dev
            || opened.ino !== stat.ino
          ) throw new Malformed(`${local} changed before descriptor open`);
          const raw = readFileSync(descriptor);
          const final = fstatSync(descriptor);
          if (
            raw.length !== stat.size
            || final.dev !== opened.dev
            || final.ino !== opened.ino
            || final.size !== opened.size
          ) throw new Malformed(`${local} changed while read`);
          total += raw.length;
          if (total > MAX_TOTAL_BYTES) {
            throw new Malformed(`packet exceeds ${MAX_TOTAL_BYTES} total bytes`);
          }
          files.set(local, raw);
        } finally {
          if (descriptor !== undefined) closeSync(descriptor);
        }
      } else {
        throw new Malformed(`packet contains a non-regular file: ${local}`);
      }
    }
  }
  walk(root);
  return files;
}

function canonical(value) {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new Malformed("canonical numbers must be safe integers");
    }
    return String(value);
  }
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    return `{${Object.keys(value).sort().map(
      (key) => `${canonical(key)}:${canonical(value[key])}`,
    ).join(",")}}`;
  }
  throw new Malformed(`unsupported canonical value ${typeof value}`);
}

function hashBytes(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function hashJson(value) {
  return hashBytes(Buffer.from(canonical(value), "utf8"));
}

function coverageCommitment(core, artifacts) {
  return hashJson(Object.fromEntries(
    ["denominators", "coverage"].map((name) => [
      name,
      core[name].map((item) => ({
        ...item,
        artifact_sha256: artifacts.get(item.artifact_id).sha256,
      })),
    ]),
  ));
}

function denominatorCheckpointTopic(anchorId, phase, protocol) {
  return `denominator:${anchorId}:${phase}:${protocol}`;
}

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function base58(value) {
  let number = 0n;
  for (const character of value) {
    const index = B58.indexOf(character);
    if (index < 0) throw new Malformed("invalid base58btc did:key");
    number = number * 58n + BigInt(index);
  }
  const octets = [];
  while (number > 0n) {
    octets.push(Number(number & 255n));
    number >>= 8n;
  }
  octets.reverse();
  const padding = value.length - value.replace(/^1+/, "").length;
  return Buffer.from([...new Array(padding).fill(0), ...octets]);
}

function didKeyBytes(value) {
  const prefix = "did:key:z";
  if (!value.startsWith(prefix)) {
    throw new Malformed("proof issuer must be a self-certifying did:key");
  }
  const raw = base58(value.slice(prefix.length));
  if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) {
    throw new Malformed("did:key is not an Ed25519 public key");
  }
  return raw.subarray(2);
}

function validateProof(proof, purpose, signedDigest, issuer) {
  exact(proof, PROOF_FIELDS, `${purpose} proof`);
  if (
    proof.type !== "bulla/ed25519-2026"
    || proof.purpose !== purpose
    || proof.issuer !== issuer
    || proof.verificationMethod !== issuer
  ) {
    throw new Malformed(`${purpose} proof identity or purpose mismatch`);
  }
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
    proof.proofValue,
  )) {
    throw new Malformed(`${purpose} proofValue is not canonical base64`);
  }
  const signature = Buffer.from(proof.proofValue, "base64");
  if (signature.toString("base64") !== proof.proofValue || signature.length !== 64) {
    throw new Malformed(`${purpose} proofValue is not canonical base64`);
  }
  const rawKey = didKeyBytes(issuer);
  const spki = Buffer.concat([
    Buffer.from("302a300506032b6570032100", "hex"),
    rawKey,
  ]);
  const key = createPublicKey({ key: spki, type: "spki", format: "der" });
  const message = Buffer.from(canonical({
    context: "bulla-proof",
    schema: "0.4",
    purpose,
    digest: signedDigest,
  }), "utf8");
  if (!verifySignature(null, message, key, signature)) {
    throw new Malformed(`${purpose} proof signature is not authentic`);
  }
}

function envelope(receipt) {
  const mandate = closed(
    receipt.mandate, new Set(["deed_schema", "authority", "bounds"]), "mandate",
  );
  const retention = closed(
    receipt.retention, new Set(["record", "disclosure"]), "retention",
  );
  if (receipt.remedy === null || typeof receipt.remedy !== "object") {
    throw new Malformed("remedy must be an object");
  }
  const result = { deed_schema: mandate.deed_schema || "0.2" };
  if (mandate.authority) result.authority = mandate.authority;
  if (mandate.bounds) result.bounds = mandate.bounds;
  if (Object.keys(receipt.remedy).length) result.recourse = receipt.remedy;
  if (retention.record) result.retention_class = retention.record;
  if (retention.disclosure) result.disclosure_class = retention.disclosure;
  return result;
}

function receiptHashes(receipt) {
  const contentPreimage = {
    schema_version: "0.4",
    canonicalization: "bulla-jcs-int/1",
    kind: "action_receipt",
    action: receipt.action,
    diagnostic_ref: receipt.diagnostic_ref,
    evidence_refs: receipt.evidence_refs,
    anchor_ref: receipt.anchor_ref,
  };
  if (receipt.conventions.length) contentPreimage.conventions = receipt.conventions;
  const content = hashJson(contentPreimage);
  const event = hashJson({
    content_hash: content,
    event_id: receipt.event_id,
    claimed_at: receipt.claimed_at,
  });
  const recourseEnvelope = envelope(receipt);
  const authorization = hashJson({
    event_hash: event,
    envelope_hash: hashJson(recourseEnvelope),
  });
  const attestation = hashJson({
    content_hash: content,
    signature: receipt.signature,
    event_hash: event,
    occurrence: receipt.occurrence,
    recourse_envelope: recourseEnvelope,
    authorization: receipt.authorization,
  });
  return {
    content,
    event,
    authorization,
    attestation,
    log_leaf: hashBytes(Buffer.concat([
      Buffer.from([0]),
      Buffer.from(attestation, "utf8"),
    ])),
  };
}

function validateSubject(actionType, subject) {
  if (!Object.hasOwn(ACTION_FIELDS, actionType)) {
    throw new Malformed(`unsupported profile action ${JSON.stringify(actionType)}`);
  }
  exact(subject, ACTION_FIELDS[actionType], `${actionType}.subject`);
  if (subject.profile !== PROFILE) throw new Malformed(`${actionType} profile mismatch`);
  const role = string(subject.issuer_role, `${actionType}.issuer_role`);
  const allowedRoles = ACTION_ALLOWED_ROLES[actionType];
  if (allowedRoles !== null && !allowedRoles.has(role)) {
    throw new Malformed(
      `${actionType}.subject.issuer_role is not permitted for this action`,
    );
  }
  const label = `${actionType}.subject`;
  if (actionType === "eval.run.authorize") {
    for (const name of [
      "run_id", "model_id", "harness_id", "safeguards_id",
      "valid_from", "valid_until",
    ]) string(subject[name], `${label}.${name}`);
    digest(subject.policy_digest, `${label}.policy_digest`);
    strings(subject.authorized_targets, `${label}.authorized_targets`, true);
    strings(subject.prohibitions, `${label}.prohibitions`, true);
    const budgets = exact(
      subject.budgets,
      new Set(["compute_units", "max_actions", "duration_seconds"]),
      `${label}.budgets`,
    );
    for (const [name, value] of Object.entries(budgets)) {
      integer(value, `${label}.budgets.${name}`);
    }
    if (
      subject.role_issuers === null
      || typeof subject.role_issuers !== "object"
      || Array.isArray(subject.role_issuers)
      || Object.keys(subject.role_issuers).length === 0
    ) throw new Malformed(`${label}.role_issuers must be a non-empty object`);
    for (const [declaredRole, issuer] of Object.entries(subject.role_issuers)) {
      string(declaredRole, `${label}.role_issuers role`);
      string(issuer, `${label}.role_issuers.${declaredRole}`);
    }
    const validFrom = instant(subject.valid_from, `${label}.valid_from`);
    const validUntil = instant(subject.valid_until, `${label}.valid_until`);
    if (validFrom >= validUntil) {
      throw new Malformed(`${label}.valid_from must precede valid_until`);
    }
  } else if (actionType === "capability.decide") {
    for (const name of ["run_id", "request_id", "decision_event_id", "anchor_id"]) {
      string(subject[name], `${label}.${name}`);
    }
    for (const name of ["request_hash", "mandate_ref", "policy_digest", "evidence_hash"]) {
      digest(subject[name], `${label}.${name}`);
    }
    if (!DECISIONS.has(subject.decision)) throw new Malformed(`${label}.decision is unsupported`);
    if (!PROTOCOLS.has(subject.protocol)) throw new Malformed(`${label}.protocol is unsupported`);
    strings(subject.rationale_codes, `${label}.rationale_codes`, true);
  } else if (actionType === "capability.observe") {
    for (const name of [
      "run_id", "request_id", "observation_id", "anchor_id", "transport_status",
    ]) string(subject[name], `${label}.${name}`);
    for (const name of [
      "decision_attestation", "effect_hash", "mandate_ref", "evidence_hash",
    ]) digest(subject[name], `${label}.${name}`);
    strings(subject.evidence_refs, `${label}.evidence_refs`, true).forEach(
      (value, index) => digest(value, `${label}.evidence_refs[${index}]`),
    );
    if (!OBSERVATION_CLASSES.has(subject.observation_class)) {
      throw new Malformed(`${label}.observation_class is unsupported`);
    }
    if (!PROTOCOLS.has(subject.protocol)) throw new Malformed(`${label}.protocol is unsupported`);
  } else if (actionType === "trajectory.decide") {
    string(subject.run_id, `${label}.run_id`);
    for (const name of ["mandate_ref", "policy_digest"]) digest(subject[name], `${label}.${name}`);
    strings(subject.ordered_lineage, `${label}.ordered_lineage`, true).forEach(
      (value, index) => digest(value, `${label}.ordered_lineage[${index}]`),
    );
    if (!TRAJECTORY_DECISIONS.has(subject.decision)) {
      throw new Malformed(`${label}.decision is unsupported`);
    }
    strings(subject.rationale_codes, `${label}.rationale_codes`, true);
    strings(subject.requested_controls, `${label}.requested_controls`, true);
  } else if (actionType === "incident.statement") {
    for (const name of ["statement_id", "incident_id", "topic", "claim"]) {
      string(subject[name], `${label}.${name}`);
    }
    if (!EPISTEMIC_STATUSES.has(subject.epistemic_status)) {
      throw new Malformed(`${label}.epistemic_status is unsupported`);
    }
    if (
      subject.epistemic_status === "counterparty_confirmed"
      && role !== "affected_party"
    ) {
      throw new Malformed(
        `${label}.counterparty_confirmed requires affected_party issuer`,
      );
    }
    strings(subject.evidence_refs, `${label}.evidence_refs`, true).forEach(
      (value, index) => digest(value, `${label}.evidence_refs[${index}]`),
    );
  } else if (actionType === "incident.handoff") {
    for (const name of [
      "incident_id", "recipient", "correction_channel", "challenge_channel",
    ]) string(subject[name], `${label}.${name}`);
    for (const name of ["mandate_ref", "timeline_hash", "coverage_hash"]) {
      digest(subject[name], `${label}.${name}`);
    }
    for (const name of ["statement_refs", "parent_refs"]) {
      strings(subject[name], `${label}.${name}`, true).forEach(
        (value, index) => digest(value, `${label}.${name}[${index}]`),
      );
    }
    strings(subject.requested_actions, `${label}.requested_actions`, true);
    strings(subject.disclosure_conditions, `${label}.disclosure_conditions`, true);
  } else if (actionType === "incident.packet.publish") {
    string(subject.packet_id, `${label}.packet_id`);
    digest(subject.packet_core_hash, `${label}.packet_core_hash`);
    const components = exact(
      subject.component_hashes, new Set(COMPONENTS), `${label}.component_hashes`,
    );
    for (const [name, value] of Object.entries(components)) {
      digest(value, `${label}.component_hashes.${name}`);
    }
  } else if (actionType === "incident.correct") {
    for (const name of ["correction_id", "reason", "correction_channel"]) {
      string(subject[name], `${label}.${name}`);
    }
    if (!new Set(["packet", "statement"]).has(subject.supersedes_kind)) {
      throw new Malformed(`${label}.supersedes_kind is unsupported`);
    }
    digest(subject.supersedes_ref, `${label}.supersedes_ref`);
    digest(subject.replacement_ref, `${label}.replacement_ref`);
  }
  return subject;
}

function validateExecutableDefinition(value, label) {
  const definition = closed(
    value, new Set(["form", "schema", "quantum"]), label,
  );
  if (definition.form !== "jsonschema+quantum/1") {
    throw new Malformed(`${label}.form must be 'jsonschema+quantum/1'`);
  }
  const schema = closed(
    definition.schema,
    new Set(["type", "properties", "required", "additionalProperties"]),
    `${label}.schema`,
  );
  if ((schema.type ?? "object") !== "object") {
    throw new Malformed(`${label}.schema.type must be 'object'`);
  }
  const properties = schema.properties ?? {};
  if (properties === null || typeof properties !== "object"
      || Array.isArray(properties)) {
    throw new Malformed(`${label}.schema.properties must be an object`);
  }
  const required = schema.required ?? [];
  if (
    !Array.isArray(required)
    || required.some((item) => typeof item !== "string" || !item.trim())
    || new Set(required).size !== required.length
  ) {
    throw new Malformed(
      `${label}.schema.required must contain unique non-empty strings`,
    );
  }
  if (typeof (schema.additionalProperties ?? true) !== "boolean") {
    throw new Malformed(`${label}.schema.additionalProperties must be boolean`);
  }
  const propertyFields = new Set([
    "type", "enum", "const", "minimum", "maximum", "pattern",
  ]);
  for (const [name, rawProperty] of Object.entries(properties)) {
    if (!name.trim()) throw new Malformed(`${label} property name must not be blank`);
    const property = closed(
      rawProperty, propertyFields, `${label}.schema.properties[${JSON.stringify(name)}]`,
    );
    const propertyType = property.type;
    if (
      propertyType !== undefined
      && !["string", "integer", "number", "boolean"].includes(propertyType)
    ) throw new Malformed(`${label} property type is unsupported`);
    if (
      Object.hasOwn(property, "enum")
      && (!Array.isArray(property.enum) || property.enum.length === 0)
    ) throw new Malformed(`${label} property enum must be a non-empty array`);
    for (const keyword of ["minimum", "maximum"]) {
      if (!Object.hasOwn(property, keyword)) continue;
      if (
        typeof property[keyword] !== "number"
        || !Number.isFinite(property[keyword])
        || !["integer", "number"].includes(propertyType)
      ) throw new Malformed(`${label} property ${keyword} is invalid`);
    }
    if (
      Object.hasOwn(property, "minimum")
      && Object.hasOwn(property, "maximum")
      && property.minimum > property.maximum
    ) throw new Malformed(`${label} property minimum exceeds maximum`);
    if (Object.hasOwn(property, "pattern")) {
      if (propertyType !== "string" || typeof property.pattern !== "string") {
        throw new Malformed(`${label} property pattern requires string type`);
      }
      try {
        new RegExp(property.pattern);
      } catch {
        throw new Malformed(`${label} property pattern is invalid`);
      }
    }
  }
  if (definition.quantum !== undefined && definition.quantum !== null) {
    const quantum = definition.quantum;
    if (typeof quantum !== "object" || Array.isArray(quantum)) {
      throw new Malformed(`${label}.quantum must be an object`);
    }
    for (const [name, rawConstraint] of Object.entries(quantum)) {
      if (!name.trim()) throw new Malformed(`${label}.quantum field must not be blank`);
      const constraint = closed(
        rawConstraint, new Set(["unit", "multipleOf"]),
        `${label}.quantum[${JSON.stringify(name)}]`,
      );
      if (typeof constraint.unit !== "string" || !constraint.unit.trim()) {
        throw new Malformed(`${label}.quantum unit is required`);
      }
      const multiple = constraint.multipleOf ?? 1;
      if (!Number.isInteger(multiple) || multiple < 1) {
        throw new Malformed(`${label}.quantum multipleOf must be positive`);
      }
      if (!Object.hasOwn(properties, name) || properties[name].type !== "integer") {
        throw new Malformed(
          `${label}.quantum requires a declared integer property`,
        );
      }
    }
  }
}

function validateConvention(value, label) {
  const convention = closed(value, new Set([
    "name", "scope", "kind", "definition", "definition_hash", "forum",
  ]), label);
  const name = string(convention.name, `${label}.name`);
  if (!name.trim()) throw new Malformed(`${label}.name must not be blank`);
  if (!string(convention.scope, `${label}.scope`).trim()) {
    throw new Malformed(`${label}.scope must not be blank`);
  }
  if (!["executable", "semantic"].includes(convention.kind)) {
    throw new Malformed(`${label}.kind is unsupported`);
  }
  digest(convention.definition_hash, `${label}.definition_hash`);
  if (convention.kind === "executable") {
    if (!Object.hasOwn(convention, "definition")) {
      throw new Malformed(`${label} executable convention requires definition`);
    }
    validateExecutableDefinition(convention.definition, `${label}.definition`);
    const expected = typeof convention.definition === "string"
      ? hashBytes(Buffer.from(convention.definition, "utf8"))
      : hashJson(convention.definition);
    if (expected !== convention.definition_hash) {
      throw new Malformed(`${label} definition_hash does not match definition`);
    }
  } else {
    const forum = exact(
      convention.forum,
      new Set(["log_endpoint", "trusted_root_ref"]),
      `${label}.forum`,
    );
    if (!string(forum.log_endpoint, `${label}.forum.log_endpoint`).trim()) {
      throw new Malformed(`${label}.forum.log_endpoint must not be blank`);
    }
    if (!string(
      forum.trusted_root_ref, `${label}.forum.trusted_root_ref`,
    ).trim()) {
      throw new Malformed(`${label}.forum.trusted_root_ref must not be blank`);
    }
    if (Object.hasOwn(convention, "definition")) {
      const definition = string(convention.definition, `${label}.definition`);
      if (hashBytes(Buffer.from(definition, "utf8")) !== convention.definition_hash) {
        throw new Malformed(`${label} definition_hash does not match definition`);
      }
    }
  }
}

function validateEnvelopeViews(receipt, label) {
  const mandate = closed(
    receipt.mandate,
    new Set(["deed_schema", "authority", "bounds"]),
    `${label}.mandate`,
  );
  const retention = closed(
    receipt.retention,
    new Set(["record", "disclosure"]),
    `${label}.retention`,
  );
  const deedSchema = mandate.deed_schema || "0.2";
  if (!["0.2", "0.3"].includes(deedSchema)) {
    throw new Malformed(`${label}.mandate.deed_schema is unsupported`);
  }
  let authority = mandate.authority;
  if (authority !== undefined && authority !== null) {
    authority = exact(
      authority,
      new Set(["principal", "policy", "delegation"]),
      `${label}.mandate.authority`,
    );
    if (
      !string(authority.principal, `${label}.mandate.authority.principal`).trim()
      || !string(authority.policy, `${label}.mandate.authority.policy`).trim()
    ) throw new Malformed(`${label}.mandate.authority fields must not be blank`);
    if (!Array.isArray(authority.delegation)) {
      throw new Malformed(`${label}.mandate.authority.delegation must be an array`);
    }
    if (
      deedSchema === "0.2"
      && authority.delegation.some((item) => typeof item !== "string")
    ) throw new Malformed(`${label} deed_schema 0.2 delegation must use strings`);
    if (
      deedSchema === "0.3"
      && authority.delegation.some(
        (item) => item === null || typeof item !== "object" || Array.isArray(item),
      )
    ) throw new Malformed(`${label} deed_schema 0.3 delegation must use objects`);
    if (deedSchema === "0.3") {
      const grantFields = new Set([
        "grantor", "grantee", "principal", "parent", "policy_digest",
        "scope_digest", "not_before", "not_after", "proof",
      ]);
      const proofFields = new Set([
        "type", "purpose", "issuer", "verificationMethod", "proofValue",
      ]);
      authority.delegation.forEach((rawGrant, index) => {
        const grantLabel = `${label}.mandate.authority.delegation[${index}]`;
        const grant = closed(rawGrant, grantFields, grantLabel);
        for (const checkpointName of ["not_before", "not_after"]) {
          if (Object.hasOwn(grant, checkpointName)) {
            exact(
              grant[checkpointName], new Set(["domain", "value"]),
              `${grantLabel}.${checkpointName}`,
            );
          }
        }
        exact(grant.proof, proofFields, `${grantLabel}.proof`);
      });
    }
  }
  const bounds = mandate.bounds;
  if (bounds !== undefined && bounds !== null) {
    const checkedBounds = closed(
      bounds,
      new Set(["scope", "expires", "rollback_window"]),
      `${label}.mandate.bounds`,
    );
    if (
      checkedBounds.scope !== null
      && typeof checkedBounds.scope === "object"
      && !Array.isArray(checkedBounds.scope)
    ) {
      validateExecutableDefinition(
        checkedBounds.scope, `${label}.mandate.bounds.scope`,
      );
      if (deedSchema !== "0.3") {
        throw new Malformed(
          `${label} structured bounds.scope requires deed_schema 0.3`,
        );
      }
    } else if (
      typeof checkedBounds.scope !== "string" || !checkedBounds.scope.trim()
    ) {
      throw new Malformed(
        `${label}.mandate.bounds.scope must be a non-empty string or executable definition`,
      );
    }
  }
  let remedy = receipt.remedy;
  if (remedy === null || typeof remedy !== "object" || Array.isArray(remedy)) {
    throw new Malformed(`${label}.remedy must be an object`);
  }
  if (Object.keys(remedy).length) {
    remedy = exact(
      remedy,
      new Set(["challenge_window", "forum", "remedies"]),
      `${label}.remedy`,
    );
    if (!string(
      remedy.challenge_window, `${label}.remedy.challenge_window`,
    ).trim()) {
      throw new Malformed(`${label}.remedy.challenge_window must not be blank`);
    }
    const forum = exact(
      remedy.forum,
      new Set(["log_endpoint", "trusted_root_ref"]),
      `${label}.remedy.forum`,
    );
    for (const name of ["log_endpoint", "trusted_root_ref"]) {
      if (!string(forum[name], `${label}.remedy.forum.${name}`).trim()) {
        throw new Malformed(`${label}.remedy.forum.${name} must not be blank`);
      }
    }
    const remedies = objects(remedy.remedies, `${label}.remedy.remedies`);
    if (!remedies.length) {
      throw new Malformed(`${label}.remedy.remedies must not be empty`);
    }
    remedies.forEach((item, index) => {
      const itemLabel = `${label}.remedy.remedies[${index}]`;
      exact(item, new Set(["rung", "verifier", "anchor"]), itemLabel);
      if (![
        "recompute", "challenge", "cure", "revert", "slash", "escalate",
      ].includes(item.rung)) throw new Malformed(`${itemLabel}.rung is unsupported`);
      for (const name of ["verifier", "anchor"]) {
        if (!string(item[name], `${itemLabel}.${name}`).trim()) {
          throw new Malformed(`${itemLabel}.${name} must not be blank`);
        }
      }
      if (item.rung === "escalate" && !authority) {
        throw new Malformed(`${label} escalate remedy requires authority`);
      }
    });
  }
  if (
    retention.record !== undefined
    && ![
      "authority-permanent", "operational", "personal-expiring",
    ].includes(retention.record)
  ) throw new Malformed(`${label}.retention.record is unsupported`);
  if (
    retention.disclosure !== undefined
    && !["public", "party", "auditor"].includes(retention.disclosure)
  ) throw new Malformed(`${label}.retention.disclosure is unsupported`);
  if (
    !authority && !bounds && Object.keys(remedy).length === 0
    && retention.record === undefined && retention.disclosure === undefined
  ) throw new Malformed(`${label} envelope must not be empty`);
}

function validateReceiptBase(receipt, label) {
  const diagnostic = closed(
    receipt.diagnostic_ref,
    new Set(["status", "ref"]),
    `${label}.diagnostic_ref`,
  );
  if (!["reference", "not_applicable", "deferred"].includes(diagnostic.status)) {
    throw new Malformed(`${label}.diagnostic_ref.status is unsupported`);
  }
  if (diagnostic.status === "reference") {
    if (!string(diagnostic.ref, `${label}.diagnostic_ref.ref`).trim()) {
      throw new Malformed(`${label}.diagnostic_ref.ref must not be blank`);
    }
  }
  objects(receipt.evidence_refs, `${label}.evidence_refs`).forEach((item, index) => {
    const itemLabel = `${label}.evidence_refs[${index}]`;
    exact(item, new Set(["name", "hash", "grounding"]), itemLabel);
    if (
      !string(item.name, `${itemLabel}.name`).trim()
      || !string(item.hash, `${itemLabel}.hash`).trim()
    ) throw new Malformed(`${itemLabel} name and hash must not be blank`);
    if (!GROUNDING_CLASSES.has(item.grounding)) {
      throw new Malformed(`${itemLabel}.grounding is unsupported`);
    }
  });
  if (receipt.stake !== null) {
    throw new Malformed(`${label}.stake is reserved and must be null`);
  }
  validateEnvelopeViews(receipt, label);
  const conventions = objects(receipt.conventions, `${label}.conventions`);
  conventions.forEach(
    (item, index) => validateConvention(item, `${label}.conventions[${index}]`),
  );
  if (conventions.length) {
    throw new Malformed(
      `${label} glyph.agent-incident-packet/0.1-draft requires conventions to be empty`,
    );
  }
  if (typeof receipt.event_id !== "string" || !UUID_V4_RE.test(receipt.event_id)) {
    throw new Malformed(`${label}.event_id must be a canonical lowercase UUIDv4`);
  }
  instant(receipt.claimed_at, `${label}.claimed_at`);
}

function validateEvidenceRules(receipt, label, teamRoles) {
  const { type } = receipt.action;
  const subject = receipt.action.subject;
  const evidence = receipt.evidence_refs;
  if (type === "capability.decide") {
    if (evidence.some((item) => item.grounding !== "self_asserted")) {
      throw new Malformed(`${label} gateway decision evidence must be self_asserted`);
    }
    if (
      canonical(evidence.map((item) => item.hash))
      !== canonical([subject.evidence_hash])
    ) {
      throw new Malformed(
        `${label} decision evidence_hash must equal the sole ActionReceipt evidence hash`,
      );
    }
  } else if (type === "capability.observe") {
    const expected = subject.issuer_role === "target"
      ? "counterparty_signed" : "self_asserted";
    if (evidence.some((item) => item.grounding !== expected)) {
      throw new Malformed(
        `${label} ${subject.issuer_role} observation evidence must be ${expected}`,
      );
    }
    if (
      canonical(evidence.map((item) => item.hash))
      !== canonical(subject.evidence_refs)
    ) {
      throw new Malformed(
        `${label} observation evidence_refs must equal the ordered ActionReceipt evidence hashes`,
      );
    }
  }
  if (
    teamRoles.has(subject.issuer_role)
    && evidence.some((item) => item.grounding === "third_party_anchored")
  ) {
    throw new Malformed(
      `${label} team-controlled infrastructure cannot claim third_party_anchored`,
    );
  }
  if (evidence.some(
    (item) => item.grounding === "execution_verified"
      && !item.name.startsWith("recomputation:"),
  )) {
    throw new Malformed(
      `${label} execution_verified is limited to named deterministic recomputations`,
    );
  }
}

function validateReceipt(
  raw, label, accepted, teamRoles, expectedAction = null,
) {
  const receipt = exact(loadJson(raw, label), RECEIPT_FIELDS, label);
  if (
    receipt.schema_version !== "0.4"
    || receipt.canonicalization !== "bulla-jcs-int/1"
    || receipt.kind !== "action_receipt"
  ) {
    throw new Malformed(`${label} is not an ActionReceipt v0.4 profile record`);
  }
  validateReceiptBase(receipt, label);
  const action = exact(receipt.action, new Set(["type", "subject"]), `${label}.action`);
  if (expectedAction !== null && action.type !== expectedAction) {
    throw new Malformed(`${label} action type mismatch`);
  }
  const subject = validateSubject(action.type, action.subject);
  const issuer = receipt.signature?.issuer;
  if (issuer !== undefined && !accepted.get(subject.issuer_role)?.has(issuer)) {
    throw new Malformed(`${label} issuer is not accepted for role ${subject.issuer_role}`);
  }
  const authority = receipt.mandate
    && typeof receipt.mandate === "object"
    && !Array.isArray(receipt.mandate)
    ? receipt.mandate.authority
    : null;
  if (
    authority === null
    || typeof authority !== "object"
    || Array.isArray(authority)
    || (receipt.mandate.deed_schema ?? "0.2") !== "0.2"
    || !Array.isArray(authority.delegation)
    || authority.delegation.length !== 0
    || authority.principal !== issuer
  ) {
    throw new Malformed(
      `${label} incident profile requires direct deed_schema 0.2 authority `
      + "with an empty delegation and principal equal to the accepted signer",
    );
  }
  const hashes = receiptHashes(receipt);
  exact(receipt.hashes, new Set(["content", "event", "attestation", "log_leaf"]), `${label}.hashes`);
  for (const name of ["content", "event", "attestation", "log_leaf"]) {
    if (receipt.hashes[name] !== hashes[name]) {
      throw new Malformed(`${label} ${name} hash mismatch`);
    }
  }
  validateProof(receipt.signature, "content", hashes.content, issuer);
  validateProof(receipt.occurrence, "occurrence", hashes.event, issuer);
  validateProof(receipt.authorization, "authorization", hashes.authorization, issuer);
  validateEvidenceRules(receipt, label, teamRoles);
  return receipt;
}

function validateCoreComponents(core) {
  const roles = new Set();
  const issuers = new Set();
  core.roles.forEach((item, index) => {
    const label = `packet-core.roles[${index}]`;
    exact(item, new Set(["role", "issuer"]), label);
    const role = string(item.role, `${label}.role`);
    const issuer = string(item.issuer, `${label}.issuer`);
    if (roles.has(role) || issuers.has(issuer)) {
      throw new Malformed("duplicate role or cross-role issuer");
    }
    roles.add(role);
    issuers.add(issuer);
  });

  const timelinePaths = new Set();
  const timelineHashes = new Set();
  const hashByPath = new Map();
  const typeByPath = new Map();
  core.timeline.forEach((item, index) => {
    const label = `packet-core.timeline[${index}]`;
    exact(item, new Set([
      "sequence", "receipt_path", "byte_length", "sha256",
      "attestation_hash", "action_type",
    ]), label);
    if (integer(item.sequence, `${label}.sequence`, 1) !== index + 1) {
      throw new Malformed("timeline sequence must be contiguous");
    }
    const path = safePath(item.receipt_path, `${label}.receipt_path`);
    if (!path.startsWith("receipts/")) {
      throw new Malformed("timeline receipts must be under receipts/");
    }
    integer(item.byte_length, `${label}.byte_length`);
    digest(item.sha256, `${label}.sha256`);
    digest(item.attestation_hash, `${label}.attestation_hash`);
    string(item.action_type, `${label}.action_type`);
    if (
      !Object.hasOwn(ACTION_FIELDS, item.action_type)
      || item.action_type === "incident.packet.publish"
    ) throw new Malformed("unsupported timeline action type");
    if (timelinePaths.has(path) || timelineHashes.has(item.attestation_hash)) {
      throw new Malformed("timeline paths and hashes must be unique");
    }
    timelinePaths.add(path);
    timelineHashes.add(item.attestation_hash);
    hashByPath.set(path, item.attestation_hash);
    typeByPath.set(path, item.action_type);
  });

  const artifacts = new Map();
  const artifactPaths = new Set();
  core.artifacts.forEach((item, index) => {
    const label = `packet-core.artifacts[${index}]`;
    exact(item, new Set([
      "artifact_id", "path", "withheld_ref", "role", "media_type",
      "byte_length", "sha256", "disclosure_state", "retention",
      "access_condition",
    ]), label);
    const artifactId = string(item.artifact_id, `${label}.artifact_id`);
    if (artifacts.has(artifactId)) throw new Malformed("duplicate artifact id");
    artifacts.set(artifactId, item);
    integer(item.byte_length, `${label}.byte_length`);
    digest(item.sha256, `${label}.sha256`);
    for (const name of ["role", "media_type", "retention", "access_condition"]) {
      string(item[name], `${label}.${name}`);
    }
    if (item.disclosure_state === "released") {
      const path = safePath(item.path, `${label}.path`);
      if (
        [CORE_FILE, PUBLISH_FILE].includes(path)
        || path.startsWith("receipts/")
        || artifactPaths.has(path)
        || item.withheld_ref !== null
      ) throw new Malformed("released artifact path or withheld_ref is invalid");
      artifactPaths.add(path);
    } else if (["controlled", "withheld"].includes(item.disclosure_state)) {
      if (item.path !== null) throw new Malformed("unreleased artifact path must be null");
      string(item.withheld_ref, `${label}.withheld_ref`);
    } else {
      throw new Malformed("unsupported artifact disclosure state");
    }
  });

  const denominatorKeys = new Set();
  const denominatorArtifacts = new Set();
  const checkpoints = new Set();
  const phaseCounts = new Map();
  core.denominators.forEach((item, index) => {
    const label = `packet-core.denominators[${index}]`;
    exact(item, new Set([
      "anchor_id", "phase", "protocol", "artifact_id", "provenance",
      "checkpoint_attestation",
    ]), label);
    string(item.anchor_id, `${label}.anchor_id`);
    if (!["decision", "effect"].includes(item.phase)) {
      throw new Malformed(`${label}.phase is unsupported`);
    }
    if (!["http", "mcp"].includes(item.protocol)) {
      throw new Malformed(`${label}.protocol is unsupported`);
    }
    const key = JSON.stringify([item.anchor_id, item.phase, item.protocol]);
    if (denominatorKeys.has(key)) throw new Malformed("duplicate denominator key");
    denominatorKeys.add(key);
    const artifactId = string(item.artifact_id, `${label}.artifact_id`);
    if (!artifacts.has(artifactId) || artifacts.get(artifactId).role !== "denominator") {
      throw new Malformed(`${label} must reference a denominator artifact`);
    }
    if (denominatorArtifacts.has(artifactId)) {
      throw new Malformed("denominator artifact is reused");
    }
    denominatorArtifacts.add(artifactId);
    if (![
      "PATH_SEPARATE_TEAM_CONTROLLED", "SEPARATELY_CONTROLLED", "UNKNOWN",
    ].includes(item.provenance)) {
      throw new Malformed(`${label}.provenance is unsupported`);
    }
    digest(item.checkpoint_attestation, `${label}.checkpoint_attestation`);
    if (checkpoints.has(item.checkpoint_attestation)) {
      throw new Malformed("denominator checkpoints must be unique");
    }
    checkpoints.add(item.checkpoint_attestation);
    if (!phaseCounts.has(item.protocol)) {
      phaseCounts.set(item.protocol, { decision: 0, effect: 0 });
    }
    phaseCounts.get(item.protocol)[item.phase] += 1;
  });
  if (
    denominatorKeys.size === 0
    || [...phaseCounts.values()].some(
      (counts) => counts.decision !== 1 || counts.effect !== 1,
    )
  ) throw new Malformed("each represented protocol requires decision/effect denominators");

  const coverageKeys = new Set();
  core.coverage.forEach((item, index) => {
    const label = `packet-core.coverage[${index}]`;
    exact(item, new Set([
      "anchor_id", "phase", "protocol", "artifact_id",
      "minimum_verification_depth",
    ]), label);
    string(item.anchor_id, `${label}.anchor_id`);
    if (!["decision", "effect"].includes(item.phase)
        || !["http", "mcp"].includes(item.protocol)) {
      throw new Malformed(`${label} phase or protocol is unsupported`);
    }
    const key = JSON.stringify([item.anchor_id, item.phase, item.protocol]);
    if (coverageKeys.has(key)) throw new Malformed("duplicate coverage key");
    coverageKeys.add(key);
    const artifactId = string(item.artifact_id, `${label}.artifact_id`);
    if (!artifacts.has(artifactId) || artifacts.get(artifactId).role !== "coverage") {
      throw new Malformed(`${label} must reference a coverage artifact`);
    }
    if (denominatorArtifacts.has(artifactId)) {
      throw new Malformed("coverage and denominator artifacts must be distinct");
    }
    if (item.minimum_verification_depth !== "attestation") {
      throw new Malformed(`${label}.minimum_verification_depth must be attestation`);
    }
  });
  if (
    denominatorKeys.size !== coverageKeys.size
    || [...denominatorKeys].some((key) => !coverageKeys.has(key))
  ) throw new Malformed("denominator and coverage keys differ");

  for (const [name, expectedType] of [
    ["statements", "incident.statement"],
    ["corrections", "incident.correct"],
  ]) {
    const seen = new Set();
    core[name].forEach((item, index) => {
      const label = `packet-core.${name}[${index}]`;
      exact(item, new Set(["receipt_path", "attestation_hash"]), label);
      const path = safePath(item.receipt_path, `${label}.receipt_path`);
      digest(item.attestation_hash, `${label}.attestation_hash`);
      if (
        hashByPath.get(path) !== item.attestation_hash
        || typeByPath.get(path) !== expectedType
      ) throw new Malformed(`${label} is not bound to the expected timeline row`);
      const key = JSON.stringify([path, item.attestation_hash]);
      if (seen.has(key)) throw new Malformed(`duplicate ${name.slice(0, -1)} reference`);
      seen.add(key);
    });
  }
  if (core.witnesses.length > 1) {
    throw new Malformed(
      "packet-core.witnesses supports at most one witness reference in this draft",
    );
  }
  const witnessIds = new Set();
  core.witnesses.forEach((item, index) => {
    const label = `packet-core.witnesses[${index}]`;
    exact(item, new Set(["witness_id", "artifact_id", "root_ref"]), label);
    const witnessId = string(item.witness_id, `${label}.witness_id`);
    if (witnessIds.has(witnessId)) throw new Malformed("duplicate witness id");
    witnessIds.add(witnessId);
    const artifactId = string(item.artifact_id, `${label}.artifact_id`);
    if (!artifacts.has(artifactId) || artifacts.get(artifactId).role !== "witness") {
      throw new Malformed(`${label} must reference a witness artifact`);
    }
    string(item.root_ref, `${label}.root_ref`);
  });
  const redactionIds = new Set();
  core.redactions.forEach((item, index) => {
    const label = `packet-core.redactions[${index}]`;
    exact(item, new Set([
      "redaction_id", "record_artifact_id", "source_artifact_id",
      "released_artifact_id", "tool", "tool_version", "rules_hash",
      "reviewer_statement_ref",
    ]), label);
    const redactionId = string(item.redaction_id, `${label}.redaction_id`);
    if (redactionIds.has(redactionId)) throw new Malformed("duplicate redaction id");
    redactionIds.add(redactionId);
    for (const name of [
      "record_artifact_id", "source_artifact_id", "released_artifact_id",
    ]) {
      if (!artifacts.has(item[name])) {
        throw new Malformed(`${label}.${name} references an unknown artifact`);
      }
    }
    const record = artifacts.get(item.record_artifact_id);
    const released = artifacts.get(item.released_artifact_id);
    if (record.role !== "redaction" || record.disclosure_state !== "released") {
      throw new Malformed("redaction record artifact must be a released redaction");
    }
    if (
      item.source_artifact_id === item.released_artifact_id
      || item.record_artifact_id === item.source_artifact_id
      || item.record_artifact_id === item.released_artifact_id
    ) throw new Malformed("redaction artifacts must be distinct");
    if (released.disclosure_state !== "released") {
      throw new Malformed("redaction released artifact must be released");
    }
    string(item.tool, `${label}.tool`);
    string(item.tool_version, `${label}.tool_version`);
    digest(item.rules_hash, `${label}.rules_hash`);
    digest(item.reviewer_statement_ref, `${label}.reviewer_statement_ref`);
  });
  return core;
}

function parseContext(raw) {
  const value = exact(loadJson(raw, "context"), new Set([
    "accepted_issuers_by_role", "trusted_witness_roots", "team_controlled_roles",
  ]), "context");
  const accepted = new Map();
  const issuerRoles = new Map();
  for (const [role, issuers] of Object.entries(value.accepted_issuers_by_role)) {
    const checkedRole = string(role, "context role");
    const checkedIssuers = new Set(strings(
      issuers, `context.accepted_issuers_by_role.${role}`, true,
    ));
    for (const issuer of checkedIssuers) {
      if (issuerRoles.has(issuer) && issuerRoles.get(issuer) !== checkedRole) {
        throw new Malformed(
          "context issuer is accepted for more than one incident role",
        );
      }
      issuerRoles.set(issuer, checkedRole);
    }
    accepted.set(checkedRole, checkedIssuers);
  }
  return {
    accepted,
    trustedRoots: new Set(strings(value.trusted_witness_roots, "trusted_witness_roots", true)),
    teamRoles: new Set(strings(
      value.team_controlled_roles, "context.team_controlled_roles", true,
    )),
  };
}

function projection(receipt) {
  const subject = receipt.action.subject;
  if (receipt.action.type === "capability.decide") {
    return {
      key: [subject.anchor_id, "decision", subject.protocol],
      value: {
        anchor_id: subject.anchor_id,
        observation_id: subject.decision_event_id,
        run_id: subject.run_id,
        protocol: subject.protocol,
        operation_ref: subject.request_hash,
        phase: "decision",
        evidence_hash: subject.evidence_hash,
      },
    };
  }
  if (receipt.action.type === "capability.observe") {
    return {
      key: [subject.anchor_id, "effect", subject.protocol],
      value: {
        anchor_id: subject.anchor_id,
        observation_id: subject.observation_id,
        run_id: subject.run_id,
        protocol: subject.protocol,
        operation_ref: subject.effect_hash,
        phase: "effect",
        evidence_hash: subject.evidence_hash,
      },
    };
  }
  return null;
}

function proofFailureDimensions(raw, problem) {
  let receipt;
  try {
    receipt = loadJson(raw, "receipt proof classification");
  } catch {
    return {
      signature: false, authority: false, occurrence: false, role: false,
    };
  }
  const missingSignature = receipt.signature === null
    || typeof receipt.signature !== "object"
    || Array.isArray(receipt.signature);
  const missingOccurrence = receipt.occurrence === null
    || typeof receipt.occurrence !== "object"
    || Array.isArray(receipt.occurrence);
  const missingAuthorization = receipt.authorization === null
    || typeof receipt.authorization !== "object"
    || Array.isArray(receipt.authorization);
  let contentFailed = missingSignature;
  let occurrenceProofFailed = missingOccurrence;
  let authorizationProofFailed = missingAuthorization;
  try {
    const hashes = receiptHashes(receipt);
    const issuer = receipt.signature?.issuer;
    for (const [proof, purpose, signedDigest, target] of [
      [receipt.signature, "content", hashes.content, "content"],
      [receipt.occurrence, "occurrence", hashes.event, "occurrence"],
      [
        receipt.authorization, "authorization", hashes.authorization,
        "authorization",
      ],
    ]) {
      try {
        validateProof(proof, purpose, signedDigest, issuer);
      } catch {
        if (target === "content") contentFailed = true;
        else if (target === "occurrence") occurrenceProofFailed = true;
        else authorizationProofFailed = true;
      }
    }
  } catch {
    // Structural errors are not themselves evidence that a proof failed.
  }
  return {
    signature: contentFailed || occurrenceProofFailed || authorizationProofFailed,
    occurrence: contentFailed || occurrenceProofFailed,
    authority: contentFailed || occurrenceProofFailed || authorizationProofFailed,
    role: missingSignature,
  };
}

const keyString = (key) => JSON.stringify(key);

function verify(packetPath, contextPath) {
  const files = readPacket(packetPath);
  if (!files.has(CORE_FILE) || !files.has(PUBLISH_FILE)) {
    throw new Malformed("packet is missing required fixed files");
  }
  const core = exact(loadJson(files.get(CORE_FILE), CORE_FILE), CORE_FIELDS, CORE_FILE);
  if (core.profile !== PROFILE) throw new Malformed("unsupported packet profile");
  string(core.packet_id, "packet_id");
  string(core.incident_id, "incident_id");
  integer(core.revision, "revision", 1);
  string(core.classification, "classification");
  digest(core.supersedes_core_hash, "supersedes_core_hash", true);
  COMPONENTS.forEach((name) => objects(core[name], `packet-core.${name}`));
  validateCoreComponents(core);
  const { accepted, trustedRoots, teamRoles } = parseContext(readFileSync(contextPath));

  const declared = new Set([CORE_FILE, PUBLISH_FILE]);
  const timelinePaths = new Set();
  const timelineHashes = new Set();
  const timelineTypesByPath = new Map();
  const timelineHashByPath = new Map();
  core.timeline.forEach((item, index) => {
    exact(item, new Set([
      "sequence", "receipt_path", "byte_length", "sha256",
      "attestation_hash", "action_type",
    ]), `timeline[${index}]`);
    if (integer(item.sequence, "timeline.sequence", 1) !== index + 1) {
      throw new Malformed("timeline sequence must be contiguous");
    }
    const local = safePath(item.receipt_path, "timeline.receipt_path");
    if (!local.startsWith("receipts/") || timelinePaths.has(local)) {
      throw new Malformed("timeline receipt path is invalid or duplicate");
    }
    timelinePaths.add(local);
    integer(item.byte_length, "timeline.byte_length");
    digest(item.sha256, "timeline.sha256");
    digest(item.attestation_hash, "timeline.attestation_hash");
    if (timelineHashes.has(item.attestation_hash)) {
      throw new Malformed("duplicate timeline attestation hash");
    }
    timelineHashes.add(item.attestation_hash);
    if (!Object.hasOwn(ACTION_FIELDS, item.action_type) || item.action_type === "incident.packet.publish") {
      throw new Malformed("unsupported timeline action type");
    }
    timelineTypesByPath.set(local, item.action_type);
    timelineHashByPath.set(local, item.attestation_hash);
    declared.add(local);
  });

  const artifacts = new Map();
  const artifactPaths = new Set();
  const unavailable = [];
  core.artifacts.forEach((item, index) => {
    exact(item, new Set([
      "artifact_id", "path", "withheld_ref", "role", "media_type",
      "byte_length", "sha256", "disclosure_state", "retention",
      "access_condition",
    ]), `artifact[${index}]`);
    const artifactId = string(item.artifact_id, "artifact_id");
    if (artifacts.has(artifactId)) throw new Malformed("duplicate artifact id");
    artifacts.set(artifactId, item);
    integer(item.byte_length, "artifact.byte_length");
    digest(item.sha256, "artifact.sha256");
    for (const name of ["role", "media_type", "retention", "access_condition"]) {
      string(item[name], `artifact.${name}`);
    }
    if (item.disclosure_state === "released") {
      const local = safePath(item.path, "artifact.path");
      if (
        [CORE_FILE, PUBLISH_FILE].includes(local)
        || local.startsWith("receipts/")
        || artifactPaths.has(local)
        || item.withheld_ref !== null
      ) {
        throw new Malformed("released artifact path or withheld_ref is invalid");
      }
      artifactPaths.add(local);
      declared.add(local);
    } else if (["controlled", "withheld"].includes(item.disclosure_state)) {
      if (item.path !== null) throw new Malformed("unreleased artifact path must be null");
      string(item.withheld_ref, "artifact.withheld_ref");
      unavailable.push(artifactId);
    } else {
      throw new Malformed("unsupported disclosure state");
    }
  });
  const structuralDenominatorKeys = new Set();
  const denominatorArtifactIds = new Set();
  const denominatorCheckpoints = new Set();
  core.denominators.forEach((item, index) => {
    const label = `packet-core.denominators[${index}]`;
    exact(item, new Set([
      "anchor_id", "phase", "protocol", "artifact_id", "provenance",
      "checkpoint_attestation",
    ]), label);
    string(item.anchor_id, `${label}.anchor_id`);
    if (!["decision", "effect"].includes(item.phase)) {
      throw new Malformed(`${label}.phase is unsupported`);
    }
    if (!["http", "mcp"].includes(item.protocol)) {
      throw new Malformed(`${label}.protocol is unsupported`);
    }
    const key = keyString([item.anchor_id, item.phase, item.protocol]);
    if (structuralDenominatorKeys.has(key)) {
      throw new Malformed("duplicate denominator key");
    }
    structuralDenominatorKeys.add(key);
    const artifactId = string(item.artifact_id, `${label}.artifact_id`);
    if (!artifacts.has(artifactId)) {
      throw new Malformed(`${label} references an unknown artifact`);
    }
    if (artifacts.get(artifactId).role !== "denominator") {
      throw new Malformed(`${label} artifact role must be 'denominator'`);
    }
    if (denominatorArtifactIds.has(artifactId)) {
      throw new Malformed("denominator artifact is reused");
    }
    denominatorArtifactIds.add(artifactId);
    if (![
      "PATH_SEPARATE_TEAM_CONTROLLED", "SEPARATELY_CONTROLLED", "UNKNOWN",
    ].includes(item.provenance)) {
      throw new Malformed(`${label}.provenance is unsupported`);
    }
    digest(item.checkpoint_attestation, `${label}.checkpoint_attestation`);
    if (denominatorCheckpoints.has(item.checkpoint_attestation)) {
      throw new Malformed(
        "each denominator must use a distinct checkpoint attestation",
      );
    }
    denominatorCheckpoints.add(item.checkpoint_attestation);
  });
  const structuralCoverageKeys = new Set();
  core.coverage.forEach((item, index) => {
    const label = `packet-core.coverage[${index}]`;
    exact(item, new Set([
      "anchor_id", "phase", "protocol", "artifact_id",
      "minimum_verification_depth",
    ]), label);
    string(item.anchor_id, `${label}.anchor_id`);
    if (!["decision", "effect"].includes(item.phase)) {
      throw new Malformed(`${label}.phase is unsupported`);
    }
    if (!["http", "mcp"].includes(item.protocol)) {
      throw new Malformed(`${label}.protocol is unsupported`);
    }
    const key = keyString([item.anchor_id, item.phase, item.protocol]);
    if (structuralCoverageKeys.has(key)) {
      throw new Malformed("duplicate coverage key");
    }
    structuralCoverageKeys.add(key);
    const artifactId = string(item.artifact_id, `${label}.artifact_id`);
    if (!artifacts.has(artifactId)) {
      throw new Malformed(`${label} references an unknown artifact`);
    }
    if (artifacts.get(artifactId).role !== "coverage") {
      throw new Malformed(`${label} artifact role must be 'coverage'`);
    }
    if (denominatorArtifactIds.has(artifactId)) {
      throw new Malformed("coverage and denominator must use distinct artifacts");
    }
    if (item.minimum_verification_depth !== "attestation") {
      throw new Malformed(
        `${label}.minimum_verification_depth must be 'attestation'`,
      );
    }
  });
  if (
    structuralDenominatorKeys.size === 0
    || structuralDenominatorKeys.size !== structuralCoverageKeys.size
    || [...structuralDenominatorKeys].some(
      (key) => !structuralCoverageKeys.has(key),
    )
  ) {
    throw new Malformed("denominator and coverage keys differ");
  }
  const redactionIds = new Set();
  core.redactions.forEach((item, index) => {
    const label = `packet-core.redactions[${index}]`;
    exact(item, new Set([
      "redaction_id", "record_artifact_id", "source_artifact_id",
      "released_artifact_id", "tool", "tool_version", "rules_hash",
      "reviewer_statement_ref",
    ]), label);
    const redactionId = string(item.redaction_id, `${label}.redaction_id`);
    if (redactionIds.has(redactionId)) throw new Malformed("duplicate redaction id");
    redactionIds.add(redactionId);
    for (const name of [
      "record_artifact_id", "source_artifact_id", "released_artifact_id",
    ]) {
      if (!artifacts.has(item[name])) {
        throw new Malformed(`${label}.${name} references an unknown artifact`);
      }
    }
    const record = artifacts.get(item.record_artifact_id);
    const released = artifacts.get(item.released_artifact_id);
    if (record.role !== "redaction") {
      throw new Malformed("redaction record artifact role must be 'redaction'");
    }
    if (record.disclosure_state !== "released") {
      throw new Malformed("redaction record artifact must be released");
    }
    if (item.source_artifact_id === item.released_artifact_id) {
      throw new Malformed("redaction source and released artifacts must differ");
    }
    if (
      item.record_artifact_id === item.source_artifact_id
      || item.record_artifact_id === item.released_artifact_id
    ) {
      throw new Malformed(
        "redaction record artifact must differ from source and release",
      );
    }
    if (released.disclosure_state !== "released") {
      throw new Malformed("redaction released artifact must be released");
    }
    string(item.tool, `${label}.tool`);
    string(item.tool_version, `${label}.tool_version`);
    digest(item.rules_hash, `${label}.rules_hash`);
    digest(item.reviewer_statement_ref, `${label}.reviewer_statement_ref`);
  });
  for (const [name, expectedType] of [
    ["statements", "incident.statement"],
    ["corrections", "incident.correct"],
  ]) {
    const references = new Set();
    core[name].forEach((item, index) => {
      const label = `packet-core.${name}[${index}]`;
      exact(item, new Set(["receipt_path", "attestation_hash"]), label);
      const path = safePath(item.receipt_path, `${label}.receipt_path`);
      digest(item.attestation_hash, `${label}.attestation_hash`);
      if (
        !timelineHashByPath.has(path)
        || !timelineHashes.has(item.attestation_hash)
        || timelineHashByPath.get(path) !== item.attestation_hash
      ) {
        throw new Malformed(`${label} must reference one timeline receipt`);
      }
      if (timelineTypesByPath.get(path) !== expectedType) {
        throw new Malformed(
          `${label} must reference an ${expectedType} timeline receipt`,
        );
      }
      const reference = keyString([path, item.attestation_hash]);
      if (references.has(reference)) {
        throw new Malformed(`duplicate ${name.slice(0, -1)} reference`);
      }
      references.add(reference);
    });
  }
  if (core.witnesses.length > 1) {
    throw new Malformed(
      "packet-core.witnesses supports at most one witness reference in this draft",
    );
  }
  const witnessIds = new Set();
  core.witnesses.forEach((item, index) => {
    const label = `packet-core.witnesses[${index}]`;
    exact(item, new Set(["witness_id", "artifact_id", "root_ref"]), label);
    const witnessId = string(item.witness_id, `${label}.witness_id`);
    if (witnessIds.has(witnessId)) throw new Malformed("duplicate witness id");
    witnessIds.add(witnessId);
    const artifactId = string(item.artifact_id, `${label}.artifact_id`);
    if (!artifacts.has(artifactId)) {
      throw new Malformed(`${label}.artifact_id references an unknown artifact`);
    }
    if (artifacts.get(artifactId).role !== "witness") {
      throw new Malformed(`${label} artifact role must be 'witness'`);
    }
    string(item.root_ref, `${label}.root_ref`);
  });
  const fileNames = new Set(files.keys());
  if (
    [...fileNames].some((name) => !declared.has(name))
    || [...declared].some((name) => !fileNames.has(name))
  ) {
    throw new Malformed("packet declared-file mismatch");
  }

  const errors = [];
  const warnings = [];
  let artifactFailed = false;
  let traceFailed = false;
  for (const [artifactId, item] of artifacts) {
    if (item.disclosure_state !== "released") continue;
    const raw = files.get(item.path);
    if (raw.length !== item.byte_length || hashBytes(raw) !== item.sha256) {
      artifactFailed = true;
      traceFailed ||= item.role === "trace";
      errors.push(`artifact ${artifactId} byte length or digest mismatch`);
    }
  }

  const roles = new Map();
  const roleIssuers = new Set();
  let roleFailed = false;
  core.roles.forEach((item, index) => {
    exact(item, new Set(["role", "issuer"]), `roles[${index}]`);
    const role = string(item.role, "role");
    const issuer = string(item.issuer, "issuer");
    if (roles.has(role) || roleIssuers.has(issuer)) {
      throw new Malformed("duplicate role or cross-role issuer");
    }
    roles.set(role, issuer);
    roleIssuers.add(issuer);
    if (!accepted.get(role)?.has(issuer)) {
      roleFailed = true;
      errors.push(`packet role ${role} is not accepted out of band`);
    }
  });

  let receiptFailed = false;
  let signatureFailed = false;
  let authorityFailed = false;
  let occurrenceFailed = false;
  let timelineByteFailed = false;
  const receiptErrorPaths = new Set();
  const receiptErrorsByPath = new Map();
  const recordReceiptError = (path, problem) => {
    if (!receiptErrorsByPath.has(path)) receiptErrorsByPath.set(path, []);
    receiptErrorsByPath.get(path).push(problem);
  };
  const receipts = core.timeline.map((item) => {
    const raw = files.get(item.receipt_path);
    if (raw.length !== item.byte_length || hashBytes(raw) !== item.sha256) {
      timelineByteFailed = true;
      receiptFailed = true;
      receiptErrorPaths.add(item.receipt_path);
      const problem = `${item.receipt_path}: exact bytes do not match packet timeline commitment`;
      recordReceiptError(item.receipt_path, problem);
      errors.push(problem);
    }
    try {
      const receipt = validateReceipt(
        raw, item.receipt_path, accepted, teamRoles, item.action_type,
      );
      if (receipt.hashes.attestation !== item.attestation_hash) {
        throw new Malformed("timeline attestation hash mismatch");
      }
      const role = receipt.action.subject.issuer_role;
      if (roles.get(role) !== receipt.signature.issuer) {
        throw new Malformed("receipt signer does not equal packet-declared role");
      }
      return receipt;
    } catch (error) {
      receiptFailed = true;
      receiptErrorPaths.add(item.receipt_path);
      const proofFailures = proofFailureDimensions(raw, error.message);
      signatureFailed ||= proofFailures.signature;
      authorityFailed ||= proofFailures.authority;
      occurrenceFailed ||= proofFailures.occurrence;
      roleFailed ||= proofFailures.role;
      if (error.message.includes("authority")) authorityFailed = true;
      if (error.message.includes("issuer") || error.message.includes("role")) {
        roleFailed = true;
      }
      const problem = `${item.receipt_path}: ${error.message}`;
      recordReceiptError(item.receipt_path, problem);
      errors.push(problem);
      return null;
    }
  });

  let publishFailed = false;
  let publish = null;
  try {
    publish = validateReceipt(
      files.get(PUBLISH_FILE), PUBLISH_FILE, accepted, teamRoles,
      "incident.packet.publish",
    );
    const subject = publish.action.subject;
    if (
      subject.packet_id !== core.packet_id
      || subject.packet_core_hash !== hashJson(core)
      || canonical(subject.component_hashes)
        !== canonical(Object.fromEntries(COMPONENTS.map((name) => [name, hashJson(core[name])])))
      || roles.get(subject.issuer_role) !== publish.signature.issuer
    ) {
      throw new Malformed("publish receipt does not bind packet core and publisher");
    }
  } catch (error) {
    publishFailed = true;
    receiptFailed = true;
    const proofFailures = proofFailureDimensions(
      files.get(PUBLISH_FILE), error.message,
    );
    signatureFailed ||= proofFailures.signature;
    authorityFailed ||= proofFailures.authority;
    occurrenceFailed ||= proofFailures.occurrence;
    roleFailed ||= proofFailures.role;
    if (error.message.includes("authority")) authorityFailed = true;
    if (error.message.includes("issuer") || error.message.includes("role")) {
      roleFailed = true;
    }
    errors.push(`${PUBLISH_FILE}: ${error.message}`);
  }

  let timelineFailed = timelineByteFailed;
  let lineageFailed = false;
  const attestations = new Map();
  receipts.forEach((receipt, index) => {
    if (receipt) attestations.set(receipt.hashes.attestation, [index, receipt]);
  });
  const mandates = new Map();
  receipts.forEach((receipt, index) => {
    if (
      receipt?.action.type === "eval.run.authorize"
      && !receiptErrorPaths.has(core.timeline[index].receipt_path)
    ) {
      mandates.set(receipt.hashes.attestation, [index, receipt]);
    }
  });
  for (const [mandateIndex, mandate] of mandates.values()) {
    if (canonical(mandate.action.subject.role_issuers) !== canonical(Object.fromEntries(roles))) {
      timelineFailed = true;
      authorityFailed = true;
      errors.push("eval.run.authorize role_issuers does not equal packet-core roles");
    }
    if (receiptErrorPaths.has(core.timeline[mandateIndex].receipt_path)) {
      authorityFailed = true;
    }
  }
  receipts.forEach((receipt, index) => {
    if (!receipt) return;
    if (receiptErrorPaths.has(core.timeline[index].receipt_path)) return;
    const { type } = receipt.action;
    const subject = receipt.action.subject;
    let mandate = null;
    if (
      ["capability.decide", "capability.observe", "trajectory.decide", "incident.handoff"].includes(type)
    ) {
      const resolved = mandates.get(subject.mandate_ref);
      if (!resolved || resolved[0] >= index) {
        timelineFailed = true;
        authorityFailed = true;
        errors.push("mandate_ref does not resolve");
      } else {
        mandate = resolved[1];
        if (receiptErrorPaths.has(core.timeline[resolved[0]].receipt_path)) {
          authorityFailed = true;
          errors.push(
            "mandate_ref does not resolve to an accepted attestation",
          );
        }
      }
    }
    if (
      ["incident.statement", "incident.handoff"].includes(type)
      && subject.incident_id !== core.incident_id
    ) {
      timelineFailed = true;
      errors.push("incident_id does not match packet-core");
    }
    if (type === "capability.observe") {
      if (mandate && subject.run_id !== mandate.action.subject.run_id) {
        authorityFailed = true;
        errors.push(
          "capability.observe run_id does not equal the referenced mandate run_id",
        );
      }
      const parent = attestations.get(subject.decision_attestation);
      if (
        !parent || parent[0] >= index || parent[1].action.type !== "capability.decide"
        || receiptErrorPaths.has(core.timeline[parent[0]].receipt_path)
        || parent[1].action.subject.run_id !== subject.run_id
        || parent[1].action.subject.request_id !== subject.request_id
        || parent[1].action.subject.decision !== "PERMIT"
        || parent[1].action.subject.protocol !== subject.protocol
        || parent[1].action.subject.mandate_ref !== subject.mandate_ref
      ) {
        lineageFailed = true;
      if (
        parent
        && (
          parent[1].action.subject.mandate_ref !== subject.mandate_ref
          || receiptErrorPaths.has(core.timeline[parent[0]].receipt_path)
        )
      ) {
          authorityFailed = true;
        }
        errors.push("observation decision parent is missing or mismatched");
      }
    } else if (type === "capability.decide" && mandate) {
      const mandateSubject = mandate.action.subject;
      if (subject.run_id !== mandateSubject.run_id) {
        authorityFailed = true;
        errors.push(
          "capability.decide run_id does not equal the referenced mandate run_id",
        );
      }
      if (subject.policy_digest !== mandateSubject.policy_digest) {
        authorityFailed = true;
        errors.push(
          "capability.decide policy_digest does not equal the referenced mandate policy_digest",
        );
      }
      const claimed = instant(
        receipt.claimed_at, "capability.decide.claimed_at",
      );
      const validFrom = instant(
        mandateSubject.valid_from, "eval.run.authorize.valid_from",
      );
      const validUntil = instant(
        mandateSubject.valid_until, "eval.run.authorize.valid_until",
      );
      if (!(validFrom <= claimed && claimed <= validUntil)) {
        authorityFailed = true;
        errors.push(
          "capability.decide claimed_at is outside the mandate's claimed validity interval",
        );
      }
    } else if (type === "trajectory.decide") {
      const expected = receipts.slice(0, index).flatMap((prior, priorIndex) => (
        prior
        && ["capability.decide", "capability.observe"].includes(prior.action.type)
        && prior.action.subject.run_id === subject.run_id
        && !receiptErrorPaths.has(core.timeline[priorIndex].receipt_path)
          ? [prior.hashes.attestation] : []
      ));
      const invalidSameRun = receipts.slice(0, index).some(
        (prior, priorIndex) => prior
          && ["capability.decide", "capability.observe"].includes(prior.action.type)
          && prior.action.subject.run_id === subject.run_id
          && receiptErrorPaths.has(core.timeline[priorIndex].receipt_path),
      );
      if (canonical(subject.ordered_lineage) !== canonical(expected)) {
        lineageFailed = true;
        errors.push("trajectory ordered_lineage mismatch");
      }
      if (invalidSameRun) {
        lineageFailed = true;
        errors.push(
          "trajectory lineage contains a same-run receipt that did not pass accepted attestation verification",
        );
      }
      if (mandate) {
        const mandateSubject = mandate.action.subject;
        if (subject.run_id !== mandateSubject.run_id) {
          authorityFailed = true;
          errors.push(
            "trajectory.decide run_id does not equal the referenced mandate run_id",
          );
        }
        if (subject.policy_digest !== mandateSubject.policy_digest) {
          authorityFailed = true;
          errors.push(
            "trajectory.decide policy_digest does not equal the referenced mandate policy_digest",
          );
        }
      }
    } else if (type === "incident.handoff") {
      const expectedStatements = core.statements.map(
        (item) => item.attestation_hash,
      );
      const statementIndexes = expectedStatements.flatMap(
        (attestation) => attestations.has(attestation)
          ? [attestations.get(attestation)[0]] : [],
      );
      if (
        canonical(subject.statement_refs) !== canonical(expectedStatements)
        || statementIndexes.length !== expectedStatements.length
        || statementIndexes.some((statementIndex) => statementIndex >= index)
        || statementIndexes.some(
          (statementIndex) => receiptErrorPaths.has(
            core.timeline[statementIndex].receipt_path,
          ),
        )
      ) {
        timelineFailed = true;
        errors.push(
          "incident.handoff statement_refs do not equal the ordered named prior statement attestations",
        );
      }
      if (subject.timeline_hash !== hashJson(core.timeline.slice(0, index))) {
        timelineFailed = true;
        errors.push("incident.handoff timeline_hash does not recompute");
      }
      if (subject.coverage_hash !== coverageCommitment(core, artifacts)) {
        timelineFailed = true;
        errors.push("incident.handoff coverage_hash does not recompute");
      }
      const prior = core.timeline.slice(0, index).map(
        (item) => item.attestation_hash,
      );
      if (canonical(subject.parent_refs) !== canonical(prior)) {
        timelineFailed = true;
        errors.push(
          "incident.handoff parent_refs do not equal the exact ordered prior timeline attestations",
        );
      }
    }
  });

  const projections = new Map();
  const duplicates = new Map();
  const projectionInvalid = new Map();
  const reportedInvalidPaths = new Set();
  const recordProjectionInvalid = (key, value) => {
    if (!projectionInvalid.has(key)) projectionInvalid.set(key, []);
    projectionInvalid.get(key).push(value);
  };
  for (const [index, receipt] of receipts.entries()) {
    if (!receipt) continue;
    const item = projection(receipt);
    if (!item) continue;
    const key = keyString(item.key);
    const path = core.timeline[index].receipt_path;
    if (receiptErrorPaths.has(path)) {
      reportedInvalidPaths.add(path);
      recordProjectionInvalid(key, {
        source: path,
        reason: receiptErrorsByPath.get(path).join("; "),
      });
      continue;
    }
    if (!projections.has(key)) projections.set(key, new Map());
    if (!duplicates.has(key)) duplicates.set(key, new Set());
    const bucket = projections.get(key);
    const duplicateIds = duplicates.get(key);
    const id = item.value.observation_id;
    if (bucket.has(id) || duplicateIds.has(id)) {
      const previous = bucket.get(id);
      bucket.delete(id);
      duplicateIds.add(id);
      if (previous) {
        recordProjectionInvalid(key, {
          source: previous.path,
          reason: `duplicate accepted receipt projection for ${JSON.stringify(id)}`,
        });
      }
      recordProjectionInvalid(key, {
        source: path,
        reason: `duplicate accepted receipt projection for ${JSON.stringify(id)}`,
      });
    } else {
      bucket.set(id, { value: item.value, path });
    }
  }
  for (const path of receiptErrorPaths) {
    if (reportedInvalidPaths.has(path)) continue;
    let item = null;
    try {
      item = projection(loadJson(files.get(path), path));
    } catch {
      item = null;
    }
    if (item) {
      recordProjectionInvalid(keyString(item.key), {
        source: path,
        reason: receiptErrorsByPath.get(path).join("; "),
      });
    }
  }

  const denominatorRefs = new Map();
  const coverageRefs = new Map();
  const denominatorPhaseCounts = new Map();
  for (const item of core.denominators) {
    exact(item, new Set([
      "anchor_id", "phase", "protocol", "artifact_id", "provenance",
      "checkpoint_attestation",
    ]), "denominator ref");
    string(item.anchor_id, "denominator.anchor_id");
    if (!["decision", "effect"].includes(item.phase)) {
      throw new Malformed("denominator.phase is unsupported");
    }
    if (!["http", "mcp"].includes(item.protocol)) {
      throw new Malformed("denominator.protocol is unsupported");
    }
    const key = keyString([item.anchor_id, item.phase, item.protocol]);
    if (denominatorRefs.has(key)) throw new Malformed("duplicate denominator key");
    digest(item.checkpoint_attestation, "denominator.checkpoint_attestation");
    denominatorRefs.set(key, item);
    if (!denominatorPhaseCounts.has(item.protocol)) {
      denominatorPhaseCounts.set(item.protocol, { decision: 0, effect: 0 });
    }
    denominatorPhaseCounts.get(item.protocol)[item.phase] += 1;
  }
  if ([...denominatorPhaseCounts.values()].some(
    (counts) => counts.decision !== 1 || counts.effect !== 1,
  )) {
    throw new Malformed(
      "each represented protocol must declare exactly one decision "
      + "denominator and one effect denominator",
    );
  }
  const capabilityProtocols = new Set(receipts.flatMap((receipt) => (
    receipt
    && ["capability.decide", "capability.observe"].includes(receipt.action.type)
      ? [receipt.action.subject.protocol] : []
  )));
  const denominatorProtocols = new Set(
    [...denominatorRefs.values()].map((item) => item.protocol),
  );
  if (
    capabilityProtocols.size !== denominatorProtocols.size
    || [...capabilityProtocols].some(
      (protocol) => !denominatorProtocols.has(protocol),
    )
  ) {
    timelineFailed = true;
    errors.push(
      "capability receipt protocols and denominator protocols must match",
    );
  }
  const namedStatementHashes = new Set(
    core.statements.map((item) => item.attestation_hash),
  );
  const handoffIndexes = receipts.flatMap(
    (receipt, index) => receipt?.action.type === "incident.handoff" ? [index] : [],
  );
  const invalidDenominatorCheckpoints = new Map();
  for (const [keyText, item] of denominatorRefs) {
    const key = JSON.parse(keyText);
    const resolved = attestations.get(item.checkpoint_attestation);
    const problems = [];
    if (
      !resolved
      || !namedStatementHashes.has(item.checkpoint_attestation)
      || resolved[1].action.type !== "incident.statement"
    ) {
      problems.push(
        "checkpoint_attestation must resolve to a named incident.statement",
      );
    } else {
      const [checkpointIndex, checkpoint] = resolved;
      const checkpointPath = core.timeline[checkpointIndex].receipt_path;
      if (receiptErrorPaths.has(checkpointPath)) {
        problems.push(
          "denominator checkpoint receipt did not pass accepted "
          + "attestation verification",
        );
      }
      const subject = checkpoint.action.subject;
      const expectedRole = key[1] === "effect"
        ? "target" : key[2] === "http" ? "gateway" : "boundary";
      if (subject.issuer_role !== expectedRole) {
        problems.push(
          `denominator checkpoint issuer_role must match phase topology (${JSON.stringify(expectedRole)})`,
        );
      }
      const expectedTopic = denominatorCheckpointTopic(...key);
      if (subject.topic !== expectedTopic) {
        problems.push(
          `denominator checkpoint topic must equal ${JSON.stringify(expectedTopic)}`,
        );
      }
      if (subject.epistemic_status !== "observed") {
        problems.push(
          "denominator checkpoint epistemic_status must be 'observed'",
        );
      }
      if (subject.claim !== "ORDERED_DENOMINATOR_SNAPSHOT") {
        problems.push(
          "denominator checkpoint claim must be 'ORDERED_DENOMINATOR_SNAPSHOT'",
        );
      }
      const artifactHash = artifacts.get(item.artifact_id).sha256;
      if (canonical(subject.evidence_refs) !== canonical([artifactHash])) {
        problems.push(
          "denominator checkpoint evidence_refs must contain only "
          + "the exact denominator artifact byte SHA",
        );
      }
      const carriedEvidence = checkpoint.evidence_refs;
      if (
        !Array.isArray(carriedEvidence)
        || carriedEvidence.length !== 1
        || carriedEvidence[0] === null
        || typeof carriedEvidence[0] !== "object"
        || carriedEvidence[0].hash !== artifactHash
        || carriedEvidence[0].grounding !== "self_asserted"
      ) {
        problems.push(
          "denominator checkpoint receipt evidence must contain exactly one "
          + "self_asserted reference to the denominator bytes",
        );
      }
      if (handoffIndexes.some((index) => checkpointIndex >= index)) {
        problems.push(
          "denominator checkpoint must occur before every incident.handoff",
        );
      }
    }
    if (problems.length) {
      timelineFailed = true;
      invalidDenominatorCheckpoints.set(keyText, problems);
      errors.push(...problems.map(
        (problem) => `denominator checkpoint ${keyText}: ${problem}`,
      ));
    }
  }
  for (const item of core.coverage) {
    exact(item, new Set([
      "anchor_id", "phase", "protocol", "artifact_id", "minimum_verification_depth",
    ]), "coverage ref");
    const key = keyString([item.anchor_id, item.phase, item.protocol]);
    if (coverageRefs.has(key)) throw new Malformed("duplicate coverage key");
    coverageRefs.set(key, item);
  }
  if (
    denominatorRefs.size !== coverageRefs.size
    || [...denominatorRefs.keys()].some((key) => !coverageRefs.has(key))
  ) {
    throw new Malformed("denominator and coverage keys differ");
  }
  if (denominatorRefs.size === 0) {
    throw new Malformed(
      "packet-core must declare at least one denominator and coverage anchor",
    );
  }

  const coverageResults = [];
  let coverageFailed = false;
  let coverageNotComputed = false;
  for (const keyText of [...denominatorRefs.keys()].sort()) {
    const key = JSON.parse(keyText);
    const denominatorRef = denominatorRefs.get(keyText);
    const coverageRef = coverageRefs.get(keyText);
    const base = {
      anchor_id: key[0], phase: key[1], protocol: key[2],
      provenance: denominatorRef.provenance,
    };
    const groupInvalid = [...(projectionInvalid.get(keyText) ?? [])];
    if (invalidDenominatorCheckpoints.has(keyText)) {
      coverageNotComputed = true;
      coverageResults.push({
        ...base, status: "NOT_COMPUTED", total: 0, receipted: 0,
        covered_ids: [], uncovered_ids: [], phantom_receipt_ids: [],
        invalid_receipts: groupInvalid,
        reasons: invalidDenominatorCheckpoints.get(keyText),
      });
      continue;
    }
    let denominatorArtifact;
    let observations;
    try {
      denominatorArtifact = artifacts.get(denominatorRef.artifact_id);
      if (denominatorArtifact.disclosure_state !== "released") {
        throw new Malformed("denominator artifact is unavailable");
      }
      const denominator = exact(
        loadJson(files.get(denominatorArtifact.path), denominatorArtifact.path),
        new Set(["schema_version", "anchor_id", "phase", "protocol", "observations"]),
        "denominator",
      );
      if (integer(denominator.schema_version, "denominator.schema_version", 1) !== 1) {
        throw new Malformed("denominator.schema_version must be 1");
      }
      if (keyString([denominator.anchor_id, denominator.phase, denominator.protocol]) !== keyText) {
        throw new Malformed("denominator identity mismatch");
      }
      const ids = new Set();
      observations = objects(denominator.observations, "observations").map((observation) => {
        exact(observation, OBSERVATION_FIELDS, "observation");
        string(observation.observation_id, "observation_id");
        if (ids.has(observation.observation_id)) {
          throw new Malformed("duplicate denominator observation id");
        }
        ids.add(observation.observation_id);
        digest(observation.operation_ref, "observation.operation_ref");
        digest(observation.evidence_hash, "observation.evidence_hash");
        if (keyString([observation.anchor_id, observation.phase, observation.protocol]) !== keyText) {
          throw new Malformed("observation belongs to another coverage group");
        }
        return observation;
      });
      if (observations.length === 0) {
        coverageNotComputed = true;
        coverageResults.push({
          ...base, status: "NOT_COMPUTED", total: 0, receipted: 0,
          covered_ids: [], uncovered_ids: [], phantom_receipt_ids: [],
          invalid_receipts: groupInvalid,
          reasons: ["denominator.observations must contain at least one event"],
        });
        continue;
      }
    } catch (error) {
      coverageNotComputed = true;
      coverageResults.push({
        ...base, status: "NOT_COMPUTED", total: 0, receipted: 0,
        covered_ids: [], uncovered_ids: [], phantom_receipt_ids: [],
        invalid_receipts: groupInvalid, reasons: [error.message],
      });
      continue;
    }
    const bucket = projections.get(keyText) ?? new Map();
    const ids = new Set(observations.map((row) => row.observation_id));
    const invalid = [...(projectionInvalid.get(keyText) ?? [])];
    const covered = [];
    for (const row of observations) {
      const candidate = bucket.get(row.observation_id);
      if (!candidate) continue;
      if (canonical(candidate.value) === canonical(row)) {
        covered.push(row.observation_id);
      } else {
        invalid.push({
          source: candidate.path,
          reason: "receipt projection does not equal the denominator observation",
        });
      }
    }
    const coveredSet = new Set(covered);
    const uncovered = observations.filter(
      (row) => !coveredSet.has(row.observation_id),
    ).map((row) => row.observation_id);
    const phantom = [...bucket.keys()].filter((id) => !ids.has(id)).sort();
    const recomputed = {
      schema_version: 1,
      anchor_id: key[0],
      phase: key[1],
      protocol: key[2],
      denominator_sha256: denominatorArtifact.sha256,
      minimum_verification_depth: "attestation",
      total: observations.length,
      receipted: covered.length,
      covered_ids: covered,
      uncovered_ids: uncovered,
      phantom_receipt_ids: phantom,
      invalid_receipts: invalid,
    };
    let status = "COMPUTED";
    const reasons = [];
    const coverageArtifact = artifacts.get(coverageRef.artifact_id);
    if (coverageArtifact.disclosure_state !== "released") {
      coverageNotComputed = true;
      coverageResults.push({
        ...base, status: "NOT_COMPUTED",
        total: observations.length, receipted: covered.length,
        covered_ids: covered, uncovered_ids: uncovered,
        phantom_receipt_ids: phantom, invalid_receipts: invalid,
        reasons: ["declared coverage artifact is unavailable"],
      });
      continue;
    }
    try {
      const declaredCoverage = exact(
        loadJson(files.get(coverageArtifact.path), coverageArtifact.path),
        new Set(Object.keys(recomputed)),
        "coverage report",
      );
      if (integer(declaredCoverage.schema_version, "coverage.schema_version", 1) !== 1) {
        throw new Malformed("coverage.schema_version must be 1");
      }
      integer(declaredCoverage.total, "coverage.total");
      integer(declaredCoverage.receipted, "coverage.receipted");
      if (canonical(declaredCoverage) !== canonical(recomputed)) {
        throw new Malformed(
          "declared coverage report does not equal the recomputed report",
        );
      }
    } catch (error) {
      status = "FAILED";
      coverageFailed = true;
      reasons.push(error.message);
      errors.push(`coverage ${keyText}: ${error.message}`);
    }
    coverageResults.push({
      ...base, status, total: observations.length, receipted: covered.length,
      covered_ids: covered, uncovered_ids: uncovered,
      phantom_receipt_ids: phantom, invalid_receipts: invalid, reasons,
    });
    if (uncovered.length) {
      const warning = "one or more denominator observations have no accepted covering receipt";
      if (!warnings.includes(warning)) warnings.push(warning);
    }
  }
  if (coverageResults.some((item) => item.status === "COMPUTED")) {
    warnings.push(
      "denominator checkpoints authenticate what the path-separated "
      + "observer reported; they do not establish completeness or "
      + "organizational independence",
    );
  }

  const statements = new Map();
  for (const item of core.statements) {
    const receipt = receipts.find(
      (candidate) => candidate?.hashes.attestation === item.attestation_hash,
    );
    statements.set(
      item.attestation_hash,
      receiptErrorPaths.has(item.receipt_path) ? null : receipt ?? null,
    );
  }
  let redactionFailed = false;
  for (const redaction of core.redactions) {
    try {
      const recordArtifact = artifacts.get(redaction.record_artifact_id);
      const sourceArtifact = artifacts.get(redaction.source_artifact_id);
      const releasedArtifact = artifacts.get(redaction.released_artifact_id);
      const record = exact(
        loadJson(files.get(recordArtifact.path), recordArtifact.path),
        new Set([
          "schema_version", "redaction_id", "source_sha256",
          "released_sha256", "tool", "tool_version", "rules_hash",
          "disclosure_safety",
        ]),
        "redaction record",
      );
      if (integer(record.schema_version, "redaction record.schema_version", 1) !== 1) {
        throw new Malformed("redaction record.schema_version must be 1");
      }
      const expectedRecord = {
        schema_version: 1,
        redaction_id: redaction.redaction_id,
        source_sha256: sourceArtifact.sha256,
        released_sha256: releasedArtifact.sha256,
        tool: redaction.tool,
        tool_version: redaction.tool_version,
        rules_hash: redaction.rules_hash,
        disclosure_safety: "NOT_COMPUTED",
      };
      if (canonical(record) !== canonical(expectedRecord)) {
        throw new Malformed(
          "redaction record does not equal core and artifact bindings",
        );
      }
      const reviewerRef = redaction.reviewer_statement_ref;
      if (!statements.has(reviewerRef)) {
        throw new Malformed("redaction reviewer statement does not resolve");
      }
      const reviewer = statements.get(reviewerRef);
      const reviewerItem = core.statements.find(
        (item) => item.attestation_hash === reviewerRef,
      );
      if (!reviewer || receiptErrorPaths.has(reviewerItem.receipt_path)) {
        throw new Malformed(
          "redaction reviewer statement is not an accepted receipt",
        );
      }
      const subject = reviewer.action.subject;
      const recordHash = recordArtifact.sha256;
      const evidence = reviewer.evidence_refs;
      if (
        subject.issuer_role !== "reviewer"
        || subject.topic !== `redaction:${redaction.redaction_id}`
        || subject.epistemic_status !== "observed"
        || subject.claim !== "REDACTION_BINDING_REVIEWED"
        || canonical(subject.evidence_refs) !== canonical([recordHash])
        || evidence.length !== 1
        || evidence[0].hash !== recordHash
        || evidence[0].grounding !== "self_asserted"
      ) {
        throw new Malformed(
          "redaction reviewer statement must bind only the exact redaction record",
        );
      }
    } catch (error) {
      redactionFailed = true;
      errors.push(error.message);
    }
  }

  const topics = new Map();
  for (const receipt of statements.values()) {
    if (!receipt) continue;
    const subject = receipt.action.subject;
    if (!topics.has(subject.topic)) topics.set(subject.topic, new Set());
    topics.get(subject.topic).add(JSON.stringify([subject.claim, subject.epistemic_status]));
  }
  const conflicts = [...topics].filter(([, claims]) => claims.size > 1).map(([topic]) => topic).sort();
  if (conflicts.length) {
    warnings.push("conflicting party statements are preserved without adjudication");
  }

  const temporal = {
    claimed_at: receipts.flatMap((receipt, index) => (
      receipt && !receiptErrorPaths.has(core.timeline[index].receipt_path)
        ? [receipt.claimed_at] : []
    )),
    received_at: [],
    witnessed_at: [],
    anchored_before: [],
  };
  if (publish) temporal.claimed_at.push(publish.claimed_at);
  let witnessInclusion = core.witnesses.length ? "VERIFIED" : "NOT_COMPUTED";
  let witnessTrust = core.witnesses.length ? "TRUSTED" : "NOT_COMPUTED";
  for (const witnessRef of core.witnesses) {
    const artifact = artifacts.get(witnessRef.artifact_id);
    if (artifact.disclosure_state !== "released") {
      witnessInclusion = "UNAVAILABLE";
      witnessTrust = "UNAVAILABLE";
      continue;
    }
    try {
      const witness = exact(loadJson(files.get(artifact.path), artifact.path), new Set([
        "schema_version", "witness_id", "root_ref", "received_at",
        "witnessed_at", "anchored_before", "included_attestation_hashes",
        "checkpoint_hash", "proof",
      ]), "witness");
      if (integer(witness.schema_version, "witness.schema_version", 1) !== 1) {
        throw new Malformed("witness.schema_version must be 1");
      }
      if (
        witness.witness_id !== witnessRef.witness_id
        || witness.root_ref !== witnessRef.root_ref
      ) {
        throw new Malformed("witness identity does not match packet-core");
      }
      const includedValues = strings(
        witness.included_attestation_hashes,
        "witness.included_attestation_hashes",
        true,
      );
      const included = new Set(includedValues);
      for (const value of included) digest(value, "witness.included_attestation_hashes[]");
      const rootTrusted = trustedRoots.has(witness.root_ref);
      if (!rootTrusted) witnessTrust = "UNTRUSTED";
      const receivedAt = string(witness.received_at, "witness.received_at");
      const witnessedAt = string(witness.witnessed_at, "witness.witnessed_at");
      const receivedInstant = instant(receivedAt, "witness.received_at");
      const witnessedInstant = instant(witnessedAt, "witness.witnessed_at");
      if (receivedInstant > witnessedInstant) {
        throw new Malformed("witness.received_at must not follow witnessed_at");
      }
      const expectedCheckpoint = hashJson({
        witness_id: witness.witness_id,
        root_ref: witness.root_ref,
        received_at: receivedAt,
        witnessed_at: witnessedAt,
        included_attestation_hashes: includedValues,
      });
      if (witness.checkpoint_hash !== expectedCheckpoint) {
        throw new Malformed("witness checkpoint_hash does not recompute");
      }
      const proof = exact(witness.proof, PROOF_FIELDS, "witness proof");
      const witnessIssuer = proof.issuer;
      validateProof(
        proof, "witness-checkpoint", expectedCheckpoint, witnessIssuer,
      );
      if (witnessIssuer !== roles.get("witness")) {
        throw new Malformed(
          "witness checkpoint issuer does not equal packet-core witness role",
        );
      }
      if (!accepted.get("witness")?.has(witnessIssuer)) {
        throw new Malformed("witness checkpoint issuer is not accepted out of band");
      }
      if ([...timelineHashes].some((item) => !included.has(item))) {
        throw new Malformed("witness does not include every timeline attestation");
      }
      if (witness.anchored_before !== null) {
        throw new Malformed(
          "anchored_before requires separate external anchor evidence; "
          + "this checkpoint schema does not carry one",
        );
      }
      if (!rootTrusted) {
        if (witnessInclusion === "VERIFIED") witnessInclusion = "NOT_COMPUTED";
        warnings.push("packet-carried witness root is not trusted out of band");
        continue;
      }
      temporal.received_at.push(receivedAt);
      temporal.witnessed_at.push(witnessedAt);
    } catch (error) {
      witnessInclusion = "FAILED";
      errors.push(error.message);
    }
  }

  const statementIndexes = new Map(
    core.statements.filter(
      (item) => attestations.has(item.attestation_hash)
        && !receiptErrorPaths.has(item.receipt_path),
    ).map(
      (item) => [item.attestation_hash, attestations.get(item.attestation_hash)[0]],
    ),
  );
  const releasedPacketRefs = new Set();
  let correctionInvalid = false;
  for (const artifact of artifacts.values()) {
    if (
      artifact.role !== "packet-core"
      || artifact.disclosure_state !== "released"
    ) continue;
    try {
      const referencedCore = exact(
        loadJson(files.get(artifact.path), artifact.path),
        CORE_FIELDS,
        "referenced packet-core",
      );
      if (referencedCore.profile !== PROFILE) {
        throw new Malformed("referenced packet-core profile mismatch");
      }
      COMPONENTS.forEach(
        (name) => objects(referencedCore[name], `referenced packet-core.${name}`),
      );
      validateCoreComponents(referencedCore);
      releasedPacketRefs.add(hashJson(referencedCore));
    } catch (error) {
      correctionInvalid = true;
      errors.push(
        `released packet-core artifact ${artifact.artifact_id} `
        + `is not a conforming packet core: ${error.message}`,
      );
    }
  }

  let correctionStatus = "NONE";
  const corrections = new Map();
  for (const item of core.corrections) {
    const resolved = attestations.get(item.attestation_hash);
    const receipt = resolved?.[1];
    if (
      !receipt
      || receiptErrorPaths.has(core.timeline[resolved[0]].receipt_path)
    ) {
      correctionInvalid = true;
      errors.push("correction receipt does not resolve");
      continue;
    }
    const subject = receipt.action.subject;
    const correctionIndex = resolved[0];
    const kind = subject.supersedes_kind;
    const supersedes = subject.supersedes_ref;
    const replacement = subject.replacement_ref;
    if (kind === "statement") {
      const supersedesIndex = statementIndexes.get(supersedes);
      const replacementIndex = statementIndexes.get(replacement);
      if (supersedesIndex === undefined || supersedesIndex >= correctionIndex) {
        correctionInvalid = true;
        errors.push(
          "incident.correct statement supersedes_ref must resolve "
          + "to a prior named statement",
        );
      }
      if (replacementIndex === undefined || replacementIndex >= correctionIndex) {
        correctionInvalid = true;
        errors.push(
          "incident.correct statement replacement_ref must resolve "
          + "to a prior named statement",
        );
      }
    } else {
      const predecessorRefs = new Set(releasedPacketRefs);
      if (core.supersedes_core_hash !== null) {
        predecessorRefs.add(core.supersedes_core_hash);
      }
      if (!predecessorRefs.has(supersedes)) {
        correctionInvalid = true;
        errors.push(
          "incident.correct packet supersedes_ref must resolve to "
          + "packet-core.supersedes_core_hash or a released packet-core artifact",
        );
      }
      if (!releasedPacketRefs.has(replacement)) {
        correctionInvalid = true;
        errors.push(
          "incident.correct packet replacement_ref must resolve to "
          + "a released packet-core artifact",
        );
      }
      if (supersedes === replacement) {
        correctionInvalid = true;
        errors.push(
          "incident.correct packet supersedes_ref and replacement_ref must differ",
        );
      }
    }
    const key = JSON.stringify([kind, supersedes]);
    if (!corrections.has(key)) corrections.set(key, new Set());
    corrections.get(key).add(replacement);
  }
  correctionStatus = correctionInvalid
    ? "FAILED"
    : [...corrections.values()].some((values) => values.size > 1)
      ? "FORKED" : corrections.size ? "PRESENT" : "NONE";
  if (correctionStatus === "FORKED") {
    warnings.push(
      "correction fork preserved; verifier does not select latest by actor time",
    );
  }

  const coverageStatus = coverageFailed
    ? "FAILED" : coverageNotComputed ? "NOT_COMPUTED" : "COMPUTED";
  const hardContentFailure = publishFailed || artifactFailed || receiptFailed;
  const hardFailure = [
    hardContentFailure, roleFailed, authorityFailed,
    timelineFailed,
    lineageFailed, redactionFailed, coverageFailed,
    witnessInclusion === "FAILED", correctionStatus === "FAILED",
  ].some(Boolean);
  const suppressed = [];
  if (hardFailure) suppressed.push("reliance");
  if (hardContentFailure) {
    suppressed.push(
      "disclosure_safety",
      "claims depending on failed packet, artifact, or receipt integrity",
    );
  }
  if (timelineFailed || lineageFailed) {
    suppressed.push("timeline and lineage dependent conclusions");
  }
  if (coverageStatus !== "COMPUTED") {
    suppressed.push("coverage conclusions");
  }
  if (redactionFailed) {
    suppressed.push("redaction and disclosure conclusions");
  }
  if (core.witnesses.length && witnessInclusion !== "VERIFIED") {
    suppressed.push("witness-derived temporal labels");
  }
  if (correctionStatus === "FAILED") {
    suppressed.push("correction resolution");
  }
  return {
    profile: PROFILE,
    packet_integrity: publishFailed ? "FAILED" : "VERIFIED",
    artifact_integrity: artifactFailed ? "FAILED" : "VERIFIED",
    receipt_integrity: receiptFailed ? "FAILED" : "VERIFIED",
    signature_status: signatureFailed ? "FAILED" : "VERIFIED",
    issuer_authenticity: roleFailed ? "FAILED" : "VERIFIED",
    authority_binding: authorityFailed ? "FAILED" : "VERIFIED",
    occurrence_binding: occurrenceFailed ? "FAILED" : "VERIFIED",
    timeline_integrity: timelineFailed ? "FAILED" : "VERIFIED",
    lineage_integrity: lineageFailed ? "FAILED" : "VERIFIED",
    trace_integrity: traceFailed
      ? "FAILED"
      : core.artifacts.some((item) => item.role === "trace" && item.disclosure_state === "released")
        ? "VERIFIED" : "NOT_COMPUTED",
    redaction_binding: redactionFailed ? "FAILED" : "VERIFIED",
    coverage_status: coverageStatus,
    coverage: coverageResults,
    denominator_provenance: core.denominators.length
      && core.denominators.every((item) => item.provenance === "SEPARATELY_CONTROLLED")
      ? "SEPARATELY_CONTROLLED"
      : core.denominators.length
        && core.denominators.every((item) => item.provenance === "PATH_SEPARATE_TEAM_CONTROLLED")
        ? "PATH_SEPARATE_TEAM_CONTROLLED" : "MIXED_OR_UNKNOWN",
    witness_inclusion: witnessInclusion,
    witness_root_trust: witnessTrust,
    temporal_evidence: temporal,
    party_conflicts: conflicts,
    correction_status: correctionStatus,
    unavailable_artifacts: unavailable,
    suppressed_conclusions: suppressed,
    disclosure_safety: "NOT_COMPUTED",
    reliance: "NOT_COMPUTED",
    errors,
    warnings,
    exit_code: hardFailure ? 1 : 0,
  };
}

function main() {
  const args = process.argv.slice(2);
  const contextIndex = args.indexOf("--context");
  if (contextIndex < 0 || !args[0] || !args[contextIndex + 1]) {
    process.stderr.write("usage: verify_packet.mjs PACKET --context CONTEXT\n");
    return 2;
  }
  try {
    const result = verify(args[0], args[contextIndex + 1]);
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return result.exit_code;
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      profile: PROFILE,
      malformed: true,
      error: error.message,
      exit_code: 2,
    })}\n`);
    return 2;
  }
}

process.exitCode = main();
