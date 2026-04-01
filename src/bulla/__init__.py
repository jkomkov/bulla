"""bulla: Witness kernel for agent tool compositions — diagnose, attest, seal."""

__version__ = "0.7.0"

from bulla.model import (
    BlindSpot,
    Bridge,
    BridgePatch,
    Composition,
    DEFAULT_POLICY_PROFILE,
    Diagnostic,
    Disposition,
    Edge,
    PolicyProfile,
    SemanticDimension,
    ToolSpec,
    WitnessError,
    WitnessErrorCode,
    WitnessReceipt,
)
from bulla.diagnostic import diagnose
from bulla.parser import load_composition
from bulla.guard import BullaGuard, BullaCheckError
from bulla.witness import witness
from bulla.infer.classifier import FieldInfo, InferredDimension

__all__ = [
    "__version__",
    "BlindSpot",
    "Bridge",
    "BridgePatch",
    "Composition",
    "DEFAULT_POLICY_PROFILE",
    "Diagnostic",
    "Disposition",
    "Edge",
    "FieldInfo",
    "InferredDimension",
    "PolicyProfile",
    "BullaCheckError",
    "BullaGuard",
    "SemanticDimension",
    "ToolSpec",
    "WitnessError",
    "WitnessErrorCode",
    "WitnessReceipt",
    "diagnose",
    "load_composition",
    "witness",
]
