"""Finite semantic partial order for structured scopes."""

import pytest

from bulla.experimental.frsl import RelationDecl, Signature, atom
from bulla.experimental.scope import (
    ScopeOrderStatus,
    StructuredScope,
    scope_leq,
    verify_scope_countermodel,
)


def _signature():
    return Signature(
        sorts={"Act": ("a0",)},
        relations={
            "authorized": RelationDecl("authorized", ("Act",)),
            "bounded": RelationDecl("bounded", ("Act",)),
        },
    )


def _forall(relation):
    return {
        "op": "forall",
        "var": "x",
        "sort": "Act",
        "body": atom(relation, ({"var": "x"},)),
    }


def test_scope_order_is_reflexive_and_rejects_boolean_coercion():
    scope = StructuredScope(_signature(), _forall("authorized"))

    result = scope_leq(scope, scope)

    assert result.status is ScopeOrderStatus.LEQ
    with pytest.raises(TypeError, match="no truth value"):
        bool(result)


def test_conjunction_is_narrower_than_each_requirement():
    signature = _signature()
    narrow = StructuredScope(
        signature,
        {
            "op": "and",
            "args": [_forall("authorized"), _forall("bounded")],
        },
    )
    broad = StructuredScope(signature, _forall("authorized"))

    assert scope_leq(narrow, broad).status is ScopeOrderStatus.LEQ


def test_failed_scope_implication_carries_replayable_countermodel():
    signature = _signature()
    narrow = StructuredScope(signature, _forall("authorized"))
    broad = StructuredScope(signature, _forall("bounded"))

    result = scope_leq(narrow, broad)

    assert result.status is ScopeOrderStatus.NOT_LEQ
    assert verify_scope_countermodel(narrow, broad, result)


def test_resource_limit_is_indeterminate_not_not_leq():
    signature = _signature()
    narrow = StructuredScope(
        signature,
        _forall("authorized"),
        reference_max_ground_atoms=1,
    )
    broad = StructuredScope(
        signature,
        _forall("bounded"),
        reference_max_ground_atoms=1,
    )

    result = scope_leq(narrow, broad)

    assert result.status is ScopeOrderStatus.INDETERMINATE
