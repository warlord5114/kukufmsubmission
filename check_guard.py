#!/usr/bin/env python3
"""Measure the guard against a corpus of realistic model hallucinations.

    python check_guard.py

This is the central quality claim of the project, so it is a separate,
committed, reproducible measurement rather than a line in a write-up. It takes
15 outputs of the kind a general-purpose model actually produces about Indian
law and checks two things for each:

    1. the guard raised the right kind of finding, and
    2. the false claim is absent from the text a user would have seen.

Two of the items are clean, correct answers. Those must pass through untouched:
a filter that mangles good output would be swapped off within a week.

Writes outputs/guard_report.md and exits non-zero on any failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001  pragma: no cover
        pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from nyaya.guard import check  # noqa: E402
from nyaya.kb import load_kb  # noqa: E402


def main() -> int:
    kb = load_kb()
    spec = json.loads((ROOT / "testcases" / "hallucinations.json").read_text(encoding="utf-8"))
    allowed = spec["allowed_ids"]
    items = spec["items"]

    print(f"knowledge base: {kb.summary()}")
    print(f"corpus: {len(items)} model outputs\n")

    rows: list[dict] = []
    failures = 0

    for item in items:
        rep = check(item["text"], kb, allowed, strict=True)
        kinds = {f.kind for f in rep.findings}
        problems: list[str] = []

        for want in item.get("must_catch", []):
            if want not in kinds:
                problems.append(f"did not raise {want!r} (raised {sorted(kinds) or 'nothing'})")
        if not item.get("must_catch") and rep.findings:
            problems.append(f"fired on a clean answer: {sorted(kinds)}")

        for banned in item.get("must_not_survive", []):
            if banned.lower() in rep.text.lower():
                problems.append(f"{banned!r} still visible to the user")
        for needed in item.get("must_survive", []):
            if needed.lower() not in rep.text.lower():
                problems.append(f"{needed!r} was destroyed (should have survived)")

        ok = not problems
        failures += 0 if ok else 1
        print(f"  [{'ok  ' if ok else 'FAIL'}] {item['id']:<34} "
              f"{len(rep.findings)} finding(s) {sorted(kinds) if kinds else ''}")
        for p in problems:
            print(f"           ↳ {p}")

        rows.append({
            "id": item["id"], "note": item["note"], "input": item["text"],
            "output": rep.text, "findings": rep.to_dict()["findings"],
            "kinds": sorted(kinds), "pass": ok, "problems": problems,
        })

    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)

    caught = sum(1 for r in rows if r["kinds"])
    clean_items = [r for r in rows if not r["problems"] and not r["kinds"]]
    L = ["# Guard: adversarial results", "",
         f"- **Corpus:** {len(rows)} realistic model outputs about Indian law",
         f"- **Passed:** {len(rows) - failures}/{len(rows)}",
         f"- **Items where the guard intervened:** {caught}",
         f"- **Clean answers left untouched:** {len(clean_items)}", "",
         "Each item below shows what the model produced and what a user would "
         "actually have seen. The point of the project is the gap between them.",
         "", "| # | What the model did | Guard raised | Passed |",
         "|---|--------------------|--------------|--------|"]
    for r in rows:
        L.append(f"| `{r['id']}` | {r['note']} | {', '.join(r['kinds']) or '—'} "
                 f"| {'✅' if r['pass'] else '❌'} |")
    L.append("")

    for r in rows:
        L += [f"## {r['id']}", "", f"_{r['note']}_", "",
              "**The model produced:**", "", "> " + r["input"].replace("\n", "\n> "), ""]
        if r["findings"]:
            L += ["**The guard raised:**", ""]
            for f in r["findings"]:
                L.append(f"- `{f['severity']}` **{f['kind']}** — "
                         f"caught `{f['snippet']}` → {f['action']}")
            L.append("")
        else:
            L += ["**The guard raised:** nothing — this answer was clean.", ""]
        L += ["**The user saw:**", "", "> " + (r["output"] or "_(nothing)_").replace("\n", "\n> "), ""]
        if r["problems"]:
            L += ["**FAILED:**", ""] + [f"- {p}" for p in r["problems"]] + [""]
        L += ["---", ""]

    (out_dir / "guard_report.md").write_text("\n".join(L), encoding="utf-8")
    (out_dir / "guard_results.json").write_text(
        json.dumps({"passed": len(rows) - failures, "total": len(rows), "items": rows},
                   indent=2, ensure_ascii=False),
        encoding="utf-8")

    print(f"\n{len(rows) - failures}/{len(rows)} passed; "
          f"guard intervened on {caught}, left {len(clean_items)} clean answer(s) alone")
    print(f"written to {out_dir / 'guard_report.md'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
