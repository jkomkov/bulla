"""The bulla__deed_* meta-tools + the registry additions that back them.

Covers the ladder this sprint climbs:
  - emit   = the in-loop, signed, logged record (the moat's data engine)
  - verify = the omission rung: DEMAND inclusion -> refuse the unlogged
  - lookup = the content-address dividend: deeds-by-composition (factual)
plus the pollution guard (only issuer-signed deeds log), composition binding,
the cross-machine HTTP path, and graceful degradation. No network except a
local in-thread HTTP registry for the cross-machine test.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

from bulla.certificate import certify, sign_certificate, to_dict
from bulla.identity import LocalEd25519Signer
from bulla.live import LiveSession
from bulla.live_proxy import BullaLiveProxy, TelemetrySink, _meta_tool_definitions
from bulla.model import Composition, Edge, SemanticDimension, ToolSpec
from bulla.registry import Deed, DeedLog, verify_inclusion_record


# ── fixtures / helpers ───────────────────────────────────────────────

def _seam_session(name: str = "deed-e2e", tool_prefix: str = "fs") -> LiveSession:
    """A live session carrying a small real composition with one hidden seam."""
    live = LiveSession(name=name)
    producer = ToolSpec(
        name=f"{tool_prefix}__read",
        internal_state=("path", "data"),
        observable_schema=("path", "data"),
    )
    consumer = ToolSpec(
        name="gh__write",
        internal_state=("path", "data"),
        observable_schema=("path", "data"),
    )
    edge = Edge(
        from_tool=f"{tool_prefix}__read", to_tool="gh__write",
        dimensions=(SemanticDimension(
            name="path_convention", from_field="path", to_field="path"
        ),),
    )
    live.session.add_tools_and_edges(tools=[producer, consumer], edges=[edge])
    return live


def _proxy(signer=None, registry=None, session=None, telemetry_path=None):
    tel = TelemetrySink(path=telemetry_path)
    p = BullaLiveProxy(backends=[], telemetry=tel, signer=signer, registry=registry)
    p.telemetry.open()
    p.session = session or _seam_session()
    p._namespaced_tools = _meta_tool_definitions()
    return p


def _call(proxy, name, arguments):
    resp = asyncio.run(proxy.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }))
    return json.loads(resp["result"]["content"][0]["text"])


# ── the tools exist and are advertised ───────────────────────────────

def test_deed_tools_are_listed():
    names = {t["name"] for t in _meta_tool_definitions()}
    assert {"bulla__deed_emit", "bulla__deed_verify", "bulla__deed_lookup"} <= names


# ── emit: sign + log + inclusion, the banked record ──────────────────

def test_emit_signs_logs_and_returns_inclusion(tmp_path):
    reg = DeedLog(tmp_path / "r.jsonl")
    p = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    out = _call(p, "bulla__deed_emit", {})
    assert out["deed"]["issuer"].startswith("did:key:")
    assert out["deed"]["composition_hash"]                  # the content-address dividend
    assert verify_inclusion_record(out["inclusion_proof"])  # logged + provable
    assert out["root"] == reg.root()
    assert len(reg) == 1
    assert "anchor" in out and "unanchored" in out["anchor"]  # never blocks on OTS


def test_emit_banks_a_record_in_telemetry(tmp_path):
    reg = DeedLog(tmp_path / "r.jsonl")
    tel_path = tmp_path / "events.jsonl"
    p = _proxy(signer=LocalEd25519Signer.generate(), registry=reg, telemetry_path=tel_path)
    out = _call(p, "bulla__deed_emit", {})
    p.telemetry.close()
    events = [json.loads(line) for line in tel_path.read_text().splitlines()]
    emitted = [e for e in events if e.get("event") == "deed_emitted"]
    assert len(emitted) == 1
    rec = emitted[0]
    assert rec["content_hash"] == out["deed"]["content_hash"]
    assert rec["composition_hash"] == out["deed"]["composition_hash"]
    assert "fee" in rec and "disposition" in rec  # {composition, conventions, outcome}


# ── verify: THE OMISSION RUNG ────────────────────────────────────────

def test_verify_logged_proceeds_unlogged_refuses(tmp_path):
    """A emits a deed; B (the relying party) verifies against the registry it
    trusts and PROCEEDS. An unlogged deed -> REFUSE. This is rung 4."""
    reg = DeedLog(tmp_path / "r.jsonl")
    a = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    emitted = _call(a, "bulla__deed_emit", {})

    b = _proxy(registry=reg)  # B trusts this registry; B itself need not sign
    ok = _call(b, "bulla__deed_verify", {"deed": emitted["deed"]})
    assert ok["included"] is True and ok["recommend"] == "proceed"

    unlogged = {"issuer": "did:key:zX", "content_hash": "sha256:x",
                "attestation_hash": "sha256:absent"}
    no = _call(b, "bulla__deed_verify", {"deed": unlogged})
    assert no["included"] is False and no["recommend"] == "refuse"
    assert "unlogged" in no["reason"]


def test_verify_full_cert_checks_integrity_and_authenticity(tmp_path):
    reg = DeedLog(tmp_path / "r.jsonl")
    signer = LocalEd25519Signer.generate()
    a = _proxy(signer=signer, registry=reg)
    # build the same signed cert the proxy logged, to hand B the full certificate
    cert = to_dict(sign_certificate(certify(a.session.composition), signer))
    _call(a, "bulla__deed_emit", {})  # log it

    b = _proxy(registry=reg)
    res = _call(b, "bulla__deed_verify", {"certificate": cert})
    assert res["integrity"] is True
    assert res["authenticity"] is True
    assert res["included"] is True
    assert res["recommend"] == "proceed"


def test_verify_refuses_tampered_full_cert(tmp_path):
    """A logged deed whose certificate is then tampered (same attestation_hash, so
    inclusion still hits) must REFUSE on integrity — included is necessary, not
    sufficient, when the full cert is on the table."""
    reg = DeedLog(tmp_path / "r.jsonl")
    signer = LocalEd25519Signer.generate()
    a = _proxy(signer=signer, registry=reg)
    cert = to_dict(sign_certificate(certify(a.session.composition), signer))
    _call(a, "bulla__deed_emit", {})  # deterministic sign -> same attestation_hash as `cert`
    tampered = dict(cert, subject=dict(cert["subject"], name="EVIL RENAME"))
    b = _proxy(registry=reg)
    res = _call(b, "bulla__deed_verify", {"certificate": tampered})
    assert res["included"] is True       # inclusion alone would have said yes
    assert res["integrity"] is False
    assert res["recommend"] == "refuse"  # integrity dominates


def test_verify_refuses_forged_full_cert(tmp_path):
    """A full cert claiming issuer B but signed by A's key -> authenticity False
    -> refuse (and it was never logged anyway)."""
    reg = DeedLog(tmp_path / "r.jsonl")
    key_a = LocalEd25519Signer.generate()
    key_b = LocalEd25519Signer.generate()
    forging = LocalEd25519Signer(seed=key_a.seed, issuer_override=key_b.issuer)
    forged = to_dict(sign_certificate(certify(_seam_session().composition), forging))
    b = _proxy(registry=reg)
    res = _call(b, "bulla__deed_verify", {"certificate": forged})
    assert res["authenticity"] is False
    assert res["recommend"] == "refuse"


def test_verify_refuses_wrong_composition(tmp_path):
    """Inclusion must bind to the composition asked about — a logged deed for a
    DIFFERENT composition does not license this one."""
    reg = DeedLog(tmp_path / "r.jsonl")
    a = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    emitted = _call(a, "bulla__deed_emit", {})
    b = _proxy(registry=reg)
    res = _call(b, "bulla__deed_verify",
                {"deed": emitted["deed"], "composition_hash": "some-other-composition"})
    assert res["composition_bound"] is False
    assert res["recommend"] == "refuse"


# ── pollution guard: only issuer-signed deeds log ────────────────────

def test_forged_deed_rejected_at_append(tmp_path):
    """A cert claiming issuer B but signed by A's key (verification-method /
    issuer mismatch) must NOT be loggable under B — the pollution guard."""
    comp = _seam_session().composition
    key_a = LocalEd25519Signer.generate()
    key_b = LocalEd25519Signer.generate()
    # claim B's did:key as issuer, but sign with A's key
    forging_signer = LocalEd25519Signer(seed=key_a.seed, issuer_override=key_b.issuer)
    forged = to_dict(sign_certificate(certify(comp), forging_signer))
    assert forged["issuer"]["id"] == key_b.issuer            # it CLAIMS to be B
    with pytest.raises(ValueError):
        Deed.from_certificate(forged)                        # but cannot be logged


def test_tampered_issuer_rejected_at_append(tmp_path):
    """Swapping the issuer on a validly signed cert breaks integrity (issuer is
    in the content-hash preimage) — rejected before it can pollute a victim."""
    comp = _seam_session().composition
    signer = LocalEd25519Signer.generate()
    cert = to_dict(sign_certificate(certify(comp), signer))
    cert["issuer"] = {"type": "did:key", "id": "did:key:zVICTIM"}  # tamper, do not re-sign
    with pytest.raises(ValueError):
        Deed.from_certificate(cert)


# ── lookup: the content-address dividend (factual, not a score) ──────

def test_lookup_returns_deeds_for_current_composition(tmp_path):
    reg = DeedLog(tmp_path / "r.jsonl")
    p = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    emitted = _call(p, "bulla__deed_emit", {})
    out = _call(p, "bulla__deed_lookup", {})  # defaults to current composition
    assert out["composition_hash"] == emitted["deed"]["composition_hash"]
    assert out["n_deeds"] == 1
    assert out["issuers"] == [emitted["deed"]["issuer"]]


def test_lookup_aggregates_distinct_issuers_for_same_composition(tmp_path):
    """Two issuers certify the SAME composition -> both appear. Reputation-by-
    composition is only possible because the content-hash is machine-independent."""
    reg = DeedLog(tmp_path / "r.jsonl")
    sess = _seam_session()
    a = _proxy(signer=LocalEd25519Signer.generate(), registry=reg, session=sess)
    b = _proxy(signer=LocalEd25519Signer.generate(), registry=reg, session=_seam_session())
    ea = _call(a, "bulla__deed_emit", {})
    eb = _call(b, "bulla__deed_emit", {})
    assert ea["deed"]["composition_hash"] == eb["deed"]["composition_hash"]  # same comp
    out = _call(a, "bulla__deed_lookup", {})
    assert out["n_deeds"] == 2
    assert len(out["issuers"]) == 2


# ── PIN THE ROOT: the cross-party trust boundary ─────────────────────
# These are the adversarial tests whose ABSENCE let "trustless" ship hollow:
# they assume the HOST is malicious, which is the entire property.

def _serve(reg):
    from bulla.http_registry import make_server
    srv = make_server(reg, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_remote_host_asserted_root_never_proceeds(tmp_path):
    """A remote registry's bare claim is self-consistent but NOT trusted: a deed
    genuinely in the host's log still yields recommend=refuse without a pinned root —
    you would be trusting the operator. (Last turn this asserted 'proceed'.)"""
    from bulla.http_registry import HttpRegistry

    reg = DeedLog(tmp_path / "r.jsonl")
    a = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    emitted = _call(a, "bulla__deed_emit", {})
    srv, port = _serve(reg)
    try:
        b = _proxy(registry=HttpRegistry(f"http://127.0.0.1:{port}"))
        res = _call(b, "bulla__deed_verify", {"deed": emitted["deed"]})
        assert res["included"] is True              # the host's proof is self-consistent…
        assert res["root_trust"] == "host-asserted"  # …but the root is the host's claim
        assert res["recommend"] == "refuse"          # never proceed on that alone
    finally:
        srv.shutdown()


def test_remote_pinned_root_proceeds(tmp_path):
    """Pin the correct (out-of-band) root and the remote inclusion is trustworthy."""
    from bulla.http_registry import HttpRegistry

    reg = DeedLog(tmp_path / "r.jsonl")
    a = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    emitted = _call(a, "bulla__deed_emit", {})
    real_root = reg.root()
    srv, port = _serve(reg)
    try:
        b = _proxy(registry=HttpRegistry(f"http://127.0.0.1:{port}"))
        res = _call(b, "bulla__deed_verify",
                    {"deed": emitted["deed"], "trusted_root": real_root})
        assert res["root_trust"] == "pinned"
        assert res["recommend"] == "proceed"
    finally:
        srv.shutdown()


def test_proxy_verify_rejects_borrowed_inclusion_from_malicious_host(tmp_path):
    """The inclusion-binding property against a host that controls the inclusion
    channel: it serves the honest root but answers the inclusion query with a valid
    proof for a DIFFERENT real leaf. The proxy binds the proof to the deed's own leaf,
    so bulla__deed_verify REFUSES even with the correct root pinned — the omission rung
    is not borrowable."""
    from bulla.http_registry import HttpRegistry

    reg = DeedLog(tmp_path / "r.jsonl")
    a = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    other = _proxy(signer=LocalEd25519Signer.generate(), registry=reg, session=_seam_session())
    deed_a = _call(a, "bulla__deed_emit", {})["deed"]
    deed_b = _call(other, "bulla__deed_emit", {})["deed"]   # a second real leaf to borrow
    real_root = reg.root()

    class _BorrowingOperator:                 # honest root/by_composition, lying inclusion
        is_remote = True
        def __init__(self, real, borrowed_att):
            self._real, self._borrowed = real, real.inclusion_by_attestation(borrowed_att)
        def __len__(self): return len(self._real)
        def root(self): return self._real.root()
        def by_composition(self, h): return self._real.by_composition(h)
        def inclusion_by_attestation(self, att): return self._borrowed  # always deed_b's proof

    srv, port = _serve(_BorrowingOperator(reg, deed_b["attestation_hash"]))
    try:
        verifier = _proxy(registry=HttpRegistry(f"http://127.0.0.1:{port}"))
        res = _call(verifier, "bulla__deed_verify",
                    {"deed": deed_a, "trusted_root": real_root})
        assert res["included"] is False     # the served proof covers deed_b's leaf, not deed_a's
        assert res["recommend"] == "refuse"
    finally:
        srv.shutdown()


def test_equivocating_host_rejected_when_root_pinned(tmp_path):
    """THE headline test. A malicious host serves a SELF-CONSISTENT fabricated log
    (a deed the attacker chose, NOT in the real log). A verifier pinning the REAL
    root refuses, because the served root differs (equivocation)."""
    from bulla.http_registry import HttpRegistry

    real = DeedLog(tmp_path / "real.jsonl")
    honest = _proxy(signer=LocalEd25519Signer.generate(), registry=real)
    _call(honest, "bulla__deed_emit", {})
    real_root = real.root()  # B trusts this, obtained out of band

    fake = DeedLog(tmp_path / "fake.jsonl")
    attacker = _proxy(signer=LocalEd25519Signer.generate(), registry=fake,
                      session=_seam_session(tool_prefix="evil"))
    evil_deed = _call(attacker, "bulla__deed_emit", {})  # logged ONLY in the fake log
    assert real.root() != fake.root()

    srv, port = _serve(fake)  # the malicious host serves its fabricated tree
    try:
        b = _proxy(registry=HttpRegistry(f"http://127.0.0.1:{port}"))
        res = _call(b, "bulla__deed_verify",
                    {"deed": evil_deed["deed"], "trusted_root": real_root})
        assert res["root_trust"] == "mismatch"   # the host's root != the root B pinned
        assert res["recommend"] == "refuse"      # equivocation caught
    finally:
        srv.shutdown()


def test_verify_inclusion_record_pins_the_root(tmp_path):
    """Unit: without a pin, self-consistency only; with a pin, the served root must
    match, else the record is rejected regardless of an otherwise-valid proof."""
    reg = DeedLog(tmp_path / "r.jsonl")
    reg.append(Deed("did:key:z", "sha256:c", "sha256:a", "comp"))
    proof = reg.inclusion_by_attestation("sha256:a")
    assert verify_inclusion_record(proof) is True
    assert verify_inclusion_record(proof, trusted_root=proof["root"]) is True
    assert verify_inclusion_record(proof, trusted_root="sha256:not-the-root") is False


def test_classify_root_trust_truth_table():
    from bulla.registry import classify_root_trust
    assert classify_root_trust(False, "sha256:r", None, None) == ("own-log", True)
    assert classify_root_trust(True, "sha256:r", None, None) == ("host-asserted", False)
    assert classify_root_trust(True, "sha256:r", "sha256:r", None) == ("pinned", True)
    assert classify_root_trust(True, "sha256:r", "sha256:x", None) == ("mismatch", False)
    assert classify_root_trust(True, None, None, None) == ("none", False)


# ── graceful degradation ─────────────────────────────────────────────

def test_emit_without_signer_errors_without_crashing(tmp_path):
    p = _proxy(registry=DeedLog(tmp_path / "r.jsonl"))  # no signer
    out = _call(p, "bulla__deed_emit", {})
    assert "error" in out
    assert "fee" in _call(p, "bulla__fee", {})  # proxy still serves other tools


def test_verify_without_registry_errors():
    p = _proxy(signer=LocalEd25519Signer.generate())  # no registry
    out = _call(p, "bulla__deed_verify", {"deed": {"attestation_hash": "sha256:x"}})
    assert "error" in out


def test_verify_unreachable_registry_fails_closed():
    """A remote registry that cannot be reached must REFUSE (you can't confirm
    inclusion), not crash or silently proceed."""
    from bulla.http_registry import HttpRegistry

    b = _proxy(registry=HttpRegistry("http://127.0.0.1:1"))  # nothing listening
    res = _call(b, "bulla__deed_verify", {"deed": {"attestation_hash": "sha256:x"}})
    assert res["included"] is False
    assert res["recommend"] == "refuse"


# ── regression locks for the QA-pass fixes ───────────────────────────

def test_emit_works_with_external_issuer(tmp_path):
    """emit must succeed for a non-did:key issuer: it verifies against its OWN
    signing key, not issuer-resolution (which would refuse an external issuer)."""
    reg = DeedLog(tmp_path / "r.jsonl")
    base = LocalEd25519Signer.generate()
    ext = LocalEd25519Signer(seed=base.seed, issuer_override="did:web:example.com:agent")
    p = _proxy(signer=ext, registry=reg)
    out = _call(p, "bulla__deed_emit", {})
    assert "error" not in out
    assert out["deed"]["issuer"] == "did:web:example.com:agent"
    assert len(reg) == 1


def test_verify_binding_request_fails_closed_without_composition(tmp_path):
    """Asking to bind to a composition while supplying a deed that declares none
    must REFUSE — the binding could not be evaluated, so it does not pass."""
    reg = DeedLog(tmp_path / "r.jsonl")
    a = _proxy(signer=LocalEd25519Signer.generate(), registry=reg)
    emitted = _call(a, "bulla__deed_emit", {})
    bare = {k: v for k, v in emitted["deed"].items() if k != "composition_hash"}
    b = _proxy(registry=reg)
    res = _call(b, "bulla__deed_verify",
                {"deed": bare, "composition_hash": emitted["deed"]["composition_hash"]})
    assert res["included"] is True            # the deed IS logged…
    assert res["composition_bound"] is False  # …but the binding can't be confirmed
    assert res["recommend"] == "refuse"       # so fail closed


def test_local_and_http_registries_satisfy_read_protocol(tmp_path):
    from bulla.http_registry import HttpRegistry
    from bulla.registry import ReadableRegistry
    assert isinstance(DeedLog(tmp_path / "r.jsonl"), ReadableRegistry)
    assert isinstance(HttpRegistry("http://x"), ReadableRegistry)


# ── Phase A registry additions (locked) ──────────────────────────────

def test_composition_hash_is_indexed_but_not_in_the_leaf():
    a = Deed("did:key:z", "sha256:c", "sha256:a")
    b = Deed("did:key:z", "sha256:c", "sha256:a", composition_hash="deadbeef")
    assert a.leaf() == b.leaf()                 # additive: no leaf/root migration
    assert b"deadbeef" not in b.canonical()


def test_legacy_jsonl_without_composition_hash_loads(tmp_path):
    path = tmp_path / "legacy.jsonl"
    path.write_text(json.dumps(
        {"issuer": "did:key:zOld", "content_hash": "sha256:c", "attestation_hash": "sha256:a"},
        sort_keys=True, separators=(",", ":")) + "\n")
    log = DeedLog(path)
    assert len(log) == 1
    assert log._deeds[0].composition_hash == ""
    assert log.by_composition("") == []          # empty hash never indexed


# ── proxy ENFORCE mode (OBSERVE -> ENFORCE): refuse BEFORE the backend ───

class _RecordingBackend:
    """Records whether it was called — proving the gate refuses before any backend I/O."""
    def __init__(self):
        self.name, self.alive, self.calls = "be", True, []
    async def start(self): pass
    async def initialize(self): pass
    async def list_tools(self): return [{"name": "do"}]
    async def call_tool(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        return {"result": {"content": [{"type": "text", "text": "did it"}]}}


def _enforce_proxy(reg, signer, enforce, backend):
    p = BullaLiveProxy(backends=[backend], telemetry=TelemetrySink(path=None),
                       signer=signer, registry=reg, enforce=enforce)
    p.telemetry.open()
    p.session = _seam_session()
    p._namespaced_tools = _meta_tool_definitions()
    return p


def _raw(proxy, name, arguments):
    return asyncio.run(proxy.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}}))


def test_proxy_enforce_refuses_before_backend(tmp_path):
    """enforce=True: a cross-owner call with no counterparty deed is REFUSED with a
    JSON-RPC -32001 carrying the refusal certificate, and the backend is NEVER invoked —
    the breach is prevented, not merely logged."""
    reg = DeedLog(tmp_path / "r.jsonl")
    backend = _RecordingBackend()
    p = _enforce_proxy(reg, LocalEd25519Signer.generate(), True, backend)
    resp = _raw(p, "be__do", {"x": 1})            # no _bulla_certificate presented
    err = resp["error"]
    assert err["code"] == -32001
    assert err["data"]["refusal_certificate"]["deficiency"] == "MISSING"
    assert backend.calls == []                    # backend untouched -> breach prevented


def test_proxy_enforce_false_is_passthrough(tmp_path):
    """enforce=False is the identity transform: the call passes straight through to the
    backend (today's advisory behaviour), unchanged."""
    reg = DeedLog(tmp_path / "r.jsonl")
    backend = _RecordingBackend()
    p = _enforce_proxy(reg, None, False, backend)
    resp = _raw(p, "be__do", {"x": 1})            # no deed, but enforce off
    assert "result" in resp
    assert len(backend.calls) == 1                # backend called normally


# ── the both-proxies loop: producer emits the cert, consumer's gate consumes it ──

def test_deed_emit_returns_cert_and_closes_the_both_proxies_loop(tmp_path):
    """A producer proxy's emit must hand over the full CERTIFICATE, not just the deed
    record — else a consumer's enforce gate cannot recompute fee=0 and refuses
    FEE_UNVERIFIABLE, breaking the both-proxies loop. With the cert the loop closes
    end-to-end across an HTTP registry under a PINNED root, and a LYING host (wrong root)
    is caught cross-proxy — the moat property, between two proxies (not one harness)."""
    from bulla.http_registry import HttpRegistry

    # PRODUCER: a coherent (fee=0) session + signer + appendable registry -> auto-emits a deed.
    reg = DeedLog(tmp_path / "r.jsonl")
    prod_session = LiveSession(name="producer")
    prod_session.session.add_tools_and_edges(
        tools=[ToolSpec(name="fs__read", internal_state=("data",), observable_schema=("data",)),
               ToolSpec(name="gh__write", internal_state=("data",), observable_schema=("data",))],
        edges=[Edge(from_tool="fs__read", to_tool="gh__write",
                    dimensions=(SemanticDimension(name="data_handoff",
                                                  from_field="data", to_field="data"),))])
    producer = _proxy(signer=LocalEd25519Signer.generate(), registry=reg, session=prod_session)
    emit = _call(producer, "bulla__deed_emit", {})
    assert emit["fee"] == 0
    assert "certificate" in emit                                       # the fix — emit hands over the cert
    assert emit["certificate"]["attestation_hash"] == emit["deed"]["attestation_hash"]

    srv, port = _serve(reg)
    real_root = reg.root()                                            # the relying party pins this out-of-band
    try:
        def consumer_gate(trusted_root, arguments):
            backend = _RecordingBackend()
            c = BullaLiveProxy(backends=[backend], telemetry=TelemetrySink(path=None),
                               registry=HttpRegistry(f"http://127.0.0.1:{port}"),
                               enforce=True, trusted_root=trusted_root)
            c.telemetry.open()
            c.session = _seam_session()
            return _raw(c, "be__do", arguments), backend.calls

        # honest cert + pinned root -> PROCEED, the consumer's backend IS called
        r, calls = consumer_gate(real_root, {"_bulla_certificate": emit["certificate"]})
        assert "result" in r and len(calls) == 1

        # a LYING host (wrong pinned root) -> REFUSE before the backend, cross-proxy
        r, calls = consumer_gate("sha256:" + "0" * 64, {"_bulla_certificate": emit["certificate"]})
        assert r["error"]["code"] == -32001 and not calls
        assert r["error"]["data"]["refusal_certificate"]["deficiency"] == "EQUIVOCATED_ROOT"
    finally:
        srv.shutdown()
