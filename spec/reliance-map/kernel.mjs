/** Browser-safe, dependency-free kernel for bulla.reliance-map/0.1-experimental. */

export const GRAPH_PROFILE = 'bulla.reliance-map/0.1-experimental'
export const LEDGER_PROFILE = 'bulla.reliance-correction-ledger/0.1-experimental'
export const CONTEXT_PROFILE = 'bulla.reliance-map-context/0.1-experimental'
export const REPORT_PROFILE = 'bulla.reliance-map-report/0.1-experimental'

const CORRECTION_ACTION = 'reliance.correct'
const HASH_RE = /^sha256:[0-9a-f]{64}$/
const NODE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/
const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const NODE_KINDS = new Set(['HANDOFF', 'CAPABILITY', 'EVIDENCE', 'RELIANCE', 'ACTION'])
const RESULT_KINDS = new Set(['RELIANCE', 'ACTION'])
const MAX_GRAPH_DEPTH = 64
const MAX_PATH_STEPS = 262144
const RELATIONS = {
  SUPPORTS: [new Set(['HANDOFF', 'CAPABILITY', 'EVIDENCE']), new Set(['RELIANCE'])],
  DERIVES: [new Set(['RELIANCE']), new Set(['RELIANCE'])],
  INFORMS: [new Set(['RELIANCE']), new Set(['ACTION'])],
}
const RECEIPT_FIELDS = [
  'schema_version', 'canonicalization', 'kind', 'action', 'diagnostic_ref',
  'evidence_refs', 'anchor_ref', 'mandate', 'remedy', 'retention', 'stake',
  'conventions', 'signature', 'occurrence', 'authorization', 'event_id',
  'claimed_at', 'producer', 'hashes',
]
const PROOF_FIELDS = ['type', 'purpose', 'issuer', 'verificationMethod', 'proofValue']
const encoder = new TextEncoder()
const decoder = new TextDecoder('utf-8', { fatal: true })

export class RelianceMapFailure extends Error {}

class StrictJsonParser {
  constructor(raw, label) {
    if (!(raw instanceof Uint8Array) || raw.byteLength > 32 * 1024 * 1024) throw new RelianceMapFailure(`${label} exceeds its byte limit`)
    this.text = decoder.decode(raw); this.label = label; this.index = 0; this.nodes = 0
  }
  parse() { const value = this.value(1); this.space(); if (this.index !== this.text.length) throw new RelianceMapFailure(`${this.label} has trailing content`); return value }
  space() { while (/[\t\n\r ]/.test(this.text[this.index] ?? '')) this.index += 1 }
  count(depth) { this.nodes += 1; if (this.nodes > 500000 || depth > 24) throw new RelianceMapFailure(`${this.label} exceeds structural limits`) }
  value(depth) {
    this.space(); this.count(depth); const character = this.text[this.index]
    if (character === '{') return this.object(depth)
    if (character === '[') return this.array(depth)
    if (character === '"') return this.string()
    if (this.text.startsWith('true', this.index)) { this.index += 4; return true }
    if (this.text.startsWith('false', this.index)) { this.index += 5; return false }
    if (this.text.startsWith('null', this.index)) { this.index += 4; return null }
    return this.number()
  }
  object(depth) {
    this.index += 1; const result = Object.create(null); const keys = new Set(); this.space()
    if (this.text[this.index] === '}') { this.index += 1; return result }
    while (true) {
      this.space(); if (this.text[this.index] !== '"') throw new RelianceMapFailure(`${this.label} has an invalid object`)
      const key = this.string(); if (keys.has(key)) throw new RelianceMapFailure(`${this.label} has duplicate member ${key}`); keys.add(key); this.space()
      if (this.text[this.index] !== ':') throw new RelianceMapFailure(`${this.label} has an invalid object`)
      this.index += 1; result[key] = this.value(depth + 1); this.space(); const separator = this.text[this.index++]
      if (separator === '}') return result
      if (separator !== ',') throw new RelianceMapFailure(`${this.label} has an invalid object`)
    }
  }
  array(depth) {
    this.index += 1; const result = []; this.space()
    if (this.text[this.index] === ']') { this.index += 1; return result }
    while (true) {
      result.push(this.value(depth + 1)); this.space(); const separator = this.text[this.index++]
      if (separator === ']') return result
      if (separator !== ',') throw new RelianceMapFailure(`${this.label} has an invalid array`)
    }
  }
  string() {
    const start = this.index; this.index += 1; let escaped = false
    while (this.index < this.text.length) {
      const code = this.text.charCodeAt(this.index)
      if (!escaped && code === 0x22) {
        this.index += 1; let value
        try { value = JSON.parse(this.text.slice(start, this.index)) } catch { throw new RelianceMapFailure(`${this.label} has an invalid string`) }
        if (encoder.encode(value).byteLength > 4096) throw new RelianceMapFailure(`${this.label} has an oversized string`)
        for (let index = 0; index < value.length; index += 1) {
          const unit = value.charCodeAt(index)
          if (unit >= 0xd800 && unit <= 0xdbff) {
            const next = value.charCodeAt(index + 1)
            if (!(next >= 0xdc00 && next <= 0xdfff)) throw new RelianceMapFailure(`${this.label} has a lone surrogate`)
            index += 1
          } else if (unit >= 0xdc00 && unit <= 0xdfff) throw new RelianceMapFailure(`${this.label} has a lone surrogate`)
        }
        return value
      }
      if (!escaped && code < 0x20) throw new RelianceMapFailure(`${this.label} has a control character`)
      if (escaped) escaped = false; else if (code === 0x5c) escaped = true
      this.index += 1
    }
    throw new RelianceMapFailure(`${this.label} has an unterminated string`)
  }
  number() {
    const match = /^-?(?:0|[1-9]\d*)/.exec(this.text.slice(this.index))
    if (!match) throw new RelianceMapFailure(`${this.label} has an invalid number`)
    this.index += match[0].length
    if (/[.eE]/.test(this.text[this.index] ?? '')) throw new RelianceMapFailure(`${this.label} contains a non-integer`)
    const value = Number(match[0]); if (!Number.isSafeInteger(value)) throw new RelianceMapFailure(`${this.label} contains an unsafe integer`)
    return value
  }
}

function bytes(value) { return value instanceof Uint8Array ? value : encoder.encode(value) }
function parse(raw, label) { return new StrictJsonParser(bytes(raw), label).parse() }
function exact(value, fields, label) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new RelianceMapFailure(`${label} must be an object`)
  const actual = Object.keys(value).sort(); const expected = [...fields].sort()
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new RelianceMapFailure(`${label} has unexpected fields`)
  return value
}
function string(value, label) { if (typeof value !== 'string' || value.length === 0 || value.trim() !== value) throw new RelianceMapFailure(`${label} must be a non-empty trimmed string`); return value }
function nodeId(value, label) { string(value, label); if (!NODE_ID_RE.test(value)) throw new RelianceMapFailure(`${label} is not a stable node identifier`); return value }
function digestValue(value, label) { if (typeof value !== 'string' || !HASH_RE.test(value)) throw new RelianceMapFailure(`${label} is not a SHA-256 digest`); return value }
function safeInteger(value, label) { if (!Number.isSafeInteger(value) || value < 0) throw new RelianceMapFailure(`${label} is not a non-negative safe integer`); return value }
function canonical(value) {
  if (value === null) return 'null'
  if (value === true) return 'true'
  if (value === false) return 'false'
  if (typeof value === 'number') { if (!Number.isSafeInteger(value)) throw new RelianceMapFailure('unsafe canonical integer'); return String(value) }
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${canonical(key)}:${canonical(value[key])}`).join(',')}}`
  throw new RelianceMapFailure('unsupported canonical value')
}
function concat(...values) { const size = values.reduce((total, value) => total + value.byteLength, 0); const output = new Uint8Array(size); let offset = 0; for (const value of values) { output.set(value, offset); offset += value.byteLength } return output }
function hex(raw) { return [...raw].map((value) => value.toString(16).padStart(2, '0')).join('') }
async function hashBytes(raw) { return `sha256:${hex(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', bytes(raw))))}` }
async function hashJson(value) { return hashBytes(encoder.encode(canonical(value))) }
function decodeBase64(value) { const raw = atob(value); return Uint8Array.from(raw, (character) => character.charCodeAt(0)) }

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
function didKey(value) {
  if (typeof value !== 'string' || !value.startsWith('did:key:z')) throw new RelianceMapFailure('issuer is not did:key')
  const input = value.slice(9); let number = 0n
  for (const character of input) { const index = B58.indexOf(character); if (index < 0) throw new RelianceMapFailure('invalid did:key'); number = number * 58n + BigInt(index) }
  const reversed = []; while (number > 0n) { reversed.push(Number(number & 255n)); number >>= 8n } reversed.reverse()
  const leading = input.length - input.replace(/^1+/, '').length
  const raw = Uint8Array.from([...new Array(leading).fill(0), ...reversed])
  if (raw.length !== 34 || raw[0] !== 0xed || raw[1] !== 0x01) throw new RelianceMapFailure('did:key is not Ed25519')
  return raw.slice(2)
}
async function verifyProof(proof, purpose, digest, issuer) {
  exact(proof, PROOF_FIELDS, `${purpose} proof`)
  if (proof.type !== 'bulla/ed25519-2026' || proof.purpose !== purpose || proof.issuer !== issuer || proof.verificationMethod !== issuer) throw new RelianceMapFailure(`${purpose} proof identity mismatch`)
  const signature = decodeBase64(proof.proofValue); if (signature.length !== 64) throw new RelianceMapFailure(`${purpose} proof length mismatch`)
  const key = await globalThis.crypto.subtle.importKey('raw', didKey(issuer), { name: 'Ed25519' }, false, ['verify'])
  const message = encoder.encode(canonical({ context: 'bulla-proof', schema: '0.4', purpose, digest }))
  if (!(await globalThis.crypto.subtle.verify('Ed25519', key, signature, message))) throw new RelianceMapFailure(`${purpose} proof signature failed`)
}
function recourseEnvelope(receipt) {
  const result = { deed_schema: receipt.mandate.deed_schema || '0.2' }
  if (receipt.mandate.authority) result.authority = receipt.mandate.authority
  if (receipt.mandate.bounds) result.bounds = receipt.mandate.bounds
  if (Object.keys(receipt.remedy).length) result.recourse = receipt.remedy
  if (receipt.retention.record) result.retention_class = receipt.retention.record
  if (receipt.retention.disclosure) result.disclosure_class = receipt.retention.disclosure
  return result
}
async function receiptHashes(receipt) {
  const preimage = {
    schema_version: '0.4', canonicalization: 'bulla-jcs-int/1', kind: 'action_receipt',
    action: receipt.action, diagnostic_ref: receipt.diagnostic_ref,
    evidence_refs: receipt.evidence_refs, anchor_ref: receipt.anchor_ref,
  }
  if (receipt.conventions.length) preimage.conventions = receipt.conventions
  const content = await hashJson(preimage)
  const event = await hashJson({ content_hash: content, event_id: receipt.event_id, claimed_at: receipt.claimed_at })
  const envelope = recourseEnvelope(receipt)
  const authorization = await hashJson({ event_hash: event, envelope_hash: await hashJson(envelope) })
  const attestation = await hashJson({ content_hash: content, signature: receipt.signature, event_hash: event, occurrence: receipt.occurrence, recourse_envelope: envelope, authorization: receipt.authorization })
  const log_leaf = await hashBytes(concat(Uint8Array.of(0), encoder.encode(attestation)))
  return { content, event, attestation, authorization, log_leaf }
}

function validateStringSet(value, label) {
  if (!Array.isArray(value)) throw new RelianceMapFailure(`${label} must be an array`)
  const result = value.map((item, index) => string(item, `${label}[${index}]`))
  if (JSON.stringify(result) !== JSON.stringify([...result].sort()) || new Set(result).size !== result.length) throw new RelianceMapFailure(`${label} must be sorted and unique`)
  return result
}

function validateGraph(value) {
  const graph = exact(value, ['profile', 'graph_id', 'nodes', 'edges'], 'graph')
  if (graph.profile !== GRAPH_PROFILE) throw new RelianceMapFailure('unsupported reliance-map profile')
  string(graph.graph_id, 'graph.graph_id')
  if (!Array.isArray(graph.nodes) || graph.nodes.length < 1 || graph.nodes.length > 16384) throw new RelianceMapFailure('graph node count is empty or over limit')
  if (!Array.isArray(graph.edges) || graph.edges.length > 65536) throw new RelianceMapFailure('graph edge count is malformed or over limit')
  const nodes = new Map(); const digests = new Set(); const order = []
  for (let index = 0; index < graph.nodes.length; index += 1) {
    const node = exact(graph.nodes[index], ['node_id', 'kind', 'artifact_digest', 'ancestry_complete'], `graph.nodes[${index}]`)
    const identifier = nodeId(node.node_id, `graph.nodes[${index}].node_id`)
    if (nodes.has(identifier)) throw new RelianceMapFailure(`duplicate graph node ${identifier}`)
    if (!NODE_KINDS.has(node.kind)) throw new RelianceMapFailure(`unsupported node kind ${node.kind}`)
    digestValue(node.artifact_digest, `${identifier}.artifact_digest`)
    if (digests.has(node.artifact_digest)) throw new RelianceMapFailure('graph artifact digests must uniquely identify one node')
    if (typeof node.ancestry_complete !== 'boolean') throw new RelianceMapFailure(`${identifier}.ancestry_complete must be Boolean`)
    nodes.set(identifier, node); digests.add(node.artifact_digest); order.push(identifier)
  }
  if (JSON.stringify(order) !== JSON.stringify([...order].sort())) throw new RelianceMapFailure('graph nodes must be sorted by node_id')
  const edges = new Set(); const edgeOrder = []; const outgoing = new Map(); const indegree = new Map([...nodes.keys()].map((identifier) => [identifier, 0]))
  for (let index = 0; index < graph.edges.length; index += 1) {
    const edge = exact(graph.edges[index], ['source', 'target', 'relation'], `graph.edges[${index}]`)
    const source = nodeId(edge.source, `graph.edges[${index}].source`); const target = nodeId(edge.target, `graph.edges[${index}].target`); string(edge.relation, `graph.edges[${index}].relation`)
    if (!nodes.has(source) || !nodes.has(target)) throw new RelianceMapFailure('graph edge references an unknown node')
    const signature = `${source}\u0000${target}\u0000${edge.relation}`
    if (edges.has(signature)) throw new RelianceMapFailure('graph contains a duplicate edge')
    const rule = RELATIONS[edge.relation]; if (!rule || !rule[0].has(nodes.get(source).kind) || !rule[1].has(nodes.get(target).kind)) throw new RelianceMapFailure('graph edge violates its typed signature')
    edges.add(signature); edgeOrder.push([source, target, edge.relation]); if (!outgoing.has(source)) outgoing.set(source, []); outgoing.get(source).push(target); indegree.set(target, indegree.get(target) + 1)
  }
  const sortedEdges = [...edgeOrder].sort((left, right) => left[0].localeCompare(right[0]) || left[1].localeCompare(right[1]) || left[2].localeCompare(right[2]))
  if (JSON.stringify(edgeOrder) !== JSON.stringify(sortedEdges)) throw new RelianceMapFailure('graph edges must be sorted by source, target, and relation')
  for (const targets of outgoing.values()) targets.sort()
  const queue = [...indegree].filter(([, degree]) => degree === 0).map(([identifier]) => identifier).sort(); const graphDepth = new Map([...nodes.keys()].map((identifier) => [identifier, 0])); let visited = 0
  for (let cursor = 0; cursor < queue.length; cursor += 1) { const current = queue[cursor]; visited += 1; for (const target of outgoing.get(current) ?? []) { graphDepth.set(target, Math.max(graphDepth.get(target), graphDepth.get(current) + 1)); if (graphDepth.get(target) > MAX_GRAPH_DEPTH) throw new RelianceMapFailure('reliance map exceeds graph depth limit'); indegree.set(target, indegree.get(target) - 1); if (indegree.get(target) === 0) queue.push(target) } }
  if (visited !== nodes.size) throw new RelianceMapFailure('reliance map must be acyclic')
  return graph
}

function validateContext(value) {
  const context = exact(value, ['profile', 'authority_epoch', 'accepted_graph_digests', 'correction_authorities', 'policy_hash'], 'context')
  if (context.profile !== CONTEXT_PROFILE) throw new RelianceMapFailure('unsupported context profile')
  safeInteger(context.authority_epoch, 'context.authority_epoch')
  const graphs = validateStringSet(context.accepted_graph_digests, 'context.accepted_graph_digests'); for (const value of graphs) digestValue(value, 'accepted graph digest')
  const authorities = validateStringSet(context.correction_authorities, 'context.correction_authorities'); if (!authorities.length) throw new RelianceMapFailure('no correction authority supplied')
  digestValue(context.policy_hash, 'context.policy_hash')
  return context
}

async function validateCorrection(receipt, index, previous, context, graphDigest) {
  exact(receipt, RECEIPT_FIELDS, `correction ${index}`)
  if (receipt.schema_version !== '0.4' || receipt.canonicalization !== 'bulla-jcs-int/1' || receipt.kind !== 'action_receipt') throw new RelianceMapFailure(`correction ${index} is not ActionReceipt v0.4`)
  if (typeof receipt.event_id !== 'string' || !UUID_V4_RE.test(receipt.event_id)) throw new RelianceMapFailure(`correction ${index} event_id is not a canonical lowercase UUIDv4`)
  if (typeof receipt.claimed_at !== 'string' || receipt.claimed_at.length === 0) throw new RelianceMapFailure(`correction ${index} claimed_at is required`)
  const action = exact(receipt.action, ['type', 'subject'], `correction ${index}.action`)
  if (action.type !== CORRECTION_ACTION) throw new RelianceMapFailure(`correction ${index} has the wrong action`)
  const subject = exact(action.subject, ['profile', 'sequence', 'previous_correction', 'target_digest', 'replacement_digest', 'reason_digest', 'authority_epoch'], `correction ${index}.subject`)
  if (subject.profile !== GRAPH_PROFILE || safeInteger(subject.sequence, 'correction sequence') !== index || subject.previous_correction !== previous || safeInteger(subject.authority_epoch, 'correction authority_epoch') !== context.authority_epoch) throw new RelianceMapFailure(`correction ${index} order or epoch mismatch`)
  digestValue(subject.target_digest, 'correction target'); digestValue(subject.replacement_digest, 'correction replacement'); if (subject.replacement_digest === subject.target_digest) throw new RelianceMapFailure('correction replacement must differ from its target'); digestValue(subject.reason_digest, 'correction reason')
  const anchor = exact(receipt.anchor_ref, ['relation', 'graph_digest'], 'anchor_ref')
  if (JSON.stringify(exact(receipt.diagnostic_ref, ['status'], 'diagnostic_ref')) !== JSON.stringify({ status: 'not_applicable' }) || !Array.isArray(receipt.evidence_refs) || receipt.evidence_refs.length || anchor.relation !== 'reliance_map' || anchor.graph_digest !== graphDigest || !Array.isArray(receipt.conventions) || receipt.conventions.length || receipt.stake !== null) throw new RelianceMapFailure(`correction ${index} uses unsupported or cross-graph fields`)
  const mandate = exact(receipt.mandate, ['authority', 'bounds'], 'mandate'); const authority = exact(mandate.authority, ['principal', 'policy', 'delegation'], 'mandate.authority'); const bounds = exact(mandate.bounds, ['scope'], 'mandate.bounds')
  const issuer = receipt.signature?.issuer
  if (!context.correction_authorities.includes(issuer) || authority.principal !== issuer || authority.policy !== context.policy_hash || JSON.stringify(authority.delegation) !== '[]' || bounds.scope !== `profile:${GRAPH_PROFILE};action:${CORRECTION_ACTION}`) throw new RelianceMapFailure(`correction ${index} authority is not accepted`)
  const remedy = exact(receipt.remedy, ['challenge_window', 'forum', 'remedies'], 'remedy'); const forum = exact(remedy.forum, ['log_endpoint', 'trusted_root_ref'], 'remedy.forum')
  if (remedy.challenge_window !== 'checkpoint:reliance-map-correction' || forum.log_endpoint !== 'https://glyphstandard.com/evidence#reliance-map' || !HASH_RE.test(forum.trusted_root_ref) || !Array.isArray(remedy.remedies) || remedy.remedies.length !== 1) throw new RelianceMapFailure(`correction ${index} remedy envelope differs`)
  const remedyItem = exact(remedy.remedies[0], ['anchor', 'rung', 'verifier'], 'remedy.remedies[0]')
  if (remedyItem.anchor !== 'forum:reliance-map' || remedyItem.rung !== 'challenge' || remedyItem.verifier !== 'recompute exact declared descendants') throw new RelianceMapFailure(`correction ${index} remedy differs`)
  const retention = exact(receipt.retention, ['disclosure', 'record'], 'retention'); if (retention.disclosure !== 'public' || retention.record !== 'authority-permanent') throw new RelianceMapFailure(`correction ${index} retention differs`)
  if (receipt.producer === null || typeof receipt.producer !== 'object' || Array.isArray(receipt.producer)) throw new RelianceMapFailure(`correction ${index} producer must be an object`)
  const computed = await receiptHashes(receipt); exact(receipt.hashes, ['content', 'event', 'attestation', 'log_leaf'], 'hashes')
  for (const name of ['content', 'event', 'attestation', 'log_leaf']) if (receipt.hashes[name] !== computed[name]) throw new RelianceMapFailure(`correction ${index} ${name} hash mismatch`)
  await verifyProof(receipt.signature, 'content', computed.content, issuer); await verifyProof(receipt.occurrence, 'occurrence', computed.event, issuer); await verifyProof(receipt.authorization, 'authorization', computed.authorization, issuer)
  return { correction: computed.attestation, target_digest: subject.target_digest, replacement_digest: subject.replacement_digest }
}

async function validateLedger(value, context, graphDigest) {
  const ledger = exact(value, ['profile', 'corrections'], 'ledger')
  if (ledger.profile !== LEDGER_PROFILE || !Array.isArray(ledger.corrections) || ledger.corrections.length > 16) throw new RelianceMapFailure('unsupported or oversized correction ledger')
  const corrections = []; let previous = null
  for (let index = 0; index < ledger.corrections.length; index += 1) { const correction = await validateCorrection(ledger.corrections[index], index, previous, context, graphDigest); corrections.push(correction); previous = correction.correction }
  return corrections
}

export async function computeRelianceMap(graphInput, ledgerInput, contextInput) {
  const graph = validateGraph(parse(graphInput, 'reliance map')); const ledger = parse(ledgerInput, 'correction ledger'); const context = validateContext(parse(contextInput, 'verification context'))
  const graphDigest = await hashJson(graph); if (!context.accepted_graph_digests.includes(graphDigest)) throw new RelianceMapFailure('reliance map is not accepted by the external context')
  const corrections = await validateLedger(ledger, context, graphDigest)
  const nodes = new Map(graph.nodes.map((node) => [node.node_id, node])); const byDigest = new Map(graph.nodes.map((node) => [node.artifact_digest, node.node_id])); const outgoing = new Map(); const incoming = new Map()
  for (const identifier of nodes.keys()) { outgoing.set(identifier, []); incoming.set(identifier, []) }
  for (const edge of graph.edges) { outgoing.get(edge.source).push(edge.target); incoming.get(edge.target).push(edge.source) }
  for (const targets of outgoing.values()) targets.sort()
  const indegree = new Map([...nodes.keys()].map((identifier) => [identifier, incoming.get(identifier).length])); const queue = [...indegree].filter(([, degree]) => degree === 0).map(([identifier]) => identifier).sort(); const topological = []
  for (let cursor = 0; cursor < queue.length; cursor += 1) { const current = queue[cursor]; topological.push(current); for (const target of outgoing.get(current)) { indegree.set(target, indegree.get(target) - 1); if (indegree.get(target) === 0) queue.push(target) } }
  const incomplete = new Map(); for (const identifier of topological) incomplete.set(identifier, !nodes.get(identifier).ancestry_complete || incoming.get(identifier).some((parent) => incomplete.get(parent)))
  const statuses = new Map(); const paths = new Map()
  for (const [identifier, node] of nodes) if (RESULT_KINDS.has(node.kind)) { statuses.set(identifier, incomplete.get(identifier) ? 'UNDETERMINED' : 'NOT_AFFECTED'); paths.set(identifier, []) }
  let pathSteps = 0
  for (const correction of corrections) {
    const start = byDigest.get(correction.target_digest); if (!start) throw new RelianceMapFailure('correction target is absent from the accepted map')
    const pathQueue = [start]; const predecessor = new Map([[start, null]])
    for (let cursor = 0; cursor < pathQueue.length; cursor += 1) { const current = pathQueue[cursor]; if (statuses.has(current)) { const path = []; for (let step = current; step !== null; step = predecessor.get(step)) path.push(step); path.reverse(); pathSteps += path.length; if (pathSteps > MAX_PATH_STEPS) throw new RelianceMapFailure('reliance map exceeds path-certificate limit'); statuses.set(current, 'AFFECTED'); paths.get(current).push({ correction: correction.correction, nodes: path }) } for (const target of outgoing.get(current)) if (!predecessor.has(target)) { predecessor.set(target, current); pathQueue.push(target) } }
  }
  const results = [...statuses.keys()].sort().map((identifier) => { const status = statuses.get(identifier); const node = nodes.get(identifier); return { node_id: identifier, node_kind: node.kind, artifact_digest: node.artifact_digest, status, paths: paths.get(identifier), conditional_action: status === 'AFFECTED' ? 'RECHECK_REQUIRED' : status === 'UNDETERMINED' ? 'NO_AUTOMATIC_CLEARANCE' : null } })
  const count = (status) => results.filter((item) => item.status === status).length
  const body = {
    profile: REPORT_PROFILE, graph_id: graph.graph_id, graph_digest: graphDigest, graph_acceptance: 'ACCEPTED', ledger_digest: await hashJson(ledger),
    summary: { declared_decisions: graph.nodes.filter((node) => RESULT_KINDS.has(node.kind)).length, graph_nodes: graph.nodes.length, graph_edges: graph.edges.length, affected: count('AFFECTED'), not_affected: count('NOT_AFFECTED'), undetermined: count('UNDETERMINED') },
    results,
    limitations: [
      'AFFECTED identifies a declared dependency path that requires rechecking; it is not a finding that an action was unsafe.',
      'NOT_AFFECTED is relative to the externally accepted graph and requires declared complete ancestry.',
      'A correction does not roll back an action, establish worldly truth, or authorize a consequence.',
    ],
  }
  return { ...body, report_digest: await hashJson(body) }
}
