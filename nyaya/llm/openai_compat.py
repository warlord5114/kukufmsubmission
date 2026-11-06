"""Any OpenAI-compatible /chat/completions endpoint.

Covers Groq (free and fast), OpenRouter, Together, OpenAI itself and a local
llama.cpp or Ollama server. Uses `requests` rather than a vendor SDK so that
swapping the model is a flag, not an install.
"""

from __future__ import annotations

import os
import time

from .base import LLMProvider

DEFAULTS: dict[str, tuple[str, str, str]] = {
    # flavour: (base_url, env var for key, default model)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
}


class OpenAICompatProvider(LLMProvider):
    is_callable = True

    def __init__(
        self,
        model: str | None = None,
        flavour: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        max_retries: int = 4,
    ) -> None:
        base_default, env_key, model_default = DEFAULTS.get(
            flavour, DEFAULTS["openai"]
        )
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or base_default).rstrip("/")
        self.model = model or model_default
        self.name = f"{flavour}:{self.model}"
        self.max_retries = max_retries
        self.api_key = api_key or os.environ.get(env_key) or os.environ.get("OPENAI_API_KEY")
        if not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            raise RuntimeError(f"{env_key} is not set (or use --model heuristic)")

    def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 1200
    ) -> str:
        import requests

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                r = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=90,
                )
                if r.status_code == 429 or r.status_code >= 500:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(min(30.0, 2.0 * (2**attempt)))
        raise RuntimeError(f"{self.name} failed after {self.max_retries} attempts: {last}")
