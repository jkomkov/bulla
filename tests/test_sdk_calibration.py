"""Integration test: compose_multi() produces same fees as calibration pipeline.

The calibration pipeline uses BullaGuard.from_tools_list() + diagnose() +
decompose_fee() directly.  The SDK's compose_multi() wraps the same
functions.  This test verifies they produce identical results on the
same input — ensuring the SDK doesn't diverge from the direct path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bulla.diagnostic import decompose_fee, diagnose
from bulla.guard import BullaGuard
from bulla.sdk import compose_multi


# Use the canonical 4-server manifests from the real-world audit example
MANIFESTS_DIR = Path(__file__).parent.parent / "examples" / "real_world_audit" / "manifests"


def _load_manifests() -> dict[str, list[dict]]:
    """Load server manifests and return {server_name: tools_list}."""
    result = {}
    for f in sorted(MANIFESTS_DIR.glob("*.json")):
        data = json.loads(f.read_text())
        tools = data.get("tools", []) if isinstance(data, dict) else data
        if isinstance(tools, list) and tools:
            result[f.stem] = tools
    return result


@pytest.mark.skipif(
    not MANIFESTS_DIR.exists(),
    reason="real_world_audit manifests not available",
)
class TestSDKCalibrationEquivalence:
    """Verify compose_multi matches the direct diagnostic path."""

    def test_fee_matches_direct_path(self):
        """compose_multi().diagnostic.coherence_fee == direct diagnose() fee."""
        server_tools = _load_manifests()
        assert len(server_tools) >= 2

        # SDK path
        sdk_result = compose_multi(server_tools)
        sdk_fee = sdk_result.diagnostic.coherence_fee

        # Direct path (same as calibration compute.py)
        combined: list[dict] = []
        for server_name, tools_list in server_tools.items():
            for tool in tools_list:
                prefixed = dict(tool)
                prefixed["name"] = f"{server_name}__{tool['name']}"
                combined.append(prefixed)

        guard = BullaGuard.from_tools_list(combined, name="direct")
        direct_fee = guard.diagnose().coherence_fee

        assert sdk_fee == direct_fee, (
            f"SDK fee ({sdk_fee}) != direct fee ({direct_fee})"
        )

    def test_decomposition_matches(self):
        """compose_multi().decomposition matches direct decompose_fee()."""
        server_tools = _load_manifests()

        sdk_result = compose_multi(server_tools)
        assert sdk_result.decomposition is not None
        sdk_boundary = sdk_result.decomposition.boundary_fee

        # Direct path
        combined: list[dict] = []
        server_groups: dict[str, list[str]] = {}
        for server_name, tools_list in server_tools.items():
            group = []
            for tool in tools_list:
                prefixed = dict(tool)
                prefixed_name = f"{server_name}__{tool['name']}"
                prefixed["name"] = prefixed_name
                combined.append(prefixed)
                group.append(prefixed_name)
            server_groups[server_name] = group

        guard = BullaGuard.from_tools_list(combined, name="direct")
        comp = guard.composition
        diag = guard.diagnose()
        partition = [frozenset(names) for names in server_groups.values()]
        direct_decomp = decompose_fee(comp, partition)

        assert sdk_boundary == direct_decomp.boundary_fee, (
            f"SDK boundary ({sdk_boundary}) != direct ({direct_decomp.boundary_fee})"
        )

    def test_receipt_is_valid(self):
        """compose_multi() receipt passes integrity verification."""
        from bulla.witness import verify_receipt_integrity

        server_tools = _load_manifests()
        result = compose_multi(server_tools)
        receipt_dict = result.receipt.to_dict()
        assert verify_receipt_integrity(receipt_dict)

    def test_blind_spots_consistent(self):
        """Blind spot count matches between SDK and direct path."""
        server_tools = _load_manifests()

        sdk_result = compose_multi(server_tools)
        sdk_bs = len(sdk_result.diagnostic.blind_spots)

        combined: list[dict] = []
        for server_name, tools_list in server_tools.items():
            for tool in tools_list:
                prefixed = dict(tool)
                prefixed["name"] = f"{server_name}__{tool['name']}"
                combined.append(prefixed)

        guard = BullaGuard.from_tools_list(combined, name="direct")
        direct_bs = len(guard.diagnose().blind_spots)

        assert sdk_bs == direct_bs
