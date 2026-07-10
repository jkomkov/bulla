"""bulla: recomputable receipts for authorless action.

A *bulla* was the clay envelope a Mesopotamian scribe sealed around a
record so it survived the absence of the parties who made it. This
package does the same for agent actions: an ActionReceipt records —
recomputably — what was done, under whose authority, within what bounds,
and how it is contested; ``bulla coverage`` measures how much of what
agents did left no receipt at all, against an anchor you did not mint.
Exact rationals throughout, no numpy, no LLM calls.

The coherence fee (the original diagnostic) remains as one measurable a
receipt can carry: on execution-derived labels it is a disclosure/omission
signal — how much convention two tools leave undisclosed at their seam —
not an execution-failure predictor. See FALSIFICATIONS.md.
"""

__version__ = "0.42.0"

from bulla.model import (
    BlindSpot,
    BoundaryObligation,
    Bridge,
    BridgePatch,
    Composition,
    ContradictionReport,
    ContradictionSeverity,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    Edge,
    ObligationVerdict,
    PackRef,
    PolicyProfile,
    ProbeResult,
    SchemaContradiction,
    SchemaOverlap,
    SemanticDimension,
    StructuralDiagnostic,
    ToolSpec,
    WitnessBasis,
    WitnessError,
    WitnessErrorCode,
    WitnessReceipt,
)
from bulla.diagnostic import (
    ConditionalDiagnostic,
    DimensionFeeDecomposition,
    FeeDecomposition,
    OpenPort,
    Resolution,
    boundary_obligations_from_decomposition,
    check_obligations,
    conditional_diagnose,
    decompose_fee,
    decompose_fee_by_dimension,
    diagnose,
    disjoint_field_decomposition_violations,
    has_disjoint_field_decomposition,
    minimum_disclosure_set,
    prescriptive_disclosure,
    resolve_conditional,
    satisfies_obligations,
)
from bulla.repair import (
    ClarificationQuestion,
    ConvergenceResult,
    RepairResult,
    WitnessGuidedPlan,
    build_witness_guided_plan,
    coordination_step,
    detect_contradictions,
    detect_contradictions_across,
    detect_expected_value_contradictions,
    extract_pack_from_probes,
    repair_composition,
    repair_step,
)
from bulla.witness_geometry import WitnessProfile, compute_profile
from bulla.incremental import IncrementalDiagnostic, FeeDelta
from bulla.update import (
    ChainHomotopy,
    Cocycle,
    CoherencePreservationCertificate,
    RepairCertificate,
    diff_classify,
    is_implementation_available,
    repair,
)
from bulla.parser import load_composition
from bulla.action_receipt import (
    ActionReceipt,
    build_action_receipt,
    build_release_receipt,
    build_tool_call_receipt,
    verify_receipt as verify_action_receipt,
)
from bulla.guard import BullaGuard, BullaCheckError
from bulla.witness import (
    verify_receipt_consistency,
    verify_receipt_integrity,
    witness,
)
from bulla.lifecycle import (
    InvalidationReason,
    ReceiptDiff,
    ValidationResult,
    diff_receipts,
    receipt_from_dict,
    validate_receipt,
)
from bulla.sdk import ComposeResult, compose, compose_multi
from bulla.infer.classifier import FieldInfo, InferredDimension
from bulla.infer.structural import compare_fields, scan_composition, schema_similarity
from bulla.config import ConfigError, McpServerEntry, find_mcp_config, parse_mcp_config
from bulla.scan import ServerScanResult, scan_mcp_servers_parallel
from bulla.proxy import (
    BullaProxySession,
    EpistemicReceipt,
    FlowRecord,
    FlowReference,
    LocalDiagnosticSummary,
    ProxyCallRecord,
)
from bulla.bridges import (
    TranslationEvidence,
    TranslationResult,
    TranslationUnavailable,
    register as register_translator,
    translate,
)
from bulla.session import AddToolResult, Session
from bulla.live import AddServerResult, LiveSession

__all__ = [
    "__version__",
    "BlindSpot",
    "Bridge",
    "BridgePatch",
    "BullaCheckError",
    "BoundaryObligation",
    "ContradictionReport",
    "ContradictionSeverity",
    "ConvergenceResult",
    "ClarificationQuestion",
    "BullaGuard",
    "Composition",
    "ConditionalDiagnostic",
    "DEFAULT_POLICY_PROFILE",
    "Diagnostic",
    "Disposition",
    "DimensionFeeDecomposition",
    "FeeDecomposition",
    "Edge",
    "FieldInfo",
    "InferredDimension",
    "PackRef",
    "PolicyProfile",
    "SchemaContradiction",
    "SchemaOverlap",
    "SemanticDimension",
    "StructuralDiagnostic",
    "ToolSpec",
    "WitnessBasis",
    "WitnessError",
    "WitnessErrorCode",
    "ObligationVerdict",
    "OpenPort",
    "ProbeResult",
    "RepairResult",
    "WitnessGuidedPlan",
    "Resolution",
    "WitnessReceipt",
    "boundary_obligations_from_decomposition",
    "check_obligations",
    "conditional_diagnose",
    "compare_fields",
    "coordination_step",
    "decompose_fee",
    "decompose_fee_by_dimension",
    "detect_contradictions",
    "detect_contradictions_across",
    "detect_expected_value_contradictions",
    "diagnose",
    "disjoint_field_decomposition_violations",
    "has_disjoint_field_decomposition",
    "extract_pack_from_probes",
    "build_witness_guided_plan",
    "minimum_disclosure_set",
    "prescriptive_disclosure",
    "repair_composition",
    "repair_step",
    "resolve_conditional",
    "satisfies_obligations",
    "scan_composition",
    "schema_similarity",
    "load_composition",
    "ActionReceipt",
    "build_action_receipt",
    "build_release_receipt",
    "build_tool_call_receipt",
    "verify_action_receipt",
    "verify_receipt_consistency",
    "verify_receipt_integrity",
    "witness",
    "ConfigError",
    "McpServerEntry",
    "ServerScanResult",
    "find_mcp_config",
    "parse_mcp_config",
    "scan_mcp_servers_parallel",
    "ComposeResult",
    "BullaProxySession",
    "compose",
    "compose_multi",
    "WitnessProfile",
    "compute_profile",
    "IncrementalDiagnostic",
    "InvalidationReason",
    "ReceiptDiff",
    "ValidationResult",
    "diff_receipts",
    "receipt_from_dict",
    "validate_receipt",
    "FeeDelta",
    # Update protocol (§9.5 Coherence-Preserving Update; gated on Phase B)
    "ChainHomotopy",
    "Cocycle",
    "CoherencePreservationCertificate",
    "RepairCertificate",
    "diff_classify",
    "is_implementation_available",
    "repair",
    "EpistemicReceipt",
    "FlowRecord",
    "FlowReference",
    "LocalDiagnosticSummary",
    "ProxyCallRecord",
    # Runtime value translation (Phase A of the Indispensability Push)
    "TranslationEvidence",
    "TranslationResult",
    "TranslationUnavailable",
    "register_translator",
    "translate",
    # Session API (Phase B of the Indispensability Push)
    "AddToolResult",
    "Session",
    # LiveSession (online proxy — Session + BullaProxySession unified)
    "AddServerResult",
    "LiveSession",
]
