"""Provider Adapter。RunnerはLLMConnectionから `get_adapter()` で取得し、共通の型（base.py）で呼ぶ"""

from typing import Any

from ..models import LLMConnection, ProviderKind
from .anthropic import AnthropicAdapter
from .base import ProviderAdapter
from .gemini import GeminiAdapter
from .openai import OpenAIAdapter

ADAPTERS: dict[int, type[ProviderAdapter]] = {
    ProviderKind.OPENAI: OpenAIAdapter,
    ProviderKind.OPENAI_COMPATIBLE: OpenAIAdapter,
    ProviderKind.ANTHROPIC: AnthropicAdapter,
    ProviderKind.GEMINI: GeminiAdapter,
}


def get_adapter(connection: LLMConnection, *, http_client: Any = None) -> ProviderAdapter:
    return ADAPTERS[connection.kind](connection, http_client=http_client)
