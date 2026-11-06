"""Turning the computed assessment into something a worried person can act on.

The whole answer is rendered here, in code, from the Assessment. Every date,
amount, deadline, forum and fee on screen was computed in casefile.py from
facts the user gave. The model's only job is an opening paragraph that says
back what happened in the user's own language, and even that is passed through
the guard before it is shown.

This is deliberate. The failure mode for a legal assistant is not a clumsy
sentence, it is a confident wrong number. Confident wrong numbers are
impossible if the numbers are not generated.
"""

from __future__ import annotations

from datetime import date

from .casefile import Assessment, Deadline, fmt_money
from .guard import GuardReport, check, render_sources
from .kb import KnowledgeBase
from .lang import t

ORIENTATION_SYSTEM = """\
You are helping an ordinary person in India who has a legal problem and cannot
afford a lawyer. You are warm, brief and practical.

Your ONLY job in this turn is to write 2-3 short sentences reflecting their
situation back to them, so they feel understood and can confirm you got the
facts right.

Hard rules, which matter more than style:
  - State NO law. No Act names, no section numbers, no case names, no legal
    conclusions. Those are supplied separately by a verified system and will be
    stripped from your output if you write them.
  - Promise NO outcome. Never say they will win or will definitely get money.
  - Invent NO facts. Use only the facts listed below.
  - Match their language. If they wrote Hinglish, reply in Hinglish. If Hindi,
    Hindi. If English, English.
  - Do not greet at length, do not apologise repeatedly, do not say "I
    understand how difficult this must be" more than once.

Output plain text only, no JSON, no headings.
"""


def build_orientation_prompt(a: Assessment, request_id: str = "orient") -> str:
    c = a.case
    facts = "\n".join(
        f"  - {k}: {v}" for k, v in c.facts.items() if str(v).strip()
    ) or "  (none recorded)"
    return f"""\
REQUEST_ID: {request_id}
TASK: reflect_back

PROBLEM TYPE: {c.domain}
THEIR LANGUAGE: {c.lang}

FACTS ON RECORD:
{facts}

WHAT THEY ORIGINALLY WROTE:
\"\"\"
{c.raw_story[:1200]}
\"\"\"

Write the 2-3 sentences now. No law, no promises, no new facts."""


# --- deterministic rendering -------------------------------------------------


def _render_deadline(d: Deadline | None, kb: KnowledgeBase, lang: str) -> list[str]:
    if not d:
        return [
            f"### ⏳ {t('deadline_header', lang)}",
            "",
            "I could not work out your deadline because a key date is missing. "
            "This is the single most important thing in your case, so it is worth "
            "finding that date before anything else.",
            "",
        ]
    out = [f"### ⏳ {t('deadline_header', lang)}", ""]
    if d.expired:
        out += [
            f"🔴 **{t('expired', lang)}**",
            "",
            f"The {d.label} period started on **{d.runs_from_date:%d %B %Y}** "
            f"({d.runs_from_desc}) and would have ended on "
            f"**{d.expires_on:%d %B %Y}**, about {abs(d.days_left)} days ago.",
        ]
        if d.condonable:
            out += ["", f"This is not necessarily the end: {d.condonation_note}"]
        else:
            out += ["", d.condonation_note]
        out += ["", "**Take this to a lawyer before spending any money on it.** "
                    "Whether the delay can be explained is exactly the kind of "
                    "judgement a person needs to make, not a tool."]
    else:
        icon = "🟠" if d.urgent else "🟢"
        out += [
            f"{icon} **{t('days_left', lang, days=d.days_left)}**",
            "",
            f"You have **{d.label}** from **{d.runs_from_date:%d %B %Y}** "
            f"({d.runs_from_desc}). That runs out on **{d.expires_on:%d %B %Y}**.",
        ]
        if d.urgent:
            out += ["", f"⚠️ {t('urgent', lang)}"]
    out.append("")
    return out


def _render_forums(a: Assessment, lang: str) -> list[str]:
    if not a.forums:
        return []
    out = [f"### 🏛️ {t('forum_header', lang)}", ""]
    informal = [f for f in a.forums if f.forum.stage == "before filing"]
    formal = [f for f in a.forums if f.forum.stage != "before filing"]

    if informal:
        out += ["**Try these first — they are free and often enough:**", ""]
        for o in informal:
            out.append(f"- **{o.forum.name}** — {o.forum.how}")
            if o.forum.typical_time:
                out.append(f"  - Usually takes: {o.forum.typical_time}")
        out.append("")
    if formal:
        out += ["**If that does not work:**", ""]
        for o in formal:
            bits = []
            if o.fee is not None:
                bits.append(f"fee {('₹' + format(o.fee, ',')) if o.fee else 'nil'}")
            elif o.fee_label:
                bits.append(o.fee_label)
            if o.forum.typical_time:
                bits.append(o.forum.typical_time)
            bits.append("lawyer recommended" if o.forum.lawyer_needed else "you can do this yourself")
            out.append(f"- **{o.forum.name}** — {'; '.join(bits)}")
            out.append(f"  - {o.forum.how}")
            if o.forum.caveat:
                out.append(f"  - ⚠️ {o.forum.caveat}")
        out.append("")
    return out


def _render_evidence(a: Assessment, kb: KnowledgeBase, lang: str) -> list[str]:
    c = a.case
    have: list[str] = []
    need: list[str] = []
    labels = {
        "has_agreement": "the signed rent agreement",
        "has_appointment_letter": "the appointment letter or contract",
        "has_proof_of_payment": "proof that you paid (bank entry, receipt or bill)",
        "has_payslips": "payslips or earlier salary credits",
        "complained_already": "a written complaint you already sent",
    }
    for key, label in labels.items():
        v = c.yes(key)
        if v is True:
            have.append(label)
        elif v is False:
            need.append(label)

    out = [f"### 📄 {t('evidence_header', lang)}", ""]
    if have:
        out += ["**You already have:**", ""] + [f"- ✅ {h}" for h in have] + [""]
    if need:
        out += ["**Worth trying to get:**", ""] + [f"- ⬜ {n}" for n in need] + [""]

    ev = kb.get("EVIDENCE_PRACTICE")
    if ev:
        out += [ev.plain_hi if lang in ("hi", "hinglish") and ev.plain_hi else ev.plain, ""]
    if need:
        out += [
            "_Missing a document does not sink your case. A written message where "
            "the other side admits the amount is often worth more than a formal "
            "receipt, so look through your WhatsApp and email first._",
            "",
        ]
    return out


def _render_escalation(a: Assessment, kb: KnowledgeBase, lang: str) -> list[str]:
    if not a.escalations:
        return []
    out = [f"### 🚨 {t('see_lawyer', lang)}", ""]
    for e in a.escalations:
        mark = {"immediate": "🔴", "high": "🟠", "medium": "🟡"}.get(e.urgency, "⚪")
        out.append(f"- {mark} {e.message}")
        if e.extra:
            out.append(f"  - **{e.extra}**")
    out += ["", t("free_aid", lang), ""]
    lsa = kb.get("LSA1987_S12")
    if lsa:
        out += [f"_{lsa.plain}_", ""]
    return out


def _render_next_steps(a: Assessment, lang: str) -> list[str]:
    c = a.case
    out = [f"### ✅ {t('next_header', lang)}", ""]
    steps: list[str] = []

    if a.deadline and not a.deadline.expired:
        steps.append(
            f"Put **{a.deadline.expires_on:%d %B %Y}** in your phone calendar now. "
            f"That is your hard deadline."
        )
    cheapest = next((f for f in a.forums if f.forum.stage == "before filing"), None)
    if cheapest:
        steps.append(f"{cheapest.forum.how}")
    steps.append(
        "Save your evidence somewhere it cannot be lost: screenshot the chats, "
        "download the bank statement, photograph the agreement."
    )
    if c.domain in ("deposit", "salary"):
        steps.append(
            "If there is no reply in 15 days, send a written legal notice. "
            "This tool can draft one for you from the facts above."
        )
    formal = next((f for f in a.forums if f.forum.stage != "before filing"), None)
    if formal:
        steps.append(f"If that fails: {formal.forum.name}.")

    out += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    out.append("")
    return out


def render_assessment(
    a: Assessment,
    kb: KnowledgeBase,
    orientation: str = "",
    *,
    lang: str | None = None,
) -> str:
    """The full grounded answer. Everything below the orientation is computed."""
    lang = lang or a.case.lang
    c = a.case
    parts: list[str] = []

    if orientation.strip():
        parts += [orientation.strip(), ""]

    amount = c.money("amount")
    header = f"## Where you stand"
    if amount:
        header += f" — {fmt_money(amount)}"
    parts += [header, ""]

    # Escalation comes first when it is serious. Burying "see a lawyer" under
    # four sections of procedure would be the wrong order for a person in
    # trouble, however tidy it looks.
    urgent_escalation = [e for e in a.escalations if e.urgency in ("immediate", "high")]
    if urgent_escalation:
        parts += _render_escalation(a, kb, lang)
        parts += ["---", ""]

    parts += _render_deadline(a.deadline, kb, lang)
    parts += _render_forums(a, lang)
    parts += _render_evidence(a, kb, lang)
    parts += _render_next_steps(a, lang)

    if a.escalations and not urgent_escalation:
        parts += _render_escalation(a, kb, lang)

    parts += ["---", "", f"_{t('disclaimer', lang)}_", ""]
    return "\n".join(parts)


def advise(
    a: Assessment,
    kb: KnowledgeBase,
    provider=None,
    *,
    request_id: str = "advice",
    strict: bool = True,
) -> tuple[str, GuardReport]:
    """Produce the answer and the guard report for it.

    The guard runs on the model's orientation paragraph only, because that is
    the only model-written text in the output. The rest is ours.
    """
    orientation = ""
    rep = GuardReport(text="")
    if provider is not None:
        try:
            raw = provider.complete(
                build_orientation_prompt(a, request_id),
                system=ORIENTATION_SYSTEM,
                max_tokens=300,
            )
            rep = check(raw, kb, a.citation_ids, strict=strict, lang=a.case.lang)
            orientation = rep.text
        except Exception:  # noqa: BLE001
            orientation = ""

    body = render_assessment(a, kb, orientation)
    sources = render_sources(kb, _cited_in_answer(a, kb), a.case.lang)
    return body + sources, rep


def _cited_in_answer(a: Assessment, kb: KnowledgeBase) -> list[str]:
    """The provisions actually relied on, in the order they were used."""
    ids: list[str] = []
    for cid in (a.deadline.cites if a.deadline else []):
        if cid not in ids:
            ids.append(cid)
    for f in a.forums:
        for cid in f.forum.cites:
            if cid not in ids:
                ids.append(cid)
    for extra in ("EVIDENCE_PRACTICE", "LSA1987_S12", "NALSA_HELPLINE"):
        if extra in a.citation_ids and extra not in ids:
            ids.append(extra)
    return ids
