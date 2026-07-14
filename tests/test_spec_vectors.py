"""The spec is the contract: the stdlib-only independent verifier (which imports
no bulla) must reproduce bulla's verdicts on the golden vectors, and its
recomputed hashes must equal bulla's. If this passes, a second implementer can
verify a receipt from the published spec alone — the definition of a protocol.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = Path(__file__).resolve().parents[1] / "spec"
_VECTORS = _SPEC / "vectors"


def _load_independent():
    spec = importlib.util.spec_from_file_location("independent_check", _VECTORS / "independent_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pytestmark = pytest.mark.skipif(not (_VECTORS / "expected.json").exists(), reason="vectors not generated")


def test_independent_verifier_reproduces_bulla_verdicts():
    ind = _load_independent()
    expected = json.loads((_VECTORS / "expected.json").read_text())
    assert expected, "no vectors"
    for name, want in expected.items():
        doc = json.loads((_VECTORS / name).read_text())
        if want.get("kind") == "witness_receipt":
            got = ind.verify_witness_receipt(doc)
        else:
            got = ind.verify_action_receipt(doc)
        for key, val in want.items():
            if key == "kind":
                continue
            assert got.get(key) == val, (
                f"{name}: independent {key}={got.get(key)!r} != expected {val!r} ({got['reasons']})"
            )


def test_spec_hashing_equals_implementation():
    """The stdlib recomputation of content/attestation/log_leaf must equal
    bulla's — the spec and the code cannot silently diverge."""
    ind = _load_independent()
    from bulla.action_receipt import ActionReceipt

    for vector in ("valid-release.json", "convention-receipt.json"):
        doc = json.loads((_VECTORS / vector).read_text())
        r = ActionReceipt.from_dict(doc)
        c = ind.content_hash(doc)
        assert c == r.content_hash, vector
        assert ind.attestation_hash(doc, c) == r.attestation_hash, vector
        assert ind.log_leaf(r.attestation_hash) == r.log_leaf, vector
        assert ind.event_hash(c, doc["timestamp"]) == r.event_hash, vector


def test_receipts_validate_against_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_SPEC / "action-receipt-v0.1.schema.json").read_text())
    doc = json.loads((_VECTORS / "valid-release.json").read_text())
    jsonschema.validate(doc, schema)  # raises on shape drift
