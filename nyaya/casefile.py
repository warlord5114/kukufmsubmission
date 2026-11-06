"""The case file: structured facts, and everything that can be computed from them.

The split that makes this system trustworthy is here. A model is good at
reading a messy story and pulling out "she moved out on 3 March". It is bad at
working out what date is twelve months after that, whether that is inside a
limitation period, and which of four forums applies at Rs 47,000. So the model
fills slots, and this module does the arithmetic.

Nothing in this file calls a model. Every number a user is shown -- the
deadline, the days remaining, the filing fee, the forum -- is computed here
from dates the user gave, and can be recomputed by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta

from .kb import Forum, KnowledgeBase, LimitationRule

# --- slot schemas ------------------------------------------------------------
# What we need to know before advice is worth giving. `ask` is the question put
# to the user, one at a time, in the order listed.

SLOTS: dict[str, list[dict]] = {
    "deposit": [
        {"key": "amount", "type": "money", "required": True,
         "ask": "How much was the security deposit?",
         "ask_hi": "सिक्योरिटी डिपॉज़िट कितना था?",
         "ask_hing": "Security deposit kitna tha?"},
        {"key": "vacated_on", "type": "date", "required": True,
         "ask": "What date did you hand over the house and move out?",
         "ask_hi": "आपने घर किस तारीख को खाली किया था?",
         "ask_hing": "Aapne ghar kis tareekh ko khaali kiya tha?"},
        {"key": "refused_on", "type": "date", "required": False,
         "ask": "When did the landlord last refuse or stop responding? (If never explicitly refused, say 'never'.)",
         "ask_hi": "मकान मालिक ने आखिरी बार कब मना किया या जवाब देना बंद किया? (अगर कभी साफ़ मना नहीं किया, तो 'never' लिखें।)",
         "ask_hing": "Makaan maalik ne aakhri baar kab mana kiya ya jawab dena band kiya?"},
        {"key": "monthly_rent", "type": "money", "required": False,
         "ask": "What was the monthly rent?",
         "ask_hi": "महीने का किराया कितना था?",
         "ask_hing": "Mahine ka kiraya kitna tha?"},
        {"key": "has_agreement", "type": "bool", "required": True,
         "ask": "Do you have a written rent agreement?",
         "ask_hi": "क्या आपके पास लिखित रेंट एग्रीमेंट है?",
         "ask_hing": "Kya aapke paas likhit rent agreement hai?"},
        {"key": "has_proof_of_payment", "type": "bool", "required": True,
         "ask": "Can you show the deposit being paid — a bank transfer, or a receipt?",
         "ask_hi": "क्या डिपॉज़िट देने का सबूत है — बैंक ट्रांसफर या रसीद?",
         "ask_hing": "Kya deposit dene ka sabooot hai — bank transfer ya receipt?"},
        {"key": "deductions_claimed", "type": "text", "required": False,
         "ask": "What reason has the landlord given for keeping it, if any?",
         "ask_hi": "मकान मालिक ने पैसा रोकने का क्या कारण बताया है?",
         "ask_hing": "Makaan maalik ne paisa rokne ka kya kaaran bataya hai?"},
        {"key": "state", "type": "text", "required": False,
         "ask": "Which city or state is the property in? The forum depends on it.",
         "ask_hi": "प्रॉपर्टी किस राज्य में है? फोरम इसी पर निर्भर करता है।",
         "ask_hing": "Property kis rajya mein hai? Forum isi par depend karta hai."},
        {"key": "landlord_name", "type": "text", "required": False,
         "ask": "What is the landlord's name and address? (Needed only for the notice.)",
         "ask_hi": "मकान मालिक का नाम और पता? (सिर्फ़ नोटिस के लिए चाहिए।)",
         "ask_hing": "Makaan maalik ka naam aur pata? (Sirf notice ke liye chahiye.)"},
    ],
    "salary": [
        {"key": "amount", "type": "money", "required": True,
         "ask": "How much salary is unpaid in total?",
         "ask_hi": "कुल कितनी सैलरी बाकी है?",
         "ask_hing": "Kul kitni salary baaki hai?"},
        {"key": "monthly_wage", "type": "money", "required": True,
         "ask": "What was your monthly salary? This decides which route is open to you.",
         "ask_hi": "आपकी महीने की सैलरी कितनी थी? इससे तय होगा कौन सा रास्ता खुलेगा।",
         "ask_hing": "Aapki mahine ki salary kitni thi? Isse tay hoga ki kaunsa rasta khulega."},
        {"key": "due_since", "type": "date", "required": True,
         "ask": "From which date has the salary been unpaid? Use the first month they missed.",
         "ask_hi": "किस तारीख से सैलरी नहीं मिली? पहला महीना बताइए।",
         "ask_hing": "Kis tareekh se salary nahi mili? Pehla mahina bataiye."},
        {"key": "still_employed", "type": "bool", "required": True,
         "ask": "Are you still working there?",
         "ask_hi": "क्या आप अभी भी वहाँ काम कर रहे हैं?",
         "ask_hing": "Kya aap abhi bhi wahan kaam kar rahe hain?"},
        {"key": "has_appointment_letter", "type": "bool", "required": True,
         "ask": "Do you have an appointment letter or written contract?",
         "ask_hi": "क्या आपके पास अपॉइंटमेंट लेटर या लिखित कॉन्ट्रैक्ट है?",
         "ask_hing": "Kya aapke paas appointment letter ya likhit contract hai?"},
        {"key": "has_payslips", "type": "bool", "required": False,
         "ask": "Do you have payslips, or bank credits from earlier months?",
         "ask_hi": "क्या पेस्लिप या पहले के महीनों की बैंक एंट्री है?",
         "ask_hing": "Kya payslip ya pehle ke mahino ki bank entry hai?"},
        {"key": "employer_name", "type": "text", "required": False,
         "ask": "What is the employer's name and office address?",
         "ask_hi": "कंपनी का नाम और ऑफिस का पता?",
         "ask_hing": "Employer ka naam aur office ka pata?"},
        {"key": "state", "type": "text", "required": False,
         "ask": "Which city or state do you work in?",
         "ask_hi": "आप किस राज्य में काम करते हैं?",
         "ask_hing": "Aap kis rajya mein kaam karte hain?"},
    ],
    "consumer": [
        {"key": "amount", "type": "money", "required": True,
         "ask": "How much did you pay? The forum depends on this figure, not on what you are claiming.",
         "ask_hi": "आपने कितने पैसे दिए थे? फोरम इसी रकम पर निर्भर करता है।",
         "ask_hing": "Aapne kitne paise diye the? Forum isi rakam par depend karta hai."},
        {"key": "paid_on", "type": "date", "required": True,
         "ask": "What date did you pay?",
         "ask_hi": "आपने किस तारीख को पेमेंट की थी?",
         "ask_hing": "Aapne kis tareekh ko payment ki thi?"},
        {"key": "problem_on", "type": "date", "required": True,
         "ask": "What date did the problem happen, or when did they refuse the refund?",
         "ask_hi": "समस्या किस दिन हुई, या उन्होंने रिफंड देने से कब मना किया?",
         "ask_hing": "Problem kis din hui, ya unhone refund dene se kab mana kiya?"},
        {"key": "what_bought", "type": "text", "required": True,
         "ask": "What did you buy, and what went wrong?",
         "ask_hi": "आपने क्या खरीदा था और क्या गड़बड़ हुई?",
         "ask_hing": "Aapne kya khareeda tha aur kya gadbad hui?"},
        {"key": "seller_name", "type": "text", "required": False,
         "ask": "Who did you buy it from — the shop, company or website?",
         "ask_hi": "आपने किससे खरीदा — दुकान, कंपनी या वेबसाइट?",
         "ask_hing": "Aapne kisse khareeda — dukaan, company ya website?"},
        {"key": "has_proof_of_payment", "type": "bool", "required": True,
         "ask": "Do you have the bill, order confirmation or bank statement?",
         "ask_hi": "क्या आपके पास बिल, ऑर्डर कन्फर्मेशन या बैंक स्टेटमेंट है?",
         "ask_hing": "Kya aapke paas bill, order confirmation ya bank statement hai?"},
        {"key": "complained_already", "type": "bool", "required": False,
         "ask": "Have you already complained to them in writing?",
         "ask_hi": "क्या आप उनसे लिखित में शिकायत कर चुके हैं?",
         "ask_hing": "Kya aap unse likhit mein shikayat kar chuke hain?"},
        {"key": "state", "type": "text", "required": False,
         "ask": "Which district and state are you in? You can file where you live.",
         "ask_hi": "आप किस ज़िले और राज्य में हैं? आप वहीं फाइल कर सकते हैं।",
         "ask_hing": "Aap kis district aur rajya mein hain? Aap wahin file kar sakte hain."},
    ],
}

DOMAIN_LABELS = {
    "deposit": "Security deposit not returned",
    "salary": "Unpaid salary",
    "consumer": "Refund for goods or services",
}


# --- parsing helpers ---------------------------------------------------------

RE_MONEY = re.compile(
    r"(?:(?:rs|inr|₹)\.?\s*)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|lac|lacs|crore|crores|k|thousand)?",
    re.I,
)

_UNIT = {"lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
         "crore": 1e7, "crores": 1e7, "k": 1e3, "thousand": 1e3}

_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y",
    "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
    "%B %Y", "%b %Y", "%m/%Y", "%Y",
)

# Devanagari digits, and Hindi month names in the spellings people actually
# type. Without these, a user writing "सितंबर 2026" has no date at all, and no
# date means no deadline -- the single most important output of the system.
_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_HINDI_MONTHS = {
    "जनवरी": "January", "फरवरी": "February", "फ़रवरी": "February",
    "मार्च": "March", "अप्रैल": "April", "अप्रेल": "April", "मई": "May",
    "जून": "June", "जुलाई": "July", "अगस्त": "August",
    "सितंबर": "September", "सितम्बर": "September", "सेप्टेंबर": "September",
    "अक्टूबर": "October", "अक्तूबर": "October",
    "नवंबर": "November", "नवम्बर": "November",
    "दिसंबर": "December", "दिसम्बर": "December",
}


def _romanise_dateish(s: str) -> str:
    """Devanagari digits and month names -> ASCII, so strptime can read them."""
    s = s.translate(_DEVA_DIGITS)
    for hi, en in _HINDI_MONTHS.items():
        if hi in s:
            s = s.replace(hi, en)
    return s


def parse_money(text: str) -> float | None:
    """'Rs 45,000' / '1.5 lakh' / '₹2,00,000' -> float. Indian units included."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text).strip().translate(_DEVA_DIGITS)
    raw = raw.replace("हज़ार", "thousand").replace("हजार", "thousand").replace("लाख", "lakh")
    m = RE_MONEY.search(raw)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    return value * _UNIT.get(unit, 1.0)


def parse_date(text: str, today: date | None = None) -> date | None:
    """Accepts real dates and the vague forms people actually type."""
    if text is None:
        return None
    if isinstance(text, date):
        return text
    s = str(text).strip().lower()
    if not s or s in ("never", "no", "none", "-"):
        return None
    today = today or date.today()

    if s in ("today",):
        return today
    if s in ("yesterday",):
        return today - timedelta(days=1)

    if m := re.match(r"(\d+)\s*(day|week|month|year)s?\s*ago", s):
        n = int(m.group(1))
        unit = m.group(2)
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        return today - timedelta(days=days)
    if m := re.match(r"last\s+(month|year|week)", s):
        days = {"week": 7, "month": 30, "year": 365}[m.group(1)]
        return today - timedelta(days=days)

    cleaned = _romanise_dateish(str(text).strip())
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned.strip(" ,।"))
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(cleaned, fmt).date()
            # "%B %Y" and "%Y" default to day 1 / Jan 1, which is the safe
            # reading for a limitation calculation: it assumes the earliest
            # date in the stated period, so a deadline is never overstated.
            return d
        except ValueError:
            continue
    return None


def parse_bool(text) -> bool | None:
    if isinstance(text, bool):
        return text
    if text is None:
        return None
    s = str(text).strip().lower().strip(".,!।")
    YES = {"y", "yes", "haan", "han", "ha", "haa", "true", "1", "yep", "yeah",
           "ji", "ji haan", "हाँ", "हां", "ha ji", "yes i have", "i have",
           "yes i do", "haan hai", "hai"}
    NO = {"n", "no", "nahi", "nahin", "nai", "false", "0", "nope", "nah",
          "नहीं", "नही", "no i dont", "no i don't", "nahi hai", "nahi h"}
    if s in YES:
        return True
    if s in NO:
        return False
    # Short sentences that lead with a yes/no: "nahi, agreement nahi hai",
    # "yes I have the agreement", "हाँ अभी भी काम कर रहा हूँ".
    first = re.split(r"[,\s]+", s)[0] if s else ""
    if first in YES:
        return True
    if first in NO:
        return False
    if len(s.split()) <= 12:
        if re.search(r"\b(?:nahi|nahin|nai|not|don'?t|never|no)\b|नहीं|नही", s):
            return False
        if re.search(r"\b(?:haan|han|yes|hai|have|got)\b|हाँ|हां", s):
            return True
    return None


def add_months(d: date, months: int) -> date:
    """Calendar-correct month arithmetic, clamping to the end of short months."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def fmt_money(v: float | None) -> str:
    """Indian digit grouping: 12,34,567."""
    if v is None:
        return "—"
    n = int(round(v))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    return f"₹{'-' if n < 0 else ''}{s}"


# --- the case file -----------------------------------------------------------


@dataclass
class Deadline:
    rule_key: str
    label: str
    runs_from_date: date
    runs_from_desc: str
    expires_on: date
    days_left: int
    expired: bool
    urgent: bool
    condonable: bool
    condonation_note: str
    cites: list[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["runs_from_date"] = self.runs_from_date.isoformat()
        d["expires_on"] = self.expires_on.isoformat()
        return d


@dataclass
class CaseFile:
    domain: str | None = None
    lang: str = "en"
    facts: dict = field(default_factory=dict)
    raw_story: str = ""
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Slots asked repeatedly with no usable answer. Treated as answered-unknown
    #: so the conversation can move on, and reported in the output as a gap.
    skipped: list[str] = field(default_factory=list)

    # --- typed accessors ---------------------------------------------
    def money(self, key: str) -> float | None:
        return parse_money(self.facts.get(key))

    def when(self, key: str, today: date | None = None) -> date | None:
        return parse_date(self.facts.get(key), today)

    def yes(self, key: str) -> bool | None:
        return parse_bool(self.facts.get(key))

    def text(self, key: str) -> str:
        v = self.facts.get(key)
        return "" if v is None else str(v).strip()

    # --- completeness ------------------------------------------------
    def schema(self) -> list[dict]:
        return SLOTS.get(self.domain or "", [])

    def missing(self, required_only: bool = True) -> list[dict]:
        """Slots still to ask about.

        A required slot needs a real value. An optional one only needs to have
        been asked: "the landlord never actually refused" is an answer, stored
        as an empty string, and re-asking it would loop forever.
        """
        out = []
        for slot in self.schema():
            required = bool(slot.get("required"))
            if required_only and not required:
                continue
            key = slot["key"]
            if key in self.skipped:
                continue
            if key not in self.facts:
                out.append(slot)
                continue
            v = self.facts[key]
            if required and (v is None or str(v).strip() == ""):
                out.append(slot)
        return out

    def next_question(self) -> dict | None:
        m = self.missing(required_only=True)
        if m:
            return m[0]
        m = self.missing(required_only=False)
        return m[0] if m else None

    @property
    def ready(self) -> bool:
        return self.domain is not None and not self.missing(required_only=True)

    def completeness(self) -> tuple[int, int]:
        req = [s for s in self.schema() if s.get("required")]
        have = sum(1 for s in req if str(self.facts.get(s["key"]) or "").strip())
        return have, len(req)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "domain_label": DOMAIN_LABELS.get(self.domain or "", self.domain),
            "lang": self.lang,
            "facts": dict(self.facts),
            "flags": list(self.flags),
            "notes": list(self.notes),
            "complete": self.ready,
            "completeness": list(self.completeness()),
        }


# --- the computed answers ----------------------------------------------------


def limitation_key_for(case: CaseFile) -> str | None:
    if case.domain == "consumer":
        return "consumer"
    if case.domain == "deposit":
        return "deposit"
    if case.domain == "salary":
        wage = case.money("monthly_wage")
        # The Payment of Wages route has a 12-month deadline and a wage ceiling.
        # Where the wage is unknown we report the tighter deadline, because
        # telling someone they have longer than they do is the costly error.
        if wage is None or wage <= 24000:
            return "salary_pwa"
        return "salary_civil"
    return None


def cause_of_action_date(case: CaseFile, today: date | None = None) -> tuple[date | None, str]:
    """When the clock started, and why that date."""
    today = today or date.today()
    if case.domain == "deposit":
        refused = case.when("refused_on", today)
        vacated = case.when("vacated_on", today)
        if refused:
            return refused, "the date the landlord refused to refund the deposit"
        if vacated:
            return vacated, "the date you handed over the house, when the deposit became payable"
        return None, ""
    if case.domain == "salary":
        due = case.when("due_since", today)
        if due:
            return due, "the date the first unpaid salary became payable"
        return None, ""
    if case.domain == "consumer":
        problem = case.when("problem_on", today)
        paid = case.when("paid_on", today)
        if problem:
            return problem, "the date the problem arose or the refund was refused"
        if paid:
            return paid, "the date you paid"
        return None, ""
    return None, ""


def compute_deadline(
    case: CaseFile, kb: KnowledgeBase, today: date | None = None
) -> Deadline | None:
    today = today or date.today()
    key = limitation_key_for(case)
    if not key:
        return None
    rule: LimitationRule | None = kb.limitation_for(key)
    if not rule:
        return None
    start, desc = cause_of_action_date(case, today)
    if not start:
        return None

    expires = add_months(start, rule.months)
    days_left = (expires - today).days
    return Deadline(
        rule_key=key,
        label=rule.label,
        runs_from_date=start,
        runs_from_desc=desc or rule.runs_from,
        expires_on=expires,
        days_left=days_left,
        expired=days_left < 0,
        urgent=0 <= days_left <= rule.warn_at_months_left * 30,
        condonable=rule.condonable,
        condonation_note=rule.condonation_note,
        cites=list(rule.cites),
    )


@dataclass
class ForumOption:
    forum: Forum
    fee: int | None
    fee_label: str

    def to_dict(self) -> dict:
        return {
            "id": self.forum.id,
            "name": self.forum.name,
            "stage": self.forum.stage,
            "how": self.forum.how,
            "fee": self.fee,
            "fee_label": self.fee_label,
            "lawyer_needed": self.forum.lawyer_needed,
            "typical_time": self.forum.typical_time,
            "caveat": self.forum.caveat,
            "cites": list(self.forum.cites),
        }


def choose_forums(case: CaseFile, kb: KnowledgeBase) -> list[ForumOption]:
    if not case.domain:
        return []
    amount = case.money("amount")
    wage = case.money("monthly_wage")
    out: list[ForumOption] = []
    for f in kb.forums_for(case.domain, amount=amount, monthly_wage=wage):
        fee, label = f.fee_for(amount)
        out.append(ForumOption(forum=f, fee=fee, fee_label=label))
    # Cheapest, least formal route first: that is the honest order of advice.
    out.sort(key=lambda o: (0 if o.forum.stage == "before filing" else 1,
                            o.fee if o.fee is not None else 9_999_999))
    return out


# --- escalation --------------------------------------------------------------

RED_FLAG_PATTERNS: dict[str, re.Pattern[str]] = {
    "personal_safety": re.compile(
        r"\b(threaten(?:ed|ing)?|beat(?:en|ing)?|hit me|assault|violence|abuse|abusing|"
        r"stalk(?:ed|ing)?|harass(?:ed|ing|ment)|unsafe|scared for my life|"
        r"maar(?:a|ne)?\s*(?:ki)?\s*dhamki|dhamki|peeta|maarpeet)\b", re.I),
    "criminal_matter": re.compile(
        r"\b(fir|first information report|police (?:case|complaint|station)|arrest(?:ed)?|"
        r"bail|jail|custody|criminal case|chargesheet|cheating case|forgery|"
        r"thana|giraftaar)\b", re.I),
    "proceedings_started": re.compile(
        r"\b(court date|hearing|summons|already filed (?:a )?case|case is (?:in|pending)|"
        r"my lawyer|decree|ex[- ]?parte|adjourn)\b", re.I),
    "received_legal_notice": re.compile(
        r"\b(received (?:a )?(?:legal )?notice|they sent me a notice|notice se mila|"
        r"vakeel ka notice|advocate(?:'s)? notice)\b", re.I),
    "ownership_dispute": re.compile(
        r"\b(title|ownership|who owns|mutation|partition|inherit(?:ance|ed)?|"
        r"sale deed|property dispute|possession of the (?:land|plot))\b", re.I),
}


@dataclass
class Escalation:
    rule_id: str
    trigger: str
    message: str
    urgency: str
    extra: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def detect_escalations(
    case: CaseFile, kb: KnowledgeBase, deadline: Deadline | None = None
) -> list[Escalation]:
    """Rule-based. The decision to send someone to a human is never the model's."""
    by_trigger = {r["trigger"]: r for r in kb.escalation_rules}
    found: list[Escalation] = []

    def add(trigger: str, extra: str = "") -> None:
        r = by_trigger.get(trigger)
        if not r or any(f.trigger == trigger for f in found):
            return
        found.append(
            Escalation(rule_id=r["id"], trigger=trigger, message=r["message"],
                       urgency=r.get("urgency", "medium"), extra=extra or r.get("extra", ""))
        )

    haystack = " ".join(
        [case.raw_story] + [str(v) for v in case.facts.values() if v is not None]
    )
    for trigger, rx in RED_FLAG_PATTERNS.items():
        if rx.search(haystack):
            add(trigger)

    if deadline and deadline.expired:
        add("limitation_expired")

    amount = case.money("amount")
    if amount is not None and amount > 2_000_000:
        add("amount_above_2000000")

    if case.domain is None:
        add("domain_unknown")

    order = {"immediate": 0, "high": 1, "medium": 2, "low": 3}
    found.sort(key=lambda e: order.get(e.urgency, 9))
    return found


@dataclass
class Assessment:
    """Everything computed, with nothing generated. The model narrates this."""

    case: CaseFile
    deadline: Deadline | None
    forums: list[ForumOption]
    escalations: list[Escalation]
    citation_ids: list[str]

    @property
    def must_see_lawyer(self) -> bool:
        return any(e.urgency in ("immediate", "high") for e in self.escalations)

    def to_dict(self) -> dict:
        return {
            "case": self.case.to_dict(),
            "deadline": self.deadline.to_dict() if self.deadline else None,
            "forums": [f.to_dict() for f in self.forums],
            "escalations": [e.to_dict() for e in self.escalations],
            "must_see_lawyer": self.must_see_lawyer,
            "citation_ids": list(self.citation_ids),
        }


def assess(case: CaseFile, kb: KnowledgeBase, today: date | None = None) -> Assessment:
    """The whole deterministic layer, in one call."""
    deadline = compute_deadline(case, kb, today)
    forums = choose_forums(case, kb)
    escalations = detect_escalations(case, kb, deadline)

    cites: list[str] = []
    for cid in (deadline.cites if deadline else []):
        if cid not in cites:
            cites.append(cid)
    for f in forums:
        for cid in f.forum.cites:
            if cid not in cites:
                cites.append(cid)
    if case.domain:
        for p in kb.for_domain(case.domain, include_general=True):
            if p.id not in cites:
                cites.append(p.id)

    return Assessment(
        case=case, deadline=deadline, forums=forums,
        escalations=escalations, citation_ids=cites,
    )


def ask_in(slot: dict, lang: str) -> str:
    """The question for a slot, in the script the person is actually writing.

    Devanagari for Hindi, Roman for Hinglish, English otherwise. Replying to
    someone's Devanagari in Roman script is the small signal that a tool was
    not built for them.
    """
    if lang == "hi":
        return slot.get("ask_hi") or slot.get("ask_hing") or slot["ask"]
    if lang == "hinglish":
        return slot.get("ask_hing") or slot.get("ask_hi") or slot["ask"]
    return slot["ask"]
