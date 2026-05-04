"""Public namespace for the LangGraph runtime integration.

Re-exports the runtime adapter's symbols so users write:

    from bulla.langgraph import bind, BullaCallbackHandler

The actual implementation lives at ``bulla.frameworks.langgraph_runtime``,
sibling to the existing static AST adapter at
``bulla.frameworks.langgraph``. This shim is the stable public surface;
the runtime-module path is internal.

Optional dep: ``pip install bulla[langgraph]`` adds ``langgraph>=1.1``
and ``langchain-core>=0.3``. ``import bulla.langgraph`` is safe
without the extras; the symbols only fail when the user actually calls
``bind()`` on a langgraph object or instantiates ``BullaCallbackHandler``.
"""

from __future__ import annotations

from bulla.frameworks.langgraph_runtime import (
    BullaCallbackHandler,
    bind,
)

__all__ = ["BullaCallbackHandler", "bind"]
