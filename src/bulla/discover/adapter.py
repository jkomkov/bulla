"""LLM adapter interface for bulla discover.

Isolates the LLM dependency behind a Protocol so the kernel never sees it.
Real adapters (OpenAI, Anthropic) are optional — install via ``pip install bulla[discover]``.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class DiscoverAdapter(Protocol):
    """Minimal interface for LLM completion used by the discovery engine."""

    def complete(self, prompt: str) -> str:
        """Send a prompt and return the raw completion text."""
        ...


class OpenAIAdapter:
    """Adapter using the OpenAI chat completions API.

    Requires ``OPENAI_API_KEY`` in the environment.
    Install: ``pip install bulla[discover]``
    """

    def __init__(self, model: str = "gpt-4o") -> None:
        try:
            import openai  # noqa: F401
        except ImportError:
            raise ImportError(
                "OpenAI adapter requires the 'openai' package. "
                "Install it with: pip install bulla[discover]"
            )
        self.model = model
        self._api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")

    def complete(self, prompt: str) -> str:
        import openai

        client = openai.OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""


class AnthropicAdapter:
    """Adapter using the Anthropic messages API.

    Requires ``ANTHROPIC_API_KEY`` in the environment.
    Install: ``pip install bulla[discover]``
    """

    def __init__(self, model: str = "claude-sonnet-4-20250514") -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            raise ImportError(
                "Anthropic adapter requires the 'anthropic' package. "
                "Install it with: pip install bulla[discover]"
            )
        self.model = model
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")

    def complete(self, prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


class OpenRouterAdapter:
    """Adapter using the OpenRouter API (OpenAI-compatible endpoint).

    Requires ``OPENROUTER_API_KEY`` in the environment.
    Supports any model available on OpenRouter.
    """

    def __init__(self, model: str = "anthropic/claude-sonnet-4-20250514") -> None:
        try:
            import openai  # noqa: F401
        except ImportError:
            raise ImportError(
                "OpenRouter adapter requires the 'openai' package. "
                "Install it with: pip install bulla[discover]"
            )
        self.model = model
        self._api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not self._api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required")

    def complete(self, prompt: str) -> str:
        import openai

        client = openai.OpenAI(
            api_key=self._api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""


class MockAdapter:
    """Testing adapter that returns a predetermined response.

    ``last_prompt`` stores the most recent prompt for test assertions.
    This attribute is specific to MockAdapter and not part of the
    ``DiscoverAdapter`` protocol.
    """

    def __init__(self, response: str) -> None:
        self.response = response
        self.last_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


def get_adapter(provider: str = "auto") -> DiscoverAdapter:
    """Get an LLM adapter by provider name or auto-detect from environment.

    Args:
        provider: One of "openai", "anthropic", "openrouter", "auto".
            "auto" checks for API keys in order: ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY.

    Raises:
        ValueError: If no adapter can be configured.
    """
    if provider == "openai":
        return OpenAIAdapter()
    if provider == "anthropic":
        return AnthropicAdapter()
    if provider == "openrouter":
        return OpenRouterAdapter()
    if provider == "auto":
        if os.environ.get("ANTHROPIC_API_KEY"):
            return AnthropicAdapter()
        if os.environ.get("OPENAI_API_KEY"):
            return OpenAIAdapter()
        if os.environ.get("OPENROUTER_API_KEY"):
            return OpenRouterAdapter()
        raise ValueError(
            "No LLM API key found. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, "
            "or OPENROUTER_API_KEY."
        )
    raise ValueError(f"Unknown provider: {provider!r}. Use 'openai', 'anthropic', 'openrouter', or 'auto'.")
