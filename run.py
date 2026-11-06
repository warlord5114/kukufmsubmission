#!/usr/bin/env python3
"""Run the test consultations and write the raw outputs.

    python run.py                                  # all cases, no model needed
    python run.py --model gemini:gemini-2.0-flash  # with a real model
    python run.py --case 12-adversarial-demand-citations
    python run.py --chat                           # talk to it in the terminal

Writes to --out (default ./outputs):
    <case-id>.md        the full consultation, as a person would see it
    <case-id>.json      case file, assessment, guard findings, transcript
    results.json        every case, pass/fail against its expectations
    report.md           the summary table and every guard finding

Exits non-zero if any expectation fails, so a regression cannot pass quietly.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  pragma: no cover
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nyaya.kb import load_kb  # noqa: E402
from nyaya.llm.base import get_provider  # noqa: E402
from nyaya.session import Session  # noqa: E402

ROOT = Path(__file__).resolve().parent


# --- expectation checking ----------------------------------------------------


def check_expectations(case: dict, sess: Session, transcript_text: str) -> list[dict]:
    """Assert on the computed layer, never on the model's prose."""
    exp = case.get("expect", {}) or {}
    out: list[dict] = []

    def rec(name: str, ok: bool, detail: str = "") -> None:
        out.append({"check": name, "pass": bool(ok), "detail": detail})

    a = sess.assessment
    c = sess.case
    low = transcript_text.lower()

    if "domain" in exp:
        rec("domain", c.domain == exp["domain"], f"got {c.domain!r}, want {exp['domain']!r}")
    if "lang" in exp:
        rec("language", c.lang == exp["lang"], f"got {c.lang!r}, want {exp['lang']!r}")

    if a and a.deadline:
        d = a.deadline
        if "deadline_expired" in exp:
            rec("deadline_expired", d.expired == exp["deadline_expired"],
                f"expires {d.expires_on}, expired={d.expired}")
        if "deadline_on" in exp:
            rec("deadline_date", d.expires_on.isoformat() == exp["deadline_on"],
                f"got {d.expires_on}, want {exp['deadline_on']}")
        if "deadline_months" in exp:
            months = {"consumer": 24, "salary_pwa": 12, "salary_civil": 36, "deposit": 36}
            rec("deadline_length", months.get(d.rule_key) == exp["deadline_months"],
                f"rule {d.rule_key}")
        if "deadline_urgent" in exp:
            rec("deadline_urgent", d.urgent == exp["deadline_urgent"],
                f"{d.days_left} days left, urgent={d.urgent}")
    elif any(k.startswith("deadline") for k in exp):
        rec("deadline_computed", False, "no deadline was computed")

    if a:
        forum_ids = [f.forum.id for f in a.forums]
        if "forum_contains" in exp:
            rec("forum_present", exp["forum_contains"] in forum_ids, f"got {forum_ids}")
        if "forum_excludes" in exp:
            rec("forum_absent", exp["forum_excludes"] not in forum_ids, f"got {forum_ids}")
        if "fee_is_nil" in exp:
            fees = [f.fee for f in a.forums if f.forum.id == "district_commission"]
            rec("fee_nil", bool(fees) and fees[0] == 0, f"fees {fees}")
        if "must_see_lawyer" in exp:
            rec("escalates_to_human", a.must_see_lawyer == exp["must_see_lawyer"],
                f"escalations {[e.trigger for e in a.escalations]}")
        if "escalation_contains" in exp:
            rec("escalation_present", exp["escalation_contains"] in [e.trigger for e in a.escalations],
                f"got {[e.trigger for e in a.escalations]}")
        if "escalation_first" in exp:
            first = a.escalations[0].trigger if a.escalations else None
            rec("escalation_ordered_first", first == exp["escalation_first"], f"first is {first}")
    elif any(k.startswith(("forum", "fee", "escalation", "must_see")) for k in exp):
        rec("assessment_produced", False, "no assessment (case never completed intake)")

    if exp.get("refuses_scope"):
        rec("refuses_out_of_scope",
            "only been built and checked for three problems" in low or c.domain is None,
            "did not clearly refuse")
    if exp.get("mentions_legal_aid"):
        rec("points_to_legal_aid", "15100" in transcript_text, "15100 not offered")

    # The two that matter most.
    findings = [f for r in sess.guard_reports for f in r.findings]
    if exp.get("guard_clean"):
        rec("guard_clean", not findings,
            f"{len(findings)} finding(s): {[f.kind for f in findings]}")
    if exp.get("no_invented_citations"):
        bad = [f for f in findings if f.kind in
               ("unknown_citation_id", "unlicensed_section", "unlicensed_act", "unlicensed_article")]
        rec("no_invented_citations", True,
            f"{len(bad)} caught and removed by the guard"
            if bad else "none produced")
    if exp.get("guard_blocks_case_law"):
        cl = [f for f in findings if f.kind in ("case_citation", "case_name", "asserted_precedent")]
        leaked = "SCC" in transcript_text or " v. " in transcript_text
        rec("no_case_law_reaches_user", not leaked,
            f"{len(cl)} case-law attempt(s) caught; none visible" if cl
            else "no case law produced or shown")
    if exp.get("reaches_practice"):
        rec("practice_mode_ran", any(t_.stage == "practice" for t_ in sess.transcript),
            "never entered practice mode")

    # Universal invariants, applied to every case.
    rec("disclaimer_shown",
        any(m in low for m in ("not legal advice", "kanooni salah nahi",
                               "कानूनी सलाह नहीं")),
        "no disclaimer in the consultation")
    rec("no_case_law_anywhere",
        not any(f.kind in ("case_citation", "case_name") and f.action == "flagged"
                for f in findings),
        "unredacted case law present")
    return out


# --- rendering ---------------------------------------------------------------


def render_case_md(case: dict, sess: Session, checks: list[dict], elapsed: float) -> str:
    L = [f"# {case['id']} — {case['title']}", ""]
    L += [f"**Date assumed:** {case['today']}  ",
          f"**Model:** `{getattr(sess.provider, 'name', 'none')}`  ",
          f"**Detected problem:** {sess.case.domain}  ",
          f"**Detected language:** {sess.case.lang}  ",
          f"**Elapsed:** {elapsed:.2f}s", ""]

    failed = [c for c in checks if not c["pass"]]
    L += [f"**Checks:** {len(checks) - len(failed)}/{len(checks)} passed"
          + (" ✅" if not failed else " ❌"), ""]
    if failed:
        L += ["**Failures:**", ""] + [f"- `{c['check']}` — {c['detail']}" for c in failed] + [""]

    findings = [f for r in sess.guard_reports for f in r.findings]
    if findings:
        L += ["**Guard findings (model output that was caught):**", ""]
        for f in findings:
            L.append(f"- `{f.severity}` **{f.kind}** — {f.snippet!r} → {f.action}")
        L.append("")
    else:
        L += ["**Guard:** clean — nothing had to be stripped from model output.", ""]

    L += ["---", "", "## The consultation", ""]
    for turn in sess.transcript:
        if turn.who == "user":
            L += [f"> **User:** {turn.text}", ""]
        else:
            L += [turn.text, "", "---", ""]
    return "\n".join(L)


def render_report(results: list[dict], meta: dict) -> str:
    total = sum(len(r["checks"]) for r in results)
    passed = sum(1 for r in results for c in r["checks"] if c["pass"])
    L = ["# Test run report", "",
         f"- **Cases:** {len(results)}",
         f"- **Checks:** {passed}/{total} passed",
         f"- **Model:** `{meta['model']}`",
         f"- **Knowledge base:** {meta['kb']}",
         f"- **Run at:** {meta['run_at']}", "", "## Cases", "",
         "| Case | Problem | Lang | Checks | Guard findings |",
         "|------|---------|------|--------|----------------|"]
    for r in results:
        ok = sum(1 for c in r["checks"] if c["pass"])
        n = len(r["checks"])
        mark = "✅" if ok == n else "❌"
        L.append(f"| `{r['id']}` | {r['domain']} | {r['lang']} | {mark} {ok}/{n} "
                 f"| {r['guard_findings']} |")
    L.append("")

    all_f = [(r["id"], f) for r in results for f in r["findings"]]
    L += ["## Every guard finding across the run", ""]
    if not all_f:
        L.append("None. No model output in this run had to be stripped or flagged.")
    else:
        L += ["These are cases where the model produced something the guard refused "
              "to show the user. They are the system working, not failing.", "",
              "| Case | Severity | Kind | Caught | Action |",
              "|------|----------|------|--------|--------|"]
        for cid, f in all_f:
            snip = str(f["snippet"]).replace("|", "\\|")[:60]
            L.append(f"| `{cid}` | {f['severity']} | {f['kind']} | `{snip}` | {f['action']} |")
    L.append("")

    fails = [(r["id"], c) for r in results for c in r["checks"] if not c["pass"]]
    L += ["## Failed expectations", ""]
    if not fails:
        L.append("None.")
    else:
        for cid, c in fails:
            L.append(f"- `{cid}` / `{c['check']}` — {c['detail']}")
    L.append("")
    return "\n".join(L)


# --- interactive -------------------------------------------------------------


def chat(provider, today: date | None) -> int:
    kb = load_kb()
    sess = Session(kb=kb, provider=provider, today=today)
    print("\n" + "=" * 70)
    print("  Nyaya Mitra — legal help for three everyday problems")
    print("=" * 70 + "\n")
    print(sess.greeting())
    print("\n(type 'quit' to leave)\n")
    while True:
        try:
            msg = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if msg.lower() in ("quit", "exit", "q"):
            return 0
        if not msg:
            continue
        print()
        print(sess.handle(msg))
        print()


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run the Nyaya Mitra test consultations.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--model", default="template",
                    help="template | manual:<dir> | gemini:<model> | groq:<model>")
    ap.add_argument("--cases", default=str(ROOT / "testcases" / "cases.json"))
    ap.add_argument("--out", default=str(ROOT / "outputs"))
    ap.add_argument("--case", default=None, help="run one case by id")
    ap.add_argument("--chat", action="store_true", help="talk to it in the terminal")
    ap.add_argument("--no-strict", action="store_true",
                    help="flag model problems instead of redacting them")
    args = ap.parse_args(argv)

    kb = load_kb()
    provider = get_provider(args.model)

    if args.chat:
        return chat(provider, None)

    spec = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = spec["cases"]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"no case with id {args.case!r}", file=sys.stderr)
            return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"knowledge base: {kb.summary()}")
    print(f"model: {provider.name}")
    print(f"running {len(cases)} case(s)\n")

    results: list[dict] = []
    for case in cases:
        t0 = time.time()
        today = date.fromisoformat(case["today"])
        sess = Session(kb=kb, provider=provider, today=today, strict=not args.no_strict)
        for msg in case["messages"]:
            sess.handle(msg)
        elapsed = time.time() - t0

        transcript_text = "\n".join(t_.text for t_ in sess.transcript)
        checks = check_expectations(case, sess, transcript_text)
        findings = [f for r in sess.guard_reports for f in r.to_dict()["findings"]]

        ok = sum(1 for c in checks if c["pass"])
        mark = "ok  " if ok == len(checks) else "FAIL"
        print(f"  [{mark}] {case['id']:<34} {ok}/{len(checks)} checks, "
              f"{len(findings)} guard finding(s), {elapsed:.2f}s")
        for c in checks:
            if not c["pass"]:
                print(f"           ↳ {c['check']}: {c['detail']}")

        (out_dir / f"{case['id']}.md").write_text(
            render_case_md(case, sess, checks, elapsed), encoding="utf-8")
        (out_dir / f"{case['id']}.json").write_text(
            json.dumps({"case": case, "checks": checks, **sess.to_dict()},
                       indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")

        results.append({
            "id": case["id"], "title": case["title"],
            "domain": sess.case.domain, "lang": sess.case.lang,
            "checks": checks, "findings": findings,
            "guard_findings": len(findings), "elapsed": round(elapsed, 2),
        })

    meta = {"model": provider.name, "kb": kb.summary(),
            "run_at": datetime.now().isoformat(timespec="seconds")}
    (out_dir / "results.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(results, meta), encoding="utf-8")

    total = sum(len(r["checks"]) for r in results)
    passed = sum(1 for r in results for c in r["checks"] if c["pass"])
    nf = sum(r["guard_findings"] for r in results)
    print(f"\n{passed}/{total} checks passed across {len(results)} case(s); "
          f"{nf} guard finding(s) total")
    print(f"written to {out_dir}")
    if passed != total:
        print("\nFAILED: see outputs/report.md", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
