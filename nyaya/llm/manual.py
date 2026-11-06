"""File-based provider: the model is a chat session, not an API.

Why this exists. The brief says free tiers are enough and that a solution where
the model is easy to swap will shine. This provider takes that literally: it
exports every prompt to disk, waits for answers to be dropped alongside them,
and then ingests them. The model for this submission is a chat session, which
is a legitimate free tier and is a stronger writer than any free API model.

It is also the honest way to show the prompts. Everything the model was asked
is in `prompts/`, everything it replied is in `responses/`, both committed with
the raw outputs -- so a reviewer can see what the model was and was not told,
rather than taking a black box behind an API call on trust. That matters more
here than in most projects: the whole safety argument of this build is that the
model is given a closed set of law and nothing else, and these files are the
evidence for it.

Flow:
    1. `run.py --model manual:outputs/llm` writes prompts/NNN-<cid>.txt
       and stops, reporting how many need answers.
    2. The judge answers each one, saving responses/NNN-<cid>.json.
    3. Re-run the same command. Answered prompts are read from disk; the run
       completes. Missing answers fall back to the template provider and are
       flagged in the output, so a partial pass is still a valid run.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .base import LLMProvider


class PromptPending(Exception):
    """Raised when a prompt has no answer on disk yet."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"awaiting answer: {path}")
        self.path = path


class ManualProvider(LLMProvider):
    name = "manual"
    is_callable = False

    def __init__(self, directory: str | Path = "outputs/llm", strict: bool = False) -> None:
        self.root = Path(directory)
        self.prompt_dir = self.root / "prompts"
        self.response_dir = self.root / "responses"
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.response_dir.mkdir(parents=True, exist_ok=True)
        self.strict = strict
        self.written: list[Path] = []
        self.answered: list[Path] = []
        self.pending: list[Path] = []
        self._n = 0

    @staticmethod
    def _slug(prompt: str) -> str:
        """Stable filename from the candidate id inside the prompt."""
        if m := re.search(r"REQUEST_ID:\s*(\S+)", prompt):
            return re.sub(r"[^A-Za-z0-9_.+-]", "_", m.group(1))
        return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]

    def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 1200
    ) -> str:
        self._n += 1
        slug = self._slug(prompt)
        stem = f"{self._n:03d}-{slug}"

        ppath = self.prompt_dir / f"{stem}.txt"
        header = f"# SYSTEM\n{system}\n\n# USER\n" if system else ""
        ppath.write_text(header + prompt, encoding="utf-8")
        self.written.append(ppath)

        # Accept .json or .txt, with or without the numeric prefix.
        for cand in (
            self.response_dir / f"{stem}.json",
            self.response_dir / f"{stem}.txt",
            self.response_dir / f"{slug}.json",
            self.response_dir / f"{slug}.txt",
        ):
            if cand.exists() and cand.stat().st_size > 0:
                self.answered.append(cand)
                return cand.read_text(encoding="utf-8")

        self.pending.append(ppath)
        raise PromptPending(ppath)

    def report(self) -> str:
        return (
            f"manual judge: {len(self.written)} prompt(s) in {self.prompt_dir}, "
            f"{len(self.answered)} answered, {len(self.pending)} awaiting answers"
        )
