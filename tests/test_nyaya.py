"""Tests for the parts that must not break silently.

    python tests/test_nyaya.py        (no pytest needed)

The emphasis is on the guarantees the advice depends on: the knowledge base is
internally consistent, the arithmetic is right, the escalation rules fire, and
the model cannot get an invented law past the guard.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Windows consoles default to cp1252 and these tests print rupee signs and
# Devanagari. `python -I` also ignores PYTHONIOENCODING, so set it here.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nyaya.casefile import (  # noqa: E402
    CaseFile, add_months, assess, cause_of_action_date, compute_deadline,
    detect_escalations, fmt_money, limitation_key_for, parse_bool, parse_date,
    parse_money,
)
from nyaya.extract import classify_domain, extract_with_regex  # noqa: E402
from nyaya.guard import check  # noqa: E402
from nyaya.kb import KBError, KnowledgeBase, load_kb  # noqa: E402
from nyaya.lang import detect  # noqa: E402
from nyaya.llm.base import extract_json  # noqa: E402
from nyaya.session import Session  # noqa: E402
from nyaya.llm.template import TemplateProvider  # noqa: E402

TODAY = date(2026, 11, 6)
KB = load_kb()
_failures: list[str] = []


def check_(cond: bool, label: str) -> None:
    if cond:
        print(f"  pass  {label}")
    else:
        print(f"  FAIL  {label}")
        _failures.append(label)


# --- knowledge base ----------------------------------------------------------


def test_kb() -> None:
    print("\nknowledge base")
    check_(len(KB.provisions) >= 25, f"{len(KB.provisions)} provisions loaded")
    check_(all(p.source for p in KB.provisions.values()),
           "every provision links to a source a user can check")
    check_(all(p.plain for p in KB.provisions.values()),
           "every provision has plain-language text")
    for domain in ("deposit", "salary", "consumer"):
        check_(len(KB.for_domain(domain)) >= 5, f"{domain} has enough law to advise on")
        check_(bool(KB.forums.get(domain)), f"{domain} has forums")
    check_(KB.limitation_for("consumer").months == 24, "consumer limitation is 2 years")
    check_(KB.limitation_for("salary_pwa").months == 12, "wage-authority limitation is 12 months")
    check_(KB.limitation_for("deposit").months == 36, "deposit limitation is 3 years")
    # The whole safety story depends on there being no case law to cite.
    check_(not any("v." in p.summary and "SCC" in p.summary for p in KB.provisions.values()),
           "the knowledge base contains no case citations")

    # A dangling cross-reference must stop the program, not degrade quietly.
    import json, tempfile, shutil
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(KB.dir / "provisions.json", tmp / "provisions.json")
    routes = json.loads((KB.dir / "routes.json").read_text(encoding="utf-8"))
    routes["limitation"]["consumer"]["cites"] = ["NO_SUCH_PROVISION"]
    (tmp / "routes.json").write_text(json.dumps(routes), encoding="utf-8")
    try:
        KnowledgeBase(tmp)
        check_(False, "a dangling citation raises KBError")
    except KBError:
        check_(True, "a dangling citation raises KBError")


# --- parsing -----------------------------------------------------------------


def test_money() -> None:
    print("\nmoney parsing")
    cases = {
        "45000": 45000, "Rs 45,000": 45000, "₹45,000": 45000,
        "45k": 45000, "1.5 lakh": 150000, "2 crore": 20000000,
        "Rs. 2,00,000": 200000, "36000 रुपये": 36000, "३६०००": 36000,
    }
    for raw, want in cases.items():
        got = parse_money(raw)
        check_(got == want, f"{raw!r} -> {want} (got {got})")
    check_(parse_money("no idea") is None, "unparseable money returns None, not 0")
    check_(fmt_money(1234567) == "₹12,34,567", "Indian digit grouping")
    check_(fmt_money(45000) == "₹45,000", "thousands grouped")


def test_dates() -> None:
    print("\ndate parsing")
    cases = {
        "2026-03-15": date(2026, 3, 15), "15/03/2026": date(2026, 3, 15),
        "15 March 2026": date(2026, 3, 15), "15th March 2026": date(2026, 3, 15),
        "March 2026": date(2026, 3, 1), "सितंबर 2026": date(2026, 9, 1),
        "अगस्त 2026": date(2026, 8, 1),
    }
    for raw, want in cases.items():
        got = parse_date(raw, TODAY)
        check_(got == want, f"{raw!r} -> {want} (got {got})")
    check_(parse_date("3 months ago", TODAY) == date(2026, 8, 8), "'3 months ago' is relative")
    check_(parse_date("never", TODAY) is None, "'never' is not a date")
    check_(parse_date("sometime last summer", TODAY) is None, "vague text returns None")
    # A month-only date must resolve to the FIRST of the month: that is the
    # earliest cause of action, so the deadline is never overstated.
    check_(parse_date("March 2026", TODAY).day == 1, "month-only resolves to day 1 (conservative)")


def test_bools() -> None:
    print("\nyes/no parsing, three languages")
    for raw in ("yes", "haan", "हाँ", "yes I have the agreement", "haan hai"):
        check_(parse_bool(raw) is True, f"{raw!r} is yes")
    for raw in ("no", "nahi", "नहीं", "no, I don't have it", "nahi, agreement nahi hai"):
        check_(parse_bool(raw) is False, f"{raw!r} is no")
    check_(parse_bool("maybe next week") is None, "ambiguous returns None")


def test_month_arithmetic() -> None:
    print("\nmonth arithmetic")
    check_(add_months(date(2026, 3, 15), 36) == date(2029, 3, 15), "3 years")
    check_(add_months(date(2026, 1, 31), 1) == date(2026, 2, 28), "clamps to end of February")
    check_(add_months(date(2024, 1, 31), 1) == date(2024, 2, 29), "handles a leap year")
    check_(add_months(date(2026, 12, 15), 12) == date(2027, 12, 15), "crosses a year")


def test_language_detection() -> None:
    print("\nlanguage detection")
    check_(detect("My landlord kept my deposit").lang == "en", "English")
    check_(detect("मेरी सैलरी नहीं मिली है").lang == "hi", "Devanagari Hindi")
    d = detect("Bhai mera makaan maalik deposit wapas nahi kar raha hai")
    check_(d.lang == "hinglish", f"Hinglish (got {d.lang})")
    check_(detect("ok").lang == "en", "a short English word is not Hinglish")


# --- the computed layer ------------------------------------------------------


def _deposit_case(**facts) -> CaseFile:
    base = {"amount": 45000, "vacated_on": "2026-03-15",
            "has_agreement": True, "has_proof_of_payment": True}
    base.update(facts)
    return CaseFile(domain="deposit", facts=base)


def test_deadlines() -> None:
    print("\ndeadline computation")
    d = compute_deadline(_deposit_case(), KB, TODAY)
    check_(d is not None and d.expires_on == date(2029, 3, 15), "deposit: 3 years from vacating")
    check_(d is not None and not d.expired, "not expired")

    # An explicit refusal moves the clock, and must take priority over vacating.
    d2 = compute_deadline(_deposit_case(refused_on="2026-06-01"), KB, TODAY)
    check_(d2.expires_on == date(2029, 6, 1), "refusal date wins over vacating date")

    old = compute_deadline(_deposit_case(vacated_on="2021-01-10"), KB, TODAY)
    check_(old.expired, "an old claim is flagged as time-barred")

    salary_low = CaseFile(domain="salary", facts={
        "amount": 54000, "monthly_wage": 18000, "due_since": "2026-08-01",
        "still_employed": True, "has_appointment_letter": False})
    check_(limitation_key_for(salary_low) == "salary_pwa",
           "a low wage opens the 12-month Payment of Wages route")
    check_(compute_deadline(salary_low, KB, TODAY).expires_on == date(2027, 8, 1),
           "12 months from the first unpaid month")

    salary_high = CaseFile(domain="salary", facts={
        "amount": 170000, "monthly_wage": 85000, "due_since": "2026-09-01",
        "still_employed": False, "has_appointment_letter": True})
    check_(limitation_key_for(salary_high) == "salary_civil",
           "a high wage closes the Payment of Wages route")

    # Unknown wage must assume the TIGHTER deadline. Telling someone they have
    # three years when they have one is the expensive direction to be wrong in.
    unknown = CaseFile(domain="salary", facts={"amount": 50000, "due_since": "2026-08-01"})
    check_(limitation_key_for(unknown) == "salary_pwa",
           "unknown wage assumes the shorter deadline")


def test_forums_and_fees() -> None:
    print("\nforum and fee selection")
    small = CaseFile(domain="consumer", facts={
        "amount": 24000, "paid_on": "2026-09-02", "problem_on": "2026-09-10",
        "what_bought": "washing machine", "has_proof_of_payment": True})
    ids = [f.forum.id for f in assess(small, KB, TODAY).forums]
    check_("district_commission" in ids, "small claim goes to the District Commission")
    check_("state_commission" not in ids, "and not to the State Commission")
    fee = next(f for f in assess(small, KB, TODAY).forums if f.forum.id == "district_commission")
    check_(fee.fee == 0, f"a claim under Rs 5 lakh has no filing fee (got {fee.fee})")

    big = CaseFile(domain="consumer", facts={
        "amount": 7500000, "paid_on": "2025-01-14", "problem_on": "2026-04-20",
        "what_bought": "flat", "has_proof_of_payment": True})
    ids = [f.forum.id for f in assess(big, KB, TODAY).forums]
    check_("state_commission" in ids, "Rs 75 lakh goes to the State Commission")
    check_("district_commission" not in ids, "and not to the District Commission")

    mid = CaseFile(domain="consumer", facts={
        "amount": 1500000, "paid_on": "2026-01-01", "problem_on": "2026-02-01",
        "what_bought": "car", "has_proof_of_payment": True})
    f = next(x for x in assess(mid, KB, TODAY).forums if x.forum.id == "district_commission")
    check_(f.fee == 400, f"Rs 15 lakh sits in the Rs 400 slab (got {f.fee})")

    # A deposit must never be routed to a consumer commission.
    dep_ids = [f.forum.id for f in assess(_deposit_case(), KB, TODAY).forums]
    check_("district_commission" not in dep_ids,
           "a tenancy deposit is not sent to the consumer forum")


def test_escalation() -> None:
    print("\nescalation to a human")
    safety = _deposit_case()
    safety.raw_story = "he threatened to hit me if I asked again"
    triggers = [e.trigger for e in detect_escalations(safety, KB)]
    check_("personal_safety" in triggers, "a threat of violence escalates")
    check_(detect_escalations(safety, KB)[0].urgency == "immediate",
           "safety is ordered first, above the money question")

    crim = CaseFile(domain="salary", facts={"amount": 40000})
    crim.raw_story = "they filed a police complaint saying I stole from the office"
    check_("criminal_matter" in [e.trigger for e in detect_escalations(crim, KB)],
           "a police complaint escalates")

    notice = _deposit_case()
    notice.raw_story = "I received a legal notice from his advocate"
    check_("received_legal_notice" in [e.trigger for e in detect_escalations(notice, KB)],
           "receiving a notice escalates")

    big = _deposit_case(amount=5000000)
    check_("amount_above_2000000" in [e.trigger for e in detect_escalations(big, KB)],
           "a large amount escalates")

    expired = _deposit_case(vacated_on="2021-01-10")
    dl = compute_deadline(expired, KB, TODAY)
    check_("limitation_expired" in [e.trigger for e in detect_escalations(expired, KB, dl)],
           "an expired deadline escalates")

    calm = assess(_deposit_case(), KB, TODAY)
    check_(not calm.must_see_lawyer, "an ordinary case does not cry wolf")


# --- extraction --------------------------------------------------------------


def test_classification() -> None:
    print("\nproblem classification")
    cases = {
        "my landlord will not return my security deposit": "deposit",
        "makaan maalik deposit wapas nahi kar raha": "deposit",
        "company has not paid my salary for 3 months": "salary",
        "मेरी कंपनी ने सैलरी नहीं दी": "salary",
        "I ordered a phone and they refuse to refund the defective unit": "consumer",
        "I paid a builder for a flat and they refuse to refund": "consumer",
    }
    for text, want in cases.items():
        got, conf, scores = classify_domain(text)
        check_(got == want, f"{text[:45]!r} -> {want} (got {got}, {scores})")
    got, conf, _ = classify_domain("I want to know about child custody after divorce")
    check_(got is None or conf < 0.25, "an out-of-scope problem is not forced into a bucket")


def test_regex_extraction() -> None:
    print("\nfact extraction without a model")
    f = extract_with_regex(
        "My landlord is keeping my 25000 deposit. I moved out on 1 October 2026.",
        "deposit", TODAY)
    check_(f.get("amount") == 25000, f"amount before the keyword (got {f.get('amount')})")
    check_(f.get("vacated_on") == "2026-10-01", f"English date order (got {f.get('vacated_on')})")

    f = extract_with_regex(
        "30000 ka deposit tha, maine 10 August 2026 ko ghar khaali kiya tha.",
        "deposit", TODAY)
    check_(f.get("vacated_on") == "2026-08-10",
           f"Hinglish verb-final date order (got {f.get('vacated_on')})")

    f = extract_with_regex("I was on 85000 per month", "salary", TODAY)
    check_(f.get("monthly_wage") == 85000,
           f"wage with no currency marker (got {f.get('monthly_wage')})")

    f = extract_with_regex("I ordered a washing machine online for 24000", "consumer", TODAY)
    check_(f.get("amount") == 24000, f"'for 24000' (got {f.get('amount')})")


# --- the guard ---------------------------------------------------------------


ALLOWED = ["CPA2019_S69", "CONTRACT1872_S73", "LIMITATION1963_ART55", "EVIDENCE_PRACTICE"]


def test_guard() -> None:
    print("\nhallucination guard")
    r = check("You have three years [[LIMITATION1963_ART55]].", KB, ALLOWED)
    check_(r.clean, f"a valid citation passes clean ({[f.kind for f in r.findings]})")
    check_("Limitation Act 1963" in r.text, "and renders from the KB, not the model")

    r = check("See [[FAKE_ID_123]] for this.", KB, ALLOWED)
    check_(not r.clean and "FAKE_ID_123" not in r.text, "an invented id is removed")
    check_(r.blocked, "an invented id is critical")

    r = check("Under [[PWA1936_S15]] you can apply.", KB, ALLOWED)
    check_("PWA1936_S15" not in r.text, "a real but unsupplied id is removed")

    r = check("File under Section 138 of the NI Act within 30 days.", KB, ALLOWED)
    check_("Section 138" not in r.text, "an unlicensed section is redacted")

    r = check("In Sharma v. Gupta (2018) 7 SCC 412 the court held you win.", KB, ALLOWED)
    check_("Sharma" not in r.text and "SCC" not in r.text, "a fabricated case is removed")
    check_(r.blocked, "case law is critical severity")

    r = check("The Supreme Court held deposits must be returned in 30 days.", KB, ALLOWED)
    check_("Supreme Court held" not in r.text, "asserted precedent is removed")

    # The claim must go with its basis, not just the citation.
    r = check("Under Section 23 of the Tenancy Rules, 2019 he owes a penalty.", KB, ALLOWED)
    check_("penalty" not in r.text, "a claim whose basis was redacted goes too")

    r = check("You will definitely win this case.", KB, ALLOWED)
    check_(any(f.kind == "outcome_guarantee" for f in r.findings), "a promised outcome is flagged")

    # A filter that mangles correct output would be switched off in a week.
    good = ("Keep the messages where he admits the amount [[EVIDENCE_PRACTICE]]. "
            "You can claim compensation [[CONTRACT1872_S73]].")
    r = check(good, KB, ALLOWED)
    check_(r.clean, f"a clean answer is untouched ({[f.kind for f in r.findings]})")
    check_("Keep the messages" in r.text, "and its prose survives intact")

    r = check("Aapke paas teen saal hain [[LIMITATION1963_ART55]].", KB, ALLOWED)
    check_(r.clean, "the guard does not misfire on Hinglish")


def test_json_extraction() -> None:
    print("\nJSON extraction")
    check_(extract_json('{"a": 1}') == {"a": 1}, "bare")
    check_(extract_json('```json\n{"a": 1}\n```') == {"a": 1}, "fenced")
    check_(extract_json('Sure!\n{"a": 1}\nHope that helps.') == {"a": 1}, "wrapped in prose")
    check_(extract_json('{"a": [1, 2,],}') == {"a": [1, 2]}, "trailing commas")
    for bad in ("", "no json here"):
        try:
            extract_json(bad)
            check_(False, f"{bad!r} raises")
        except ValueError:
            check_(True, f"{bad!r} raises")


# --- end to end --------------------------------------------------------------


def test_session_never_loops() -> None:
    print("\nconversation safety")
    s = Session(kb=KB, provider=TemplateProvider(), today=TODAY)
    s.handle("My landlord kept my deposit")
    # Answer everything with something unparseable. The system must still
    # terminate and produce an answer rather than re-asking forever.
    for _ in range(14):
        s.handle("hmm I am not sure about that")
    check_(s.stage == "assessed", f"reaches an assessment anyway (stage={s.stage})")
    check_(bool(s.case.skipped), f"and records what it could not learn ({s.case.skipped})")

    s2 = Session(kb=KB, provider=TemplateProvider(), today=TODAY)
    out = s2.handle("I want to know about child custody and maintenance after divorce")
    check_("15100" in out, "an out-of-scope problem is handed to free legal aid")
    check_(s2.case.domain is None, "and is not forced into a covered bucket")


def test_session_full_consultation() -> None:
    print("\nend-to-end consultation")
    s = Session(kb=KB, provider=TemplateProvider(), today=TODAY)
    s.handle("My landlord in Pune is not returning my security deposit of 45000. "
             "I moved out on 15 March 2026. I have the agreement and the bank transfer.")
    check_(s.case.domain == "deposit", "classified")
    check_(s.case.ready, f"intake complete in one message (missing {s.case.missing()})")
    a = s.assessment
    check_(a is not None and a.deadline.expires_on == date(2029, 3, 15), "deadline computed")

    notice_prompt = s.handle("notice")
    check_("addressed" in notice_prompt.lower() or "name" in notice_prompt.lower(),
           "asks who the notice is for before drafting it")
    notice = s.handle("Mr R. Deshmukh, 14 Shaniwar Peth, Pune")
    check_("LEGAL NOTICE" in notice, "produces a notice")
    check_("Mr R. Deshmukh" in notice, "addressed to a real person, not a placeholder")
    check_("₹45,000" in notice, "with the amount")
    check_("DRAFT" in notice, "clearly marked as a draft to check")
    check_("registered post" in notice.lower(), "and says how to send it")

    summary = s.handle("summary")
    check_("case file" in summary.lower(), "produces a case file to take to a lawyer")
    check_("15100" in summary, "which carries the legal aid number")

    check_(all(r.clean for r in s.guard_reports),
           "no guard finding across a clean consultation")


def main() -> int:
    test_kb()
    test_money()
    test_dates()
    test_bools()
    test_month_arithmetic()
    test_language_detection()
    test_deadlines()
    test_forums_and_fees()
    test_escalation()
    test_classification()
    test_regex_extraction()
    test_guard()
    test_json_extraction()
    test_session_never_loops()
    test_session_full_consultation()

    print("\n" + "=" * 62)
    if _failures:
        print(f"{len(_failures)} FAILURE(S):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
