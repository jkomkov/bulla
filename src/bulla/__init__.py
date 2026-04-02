"""bulla: Witness kernel for agent tool compositions — diagnose, attest, seal."""

__version__ = "0.9.1"

from bulla.model import (
    BlindSpot,
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
from bulla.diagnostic import diagnose
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
    "BullaGuard",
    "Composition",
    "DEFAULT_POLICY_PROFILE",
    "Diagnostic",
    "Disposition",
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
    "WitnessReceipt",
    "diagnose",
    "load_composition",
    "verify_receipt_consistency",
    "verify_receipt_integrity",
    "witness",
]
