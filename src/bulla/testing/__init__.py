"""Public testing utilities for bulla encoding adapters.

Use `bulla.testing.synthetic_compositions` to build known-fee fixtures
for any encoding adapter, before running the adapter on real data.

The synthetic-control discipline catches encoding-coarseness bugs that
would otherwise produce vacuous fee=0 across an entire historical sweep
(see G24 commit 6ba3f89 for the canonical worked example: a 600-commit
sweep that would have been blocked by the lack of this discipline).
"""

from bulla.testing.synthetic_compositions import (
    EncodingCapabilityAudit,
    audit_encoding_capability,
    build_cycle_from_tools,
    build_hub_spoke_from_tools,
    build_known_nonvanishing,
    build_known_vanishing,
)
from bulla.testing.eval_gap_pairs import (
    EvalGapFixture,
    build_evalgap_fixtures,
    load_babel_positive_ids,
)
from bulla.testing.active_repair_tasks import (
    ArmMetrics,
    HiddenSeamTask,
    build_hidden_seam_tasks,
    evaluate_arms,
    write_benchmark_report,
)

__all__ = [
    "EncodingCapabilityAudit",
    "audit_encoding_capability",
    "build_cycle_from_tools",
    "build_hub_spoke_from_tools",
    "build_known_nonvanishing",
    "build_known_vanishing",
    "EvalGapFixture",
    "build_evalgap_fixtures",
    "load_babel_positive_ids",
    "ArmMetrics",
    "HiddenSeamTask",
    "build_hidden_seam_tasks",
    "evaluate_arms",
    "write_benchmark_report",
]
