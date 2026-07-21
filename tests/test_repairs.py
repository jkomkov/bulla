from __future__ import annotations

from bulla.experimental.repairs import (
    RepairCatalog,
    RepairKind,
    RepairOption,
    RepairTarget,
    minimal_repairs,
    verify_repair_plan,
)
from bulla.reliance import STRICT_RELIANCE_POLICY


def _view():
    return {
        "ok": True,
        "verified_to": "attestation",
        "authority_authentic": "verified",
        "effective_grounding": "counterparty_signed",
        "conventions": {},
        "chain_integrity": "not_applicable",
        "principal_binding": "not_applicable",
        "policy_binding": "not_applicable",
        "scope_binding": "not_applicable",
        "bounds_conformance": "not_applicable",
        "temporal_status": "unresolved",
        "revocation_status": "unresolved",
    }


def _catalog():
    return RepairCatalog(
        catalog_id="strict-transport-repairs/v1",
        options=(
            RepairOption(
                option_id="obtain-checkpoint",
                kind=RepairKind.TIME,
                target=RepairTarget.VERIFICATION,
                dimension="temporal_status",
                value="within_window",
                statement="Obtain a comparable witnessed checkpoint.",
                cost={"evidence_items": 1, "latency_units": 1},
            ),
            RepairOption(
                option_id="obtain-revocation-proof",
                kind=RepairKind.EVIDENCE,
                target=RepairTarget.VERIFICATION,
                dimension="revocation_status",
                value="not_revoked",
                statement="Obtain a fresh revocation proof.",
                cost={"evidence_items": 1, "latency_units": 1},
            ),
            RepairOption(
                option_id="accept-unresolved-revocation",
                kind=RepairKind.POLICY_SUBSTITUTION,
                target=RepairTarget.POLICY,
                dimension="revocation_status",
                value="unresolved",
                statement="The policy author explicitly accepts unresolved revocation.",
                cost={"authority_acts": 1},
                authority_ref="did:example:policy-author",
            ),
        ),
    )


def test_minimal_repairs_are_declared_counterfactual_antichain():
    plans = minimal_repairs(_view(), STRICT_RELIANCE_POLICY, _catalog())
    assert {plan.option_ids for plan in plans} == {
        ("obtain-checkpoint", "obtain-revocation-proof"),
        ("accept-unresolved-revocation", "obtain-checkpoint"),
    }
    assert all(
        verify_repair_plan(plan, _view(), STRICT_RELIANCE_POLICY, _catalog())
        for plan in plans
    )


def test_undeclared_repairs_are_never_invented():
    catalog = RepairCatalog(
        catalog_id="time-only/v1",
        options=(_catalog().options[0],),
    )
    assert minimal_repairs(_view(), STRICT_RELIANCE_POLICY, catalog) == ()
