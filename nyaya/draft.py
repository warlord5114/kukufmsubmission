"""Drafting the legal notice.

A notice is the highest-value thing this tool produces. For a withheld deposit
or unpaid salary it very often ends the matter, it costs the price of a
registered post, and it is the step people most often skip because the format
feels intimidating.

The draft is assembled from a skeleton, not generated. The model writes one
paragraph: the narrative of what happened, in the user's own facts. Everything
legally operative -- the demand, the amount, the deadline, the statement of
what follows non-compliance -- is fixed text, because those are the parts that
have to be right.

The output is always marked as a draft for the user to check, and it never
claims to be from a lawyer.
"""

from __future__ import annotations

from datetime import date, timedelta

from .casefile import Assessment, fmt_money
from .guard import GuardReport, check
from .kb import KnowledgeBase

NARRATIVE_SYSTEM = """\
You write one factual paragraph for a legal notice in India.

Rules:
  - Third person, past tense, formal but plain. "The Sender paid..." not "I paid...".
  - ONLY the facts given. Invent nothing: no dates, no amounts, no conversations,
    no names that were not supplied.
  - State NO law. No Act names, no section numbers, no cases. They are added
    separately from a verified source and will be stripped from your output.
  - Make no threats and promise no outcome.
  - 60 to 110 words. One paragraph. No headings, no salutation, no sign-off.
  - Write in English regardless of the language the person used; the notice
    goes to a landlord, employer or company and English is the safe default.
    A translation can be added underneath separately.

Output the paragraph as plain text only.
"""

COMPLIANCE_DAYS = 15


def build_narrative_prompt(a: Assessment, request_id: str = "notice") -> str:
    c = a.case
    facts = "\n".join(f"  - {k}: {v}" for k, v in c.facts.items() if str(v).strip())
    return f"""\
REQUEST_ID: {request_id}
TASK: notice_narrative
PROBLEM TYPE: {c.domain}

FACTS (use only these):
{facts}

WHAT THEY ORIGINALLY WROTE (for context on sequence; do not import new facts):
\"\"\"
{c.raw_story[:900]}
\"\"\"

Write the single factual paragraph now."""


_SUBJECTS = {
    "deposit": "Demand for refund of security deposit",
    "salary": "Demand for payment of unpaid wages",
    "consumer": "Demand for refund in respect of a deficient service / defective goods",
}

_RECIPIENT_KEY = {
    "deposit": "landlord_name",
    "salary": "employer_name",
    "consumer": "seller_name",
}


def _fallback_narrative(a: Assessment) -> str:
    """Used when there is no model. Stiff, but accurate and complete."""
    c = a.case
    amt = fmt_money(c.money("amount"))
    if c.domain == "deposit":
        vac = c.when("vacated_on")
        rent = c.money("monthly_rent")
        bits = [
            f"The Sender was a tenant of the premises owned by the Addressee"
            + (f", at a monthly rent of {fmt_money(rent)}" if rent else "")
            + f", and paid a refundable security deposit of {amt}."
        ]
        if vac:
            bits.append(
                f"The Sender vacated the premises and handed over peaceful possession "
                f"on {vac:%d %B %Y}."
            )
        bits.append(
            "Despite the tenancy having ended and repeated requests, the Addressee "
            "has failed and neglected to refund the said security deposit."
        )
        if c.text("deductions_claimed"):
            bits.append(
                f"The Addressee has sought to justify withholding the amount on the "
                f"ground that: {c.text('deductions_claimed')}. The Sender disputes this, "
                f"and no itemised account or supporting evidence has been furnished."
            )
        return " ".join(bits)

    if c.domain == "salary":
        due = c.when("due_since")
        wage = c.money("monthly_wage")
        bits = [
            f"The Sender was employed by the Addressee"
            + (f" at a monthly salary of {fmt_money(wage)}" if wage else "")
            + "."
        ]
        if due:
            bits.append(
                f"Salary payable to the Sender has remained unpaid since "
                f"{due:%d %B %Y}, and the total amount outstanding is {amt}."
            )
        else:
            bits.append(f"An amount of {amt} remains outstanding and unpaid.")
        if c.yes("still_employed") is False:
            bits.append("The Sender's employment has since ended, and the amount "
                        "fell due for payment on the cessation of employment.")
        bits.append(
            "Despite requests, the Addressee has failed to pay the said amount or "
            "to furnish any lawful reason for withholding it."
        )
        return " ".join(bits)

    bought = c.text("what_bought")
    paid = c.when("paid_on")
    prob = c.when("problem_on")
    bits = [
        f"The Sender paid the Addressee a sum of {amt}"
        + (f" on {paid:%d %B %Y}" if paid else "")
        + (f" for {bought}" if bought else " for goods and/or services")
        + "."
    ]
    if prob:
        bits.append(
            f"On or about {prob:%d %B %Y} the said goods and/or services were found "
            f"to be deficient, and the Sender sought a refund."
        )
    bits.append(
        "Despite requests, the Addressee has failed and neglected to refund the "
        "said amount."
    )
    return " ".join(bits)


def draft_notice(
    a: Assessment,
    kb: KnowledgeBase,
    provider=None,
    *,
    today: date | None = None,
    compliance_days: int = COMPLIANCE_DAYS,
    sender_name: str = "",
    sender_address: str = "",
    request_id: str = "notice",
    strict: bool = True,
) -> tuple[str, GuardReport]:
    """Assemble the notice. Returns (markdown, guard report on model text)."""
    today = today or date.today()
    c = a.case
    rep = GuardReport(text="")

    narrative = ""
    if provider is not None:
        try:
            raw = provider.complete(
                build_narrative_prompt(a, request_id), system=NARRATIVE_SYSTEM, max_tokens=400
            )
            rep = check(raw, kb, a.citation_ids, strict=strict)
            narrative = rep.text.strip()
        except Exception:  # noqa: BLE001
            narrative = ""
    if not narrative:
        narrative = _fallback_narrative(a)

    amount = c.money("amount")
    recipient = c.text(_RECIPIENT_KEY.get(c.domain or "", "")) or "[Name and full address of the other side]"
    subject = _SUBJECTS.get(c.domain or "", "Demand for payment")
    comply_by = today + timedelta(days=compliance_days)

    # The legal basis is pulled from the KB, never written by the model.
    basis_ids = {
        "deposit": ["CONTRACT1872_S73"],
        "salary": ["PWA1936_S5"],
        "consumer": ["CPA2019_S2_11", "CPA2019_S2_47"],
    }.get(c.domain or "", [])
    basis_lines = []
    for pid in basis_ids:
        p = kb.get(pid)
        if p:
            basis_lines.append(f"{p.summary} ({p.cite_label()})")

    L: list[str] = []
    L += [
        "> **DRAFT — read this before you send it.** Check every name, date and "
        "amount against your own records. This was assembled by a tool, not written "
        "by a lawyer, and it does not make the tool your lawyer. Sending it is your "
        "decision.",
        "",
        "---",
        "",
        "**LEGAL NOTICE**",
        "",
        f"**Date:** {today:%d %B %Y}",
        "",
        "**From:**  ",
        f"{sender_name or '[Your full name]'}  ",
        f"{sender_address or '[Your full address]'}  ",
        "",
        "**To:**  ",
        f"{recipient}  ",
        "",
        f"**Subject:** {subject}"
        + (f" — {fmt_money(amount)}" if amount else ""),
        "",
        "Sir / Madam,",
        "",
        "1. " + narrative,
        "",
    ]

    n = 2
    if basis_lines:
        L += [f"{n}. The Addressee's conduct is a breach of the obligations owed to "
              f"the Sender. " + " ".join(basis_lines), ""]
        n += 1

    demand = (
        f"the sum of {fmt_money(amount)}" if amount else "the amount lawfully due"
    )
    L += [
        f"{n}. The Sender hereby calls upon the Addressee to pay {demand} within "
        f"**{compliance_days} days** of receipt of this notice, that is on or before "
        f"**{comply_by:%d %B %Y}**.",
        "",
    ]
    n += 1
    L += [
        f"{n}. Should the Addressee fail to comply within the said period, the Sender "
        f"shall be constrained to initiate appropriate proceedings before the "
        f"competent forum for recovery of the said amount, together with interest, "
        f"compensation and costs, entirely at the risk and expense of the Addressee.",
        "",
    ]
    n += 1
    L += [
        f"{n}. A copy of this notice is retained for record.",
        "",
        "Yours faithfully,",
        "",
        "  \n".join(["", f"{sender_name or '[Your signature and name]'}", ""]),
        "",
        "---",
        "",
        "**How to send it**",
        "",
        "- Print two copies. Sign both. Keep one.",
        "- Send by **registered post with acknowledgement due** from any post office. "
        "Keep the receipt and the tracking slip — that receipt is your proof of service "
        "and matters as much as the notice itself.",
        "- Email a scanned copy as well, if you have an address for them. Two routes, "
        "two proofs.",
        f"- Diarise **{comply_by:%d %B %Y}**. If nothing has arrived by then, move to "
        "the next step.",
        "",
    ]
    if a.deadline and not a.deadline.expired:
        L += [
            f"> ⏳ Sending a notice does **not** pause your legal deadline of "
            f"**{a.deadline.expires_on:%d %B %Y}**. If they ignore you, do not keep "
            f"waiting past that date.",
            "",
        ]
    return "\n".join(L), rep


# --- practice: play the other side ------------------------------------------

OPPONENT_SYSTEM = """\
You are role-playing the OTHER SIDE in an Indian civil dispute so that the user
can practise answering before they face the real thing. You are not their
adviser in this turn.

How to play it:
  - Be the landlord, employer or company. Be plausible, not cartoonish. Most
    real opponents are polite, confident and reasonable-sounding.
  - Use the excuses real opponents actually use: disputed damage to the
    property, "company policy", a verbal agreement the user cannot prove, the
    claim that they already paid, delay and deflection, an offer of half.
  - Push on the user's weakest point, which is usually a missing document.
  - Stay in character. 2-4 sentences per turn. Do not break into advice.

Hard limits:
  - Cite NO law. No Act names, no sections, no cases. A real opponent bluffs
    vaguely ("legally you have no case") and so should you.
  - Make no threats of violence and nothing abusive.
  - Invent no facts about the user beyond what is given.

Output the opponent's reply as plain text only.
"""

_OPPONENT_ROLE = {
    "deposit": "the landlord",
    "salary": "the employer's HR manager",
    "consumer": "the company's customer support manager",
}


def build_opponent_prompt(
    a: Assessment, history: list[tuple[str, str]], user_message: str, request_id: str = "opp"
) -> str:
    c = a.case
    facts = "\n".join(f"  - {k}: {v}" for k, v in c.facts.items() if str(v).strip())
    weak: list[str] = []
    if c.yes("has_agreement") is False:
        weak.append("they have no written agreement")
    if c.yes("has_appointment_letter") is False:
        weak.append("they have no appointment letter")
    if c.yes("has_proof_of_payment") is False:
        weak.append("they cannot prove the payment")
    if a.deadline and a.deadline.urgent:
        weak.append("they have left it very late")
    convo = "\n".join(f"  {who}: {msg}" for who, msg in history[-6:]) or "  (opening exchange)"

    return f"""\
REQUEST_ID: {request_id}
TASK: roleplay_opponent
YOU ARE: {_OPPONENT_ROLE.get(c.domain or '', 'the other side')}

THEIR CASE:
{facts}

THEIR WEAK POINTS (press one of these):
{chr(10).join('  - ' + w for w in weak) or '  - none obvious; try delay and part-payment'}

CONVERSATION SO FAR:
{convo}

THEY JUST SAID:
\"\"\"
{user_message}
\"\"\"

Reply in character now."""


def opponent_reply(
    a: Assessment,
    kb: KnowledgeBase,
    history: list[tuple[str, str]],
    user_message: str,
    provider=None,
    *,
    request_id: str = "opp",
    strict: bool = True,
) -> tuple[str, GuardReport]:
    if provider is None:
        return (
            "_Practice mode needs a model. Run with `--model gemini:...` or "
            "`--model manual:...` to rehearse against the other side._",
            GuardReport(text=""),
        )
    try:
        raw = provider.complete(
            build_opponent_prompt(a, history, user_message, request_id),
            system=OPPONENT_SYSTEM,
            max_tokens=320,
        )
    except Exception as exc:  # noqa: BLE001
        return f"_Could not reach the model: {exc}_", GuardReport(text="")

    # The opponent is still run through the guard. A fake section quoted by a
    # roleplayed landlord is exactly as damaging as one quoted in advice --
    # the user may believe it and give up.
    rep = check(raw, kb, a.citation_ids, strict=strict)
    return rep.text, rep


COACH_SYSTEM = """\
You are coaching someone who has just practised arguing with the other side in
a dispute. Give short, concrete feedback on how they handled it.

  - 3 bullet points maximum: what worked, what to fix, the one line to use next time.
  - Reference what they actually said. Be specific, not generic encouragement.
  - Cite NO law and promise NO outcome.
  - Plain text, no headings.
"""


def coach(
    a: Assessment, kb: KnowledgeBase, history: list[tuple[str, str]], provider=None,
    *, request_id: str = "coach", strict: bool = True,
) -> tuple[str, GuardReport]:
    if provider is None or not history:
        return "", GuardReport(text="")
    convo = "\n".join(f"  {who}: {msg}" for who, msg in history)
    prompt = (
        f"REQUEST_ID: {request_id}\nTASK: coach\n\nTHE PRACTICE CONVERSATION:\n{convo}\n\n"
        f"Give the feedback now."
    )
    try:
        raw = provider.complete(prompt, system=COACH_SYSTEM, max_tokens=300)
    except Exception:  # noqa: BLE001
        return "", GuardReport(text="")
    rep = check(raw, kb, a.citation_ids, strict=strict)
    return rep.text, rep
