"""The consultation: one object that holds a conversation together.

Deliberately a small state machine rather than a free-running chat loop. The
states are: work out the problem, collect the facts, give the assessment, then
offer the things that follow from it (a notice, a practice argument).

A free-running chat would be easier to build and worse to rely on. A state
machine means the system always knows what it still does not know, asks for
exactly that, and cannot drift into giving advice on facts it never collected.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from .advise import advise
from .casefile import (
    DOMAIN_LABELS, Assessment, CaseFile, ask_in, assess, parse_bool, parse_date,
    parse_money,
)
from .draft import coach, draft_notice, opponent_reply
from .extract import classify_domain, extract_facts
from .guard import GuardReport
from .kb import KnowledgeBase, load_kb
from .lang import detect, t

STAGES = ("triage", "intake", "assessed", "practice")

RISK_TO_TRIGGER = {
    "threat_or_violence": "personal_safety",
    "police_or_fir": "criminal_matter",
    "court_case_started": "proceedings_started",
    "received_legal_notice": "received_legal_notice",
    "property_ownership_dispute": "ownership_dispute",
}


@dataclass
class Turn:
    who: str          # user | assistant
    text: str
    stage: str = ""
    guard: dict | None = None


@dataclass
class Session:
    kb: KnowledgeBase = field(default_factory=load_kb)
    provider: object | None = None
    case: CaseFile = field(default_factory=CaseFile)
    stage: str = "triage"
    transcript: list[Turn] = field(default_factory=list)
    practice_history: list[tuple[str, str]] = field(default_factory=list)
    guard_reports: list[GuardReport] = field(default_factory=list)
    today: date | None = None
    strict: bool = True
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    #: Slots asked but never usefully answered. Surfaced in the answer.
    unanswered: list[str] = field(default_factory=list)
    _asked: dict[str, int] = field(default_factory=dict)
    _awaiting: str = ""

    # --- helpers ---------------------------------------------------------

    def _say(self, text: str, guard: GuardReport | None = None) -> str:
        self.transcript.append(
            Turn("assistant", text, self.stage, guard.to_dict() if guard else None)
        )
        if guard is not None:
            self.guard_reports.append(guard)
        return text

    def _heard(self, text: str) -> None:
        self.transcript.append(Turn("user", text, self.stage))

    @property
    def assessment(self) -> Assessment | None:
        if not self.case.ready:
            return None
        return assess(self.case, self.kb, self.today)

    def greeting(self) -> str:
        return (
            f"{t('greeting', self.case.lang)}\n\n"
            f"_{t('disclaimer', self.case.lang)}_"
        )

    # --- the one entry point ---------------------------------------------

    def handle(self, message: str) -> str:
        """Take one user message, return the assistant's reply."""
        self._heard(message)

        det = detect(message)
        # Lock the language once there is a confident signal, so it does not
        # flip every time the user types a short English word like "yes".
        if det.lang != "en" or not self.case.lang or len(message.split()) > 4:
            if det.confidence > 0.3:
                self.case.lang = det.lang
        self.case.raw_story = (self.case.raw_story + "\n" + message).strip()

        if self.stage == "triage":
            return self._do_triage(message)
        if self.stage == "intake":
            return self._do_intake(message)
        if self.stage == "practice":
            return self._do_practice(message)
        return self._do_followup(message)

    # --- stages ----------------------------------------------------------

    def _do_triage(self, message: str) -> str:
        domain, conf, scores = classify_domain(self.case.raw_story)
        if domain is None or conf < 0.25:
            opts = "\n".join(f"- **{k}** — {v}" for k, v in DOMAIN_LABELS.items())
            return self._say(
                f"{t('not_covered', self.case.lang)}\n\n"
                f"{t('which_problem', self.case.lang)}\n\n{opts}\n\n"
                f"If it is none of these, call **15100** for free legal aid — they "
                f"cover everything, and they are better placed than I am.\n\n"
                f"---\n\n_{t('disclaimer', self.case.lang)}_"
            )

        self.case.domain = domain
        self.stage = "intake"
        self.case.notes.append(f"classified as {domain} (confidence {conf}, scores {scores})")
        return self._do_intake(message, first=True)

    def _do_intake(self, message: str, first: bool = False) -> str:
        # Fill whatever this message gives us. A reply to a specific question is
        # parsed as that slot's type first -- storing the raw sentence
        # ("nahi, agreement nahi hai") into a boolean slot makes it look filled
        # while holding a value nothing downstream can read.
        pending = getattr(self, "_pending_slot", None)
        if pending and not first:
            raw = message.strip()
            key, kind = pending["key"], pending["type"]
            if kind == "bool":
                b = parse_bool(raw)
                if b is not None:
                    self.case.facts[key] = b
            elif kind == "money":
                v = parse_money(raw)
                if v is not None and v > 0:
                    self.case.facts[key] = v
            elif kind == "date":
                d = parse_date(raw, self.today)
                if d is not None:
                    self.case.facts[key] = d.isoformat()
                elif raw.lower() in ("never", "no", "none", "nahi", "कभी नहीं"):
                    # An explicit "never refused" is an answer, not a gap.
                    self.case.facts[key] = ""
                    self.case.notes.append(f"{key}: user said never")
            else:
                self.case.facts.setdefault(key, raw)

        new, risks = extract_facts(
            message if not first else self.case.raw_story,
            self.case.domain or "",
            self.case,
            self.provider,
            today=self.today,
            request_id=f"{self.session_id}-extract-{len(self.transcript)}",
        )
        for k, v in new.items():
            self.case.facts.setdefault(k, v)
        for r in risks:
            trig = RISK_TO_TRIGGER.get(r)
            if trig and trig not in self.case.flags:
                self.case.flags.append(trig)

        if self.case.ready:
            return self._deliver_assessment()

        nxt = self.case.next_question()
        # Never ask the same thing a third time. If an answer will not parse --
        # the person types a sentence where a date was wanted, or answers a
        # different question than the one asked -- re-asking forever is the
        # worst possible behaviour for someone already stressed. Record the gap,
        # move on, and say in the answer what is missing.
        while nxt is not None:
            key = nxt["key"]
            asked = self._asked.get(key, 0)
            if asked < 2:
                self._asked[key] = asked + 1
                break
            if key not in self.case.skipped:
                self.case.skipped.append(key)
            self.case.notes.append(f"{key}: asked twice, no usable answer; skipped")
            if key not in self.unanswered:
                self.unanswered.append(key)
            nxt = self.case.next_question()

        if nxt is None:
            return self._deliver_assessment()
        self._pending_slot = nxt

        have, total = self.case.completeness()
        q = ask_in(nxt, self.case.lang)
        prefix = ""
        if first:
            prefix = (
                f"Got it — this looks like **{DOMAIN_LABELS[self.case.domain]}**. "
                f"I need a few facts before I can tell you anything useful.\n\n"
            )
        return self._say(f"{prefix}**({have}/{total})** {q}")

    def _deliver_assessment(self) -> str:
        self.stage = "assessed"
        a = assess(self.case, self.kb, self.today)
        # Flags picked up during intake feed the escalation rules.
        for trig in self.case.flags:
            if not any(e.trigger == trig for e in a.escalations):
                rule = next((r for r in self.kb.escalation_rules if r["trigger"] == trig), None)
                if rule:
                    from .casefile import Escalation

                    a.escalations.insert(
                        0,
                        Escalation(rule_id=rule["id"], trigger=trig, message=rule["message"],
                                   urgency=rule.get("urgency", "medium"), extra=rule.get("extra", "")),
                    )
        text, rep = advise(
            a, self.kb, self.provider,
            request_id=f"{self.session_id}-advice", strict=self.strict,
        )
        offer = (
            "\n\n---\n\n**What else I can do**\n\n"
            "- `notice` — draft a legal notice you can send\n"
            "- `practice` — rehearse the argument against the other side\n"
            "- `summary` — your case file, to save or show a lawyer"
        )
        return self._say(text + offer, rep)

    def _do_followup(self, message: str) -> str:
        cmd = message.strip().lower()
        a = self.assessment
        if a is None:
            self.stage = "intake"
            return self._do_intake(message)

        if cmd.startswith("notice") or self._awaiting == "notice_recipient":
            # A notice addressed to "[Name and full address of the other side]"
            # cannot be posted. Ask once for the one fact that makes it sendable.
            key = {"deposit": "landlord_name", "salary": "employer_name",
                   "consumer": "seller_name"}.get(self.case.domain or "", "")
            if self._awaiting == "notice_recipient":
                self._awaiting = ""
                if key and message.strip() and message.strip().lower() not in ("skip", "no", "later"):
                    self.case.facts[key] = message.strip()
            elif key and not self.case.text(key):
                self._awaiting = "notice_recipient"
                slot = next((s for s in self.case.schema() if s["key"] == key), None)
                q = ask_in(slot, self.case.lang) if slot else "Who is the notice addressed to?"
                return self._say(
                    f"{q}\n\n_A notice has to name and reach the other side, so this "
                    f"is the one thing I cannot leave blank. Type `skip` to get the "
                    f"draft with a placeholder instead._"
                )
            a = self.assessment or a
            text, rep = draft_notice(
                a, self.kb, self.provider, today=self.today,
                request_id=f"{self.session_id}-notice", strict=self.strict,
            )
            return self._say(text, rep)

        if cmd.startswith("practice"):
            self.stage = "practice"
            self.practice_history = []
            role = {"deposit": "your landlord", "salary": "your employer",
                    "consumer": "the company"}.get(self.case.domain or "", "the other side")
            return self._say(
                f"**Practice mode.** I will play {role}. Say what you would say to "
                f"them, and I will push back the way they probably will.\n\n"
                f"Type `done` when you want feedback.\n\n"
                f"---\n\n_{role.title()}:_ Yes? What is this about?"
            )

        if cmd.startswith("summary"):
            return self._say(self.case_summary())

        # Anything else during follow-up: treat as a correction to the facts.
        new, _ = extract_facts(
            message, self.case.domain or "", self.case, self.provider,
            today=self.today, request_id=f"{self.session_id}-correct",
        )
        if new:
            self.case.facts.update(new)
            changed = ", ".join(f"**{k}** → {v}" for k, v in new.items())
            return self._say(
                f"Updated: {changed}. Recalculating.\n\n" + self._deliver_assessment()
            )
        return self._say(
            "I can `notice`, `practice` or `summary`. Or tell me a fact that has "
            "changed and I will redo the assessment."
        )

    def _do_practice(self, message: str) -> str:
        a = self.assessment
        if a is None:
            self.stage = "intake"
            return self._do_intake(message)

        if message.strip().lower() in ("done", "stop", "exit", "bas", "khatam"):
            self.stage = "assessed"
            fb, rep = coach(
                a, self.kb, self.practice_history, self.provider,
                request_id=f"{self.session_id}-coach", strict=self.strict,
            )
            return self._say(
                ("**How that went**\n\n" + fb) if fb else "Practice ended.",
                rep,
            )

        self.practice_history.append(("you", message))
        reply, rep = opponent_reply(
            a, self.kb, self.practice_history, message, self.provider,
            request_id=f"{self.session_id}-opp-{len(self.practice_history)}",
            strict=self.strict,
        )
        self.practice_history.append(("them", reply))
        return self._say(f"_Them:_ {reply}", rep)

    # --- output ----------------------------------------------------------

    def case_summary(self) -> str:
        from .casefile import fmt_money

        c = self.case
        a = self.assessment
        L = ["## Your case file", "",
             f"**Problem:** {DOMAIN_LABELS.get(c.domain or '', 'not identified')}  ",
             f"**Prepared:** {(self.today or date.today()):%d %B %Y}  ",
             f"**Session:** `{self.session_id}`", "", "### What happened", ""]
        for slot in c.schema():
            v = c.facts.get(slot["key"])
            if v is None or str(v).strip() == "":
                continue
            label = slot["ask"].rstrip("?").rstrip(".")
            if slot["type"] == "money":
                v = fmt_money(c.money(slot["key"]))
            elif slot["type"] == "bool":
                v = "Yes" if c.yes(slot["key"]) else "No"
            L.append(f"- **{label}:** {v}")
        if a and a.deadline:
            L += ["", "### Deadline", "",
                  f"- **{a.deadline.label}** from {a.deadline.runs_from_date:%d %B %Y} "
                  f"({a.deadline.runs_from_desc})",
                  f"- **Expires:** {a.deadline.expires_on:%d %B %Y}"
                  + (" — **already passed**" if a.deadline.expired
                     else f" ({a.deadline.days_left} days away)")]
        if a and a.forums:
            L += ["", "### Where this goes", ""]
            for o in a.forums:
                L.append(f"- {o.forum.name}"
                         + (f" — fee {o.fee_label}" if o.fee is not None else ""))
        L += ["", "---", "",
              "_Take this page to a lawyer or to the District Legal Services "
              "Authority. It is written so someone can read your situation in "
              "under a minute. Free legal aid: **15100**._"]
        return "\n".join(L)

    def to_dict(self) -> dict:
        a = self.assessment
        return {
            "session_id": self.session_id,
            "stage": self.stage,
            "case": self.case.to_dict(),
            "assessment": a.to_dict() if a else None,
            "transcript": [
                {"who": t_.who, "text": t_.text, "stage": t_.stage, "guard": t_.guard}
                for t_ in self.transcript
            ],
            "guard": {
                "reports": len(self.guard_reports),
                "total_findings": sum(len(r.findings) for r in self.guard_reports),
                "clean": all(r.clean for r in self.guard_reports),
                "findings": [
                    f for r in self.guard_reports for f in r.to_dict()["findings"]
                ],
            },
        }
