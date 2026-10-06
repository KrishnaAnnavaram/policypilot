"""LLM clients behind one tiny interface: ``complete(system, user, json_mode=False) -> str``."""
from .base import LLM, LLMError, ScriptedLLM

__all__ = ["LLM", "LLMError", "ScriptedLLM", "build_llm"]


def build_llm(settings) -> LLM:
    if settings.llm_provider == "offline":
        from .offline import OfflineLLM

        return OfflineLLM()
    from .openai_compat import OpenAICompatibleLLM

    return OpenAICompatibleLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                               timeout_s=settings.llm_timeout_s)
