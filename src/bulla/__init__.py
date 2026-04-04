"""bulla: Witness kernel for agent tool compositions — diagnose, attest, seal."""

__version__ = "0.17.0"

from bulla.model import (
    BlindSpot,
    BoundaryObligation,
    Bridge,
    BridgePatch,
    Composition,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    Edge,
    PackRef,
    PolicyProfile,
    SemanticDimension,
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
    conditional_diagnose,
    decompose_fee,
    diagnose,
    minimum_disclosure_set,
    prescriptive_disclosure,
    resolve_conditional,
    satisfies_obligations,
)
from bulla.parser import load_composition
from bulla.guard import BullaGuard, BullaCheckError
from bulla.witness import (
    verify_receipt_consistency,
    verify_receipt_integrity,
    witness,
)
from bulla.infer.classifier import FieldInfo, InferredDimension

__all__ = [
    "__version__",
    "BlindSpot",
    "Bridge",
    "BridgePatch",
    "BullaCheckError",
    "BoundaryObligation",
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
    "SemanticDimension",
    "ToolSpec",
    "WitnessBasis",
    "WitnessError",
    "WitnessErrorCode",
    "OpenPort",
    "Resolution",
    "WitnessReceipt",
    "conditional_diagnose",
    "decompose_fee",
    "diagnose",
    "minimum_disclosure_set",
    "prescriptive_disclosure",
    "resolve_conditional",
    "satisfies_obligations",
    "load_composition",
    "verify_receipt_consistency",
    "verify_receipt_integrity",
    "witness",
]
