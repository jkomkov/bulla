"""Tests for the narrative scan formatter and the pairwise-fee
computation it consumes.

The formatter is a pure function over a Diagnostic. The tests build
synthetic Diagnostic + BlindSpot fixtures rather than running the
full scan pipeline, so they're fast and deterministic.

The moat-case trigger (every pair fee=0 AND global fee>0) is the
load-bearing assertion. Wrong trigger logic would either suppress
the section when it should fire or fire it as noise on already-
pairwise-visible failures.
"""

from __future__ import annotations

from bulla.model import BlindSpot, Bridge, Diagnostic
from bulla.scan_format import (
    _is_moat_case,
    compute_pairwise_fees,
    format_scan_narrative,
)


def _diag(
    *,
    fee: int = 0,
    blind_spots: tuple[BlindSpot, ...] = (),
    name: str = "test-composition",
) -> Diagnostic:
    """Minimal Diagnostic constructor for narrative-formatter testing.

    Only the fields the formatter actually reads are set; the rest
    take dummy values."""
    return Diagnostic(
        name=name,
        n_tools=2,
        n_edges=1,
        betti_1=0,
        dim_c0_obs=0,
        dim_c0_full=0,
        dim_c1=0,
        rank_obs=0,
        rank_full=0,
        h1_obs=0,
        h1_full=fee,
        coherence_fee=fee,
        blind_spots=blind_spots,
        bridges=(),
        n_unbridged=len(blind_spots),
        h1_after_bridge=fee,
    )


def _bs(
    dim: str,
    *,
    edge: str = "a→b",
    from_field: str = "x",
    to_field: str = "x",
    from_tool: str = "a",
    to_tool: str = "b",
    from_hidden: bool = True,
    to_hidden: bool = True,
) -> BlindSpot:
    return BlindSpot(
        dimension=dim,
        edge=edge,
        from_field=from_field,
        to_field=to_field,
        from_hidden=from_hidden,
        to_hidden=to_hidden,
        from_tool=from_tool,
        to_tool=to_tool,
    )


# ── 1: fee=0 produces the clean message ─────────────────────────────


def test_fee_zero_clean_message():
    d = _diag(fee=0)
    out = format_scan_narrative(
        d, server_names=["filesystem", "github"],
        config_source="~/.cursor/mcp.json",
    )
    assert "Composition is clean" in out
    assert "Fee = 0, no blind spots" in out
    assert "filesystem" in out and "github" in out
    assert "~/.cursor/mcp.json" in out


# ── 2: fee>0 lists every blind spot with its explanation ────────────


def test_fee_positive_lists_blind_spots():
    d = _diag(
        fee=2,
        blind_spots=(
            _bs("path_convention", from_tool="filesystem", to_tool="github"),
            _bs("temporal_format", from_tool="analytics", to_tool="storage"),
        ),
    )
    out = format_scan_narrative(d, server_names=["filesystem", "github"])
    assert "Coherence fee: 2" in out
    # Both human labels should appear.
    assert "path format" in out
    assert "timestamp format" in out
    # The path-convention failure-mode example should mention the path.
    assert "filesystem" in out and "github" in out


def test_each_canonical_dimension_resolves():
    """Each of the 10 most-likely-to-fire dimensions produces a
    non-empty narrative without falling back to the generic
    explanation."""
    canonical_dims = [
        "path_convention", "temporal_format", "currency_code",
        "country_code", "language_code", "media_type",
        "encoding", "id_offset", "timezone", "sort_direction",
    ]
    for dim in canonical_dims:
        d = _diag(fee=1, blind_spots=(_bs(dim),))
        out = format_scan_narrative(d, server_names=["a", "b"])
        # The fallback header would say "convention" generically; the
        # canonical dimensions all have specific labels.
        assert "Coherence fee: 1" in out
        # No generic-fallback marker.
        assert "doesn't yet" not in out, (
            f"dimension {dim!r} fell back to generic explanation"
        )


# ── 3: Unknown dimension falls back gracefully ──────────────────────


def test_unknown_dimension_falls_back():
    d = _diag(fee=1, blind_spots=(_bs("totally_made_up_dimension"),))
    out = format_scan_narrative(d, server_names=["a", "b"])
    # Falls through to the generic explanation rather than raising.
    assert "Coherence fee: 1" in out
    assert "convention" in out  # the fallback's human_label


# ── 4: Pairwise moat-case trigger ───────────────────────────────────


class TestMoatTrigger:
    def test_fires_when_global_positive_and_all_pairs_clean(self):
        # Three servers, all pairs at fee=0, global fee=2.
        pairwise = {("a", "b"): 0, ("a", "c"): 0, ("b", "c"): 0}
        assert _is_moat_case(global_fee=2, pairwise_fees=pairwise) is True

    def test_skips_when_a_pair_already_has_fee(self):
        pairwise = {("a", "b"): 1, ("a", "c"): 0, ("b", "c"): 0}
        # The failure is already pairwise-visible; the moat case is
        # not present.
        assert _is_moat_case(global_fee=2, pairwise_fees=pairwise) is False

    def test_skips_when_global_zero(self):
        pairwise = {("a", "b"): 0, ("a", "c"): 0, ("b", "c"): 0}
        assert _is_moat_case(global_fee=0, pairwise_fees=pairwise) is False

    def test_skips_when_pairwise_dict_empty(self):
        assert _is_moat_case(global_fee=2, pairwise_fees={}) is False


# ── 5: Pairwise block rendering ─────────────────────────────────────


def test_pairwise_block_renders_when_moat_case():
    d = _diag(fee=2, blind_spots=(_bs("path_convention"),))
    pairwise = {("a", "b"): 0, ("a", "c"): 0, ("b", "c"): 0}
    out = format_scan_narrative(
        d, server_names=["a", "b", "c"], pairwise_fees=pairwise,
    )
    assert "Pairwise checks:" in out
    assert "a × b" in out
    assert "a × c" in out
    assert "b × c" in out
    assert "Global composition: fee = 2" in out
    assert "Every pair looks clean" in out


def test_pairwise_block_suppressed_when_not_moat_case():
    d = _diag(fee=2, blind_spots=(_bs("path_convention"),))
    # One pair already has positive fee — not the moat case.
    pairwise = {("a", "b"): 2, ("a", "c"): 0, ("b", "c"): 0}
    out = format_scan_narrative(
        d, server_names=["a", "b", "c"], pairwise_fees=pairwise,
    )
    assert "Pairwise checks:" not in out


def test_pairwise_block_suppressed_when_no_pairwise_supplied():
    d = _diag(fee=2, blind_spots=(_bs("path_convention"),))
    out = format_scan_narrative(d, server_names=["a", "b", "c"])
    assert "Pairwise checks:" not in out


# ── 6: Header and footer ────────────────────────────────────────────


def test_no_servers_emits_short_message():
    d = _diag(fee=0)
    out = format_scan_narrative(d, server_names=[])
    assert "No servers in this composition." in out


def test_footer_points_at_json_flag():
    d = _diag(fee=1, blind_spots=(_bs("path_convention"),))
    out = format_scan_narrative(d, server_names=["a", "b"])
    assert "bulla scan --json" in out


def test_singular_server_word_when_n_is_one():
    d = _diag(fee=0)
    out = format_scan_narrative(d, server_names=["solo"])
    assert "Found 1 server: solo" in out


def test_blind_spot_block_includes_tool_endpoint_pair():
    d = _diag(
        fee=1,
        blind_spots=(_bs(
            "path_convention",
            from_tool="filesystem.read_file",
            to_tool="github.create_file",
            from_field="path",
            to_field="path",
        ),),
    )
    out = format_scan_narrative(d, server_names=["filesystem", "github"])
    assert "filesystem.read_file" in out
    assert "github.create_file" in out


# ── 7: compute_pairwise_fees end-to-end ─────────────────────────────


def test_compute_pairwise_fees_returns_sorted_keys():
    """The function must produce keys with name_a < name_b so the
    formatter's sorted iteration is deterministic."""
    server_tools = {
        "github": [{"name": "create_file", "description": "",
                    "inputSchema": {"type": "object", "properties": {}}}],
        "filesystem": [{"name": "read_file", "description": "",
                        "inputSchema": {"type": "object", "properties": {}}}],
    }
    fees = compute_pairwise_fees(server_tools)
    # Keys are alphabetically sorted (filesystem < github).
    assert ("filesystem", "github") in fees
    # And the fee is an int.
    assert isinstance(fees[("filesystem", "github")], int)


def test_compute_pairwise_fees_uses_real_schemas_not_empty_stubs():
    """Regression for the second-review Bug 2: the pre-fix code
    rebuilt per-server tool dicts from ``guard.composition.tools``
    with ``inputSchema={}``, and compose_multi on those empty schemas
    always returned fee=0. The pairwise block was structurally lying
    because _is_moat_case fired even on compositions with genuine
    pairwise seams.

    Pre-fix path was the synthetic-empty-schema reconstruction in
    cli._cmd_scan; with bug-2 fixed, the CLI passes the original
    per-server tool dicts (with their real schemas) to
    compute_pairwise_fees instead. This test exercises that fixed
    path via the canned canonical-demo manifests, which carry real
    schemas with hidden fields and are known to produce a positive
    pairwise fee.
    """
    import json
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent / "examples" / "awareness-gap-demo" / "manifests"
    manifests: dict[str, list[dict]] = {}
    for name in ("filesystem", "github"):
        data = json.loads((here / f"{name}.json").read_text())
        manifests[name] = data["tools"]

    fees = compute_pairwise_fees(manifests)
    # Canonical filesystem+github with real schemas: pairwise fee > 0.
    # Pre-fix (empty-schema reconstruction) this was silently 0.
    assert fees[("filesystem", "github")] > 0, (
        "pairwise fee on filesystem×github should be > 0 when "
        "real schemas are passed; got "
        f"{fees[('filesystem', 'github')]} (Bug 2 regression — "
        "the empty-schema reconstruction would produce 0)"
    )
