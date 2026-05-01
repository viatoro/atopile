# ruff: noqa: F401
"""Providers subpackage — LLM provider abstraction."""

from atopile.agent.providers.anthropic import AnthropicProvider
from atopile.agent.providers.base import (
    LLMProvider,
    LLMResponse,
    ModelId,
    StreamDone,
    StreamEvent,
    StreamTextDelta,
    StreamToolCallDelta,
    StreamToolCallStart,
    TokenUsage,
    ToolCall,
)
from atopile.agent.providers.openai import OpenAIProvider


def make_provider(model: ModelId, *, timeout: float = 120.0) -> LLMProvider:
    """Construct the right provider for ``model``."""
    if model.provider_kind == "anthropic":
        return AnthropicProvider(model=model, timeout=timeout)
    return OpenAIProvider(model=model, timeout=timeout)
