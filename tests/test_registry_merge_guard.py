"""Merge guard for the hardened read-side registry API.

WHY THIS FILE EXISTS. ``bulla/src`` on main is authoritative. At least one
research branch (``research/enforcement-type-layer``) carries a PRE-HARDENING
re-implementation of this module: a ``registry.py`` without Pin-the-Root
(175aa2a) or inclusion-leaf binding (ccfbb23), whose ``verify_inclusion_record``
takes only ``rec`` and therefore trusts the host's own served root. Because
``registry.py`` did not exist at that branch's merge-base, any merge surfaces
as an add/add conflict — and whoever resolves it toward the research copy
silently reverts the omission rung to "the host says it's logged."

This file makes that mistake loud instead of silent:

* it fails at IMPORT/collection time if the hardened surface is gone, and
* it fails at ASSERT time if the refusal semantics regress.

Policy: research branches REBASE onto main; never resolve a ``bulla/src``
conflict toward a research copy. (Audit 2026-07-01, finding "branch hazard";
workplan PR 0.4.)
"""

from __future__ import annotations

import inspect

import pytest

# Import-time canary: a pre-hardening registry.py lacks every name below, so a
# wrong merge fails collection here, before any test logic runs.
from bulla.registry import (
    Deed,
    DeedLog,
    classify_root_trust,
    deed_leaf,
    verify_deed_record,
    verify_inclusion_record,
    verify_served_deed,
)


def _deed(i: int) -> Deed:
    h = f"{i:02x}" * 32
    return Deed(f"did:key:zGuard{i}", f"sha256:{h}", f"sha256:{h[::-1]}")


def _log_with(n: int, tmp_path) -> DeedLog:
    log = DeedLog(tmp_path / "guard-log.jsonl")
    for i in range(n):
        log.append(_deed(i))
    return log


def test_hardened_read_side_signatures_exist():
    """Signature canary: the hardening added keyword-only ``trusted_root`` /
    ``expected_leaf`` to ``verify_inclusion_record`` and made ``trusted_root``
    REQUIRED on ``verify_served_deed``. A merged-in pre-hardening copy that
    happens to define the names with the old shapes still fails here."""
    vir = inspect.signature(verify_inclusion_record).parameters
    assert "trusted_root" in vir and vir["trusted_root"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "expected_leaf" in vir and vir["expected_leaf"].kind is inspect.Parameter.KEYWORD_ONLY

    vsd = inspect.signature(verify_served_deed).parameters
    assert "trusted_root" in vsd and vsd["trusted_root"].kind is inspect.Parameter.KEYWORD_ONLY
    assert vsd["trusted_root"].default is inspect.Parameter.empty  # required, not optional

    # verify_deed_record and deed_leaf are exercised elsewhere; here they only
    # need to exist (the import above) and be callable.
    assert callable(verify_deed_record) and callable(deed_leaf)


def test_remote_bare_root_is_never_trusted():
    """The Pin-the-Root semantics in one line: a remote host's bare claim must
    classify as host-asserted and MUST NOT license proceed."""
    assert classify_root_trust(True, "sha256:" + "ab" * 32, None, None) == (
        "host-asserted",
        False,
    )
    # A local log whose root you computed yourself is the trusted counterpart.
    label, trusted = classify_root_trust(False, "sha256:" + "ab" * 32, None, None)
    assert (label, trusted) == ("own-log", True)


def test_pinned_root_mismatch_is_refused(tmp_path):
    """Equivocation arm: a proof that self-verifies against the SERVED root must
    still be refused when it does not match the root the consumer pinned."""
    log = _log_with(3, tmp_path)
    rec = log.inclusion(1)

    # Self-consistency alone passes (that is all the pre-hardening code checked) …
    assert verify_inclusion_record(rec)
    # … pinning the true root passes …
    assert verify_inclusion_record(rec, trusted_root=log.root())
    # … and pinning a DIFFERENT root refuses, even though the proof is valid
    # against the root the host served. This is the line a bad merge deletes.
    assert not verify_inclusion_record(rec, trusted_root="sha256:" + "00" * 32)


def test_borrowed_inclusion_is_refused(tmp_path):
    """Leaf-binding arm: a genuine proof for deed A must not authenticate deed B.
    Without ``expected_leaf`` the pre-hardening code accepted exactly this."""
    log = _log_with(3, tmp_path)
    rec_a = log.inclusion(0)

    leaf_a = deed_leaf(
        {
            "issuer": _deed(0).issuer,
            "content_hash": _deed(0).content_hash,
            "attestation_hash": _deed(0).attestation_hash,
        }
    )
    leaf_b = deed_leaf(
        {
            "issuer": _deed(1).issuer,
            "content_hash": _deed(1).content_hash,
            "attestation_hash": _deed(1).attestation_hash,
        }
    )

    assert verify_inclusion_record(rec_a, trusted_root=log.root(), expected_leaf=leaf_a)
    assert not verify_inclusion_record(rec_a, trusted_root=log.root(), expected_leaf=leaf_b)


def test_classify_root_trust_mismatch_arm():
    """Pinned-but-different is a possible equivocation and must not be trusted."""
    served = "sha256:" + "ab" * 32
    pinned = "sha256:" + "cd" * 32
    assert classify_root_trust(True, served, pinned, None) == ("mismatch", False)
    assert classify_root_trust(True, served, served, None) == ("pinned", True)
