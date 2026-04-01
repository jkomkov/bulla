"""Auto-inference of composition specs from tool definitions."""

from bulla.infer.classifier import (
    FieldInfo,
    InferredDimension,
    classify_description,
    classify_field,
    classify_field_by_name,
    classify_fields,
    classify_schema_signal,
    classify_tool_rich,
)
from bulla.infer.mcp import extract_field_infos

__all__ = [
    "FieldInfo",
    "InferredDimension",
    "classify_description",
    "classify_field",
    "classify_field_by_name",
    "classify_fields",
    "classify_schema_signal",
    "classify_tool_rich",
    "extract_field_infos",
]
