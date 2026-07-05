"""Recourse-conformance scenarios v0 — the benchmark that defines the category.

A *recourse-conformant* receipt system is one where a relying party, facing a
host that controls the channel, can still: recompute the verdict, detect
omission and equivocation, refuse borrowed proofs and unpinned roots, complete
a cure, and read a well-formed appeal path whose every remedy names a verifier
and a stateful anchor (the modality law — there is no respondent left to
serve, so remedies execute against artifacts and stakes).

v0 ships ~20 scenarios in five groups, each a named, runnable check built
from bulla's own primitives (the constructive half of the Recourse Ladder
characterization). `bulla/tests/test_conformance_v0.py` runs them all;
`python -m bulla.conformance` prints the scenario table.
"""

from bulla.conformance.scenarios import SCENARIOS, Scenario, run_all  # noqa: F401
