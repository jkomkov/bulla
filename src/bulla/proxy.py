"""Composition-aware session proxy for Bulla.

The proxy sits above the existing witness kernel. It does not replace the
measurement layer and it does not mutate the underlying composition model.
Instead it adds session state:

- which tool calls happened,
- which output fields were routed into which later input fields,
- what structural conflicts appeared on those concrete flows,
- and a running receipt chain over the session.

This is intentionally a programmatic Phase 2 surface, not a transport-level
JSON-RPC proxy yet. It is enough to drive integrations and collect the local
update traces the next theorem cycle actually needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import math
import random
from fractions import Fraction
from itertools import combinations

from bulla.coboundary import matrix_rank
from bulla.guard import BullaGuard
from bulla.infer.mcp import extract_field_infos
from bulla.infer.structural import compare_fields
from bulla.model import (
    DEFAULT_POLICY_PROFILE,
    PolicyProfile,
    SchemaContradiction,
    SchemaOverlap,
    WitnessReceipt,
)
from bulla.sdk import ComposeResult, compose_multi
from bulla.witness import witness


@dataclass(frozen=True)
class FlowReference:
    """Reference one field on one prior proxy call."""

    call_id: int
    field: str


@dataclass(frozen=True)
class FlowRecord:
    """Concrete observed flow from one prior call into one target field."""

    source_call_id: int
    source_server: str
    source_tool: str
    source_field: str
    target_server: str
    target_tool: str
    target_field: str
    category: str
    details: str
    mismatch_type: str = ""
    severity: float = 0.0


@dataclass(frozen=True)
class RepairGeometry:
    """Witness-geometry repair profile for a composition with fee > 0.

    Four objects that determine the full repair landscape:
    - fee: obligation count (rank of K)
    - repair_entropy: log(beta), structural flexibility
    - reachable_basis_count: bases optimal under some cost in the family
    - stability_ratio: reachable / total (operational compression)
    - robustness_margin: median cost gap between best and second-best basis
    - repair_mode: 'rigid' | 'flexible' | 'operationally_sparse'
    - recommended_basis: the minimum-cost repair under the cost model
    - greedy_basis: the default unit-cost greedy repair
    """

    fee: int
    beta: int
    repair_entropy: float
    component_sizes: tuple[int, ...]
    reachable_basis_count: int
    stability_ratio: float
    robustness_margin: float
    repair_mode: str
    recommended_basis: tuple[tuple[str, str], ...]
    greedy_basis: tuple[tuple[str, str], ...]
    field_costs: dict[tuple[str, str], float]
    forced_cost: float = 0.0
    geometry_dividend: float = 0.0
    sigma_star: float = 0.0
    residual_regime: str = "uniform_product"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fee": self.fee,
            "beta": self.beta,
            "repair_entropy": round(self.repair_entropy, 4),
            "component_sizes": list(self.component_sizes),
            "reachable_basis_count": self.reachable_basis_count,
            "stability_ratio": round(self.stability_ratio, 4),
            "robustness_margin": round(self.robustness_margin, 4),
            "repair_mode": self.repair_mode,
            "recommended_basis": [
                [t, f] for t, f in self.recommended_basis
            ],
            "greedy_basis": [[t, f] for t, f in self.greedy_basis],
            "field_costs": {
                f"{t}::{f}": c for (t, f), c in self.field_costs.items()
            },
            "forced_cost": round(self.forced_cost, 4),
            "geometry_dividend": round(self.geometry_dividend, 4),
            "sigma_star": round(self.sigma_star, 4),
            "residual_regime": self.residual_regime,
        }

    def epistemic_view(self) -> EpistemicReceipt:
        """Derive the narrow product-facing epistemic receipt."""
        return EpistemicReceipt.from_repair_geometry(self)


@dataclass(frozen=True)
class EpistemicReceipt:
    """Product-facing view: what Bulla promises, and with what confidence.

    This is the narrow contract layered on top of RepairGeometry.
    It answers one question: what does Bulla promise here, and how
    exactly?

    Always present: fee, geometry_dividend, sigma_star, regime.
    Conditional (regime != "exact"): forced_cost, downgrade.
    Optional: recommended_repair.

    This object is local to a call cluster (not session-wide) and
    is NOT part of the sealed WitnessReceipt hash contract.
    """

    fee: int
    geometry_dividend: float
    sigma_star: float
    regime: str  # "exact" | "surrogate" | "unresolved"
    forced_cost: float | None = None
    downgrade: str | None = None
    recommended_repair: tuple[tuple[str, str], ...] | None = None

    @classmethod
    def from_repair_geometry(cls, rg: RepairGeometry) -> EpistemicReceipt:
        """Derive the product view from the internal analytical object."""
        # Gate logic: determine regime and downgrade reason
        has_coloops = rg.forced_cost > 0
        is_uniform = rg.residual_regime == "uniform_product"

        if is_uniform and not has_coloops:
            regime = "exact"
            forced_cost = None
            downgrade = None
        else:
            regime = "surrogate"
            forced_cost = rg.forced_cost if has_coloops else None
            reasons = []
            if has_coloops:
                reasons.append("coloop_burden")
            if not is_uniform:
                reasons.append("nonuniform_essential")
            downgrade = "+".join(reasons)

        return cls(
            fee=rg.fee,
            geometry_dividend=round(rg.geometry_dividend, 4),
            sigma_star=round(rg.sigma_star, 4),
            regime=regime,
            forced_cost=round(forced_cost, 4) if forced_cost is not None else None,
            downgrade=downgrade,
            recommended_repair=rg.recommended_basis,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "fee": self.fee,
            "geometry_dividend": self.geometry_dividend,
            "sigma_star": self.sigma_star,
            "regime": self.regime,
        }
        if self.forced_cost is not None:
            d["forced_cost"] = self.forced_cost
        if self.downgrade is not None:
            d["downgrade"] = self.downgrade
        if self.recommended_repair is not None:
            d["recommended_repair"] = [
                [t, f] for t, f in self.recommended_repair
            ]
        return d


@dataclass(frozen=True)
class LocalDiagnosticSummary:
    """Kernel measurement on the traced local subcomposition."""

    call_id: int
    cluster_call_ids: tuple[int, ...]
    tool_names: tuple[str, ...]
    n_tools: int
    n_edges: int
    betti_1: int
    coherence_fee: int
    blind_spots: int
    contradictions: int
    repair_geometry: RepairGeometry | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "call_id": self.call_id,
            "cluster_call_ids": list(self.cluster_call_ids),
            "tool_names": list(self.tool_names),
            "n_tools": self.n_tools,
            "n_edges": self.n_edges,
            "betti_1": self.betti_1,
            "coherence_fee": self.coherence_fee,
            "blind_spots": self.blind_spots,
            "contradictions": self.contradictions,
        }
        if self.repair_geometry is not None:
            d["repair_geometry"] = self.repair_geometry.to_dict()
        return d


@dataclass(frozen=True)
class ProxyCallRecord:
    """One call observed by the session proxy."""

    call_id: int
    server: str
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    flows: tuple[FlowRecord, ...]
    local_diagnostic: LocalDiagnosticSummary
    receipt: WitnessReceipt


# ── Environment-native cost model ──────────────────────────────────

# Sensitivity buckets: higher = more costly to disclose.
# Derived from field semantics, not invented.
_SENSITIVITY_KEYWORDS: list[tuple[str, float]] = [
    ("password", 10.0), ("secret", 10.0), ("token", 10.0), ("key", 8.0),
    ("credential", 10.0), ("auth", 7.0),
    ("path", 6.0), ("file", 5.0), ("url", 4.0), ("uri", 4.0),
    ("state", 5.0), ("status", 4.0),
    ("content", 3.0), ("body", 3.0), ("message", 3.0),
    ("name", 2.0), ("title", 2.0), ("label", 2.0),
    ("page", 1.0), ("cursor", 1.0), ("offset", 1.0), ("limit", 1.0),
    ("sort", 1.0), ("order", 1.0), ("direction", 1.0),
    ("count", 1.0), ("number", 1.0),
]


def field_sensitivity(field_name: str) -> float:
    """Environment-derived sensitivity score for a field name.

    Scans field_name against keyword buckets. Returns the highest
    matching sensitivity, or 2.0 as default (mild caution).
    """
    lower = field_name.lower()
    best = 2.0
    for keyword, score in _SENSITIVITY_KEYWORDS:
        if keyword in lower:
            best = max(best, score)
    return best


def _is_cross_server(tool_name: str, server_tools: dict[str, list]) -> bool:
    """Check if a tool's server differs from the majority."""
    server = tool_name.split("__", 1)[0] if "__" in tool_name else ""
    return bool(server)


def compute_field_costs(
    hidden_basis: list[tuple[str, str]],
    leverage: list[Fraction],
) -> dict[tuple[str, str], float]:
    """Compute per-field disclosure cost from environment signals.

    cost(h) = sensitivity(field) + leverage_weight

    Sensitivity is derived from field name semantics.
    Leverage weight adds 1.0 for fields with high leverage (>0.8),
    reflecting that indispensable fields are costlier to expose.
    """
    costs: dict[tuple[str, str], float] = {}
    for i, (tool, field) in enumerate(hidden_basis):
        sens = field_sensitivity(field)
        lev_weight = 1.0 if float(leverage[i]) > 0.8 else 0.0
        # Cross-server penalty: disclosing across server boundaries is riskier
        cross_penalty = 1.0 if "__" in tool else 0.0
        costs[(tool, field)] = sens + lev_weight + cross_penalty
    return costs


def compute_repair_geometry(
    guard: BullaGuard,
    costs: dict[tuple[str, str], float] | None = None,
) -> RepairGeometry | None:
    """Compute the full repair geometry for a composition.

    Returns None if fee == 0 (no repair needed).
    """
    from bulla.witness_geometry import (
        _connected_components_of_gram,
        compute_profile,
        weighted_greedy_repair,
    )

    comp = guard.composition
    profile = compute_profile(list(comp.tools), list(comp.edges))
    if profile.fee == 0:
        return None

    # Component sizes and beta
    components = _connected_components_of_gram(profile.K)
    component_sizes = tuple(
        sorted([len(c) for c in components if len(c) > 1], reverse=True)
    )
    beta = 1
    for s in component_sizes:
        beta *= s
    repair_entropy = math.log(beta) if beta > 1 else 0.0

    # Cost model
    if costs is None:
        costs = compute_field_costs(profile.hidden_basis, profile.leverage)

    # Recommended basis under cost model
    cost_fractions = {
        k: Fraction(int(v * 100), 100) for k, v in costs.items()
    }
    recommended = weighted_greedy_repair(
        profile.K, profile.hidden_basis, cost_fractions
    )

    # Enumerate bases and compute stability (only for tractable fee)
    max_fee_for_enumeration = 14
    if profile.fee <= max_fee_for_enumeration:
        n = len(profile.K)
        bases = []
        for combo in combinations(range(n), profile.fee):
            sub = [[profile.K[i][j] for j in combo] for i in combo]
            if matrix_rank(sub) == profile.fee:
                bases.append(combo)

        # Stability under cost perturbation
        rng = random.Random(42)
        distinct_optimal: set[frozenset[int]] = set()
        margins: list[float] = []
        n_trials = 50
        for _ in range(n_trials):
            trial_costs = [Fraction(rng.randint(1, 10)) for _ in range(n)]
            best_basis = None
            best_cost: Fraction | None = None
            second_cost: Fraction | None = None
            for b in bases:
                c = sum(trial_costs[i] for i in b)
                if best_cost is None or c < best_cost:
                    second_cost = best_cost
                    best_cost = c
                    best_basis = b
                elif second_cost is None or c < second_cost:
                    second_cost = c
            if best_basis is not None:
                distinct_optimal.add(frozenset(best_basis))
            if best_cost is not None and second_cost is not None:
                margins.append(float(second_cost - best_cost))

        reachable = len(distinct_optimal)
        stability_ratio = reachable / beta if beta > 0 else 1.0
        robustness_margin = (
            sorted(margins)[len(margins) // 2] if margins else 0.0
        )
    else:
        reachable = beta  # can't enumerate; assume full
        stability_ratio = 1.0
        robustness_margin = 0.0

    # Classify repair mode
    if beta <= profile.fee + 1:
        repair_mode = "rigid"
    elif stability_ratio < 0.5:
        repair_mode = "operationally_sparse"
    else:
        repair_mode = "flexible"

    # Forced/residual decomposition (boundary note)
    forced_cost = sum(
        costs.get(cl, 0.0) for cl in profile.coloops
    )
    # Essential matroid: nontrivial components excluding coloop singletons
    coloop_set = set(profile.coloops)
    essential_components = [
        c for c in components
        if len(c) > 1
        or (len(c) == 1
            and profile.hidden_basis[c[0]] not in coloop_set
            and profile.leverage[c[0]] != 0)
    ]
    # TODO: replace coloop-free heuristic with true uniform-product detection.
    # See papers/witness-geometry-beyond-fee/BOUNDARY-NOTE.md §6 Problem 1.
    # Check uniform-product: each nontrivial essential component
    # should have rank = size - 1 (no coloops, no rank deficit > 1)
    is_uniform_product = len(profile.coloops) == 0
    if not is_uniform_product:
        # After removing coloops/loops, check if residual looks uniform-product
        # Conservative: if coloops exist, mark as potentially non-uniform
        # and test component structure
        ess_nontrivial = [c for c in essential_components if len(c) > 1]
        product_bases = 1
        for c in ess_nontrivial:
            product_bases *= len(c)
        # Would need full basis enumeration to confirm — flag conservatively
        is_uniform_product = len(profile.coloops) == 0
    residual_regime = "uniform_product" if is_uniform_product else "general"

    # Geometry dividend on essential components
    geometry_dividend = 0.0
    sigma_star_formula = 0.0
    for comp_indices in components:
        if len(comp_indices) > 1:
            comp_costs = [costs.get(profile.hidden_basis[i], 0.0)
                          for i in comp_indices]
            geometry_dividend += max(comp_costs)
            sigma_star_formula += sum(comp_costs) - max(comp_costs)

    # Sigma star: exact from recommended basis cost
    sigma_star = sum(costs.get(h, 0.0) for h in recommended)

    return RepairGeometry(
        fee=profile.fee,
        beta=beta,
        repair_entropy=repair_entropy,
        component_sizes=component_sizes,
        reachable_basis_count=reachable,
        stability_ratio=stability_ratio,
        robustness_margin=robustness_margin,
        repair_mode=repair_mode,
        recommended_basis=tuple(tuple(p) for p in recommended),
        greedy_basis=tuple(
            tuple(p) for p in profile.basis_greedy
        ),
        field_costs=costs,
        forced_cost=forced_cost,
        geometry_dividend=geometry_dividend,
        sigma_star=sigma_star,
        residual_regime=residual_regime,
    )


class BullaProxySession:
    """Track tool calls, field flows, and running witness receipts.

    The session is constructed from the tools/list surfaces for the servers
    that may participate in the session. Each call then threads a new receipt
    from the previous one, accumulating any flow-level schema conflicts.

    ``argument_sources`` is explicit on purpose: the proxy only records a flow
    when the caller can attest which prior output field supplied a given input
    field. This keeps the receipt chain factual instead of guessy.
    """

    def __init__(
        self,
        server_tools: dict[str, list[dict[str, Any]]],
        *,
        policy: PolicyProfile = DEFAULT_POLICY_PROFILE,
    ) -> None:
        if not server_tools:
            raise ValueError("server_tools must not be empty")

        self._server_tools = {
            server: [dict(tool) for tool in tools]
            for server, tools in server_tools.items()
        }
        self._policy = policy
        self._baseline = compose_multi(self._server_tools, policy=policy)
        self._guard = self._build_guard()
        self._current_receipt = self._baseline.receipt
        self._calls: list[ProxyCallRecord] = []
        self._call_by_id: dict[int, ProxyCallRecord] = {}
        self._flow_conflicts: list[SchemaContradiction] = []
        self._qualified_tools = self._build_tool_index()
        self._field_infos = self._build_field_index()

    @property
    def baseline(self) -> ComposeResult:
        """Static composition-time result for the server set."""
        return self._baseline

    @property
    def current_receipt(self) -> WitnessReceipt:
        """Latest running receipt for the session."""
        return self._current_receipt

    @property
    def calls(self) -> tuple[ProxyCallRecord, ...]:
        return tuple(self._calls)

    @property
    def flow_conflicts(self) -> tuple[SchemaContradiction, ...]:
        return tuple(self._flow_conflicts)

    def replay_trace(self, trace: list[dict[str, Any]]) -> tuple[ProxyCallRecord, ...]:
        """Replay a list of serialized proxy calls."""
        records: list[ProxyCallRecord] = []
        for item in trace:
            raw_sources = item.get("argument_sources") or {}
            argument_sources = {
                field: FlowReference(
                    call_id=payload["call_id"],
                    field=payload["field"],
                )
                for field, payload in raw_sources.items()
            }
            records.append(
                self.record_call(
                    item["server"],
                    item["tool"],
                    arguments=item.get("arguments"),
                    result=item.get("result"),
                    argument_sources=argument_sources,
                )
            )
        return tuple(records)

    def record_call(
        self,
        server: str,
        tool: str,
        *,
        arguments: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        argument_sources: dict[str, FlowReference] | None = None,
    ) -> ProxyCallRecord:
        """Record one tool call and update the running receipt chain."""
        qualified_tool = f"{server}__{tool}"
        if qualified_tool not in self._field_infos:
            raise ValueError(f"unknown tool {qualified_tool!r}")

        call_id = len(self._calls) + 1
        arguments = dict(arguments or {})
        result = None if result is None else dict(result)
        flows = self._resolve_flows(
            qualified_tool,
            arguments=arguments,
            argument_sources=argument_sources or {},
        )
        local_diagnostic = self._compute_local_diagnostic(
            call_id,
            qualified_tool,
            argument_sources or {},
        )

        new_conflicts = [
            self._flow_record_to_contradiction(flow)
            for flow in flows
            if flow.category in {"contradiction", "homonym"}
        ]
        self._flow_conflicts.extend(new_conflicts)

        diag = self._baseline.diagnostic
        comp = self._guard.composition
        contradiction_score = round(sum(c.severity for c in self._flow_conflicts))
        receipt = witness(
            diag,
            comp,
            policy_profile=self._policy,
            witness_basis=self._guard.witness_basis,
            parent_receipt_hashes=(self._current_receipt.receipt_hash,),
            structural_contradictions=tuple(self._flow_conflicts) or None,
            contradiction_score=contradiction_score,
        )
        self._current_receipt = receipt

        record = ProxyCallRecord(
            call_id=call_id,
            server=server,
            tool=tool,
            arguments=arguments,
            result=result,
            flows=tuple(flows),
            local_diagnostic=local_diagnostic,
            receipt=receipt,
        )
        self._calls.append(record)
        self._call_by_id[record.call_id] = record
        return record

    def make_ref(self, call_id: int, field: str) -> FlowReference:
        """Small helper for building explicit flow references."""
        if call_id not in self._call_by_id:
            raise ValueError(f"unknown call_id {call_id}")
        return FlowReference(call_id=call_id, field=field)

    def _build_guard(self):
        prefixed_tools: list[dict[str, Any]] = []
        for server, tools in self._server_tools.items():
            for tool in tools:
                clone = dict(tool)
                clone["name"] = f"{server}__{tool.get('name', 'unknown')}"
                prefixed_tools.append(clone)
        return BullaGuard.from_tools_list(prefixed_tools, name="proxy-session")

    def _build_tool_index(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for server, tools in self._server_tools.items():
            for tool in tools:
                clone = dict(tool)
                qualified_tool = f"{server}__{tool.get('name', 'unknown')}"
                clone["name"] = qualified_tool
                index[qualified_tool] = clone
        return index

    def _build_field_index(self) -> dict[str, dict[str, Any]]:
        return {
            qualified_tool: {info.name: info for info in extract_field_infos(tool)}
            for qualified_tool, tool in self._qualified_tools.items()
        }

    def _resolve_flows(
        self,
        qualified_target_tool: str,
        *,
        arguments: dict[str, Any],
        argument_sources: dict[str, FlowReference],
    ) -> list[FlowRecord]:
        target_server, target_tool = qualified_target_tool.split("__", 1)
        target_fields = self._field_infos[qualified_target_tool]
        flows: list[FlowRecord] = []

        for target_field, ref in argument_sources.items():
            if target_field not in target_fields:
                raise ValueError(
                    f"target field {target_field!r} not found on {qualified_target_tool}"
                )
            if ref.call_id not in self._call_by_id:
                raise ValueError(f"unknown source call_id {ref.call_id}")

            source_call = self._call_by_id[ref.call_id]
            qualified_source_tool = f"{source_call.server}__{source_call.tool}"
            source_fields = self._field_infos.get(qualified_source_tool, {})
            if ref.field not in source_fields:
                raise ValueError(
                    f"source field {ref.field!r} not found on {qualified_source_tool}"
                )

            overlap, contradiction = compare_fields(
                source_fields[ref.field],
                target_fields[target_field],
                tool_a=qualified_source_tool,
                tool_b=qualified_target_tool,
            )
            mismatch_type, severity = self._overlap_conflict(overlap, contradiction)
            flows.append(
                FlowRecord(
                    source_call_id=ref.call_id,
                    source_server=source_call.server,
                    source_tool=source_call.tool,
                    source_field=ref.field,
                    target_server=target_server,
                    target_tool=target_tool,
                    target_field=target_field,
                    category=overlap.category,
                    details=overlap.details,
                    mismatch_type=mismatch_type,
                    severity=severity,
                )
            )

        return flows

    def _compute_local_diagnostic(
        self,
        call_id: int,
        qualified_tool: str,
        argument_sources: dict[str, FlowReference],
    ) -> LocalDiagnosticSummary:
        cluster_call_ids = self._collect_cluster_call_ids(argument_sources)
        tool_names = self._cluster_tool_names(cluster_call_ids, qualified_tool)
        guard = BullaGuard.from_tools_list(
            [dict(self._qualified_tools[name]) for name in tool_names],
            name=f"proxy-local-{call_id}",
        )
        diag = guard.diagnose()
        struct = guard.structural_diagnostic
        repair_geo = compute_repair_geometry(guard) if diag.coherence_fee > 0 else None
        return LocalDiagnosticSummary(
            call_id=call_id,
            cluster_call_ids=tuple(sorted(cluster_call_ids | {call_id})),
            tool_names=tuple(tool_names),
            n_tools=diag.n_tools,
            n_edges=diag.n_edges,
            betti_1=diag.betti_1,
            coherence_fee=diag.coherence_fee,
            blind_spots=len(diag.blind_spots),
            contradictions=0 if struct is None else len(struct.contradictions),
            repair_geometry=repair_geo,
        )

    def _collect_cluster_call_ids(
        self,
        argument_sources: dict[str, FlowReference],
    ) -> set[int]:
        cluster: set[int] = set()
        stack = [ref.call_id for ref in argument_sources.values()]
        while stack:
            call_id = stack.pop()
            if call_id in cluster:
                continue
            cluster.add(call_id)
            prior = self._call_by_id.get(call_id)
            if prior is None:
                continue
            stack.extend(flow.source_call_id for flow in prior.flows)
        return cluster

    def _cluster_tool_names(
        self,
        cluster_call_ids: set[int],
        qualified_tool: str,
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for call_id in sorted(cluster_call_ids):
            prior = self._call_by_id.get(call_id)
            if prior is None:
                continue
            name = f"{prior.server}__{prior.tool}"
            if name not in seen:
                ordered.append(name)
                seen.add(name)
        if qualified_tool not in seen:
            ordered.append(qualified_tool)
        return ordered

    @staticmethod
    def _overlap_conflict(
        overlap: SchemaOverlap,
        contradiction: SchemaContradiction | None,
    ) -> tuple[str, float]:
        if contradiction is not None:
            return contradiction.mismatch_type, contradiction.severity
        if overlap.category == "homonym":
            return "type", 1.0
        return "", 0.0

    @staticmethod
    def _flow_record_to_contradiction(flow: FlowRecord) -> SchemaContradiction:
        return SchemaContradiction(
            field_a=flow.source_field,
            field_b=flow.target_field,
            tool_a=f"{flow.source_server}__{flow.source_tool}",
            tool_b=f"{flow.target_server}__{flow.target_tool}",
            mismatch_type=flow.mismatch_type or "type",
            severity=flow.severity,
            details=flow.details,
        )
