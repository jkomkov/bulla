"""The experimental authority profile is occurrence-bound and fail-closed."""

from bulla.experimental.authority_holonomy import authority_translation_hash, verify_authority_cycle
from bulla.identity import LocalEd25519Signer


def _translation(
    source: LocalEd25519Signer,
    target: LocalEd25519Signer,
    permutation: tuple[int, ...],
    *,
    occurrence: str = "sha256:" + "a" * 64,
) -> dict:
    document = {
        "kind": "authority_translation",
        "version": "0.1-experimental",
        "source_owner": source.issuer,
        "target_owner": target.issuer,
        "occurrence_hash": occurrence,
        "authority_vocabulary": ["principal", "delegate", "forum"],
        "translation": list(permutation),
    }
    document["signature"] = source.sign(authority_translation_hash({**document, "signature": {}}))
    return document


def _nontrivial_cycle() -> list[dict]:
    a, b, c = (LocalEd25519Signer.generate() for _ in range(3))
    return [
        _translation(a, b, (1, 0, 2)),
        _translation(b, c, (0, 2, 1)),
        _translation(c, a, (0, 1, 2)),
    ]


def test_authentic_nonidentity_cycle_is_a_descent_obstruction():
    result = verify_authority_cycle(_nontrivial_cycle())
    assert result["ok"] is True
    assert result["authenticated"] is True
    assert result["descent_obstruction"] is True
    assert result["holonomy"] != result["identity"]


def test_flat_authenticated_cycle_has_no_obstruction():
    a, b, c = (LocalEd25519Signer.generate() for _ in range(3))
    cycle = [
        _translation(a, b, (1, 0, 2)),
        _translation(b, c, (0, 2, 1)),
        _translation(c, a, (1, 2, 0)),
    ]
    result = verify_authority_cycle(cycle)
    assert result["authenticated"] is True
    assert result["descent_obstruction"] is False
    assert result["holonomy"] == result["identity"]


def test_tampering_after_signature_cannot_establish_an_obstruction():
    cycle = _nontrivial_cycle()
    cycle[1]["translation"] = [2, 1, 0]
    result = verify_authority_cycle(cycle)
    assert result["ok"] is True
    assert result["authenticated"] is False
    assert result["descent_obstruction"] is False
    assert result["checks"]["translation_1_signature"] is False


def test_occurrence_mismatch_and_open_or_reordered_cycles_fail_closed():
    cycle = _nontrivial_cycle()
    wrong_occurrence = [dict(item) for item in cycle]
    wrong_occurrence[2] = dict(wrong_occurrence[2], occurrence_hash="sha256:" + "b" * 64)
    assert verify_authority_cycle(wrong_occurrence)["ok"] is False
    assert verify_authority_cycle(cycle[:2])["ok"] is False
    assert verify_authority_cycle([cycle[1], cycle[0], cycle[2]])["ok"] is False


def test_source_owner_must_match_the_authentic_signature_issuer():
    cycle = _nontrivial_cycle()
    cycle[0]["signature"] = dict(
        cycle[0]["signature"], issuer=cycle[1]["source_owner"]
    )
    result = verify_authority_cycle(cycle)
    assert result["ok"] is True
    assert result["checks"]["translation_0_issuer_bound"] is False
    assert result["descent_obstruction"] is False
