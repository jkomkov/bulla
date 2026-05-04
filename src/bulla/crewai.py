"""Public namespace for the CrewAI runtime integration.

Re-exports the runtime adapter's symbols so users write:

    from bulla.crewai import bind, BullaCrewCallback

The actual implementation lives at ``bulla.frameworks.crewai_runtime``,
sibling to the existing static AST adapter at
``bulla.frameworks.crewai``.

Optional dep: ``pip install bulla[crewai]`` adds ``crewai>=0.80``.
``import bulla.crewai`` is safe without the extra; the symbols only
fail when the user actually calls ``bind()`` on a Crew or wires the
callback into a kickoff.
"""

from __future__ import annotations

from bulla.frameworks.crewai_runtime import (
    BullaCrewCallback,
    bind,
)

__all__ = ["BullaCrewCallback", "bind"]
