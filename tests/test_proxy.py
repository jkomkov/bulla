from __future__ import annotations

from bulla.model import Disposition
from bulla.proxy import BullaProxySession, EpistemicReceipt, RepairGeometry


def test_proxy_tracks_flow_conflict_and_chains_receipts():
    session = BullaProxySession(
        {
            "source": [
                {
                    "name": "list_orders",
                    "inputSchema": {"type": "object", "properties": {}},
                    "outputSchema": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["open", "closed"],
                            }
                        },
                    },
                }
            ],
            "target": [
                {
                    "name": "filter_orders",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ["draft", "published"],
                            }
                        },
                    },
                    "outputSchema": {"type": "object", "properties": {}},
                }
            ],
        }
    )

    first = session.record_call(
        "source",
        "list_orders",
        result={"status": "open"},
    )
    second = session.record_call(
        "target",
        "filter_orders",
        arguments={"status": "open"},
        argument_sources={"status": session.make_ref(first.call_id, "status")},
    )

    assert first.receipt.parent_receipt_hashes == (
        session.baseline.receipt.receipt_hash,
    )
    assert second.receipt.parent_receipt_hashes == (first.receipt.receipt_hash,)
    assert len(second.flows) == 1
    assert second.flows[0].category == "contradiction"
    assert first.local_diagnostic.coherence_fee == 0
    assert second.local_diagnostic.n_tools == 2
    assert second.receipt.contradiction_score > 0
    assert second.receipt.structural_contradictions is not None
    assert len(second.receipt.structural_contradictions) == 1
    assert second.receipt.disposition == Disposition.PROCEED_WITH_CAUTION


def test_proxy_escalates_homonym_flow_to_structural_conflict():
    session = BullaProxySession(
        {
            "left": [
                {
                    "name": "emit_count",
                    "inputSchema": {"type": "object", "properties": {}},
                    "outputSchema": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                }
            ],
            "right": [
                {
                    "name": "expect_count",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"count": {"type": "string"}},
                    },
                    "outputSchema": {"type": "object", "properties": {}},
                }
            ],
        }
    )

    source = session.record_call("left", "emit_count", result={"count": 3})
    target = session.record_call(
        "right",
        "expect_count",
        arguments={"count": 3},
        argument_sources={"count": session.make_ref(source.call_id, "count")},
    )

    assert len(target.flows) == 1
    assert target.flows[0].category == "homonym"
    assert target.flows[0].mismatch_type == "type"
    assert target.local_diagnostic.n_tools == 2
    assert target.receipt.structural_contradictions is not None
    assert target.receipt.structural_contradictions[0].mismatch_type == "type"


def test_proxy_local_diagnostic_tracks_transitive_cluster():
    session = BullaProxySession(
        {
            "left": [
                {
                    "name": "emit_path",
                    "inputSchema": {"type": "object", "properties": {}},
                    "outputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            "middle": [
                {
                    "name": "rewrite_path",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                    "outputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            "right": [
                {
                    "name": "consume_path",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                    "outputSchema": {"type": "object", "properties": {}},
                }
            ],
        }
    )

    call_1 = session.record_call("left", "emit_path", result={"path": "/tmp/a"})
    call_2 = session.record_call(
        "middle",
        "rewrite_path",
        arguments={"path": "/tmp/a"},
        result={"path": "src/a"},
        argument_sources={"path": session.make_ref(call_1.call_id, "path")},
    )
    call_3 = session.record_call(
        "right",
        "consume_path",
        arguments={"path": "src/a"},
        argument_sources={"path": session.make_ref(call_2.call_id, "path")},
    )

    assert call_3.local_diagnostic.cluster_call_ids == (1, 2, 3)
    assert call_3.local_diagnostic.n_tools == 3
    assert call_3.local_diagnostic.n_edges >= 2


def test_proxy_accepts_hyphenated_server_names():
    session = BullaProxySession(
        {
            "left-server": [
                {
                    "name": "emit_path",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                    "outputSchema": {"type": "object", "properties": {}},
                }
            ],
            "right-server": [
                {
                    "name": "consume_path",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                    "outputSchema": {"type": "object", "properties": {}},
                }
            ],
        }
    )

    source = session.record_call("left-server", "emit_path", arguments={"path": "/tmp/a"})
    target = session.record_call(
        "right-server",
        "consume_path",
        arguments={"path": "/tmp/a"},
        argument_sources={"path": session.make_ref(source.call_id, "path")},
    )

    assert target.local_diagnostic.n_tools == 2
    assert target.local_diagnostic.n_edges == 1


# ── Epistemic receipt tests ──


def _two_server_session():
    """Helper: two servers sharing a hidden 'path' dimension."""
    return BullaProxySession({
        "alpha": [{
            "name": "get_data",
            "inputSchema": {"type": "object", "properties": {
                "query": {"type": "string"},
            }},
            "outputSchema": {"type": "object", "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            }},
        }],
        "beta": [{
            "name": "process_data",
            "inputSchema": {"type": "object", "properties": {
                "path": {"type": "string"},
                "mode": {"type": "string"},
            }},
            "outputSchema": {"type": "object", "properties": {
                "result": {"type": "string"},
            }},
        }],
    })


def test_epistemic_receipt_exact_regime():
    """DFD corpus compositions produce regime=exact with no downgrade."""
    session = _two_server_session()
    c1 = session.record_call("alpha", "get_data", result={"path": "/tmp"})
    c2 = session.record_call(
        "beta", "process_data",
        arguments={"path": "/tmp"},
        argument_sources={"path": session.make_ref(c1.call_id, "path")},
    )

    rg = c2.local_diagnostic.repair_geometry
    assert rg is not None
    er = rg.epistemic_view()

    assert er.regime == "exact"
    assert er.fee == rg.fee
    assert er.geometry_dividend == round(rg.geometry_dividend, 4)
    assert er.sigma_star == round(rg.sigma_star, 4)
    assert er.forced_cost is None
    assert er.downgrade is None
    assert er.recommended_repair is not None


def test_epistemic_receipt_exact_omits_conditional_fields():
    """In exact regime, to_dict omits forced_cost and downgrade."""
    session = _two_server_session()
    c1 = session.record_call("alpha", "get_data", result={"path": "/tmp"})
    c2 = session.record_call(
        "beta", "process_data",
        arguments={"path": "/tmp"},
        argument_sources={"path": session.make_ref(c1.call_id, "path")},
    )

    d = c2.local_diagnostic.repair_geometry.epistemic_view().to_dict()
    assert "forced_cost" not in d
    assert "downgrade" not in d
    assert "fee" in d
    assert "geometry_dividend" in d
    assert "sigma_star" in d
    assert "regime" in d
    assert d["regime"] == "exact"


def test_epistemic_receipt_surrogate_from_coloops():
    """Coloops produce regime=surrogate with downgrade=coloop_burden."""
    rg = RepairGeometry(
        fee=3, beta=3, repair_entropy=1.0986,
        component_sizes=(3,),
        reachable_basis_count=3, stability_ratio=1.0,
        robustness_margin=2.0, repair_mode="rigid",
        recommended_basis=(("a", "x"), ("a", "y")),
        greedy_basis=(("a", "x"), ("a", "y")),
        field_costs={("a", "x"): 1.0, ("a", "y"): 2.0, ("a", "z"): 10.0},
        forced_cost=10.0,
        geometry_dividend=2.0,
        sigma_star=13.0,
        residual_regime="uniform_product",
    )

    er = rg.epistemic_view()
    assert er.regime == "surrogate"
    assert er.downgrade == "coloop_burden"
    assert er.forced_cost == 10.0

    d = er.to_dict()
    assert "forced_cost" in d
    assert "downgrade" in d
    assert d["downgrade"] == "coloop_burden"


def test_epistemic_receipt_surrogate_from_nonuniform():
    """Non-uniform essential matroid produces downgrade=nonuniform_essential."""
    rg = RepairGeometry(
        fee=4, beta=11, repair_entropy=2.3979,
        component_sizes=(6,),
        reachable_basis_count=11, stability_ratio=1.0,
        robustness_margin=1.0, repair_mode="flexible",
        recommended_basis=(("a", "x"), ("a", "y"), ("a", "z"), ("a", "w")),
        greedy_basis=(("a", "x"), ("a", "y"), ("a", "z"), ("a", "w")),
        field_costs={("a", "x"): 1.0, ("a", "y"): 2.0, ("a", "z"): 3.0,
                     ("a", "w"): 4.0, ("a", "v"): 5.0, ("a", "u"): 6.0},
        forced_cost=0.0,
        geometry_dividend=6.0,
        sigma_star=10.0,
        residual_regime="general",
    )

    er = rg.epistemic_view()
    assert er.regime == "surrogate"
    assert er.downgrade == "nonuniform_essential"
    assert er.forced_cost is None  # no coloops, so forced_cost omitted


def test_epistemic_receipt_no_receipt_when_fee_zero():
    """Fee=0 means no RepairGeometry and no epistemic receipt."""
    session = BullaProxySession({
        "single": [{
            "name": "solo_tool",
            "inputSchema": {"type": "object", "properties": {
                "x": {"type": "string"},
            }},
            "outputSchema": {"type": "object", "properties": {}},
        }],
    })

    c1 = session.record_call("single", "solo_tool", arguments={"x": "1"})
    assert c1.local_diagnostic.repair_geometry is None


def test_epistemic_receipt_does_not_alter_witness_receipt_hash():
    """Adding epistemic receipt must not change the sealed WitnessReceipt hash."""
    session = _two_server_session()
    c1 = session.record_call("alpha", "get_data", result={"path": "/tmp"})
    c2 = session.record_call(
        "beta", "process_data",
        arguments={"path": "/tmp"},
        argument_sources={"path": session.make_ref(c1.call_id, "path")},
    )

    # The receipt hash is a property of WitnessReceipt, not of EpistemicReceipt
    receipt_hash = c2.receipt.receipt_hash
    assert isinstance(receipt_hash, str)
    assert len(receipt_hash) == 64  # SHA-256 hex

    # Accessing epistemic view does not change the receipt
    _ = c2.local_diagnostic.repair_geometry.epistemic_view()
    assert c2.receipt.receipt_hash == receipt_hash
