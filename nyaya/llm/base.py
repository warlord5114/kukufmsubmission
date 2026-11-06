"""Model adapter. One interface, four backends, no vendor SDK anywhere else.

The brief says a stronger model may be connected through an API at interview
time, so swapping the model is a command-line flag and adding one is one file.

    template  - no model at all. Regex slot-filling plus templates over the
                computed assessment. The product still works offline, because
                the legally load-bearing parts were never the model's job.
    manual    - writes prompts to disk and reads answers back; used when the
                model is a chat session rather than an API.
    gemini    - Google AI Studio free tier.
    openai    - any OpenAI-compatible endpoint (Groq, OpenRouter, local).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str = "abstract"
    is_callable: bool = True

    @abstractmethod
    def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 1400
    ) -> str: ...

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.name!r}>"


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_json(text: str) -> dict | list:
    """Pull JSON out of a model reply. Forgiving about wrapping, strict about content."""
    if not text or not text.strip():
        raise ValueError("empty model response")
    candidates: list[str] = []
    if m := _FENCE.search(text):
        candidates.append(m.group(1))
    candidates.append(text.strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if 0 <= i < j:
            candidates.append(text[i : j + 1])

    last: Exception | None = None
    for cand in candidates:
        cand = cand.strip()
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError as exc:
            last = exc
            try:
                return json.loads(re.sub(r",\s*([}\]])", r"\1", cand))
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON in model response: {last}")


def get_provider(spec: str, **kw) -> LLMProvider:
    """Build a provider from a string, so the CLI can take `--model`."""
    kind, _, arg = spec.partition(":")
    kind = kind.strip().lower()

    if kind in ("template", "none", "offline", "stub"):
        from .template import TemplateProvider

        return TemplateProvider()
    if kind == "manual":
        from .manual import ManualProvider

        return ManualProvider(arg or "outputs/llm", **kw)
    if kind == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(arg or "gemini-2.0-flash", **kw)
    if kind in ("openai", "groq", "openrouter", "together"):
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(model=arg, flavour=kind, **kw)
    raise ValueError(f"unknown model spec {spec!r}")
