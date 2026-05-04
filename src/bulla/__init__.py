"""bulla: Witness kernel for agentic compositions.

Computes the coherence fee — the exact number of independent semantic
dimensions that bilateral schema validation cannot detect — when AI
agents compose tools across MCP servers, LangGraph graphs, or CrewAI
crews. Exact rationals throughout, no numpy, no LLM calls.
"""

__version__ = "0.37.0"

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
    FeeDecomposition,
    OpenPort,
    Resolution,
    boundary_obligations_from_decomposition,
    check_obligations,
    conditional_diagnose,
    decompose_fee,
    diagnose,
    minimum_disclosure_set,
    prescriptive_disclosure,
    resolve_conditional,
    satisfies_obligations,
)
from bulla.repair import (
    ConvergenceResult,
    RepairResult,
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
    "BullaGuard",
    "Composition",
    "ConditionalDiagnostic",
    "DEFAULT_POLICY_PROFILE",
    "Diagnostic",
    "Disposition",
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
    "Resolution",
    "WitnessReceipt",
    "boundary_obligations_from_decomposition",
    "check_obligations",
    "conditional_diagnose",
    "compare_fields",
    "coordination_step",
    "decompose_fee",
    "detect_contradictions",
    "detect_contradictions_across",
    "detect_expected_value_contradictions",
    "diagnose",
    "extract_pack_from_probes",
    "minimum_disclosure_set",
    "prescriptive_disclosure",
    "repair_composition",
    "repair_step",
    "resolve_conditional",
    "satisfies_obligations",
    "scan_composition",
    "schema_similarity",
    "load_composition",
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
