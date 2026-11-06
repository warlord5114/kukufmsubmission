"""Turning a messy story into structured facts.

This is the one job the model is genuinely better at than code: reading
"landlord ne 45k deposit rakha hua hai, maine March mein ghar khaali kiya"
and producing {amount: 45000, vacated_on: "2026-03-01"}.

Two things keep it safe:

  * The model returns only slot values, never advice and never law. Nothing it
    writes here reaches the user directly; it populates a form, and the form is
    shown back for confirmation.
  * Every value it returns is re-parsed by the deterministic parsers in
    casefile.py. A date the model invents in the wrong format is dropped, not
    trusted. A number it hallucinates is still only a number in a field the
    user can see and correct.

There is also a regex extractor that runs first and always. It handles the
common shapes without a model call, which makes the offline provider genuinely
useful rather than a stub.
"""

from __future__ import annotations

import re
from datetime import date

from .casefile import SLOTS, CaseFile, parse_bool, parse_date, parse_money
from .llm.base import LLMProvider, extract_json

# --- domain classification (no model needed) ---------------------------------

# Weighted signals. Weight matters because the generic words overlap: "flat"
# appears in a builder-refund case as often as in a tenancy one, so a bare
# mention is worth far less than "landlord" or "security deposit".
DOMAIN_SIGNALS: dict[str, list[tuple[str, int]]] = {
    "deposit": [
        (r"\bsecurity deposit\b", 3), (r"\blandlord\b", 3), (r"\btenant\b", 3),
        (r"\brent(?:al)? agreement\b", 3), (r"\bmakaan\s*maalik\b", 3),
        (r"\bmakan\s*malik\b", 3), (r"\bमकान\s*मालिक\b", 3), (r"\bकिरायेदार\b", 3),
        (r"\bdeposit\b", 2), (r"\bvacat(?:e|ed|ing)\b", 2), (r"\bmoved out\b", 2),
        (r"\bghar kha?ali\b", 2), (r"\bkiray[ae]\b", 2), (r"\bमकान\b", 2),
        (r"\bकिराया\b", 2), (r"\bजमा\s*राशि\b", 2), (r"\bडिपॉजिट\b", 2),
        (r"\bbrokerage\b", 1), (r"\bmakaan\b", 1), (r"\blease\b", 1),
        (r"\bpg\b", 1), (r"\bpaying guest\b", 2),
    ],
    "salary": [
        (r"\bsalary\b", 3), (r"\bwages?\b", 3), (r"\bemployer\b", 3),
        (r"\bappointment letter\b", 3), (r"\bpayslip\b", 3), (r"\btankhwah\b", 3),
        (r"\bvetan\b", 3), (r"\bpagar\b", 3), (r"\bसैलरी\b", 3), (r"\bवेतन\b", 3),
        (r"\bतनख्वाह\b", 3), (r"\bfull and final\b", 3), (r"\bf&f\b", 3),
        (r"\bnotice period\b", 2), (r"\bresign(?:ed|ation)?\b", 2),
        (r"\bnaukri\b", 2), (r"\bनौकरी\b", 2), (r"\bemploy(?:ed|ment)\b", 2),
        (r"\bhr\b", 1), (r"\bboss\b", 1), (r"\boffice\b", 1), (r"\bकंपनी\b", 1),
    ],
    "consumer": [
        (r"\brefund\b", 3), (r"\bdefective\b", 3), (r"\bfaulty\b", 3),
        (r"\bwarranty\b", 3), (r"\be-?commerce\b", 3), (r"\bseller\b", 3),
        (r"\bरिफंड\b", 3), (r"\bपैसा वापस\b", 3), (r"\bpaisa wapas\b", 3),
        (r"\bbuilder\b", 3), (r"\bpossession was promised\b", 3),
        (r"\border(?:ed)?\b", 2), (r"\bdelivery\b", 2), (r"\bbooking\b", 2),
        (r"\bcancell?(?:ed|ation)\b", 2), (r"\bamazon\b", 2), (r"\bflipkart\b", 2),
        (r"\bsaamaan\b", 2), (r"\bsaman\b", 2), (r"\bdukaan\b", 2),
        (r"\bदुकान\b", 2), (r"\bसामान\b", 2), (r"\bखराब\b", 2),
        (r"\bwebsite\b", 1), (r"\bticket\b", 1), (r"\bbought\b", 1),
        (r"\bpurchase[ds]?\b", 1), (r"\bkharab\b", 1),
    ],
}


RE_DEVANAGARI_ANY = re.compile(r"[ऀ-ॿ]")


def _compile_signal(pattern: str) -> re.Pattern[str]:
    r"""Compile a signal pattern, dropping \b around Devanagari.

    Python's \b is Unicode-aware but Devanagari matras (ै, ी, ं) are non-spacing
    marks, which are not word characters -- so `\bसैलरी\b` never matches, and
    every Hindi signal silently scored zero. Devanagari words are space
    separated here, so plain substring matching is correct and sufficient.
    """
    if RE_DEVANAGARI_ANY.search(pattern):
        pattern = pattern.replace(r"\b", "")
    return re.compile(pattern)


_COMPILED_SIGNALS: dict[str, list[tuple[re.Pattern[str], int]]] = {
    domain: [(_compile_signal(p), w) for p, w in patterns]
    for domain, patterns in DOMAIN_SIGNALS.items()
}


def classify_domain(text: str) -> tuple[str | None, float, dict[str, int]]:
    """Which of the three problems is this? Returns (domain, confidence, scores)."""
    low = (text or "").lower()
    scores: dict[str, int] = {}
    for domain, patterns in _COMPILED_SIGNALS.items():
        scores[domain] = sum(w for rx, w in patterns if rx.search(low))
    best = max(scores, key=lambda k: scores[k]) if scores else None
    if not best or scores[best] == 0:
        return None, 0.0, scores
    total = sum(scores.values())
    runner_up = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
    # Confidence reflects both strength and separation from the next domain.
    margin = 1.0 if scores[best] >= runner_up * 1.5 else 0.6
    conf = (scores[best] / max(1, total)) * margin
    return best, round(min(1.0, conf), 2), scores


# --- regex slot extraction ---------------------------------------------------

# A money amount must be 3+ digits, or carry a unit. Allowing a bare "1" let
# "my 25000 deposit. I moved out on 1 October" match the day of the month as
# the deposit, because the keyword-first pattern reached across the full stop.
_NUM = (r"(?:rs\.?|inr|₹|रु\.?|रुपये)?\s*"
        r"(?:[\d,]{3,}(?:\.\d+)?|\d+(?:\.\d+)?\s*(?:lakhs?|lacs?|crores?|k|हज़ार|लाख))"
        r"\s*(?:lakhs?|lacs?|crores?|k|हज़ार|लाख)?")
# Gap that cannot cross a sentence boundary or another number.
_GAP = r"[^\d.;!?\n]"
_CUR = r"(?:rs\.?|inr|₹|रु\.?|रुपये)\s*[\d,]+(?:\.\d+)?\s*(?:lakhs?|lacs?|crores?|k)?"
_PERMONTH = r"(?:per month|a month|/\s*month|monthly|mahina|mahine|महीना|महीने|p\.?m\.?)"
# Dates in every shape people type, Devanagari month names included.
_DATE = (r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
         r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s+\d{4}"
         r"|[A-Za-z]+\s+\d{1,2},?\s+\d{4}"
         r"|[A-Za-z]+\s+\d{4}"
         r"|\d{1,2}\s+[ऀ-ॿ]+\s+\d{4}"
         r"|[ऀ-ॿ]+\s+\d{4})")

RE_AMOUNT_NEAR = {
    "amount": [
        rf"(?:deposit|security|डिपॉजिट|जमा){_GAP}{{0,25}}?({_NUM})",
        # "my 25000 deposit" -- number before the keyword, no currency marker.
        # Requiring "Rs" here lost the amount in half the natural phrasings.
        rf"([\d,]{{3,}}(?:\.\d+)?\s*(?:lakhs?|lacs?|crores?|k)?)\s*\w{{0,8}}\s*"
        rf"(?:deposit|security|डिपॉजिट)",
        rf"({_CUR}){_GAP}{{0,25}}?(?:deposit|security|डिपॉजिट)",
        rf"(?:unpaid|pending|outstanding|owe[sd]?|due|baki|bakaya|बाकी){_GAP}{{0,25}}?({_NUM})",
        rf"(?:total(?:ling|s)?(?: of)?|around|about|approx\w*|कुल)\s*({_NUM})",
        # Consumer phrasing: "ordered X for 24000", "paid a builder 7500000"
        rf"(?:paid|spent|bought|ordered|booked|cost|price|diye|diya|दिए|दिया)"
        rf"{_GAP}{{0,30}}?({_NUM})",
        rf"\bfor\s+({_CUR})",
        rf"\bfor\s+([\d,]{{4,}}(?:\.\d+)?\s*(?:lakhs?|lacs?|crores?|k)?)\b",
    ],
    "monthly_rent": [
        rf"(?:rent|kiray[ae]|किराया|किराये){_GAP}{{0,25}}?({_NUM})",
        rf"({_NUM})\s*{_PERMONTH}\s*(?:rent|kiraya|किराया)",
    ],
    "monthly_wage": [
        rf"(?:salary|wage|tankhwah|pagar|vetan|सैलरी|वेतन|तनख्वाह){_GAP}{{0,30}}?({_NUM})",
        # "I was on 85000 per month" -- no currency marker at all
        rf"({_NUM})\s*{_PERMONTH}",
        rf"{_PERMONTH}\D{{0,15}}?({_NUM})",
    ],
}

# Each slot gets patterns for BOTH word orders. English puts the date after the
# verb ("vacated on 3 March"); Hindi and Hinglish are verb-final and put it
# before ("3 March ko ghar khaali kiya"). Only handling the first order silently
# dropped every date in the Hinglish and Devanagari test cases, which then
# dropped the deadline -- the single most important number in the answer.
_VACATE_V = (r"vacat(?:e|ed|ing)|moved? out|left (?:the |a |my )?(?:house|flat|room|place|"
             r"rented\s+\w+)|handed over|kha?ali kiya|खाली किया|छोड़ा")
_DUE_V = (r"unpaid since|not paid since|pending since|due (?:on|since)|nahi mili|"
          r"nahi diya|से नहीं|नहीं मिली|owed since|outstanding since")
_PAID_V = r"paid|bought|ordered|booked|purchased|खरीदा|दिया"
_REFUSE_V = (r"refus(?:e|ed|ing)|denied|said no|mana kiya|mana kar diya|इनकार|मना किया|"
             r"stopped replying|stopped responding")

RE_DATE_NEAR = {
    "vacated_on": [
        rf"(?:{_VACATE_V})\D{{0,25}}?{_DATE}",
        rf"{_DATE}\s*(?:ko|को|,)?\s*\w{{0,12}}\s*(?:{_VACATE_V})",
        rf"(?:in|on)\s+{_DATE}\D{{0,30}}?(?:{_VACATE_V})",
    ],
    "due_since": [
        rf"(?:{_DUE_V})\D{{0,20}}?{_DATE}",
        rf"{_DATE}\s*(?:se|से|from)\s*\w{{0,12}}\s*(?:{_DUE_V})",
        rf"(?:since|from)\s+{_DATE}",
    ],
    "paid_on": [
        rf"(?:{_PAID_V})\D{{0,30}}?(?:on\s+)?{_DATE}",
        rf"{_DATE}\s*(?:ko|को)?\s*\w{{0,10}}\s*(?:{_PAID_V})",
    ],
    "refused_on": [
        rf"(?:{_REFUSE_V})\D{{0,25}}?{_DATE}",
        rf"{_DATE}\D{{0,25}}?(?:{_REFUSE_V})",
    ],
    "problem_on": [
        rf"(?:acknowledged|promised a refund|went wrong|broke|damaged|arrived|"
        rf"refus(?:e|ed)|complained)\D{{0,25}}?(?:on\s+)?{_DATE}",
    ],
}

RE_BOOL_NEAR = {
    "has_agreement": (
        r"\b(?:have|hai|signed|there is)\b[^.]{0,30}\b(?:rent )?agreement\b",
        r"\bno\b[^.]{0,20}\bagreement\b|\bagreement nahi\b|\bwithout (?:any )?agreement\b",
    ),
    "has_appointment_letter": (
        r"\b(?:have|hai|signed|got)\b[^.]{0,30}\b(?:appointment letter|offer letter|contract)\b",
        r"\bno\b[^.]{0,20}\b(?:appointment letter|offer letter|contract)\b|\bletter nahi\b",
    ),
    "has_proof_of_payment": (
        r"\b(?:receipt|bill|invoice|bank transfer|upi|neft|screenshot|statement)\b",
        r"\b(?:cash|no receipt|receipt nahi|bina receipt)\b",
    ),
    "still_employed": (
        r"\bstill (?:working|employed)\b|\babhi bhi kaam\b",
        r"\b(?:resigned|quit|left|fired|terminated|laid off|nikal diya|chhod diya)\b",
    ),
}


def extract_with_regex(text: str, domain: str, today: date | None = None) -> dict:
    """Best-effort slot fill with no model. Only confident matches are returned."""
    low = (text or "").lower()
    out: dict = {}

    for key, patterns in RE_AMOUNT_NEAR.items():
        if key not in {s["key"] for s in SLOTS.get(domain, [])}:
            continue
        for p in patterns:
            if m := re.search(p, low, re.I):
                v = parse_money(m.group(1))
                if v and v > 0:
                    out[key] = v
                    break

    for key, patterns in RE_DATE_NEAR.items():
        if key not in {s["key"] for s in SLOTS.get(domain, [])}:
            continue
        for p in patterns:
            if m := re.search(p, low, re.I):
                d = parse_date(m.group(1), today)
                if d:
                    out[key] = d.isoformat()
                    break

    for key, (yes_p, no_p) in RE_BOOL_NEAR.items():
        if key not in {s["key"] for s in SLOTS.get(domain, [])}:
            continue
        if re.search(no_p, low, re.I):
            out[key] = False
        elif re.search(yes_p, low, re.I):
            out[key] = True

    if m := re.search(
        r"\b(?:in|at|from)\s+(delhi|mumbai|bangalore|bengaluru|chennai|kolkata|pune|"
        r"hyderabad|noida|gurgaon|gurugram|ahmedabad|jaipur|lucknow|chandigarh|kochi|"
        r"indore|bhopal|patna|nagpur|surat|maharashtra|karnataka|tamil nadu|kerala|"
        r"telangana|gujarat|rajasthan|punjab|haryana|bihar|odisha|assam|west bengal|"
        r"uttar pradesh|madhya pradesh|andhra pradesh)\b",
        low,
    ):
        out["state"] = m.group(1).title()

    return out


# --- model-assisted extraction -----------------------------------------------

EXTRACT_SYSTEM = """\
You extract facts from a person's description of a legal problem in India. You
are a form-filler, not an adviser.

Hard rules:
  - Output ONLY a JSON object of field values. No prose, no advice, no law.
  - Use ONLY the field names given. Omit any field the person did not mention.
  - NEVER guess a value. If the person did not say the amount, omit it. A wrong
    number here produces a wrong legal deadline downstream.
  - Dates: output YYYY-MM-DD. If only a month and year were given, use the
    first day of that month. If only a year, use January 1 of that year.
  - Money: output a plain number of rupees, no symbols, no commas. "45k" is
    45000, "1.5 lakh" is 150000, "2 crore" is 20000000.
  - Booleans: true or false.
  - The person may write in English, Hindi, or Romanised Hindi (Hinglish).
    Understand all three.

Also return:
  "mentioned_risks": a list of any of these that the person described, as
  plain strings: "threat_or_violence", "police_or_fir", "court_case_started",
  "received_legal_notice", "property_ownership_dispute". Omit if none.
"""


def build_extract_prompt(text: str, domain: str, known: dict, request_id: str) -> str:
    fields = []
    for slot in SLOTS.get(domain, []):
        if slot["key"] in known and str(known[slot["key"]]).strip():
            continue
        fields.append(f'  "{slot["key"]}" ({slot["type"]}) — {slot["ask"]}')
    known_block = (
        "\n".join(f"  {k}: {v}" for k, v in known.items() if str(v).strip())
        or "  (nothing yet)"
    )
    return f"""\
REQUEST_ID: {request_id}
TASK: extract_facts
PROBLEM TYPE: {domain}

ALREADY KNOWN (do not re-extract, do not contradict):
{known_block}

FIELDS STILL NEEDED:
{chr(10).join(fields) if fields else "  (none)"}

WHAT THE PERSON WROTE:
\"\"\"
{text}
\"\"\"

Return the JSON object now. Omit anything they did not actually say."""


def extract_facts(
    text: str,
    domain: str,
    case: CaseFile,
    provider: LLMProvider | None = None,
    *,
    today: date | None = None,
    request_id: str = "extract",
) -> tuple[dict, list[str]]:
    """Regex first, then the model for whatever is still missing.

    Returns (new_facts, risk_tags). Every value is re-parsed and anything that
    does not parse is discarded rather than stored.
    """
    found = extract_with_regex(text, domain, today)
    risks: list[str] = []

    if provider is not None:
        known = {**case.facts, **found}
        prompt = build_extract_prompt(text, domain, known, request_id)
        try:
            raw = provider.complete(prompt, system=EXTRACT_SYSTEM, max_tokens=600)
            data = extract_json(raw)
            if isinstance(data, dict):
                risks = [str(r) for r in (data.pop("mentioned_risks", None) or [])]
                valid_keys = {s["key"] for s in SLOTS.get(domain, [])}
                for k, v in data.items():
                    if k not in valid_keys or v in (None, "", []):
                        continue
                    found.setdefault(k, v)
        except Exception:  # noqa: BLE001
            # The model is an accelerator here, never a dependency. A failed
            # call leaves the regex results and the user is simply asked.
            pass

    # Re-parse everything. The model's output is a suggestion, not a fact.
    cleaned: dict = {}
    types = {s["key"]: s["type"] for s in SLOTS.get(domain, [])}
    for k, v in found.items():
        kind = types.get(k)
        if kind == "money":
            pv = parse_money(v)
            if pv is not None and pv > 0:
                cleaned[k] = pv
        elif kind == "date":
            pv = parse_date(v, today)
            if pv is not None:
                cleaned[k] = pv.isoformat()
        elif kind == "bool":
            pv = parse_bool(v)
            if pv is not None:
                cleaned[k] = pv
        else:
            s = str(v).strip()
            if s:
                cleaned[k] = s
    return cleaned, risks
