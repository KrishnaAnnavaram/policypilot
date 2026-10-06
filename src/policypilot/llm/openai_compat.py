"""Client for any OpenAI-compatible chat-completions endpoint (Groq, OpenAI, vLLM, Ollama ...)."""
from __future__ import annotations

import time

import httpx

from .base import LLMError

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float = 30.0,
                 max_retries: int = 2, client: httpx.Client | None = None):
        if not api_key:
            raise ValueError("an API key is required (set LLM_API_KEY)")
        self.name = model
        self.model = model
        self.max_retries = max(0, max_retries)
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._client = client or httpx.Client(timeout=timeout_s)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        payload: dict = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_error = "no attempt made"
        for attempt in range(self.max_retries + 1):          # bounded loop, never recursive
            try:
                resp = self._client.post(self._url, headers=self._headers, json=payload)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()["choices"][0]["message"]["content"] or ""
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise LLMError(f"unexpected response shape from {self._url}") from exc
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code not in _RETRYABLE:
                    break
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 8))
        raise LLMError(f"LLM request failed: {last_error}")
