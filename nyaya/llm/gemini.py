"""Google AI Studio (Gemini) provider -- free tier is enough for this pipeline.

Cost note: one grounded answer sends ~900 input tokens and asks for ~350
output. A full six-turn consultation is well under 10k tokens, which sits
inside the AI Studio free tier and costs a fraction of a paisa on paid
Flash pricing.
"""

from __future__ import annotations

import os
import time

from .base import LLMProvider


class GeminiProvider(LLMProvider):
    is_callable = True

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        api_key: str | None = None,
        max_retries: int = 4,
    ) -> None:
        self.model = model
        self.name = f"gemini:{model}"
        self.max_retries = max_retries
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey, or use --model heuristic."
            )
        try:
            from google import genai  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install google-genai") from exc
        self._client = genai.Client(api_key=key)

    def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 1200
    ) -> str:
        from google.genai import types  # type: ignore

        cfg = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.2,  # legal advice: keep it repeatable, not creative
            response_mime_type="application/json",
            system_instruction=system,
        )
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.models.generate_content(
                    model=self.model, contents=prompt, config=cfg
                )
                return resp.text or ""
            except Exception as exc:  # noqa: BLE001 - provider SDKs raise broadly
                last = exc
                # Free tiers rate-limit aggressively; back off and retry.
                time.sleep(min(30.0, 2.0 * (2**attempt)))
        raise RuntimeError(f"gemini failed after {self.max_retries} attempts: {last}")
