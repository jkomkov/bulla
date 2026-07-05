"""Agent-facing assets bundled with the Bulla package.

The canonical system-prompt fragment lives here so ``bulla proxy
--inject-prompt`` works for pip-installed users without needing
access to the source repo.
"""

from __future__ import annotations

from importlib import resources


def get_system_prompt_v1() -> str:
    """Return the v1 system-prompt fragment as a string."""
    pkg = resources.files(__name__)
    return (pkg / "system_prompt_v1.md").read_text(encoding="utf-8")


__all__ = ["get_system_prompt_v1"]
