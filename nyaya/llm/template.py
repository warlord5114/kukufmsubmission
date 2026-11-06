"""No-model provider: proves the product does not depend on a model.

Everything legally load-bearing -- the deadline, the forum, the fee, the
escalation to a human -- is computed in casefile.py from the user's own dates.
So with no model at all the assistant still gives a correct, complete answer;
it just sounds like a form rather than a person.

That is the honest baseline the write-up measures the model against, and it is
also a real deployment option: an NGO running this on a cheap machine with no
internet gets the whole legal pipeline and loses only the conversational
polish.

It answers each task by shape, reading the facts already in the prompt.
"""

from __future__ import annotations

import json
import re

from .base import LLMProvider

RE_TASK = re.compile(r"^TASK:\s*(\S+)", re.M)
RE_FACTS = re.compile(r"^\s{2}-\s*(\w+):\s*(.+)$", re.M)
RE_PROBLEM = re.compile(r"^PROBLEM TYPE:\s*(\S+)", re.M)
RE_STORY = re.compile(r'"""\s*(.*?)\s*"""', re.S)


class TemplateProvider(LLMProvider):
    name = "template (no model)"

    def complete(
        self, prompt: str, *, system: str | None = None, max_tokens: int = 1400
    ) -> str:
        task = (RE_TASK.search(prompt) or [None, ""])[1]
        facts = dict(RE_FACTS.findall(prompt))
        domain = (RE_PROBLEM.search(prompt) or [None, ""])[1]

        if task == "extract_facts":
            # Extraction is handled by regex in extract.py before this is called,
            # so there is nothing useful to add. Returning an empty object is
            # the honest answer, not a guess.
            return json.dumps({})

        if task == "reflect_back":
            return self._reflect(domain, facts)

        if task == "notice_narrative":
            # draft.py has a deterministic fallback that is better than anything
            # that can be assembled here; signal that by returning nothing.
            return ""

        if task == "roleplay_opponent":
            return (
                "I have already explained my position on this. As far as I am "
                "concerned the matter is settled, and I do not think you will get "
                "anywhere by pursuing it. If you want to send something in writing, "
                "send it."
            )

        if task == "coach":
            return (
                "- You stated your position, which is the right instinct.\n"
                "- Give exact dates and amounts rather than general complaints; "
                "specifics are much harder to brush off.\n"
                "- Next time, ask them to put their refusal in writing. A written "
                "refusal is the most useful document you can get from them."
            )

        return ""

    @staticmethod
    def _reflect(domain: str, facts: dict) -> str:
        from ..casefile import fmt_money, parse_date, parse_money

        def money_of(key: str) -> str:
            return fmt_money(parse_money(facts.get(key, "")))

        def date_of(key: str) -> str:
            d = parse_date(facts.get(key, ""))
            return f"{d:%d %B %Y}" if d else ""

        money = money_of("amount") if facts.get("amount") else ""
        if domain == "deposit":
            when = date_of("vacated_on")
            s = f"So your landlord is holding back a deposit of {money}" if money else \
                "So your landlord is holding back your security deposit"
            if when:
                s += f", and you moved out on {when}"
            return s + ". Let me set out where that leaves you."
        if domain == "salary":
            since = date_of("due_since")
            s = f"So you are owed {money} in unpaid salary" if money else \
                "So your salary has not been paid"
            if since:
                s += f", going back to {since}"
            return s + ". Here is what you can do about it."
        if domain == "consumer":
            what = facts.get("what_bought", "")
            s = f"So you paid {money}" if money else "So you paid for something"
            if what:
                s += f" for {what}"
            return s + " and the refund has not come through. Here is where you stand."
        return "Here is where things stand based on what you have told me."
