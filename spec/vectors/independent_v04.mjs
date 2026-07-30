#!/usr/bin/env node
// Zero-Bulla, dependency-free Node checker for the ActionReceipt v0.4 vector.

import { createHash, createPublicKey, verify as verifySignature } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const SAFE = 9007199254740991;
const PROFILE = "bulla-jcs-int/1";
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function canon(value) {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error("nonportable number");
    return String(value);
  }
  if (typeof value === "string") {
    for (const unit of value.split("")) {
      const code = unit.charCodeAt(0);
      if (code >= 0xd800 && code <= 0xdfff) {
        const roundtrip = JSON.parse(JSON.stringify(value));
        if (roundtrip !== value) throw new Error("lone surrogate");
      }
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canon).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort((a, b) => a.localeCompare(b, "en", { usage: "sort", sensitivity: "variant" }));
    // JS default UTF-16 ordering is the RFC 8785 member-name ordering.
    keys.sort();
    return `{${keys.map((key) => `${canon(key)}:${canon(value[key])}`).join(",")}}`;
  }
  throw new Error("unsupported canonical value");
}

const H = (value) => `sha256:${createHash("sha256").update(canon(value), "utf8").digest("hex")}`;

function envelope(receipt) {
  const out = { deed_schema: receipt.mandate.deed_schema ?? "0.2" };
  if (receipt.mandate.authority) out.authority = receipt.mandate.authority;
  if (receipt.mandate.bounds) out.bounds = receipt.mandate.bounds;
  if (Object.keys(receipt.remedy).length) out.recourse = receipt.remedy;
  if (receipt.retention.record) out.retention_class = receipt.retention.record;
  if (receipt.retention.disclosure) out.disclosure_class = receipt.retention.disclosure;
  return out;
}

function hashes(receipt) {
  const preimage = {
    schema_version: "0.4", kind: receipt.kind, action: receipt.action,
    diagnostic_ref: receipt.diagnostic_ref, evidence_refs: receipt.evidence_refs,
    anchor_ref: receipt.anchor_ref, canonicalization: PROFILE,
  };
  if (receipt.conventions.length) preimage.conventions = receipt.conventions;
  const content = H(preimage);
  const event = H({ content_hash: content, event_id: receipt.event_id, claimed_at: receipt.claimed_at });
  const env = envelope(receipt);
  const authorization = H({ event_hash: event, envelope_hash: H(env) });
  const attestation = H({
    content_hash: content, signature: receipt.signature, event_hash: event,
    occurrence: receipt.occurrence, recourse_envelope: env,
    authorization: receipt.authorization,
  });
  const logLeaf = `sha256:${createHash("sha256").update(Buffer.concat([Buffer.from([0]), Buffer.from(attestation)])).digest("hex")}`;
  return { content, event, authorization, attestation, log_leaf: logLeaf };
}

function base58(value) {
  let number = 0n;
  for (const char of value) number = number * 58n + BigInt(B58.indexOf(char));
  let hex = number.toString(16);
  if (hex.length % 2) hex = `0${hex}`;
  const body = number === 0n ? Buffer.alloc(0) : Buffer.from(hex, "hex");
  return Buffer.concat([Buffer.alloc(value.match(/^1*/)[0].length), body]);
}

function verifyProof(proof, purpose, digest) {
  if (proof.purpose !== purpose || proof.issuer !== proof.verificationMethod) return false;
  const raw = base58(proof.verificationMethod.replace(/^did:key:z/, ""));
  if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) return false;
  const spki = Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), raw.subarray(2)]);
  const key = createPublicKey({ key: spki, format: "der", type: "spki" });
  const preimage = JSON.stringify({ context: "bulla-proof", schema: "0.4", purpose, digest }, Object.keys({ context: 1, digest: 1, purpose: 1, schema: 1 }).sort());
  return verifySignature(null, Buffer.from(preimage), key, Buffer.from(proof.proofValue, "base64"));
}

const defaultVector = fileURLToPath(new URL("./v04-occurrence-bound.json", import.meta.url));
const path = process.argv[2] ?? defaultVector;
const receipt = JSON.parse(readFileSync(path, "utf8"));
if (receipt.schema_version !== "0.4" || receipt.canonicalization !== PROFILE) throw new Error("not v0.4");
const computed = hashes(receipt);
const digestOk = ["content", "event", "attestation", "log_leaf"].every((key) => receipt.hashes[key] === computed[key]);
const proofsOk = verifyProof(receipt.signature, "content", computed.content)
  && verifyProof(receipt.occurrence, "occurrence", computed.event)
  && verifyProof(receipt.authorization, "authorization", computed.authorization);
console.log(JSON.stringify({ digest_ok: digestOk, proofs_ok: proofsOk, hashes: computed }, null, 2));
process.exitCode = digestOk && proofsOk ? 0 : 1;
