# Submission checklist

Everything the Kuku form asks for, and exactly what to paste where.

---

## Before you start

**Pick one problem.** The brief says *"One problem, one submission. Please pick
one problem and submit once."* Both are built:

| | |
|---|---|
| `ai-lawyer/` | **Problem 2 — AI Lawyer.** This folder. |
| `promo-finder/` | Problem 1 — Find the Promos. Complete and passing. |

Submit one. Do not link both.

---

## 1. Solution doc (required, PDF, ≤ 2 pages)

The source is [`docs/SOLUTION.html`](docs/SOLUTION.html), typeset to fit two A4
pages exactly.

1. Open `docs/SOLUTION.html` in Chrome.
2. `Ctrl+P` → **Destination: Save as PDF**.
3. Set **Margins: Default**, **Scale: 100%**, and tick **Background graphics**
   (without it the diagram and the callout boxes lose their shading).
4. Confirm the preview shows **2 pages**, then save as `Nyaya-Mitra-Solution.pdf`.
5. Upload to Drive → **Share → Anyone with the link → Viewer**.

[`docs/SOLUTION.md`](docs/SOLUTION.md) is the same content as Markdown if you
would rather convert it another way.

## 2. Prototype link (required)

Either of these satisfies the brief. Doing both is better.

### GitHub repo

```bash
git remote add origin https://github.com/<you>/nyaya-mitra.git
```

```bash
git push -u origin main
```

Make the repo **public**, or the reviewer cannot open it.

### Streamlit Community Cloud (free)

1. Go to <https://share.streamlit.io> and sign in with GitHub.
2. **New app** → pick the repo → branch `main` → main file `app.py`.
3. Under **Advanced settings**, set **Python version 3.11**.
4. Deploy. No secrets are needed: the app defaults to `--model template`, which
   needs no API key and no network.
5. *(Optional)* To run the deployed app with a real model, add
   `GEMINI_API_KEY = "..."` under **Secrets**, then type
   `gemini:gemini-2.0-flash` into the Model box in the sidebar.

## 3. Raw outputs (required)

[`outputs/`](outputs) — 34 files, all committed, all regenerable:

| file | what it is |
|---|---|
| `guard_report.md` | **The headline.** 15 realistic model hallucinations, with what the model produced and what the user actually saw, side by side. |
| `report.md` | 15 scripted consultations, 84 checks, pass/fail table. |
| `01-…` … `15-…` `.md` | Each consultation in full, as a person would see it. |
| `01-…` … `15-…` `.json` | Case file, computed assessment, guard findings, transcript. |
| `results.json`, `guard_results.json` | Machine-readable results. |

If the form wants a folder rather than the repo, upload `outputs/` to Drive and
share it **Anyone with the link → Viewer**.

## 4. Walkthrough video (optional, ≤ 3 min, recommended)

A run that shows the point of the build:

1. **0:00–0:30** — the problem. Say the sentence from the brief: models invent
   laws, sections and cases. Show `kb/provisions.json` — a closed, hand-written,
   source-linked set.
2. **0:30–1:30** — `streamlit run app.py`. Click the **Deposit** example. Point
   at the sidebar: case file filling up, deadline computed to a real date, guard
   panel. Then click **Draft a legal notice**.
3. **1:30–2:30** — `python check_guard.py`. 15/15. Open `outputs/guard_report.md`
   and read the `H02-fabricated-case` before/after aloud. That is the whole
   project in fifteen seconds.
4. **2:30–3:00** — the honest limits: no case law, state law is a hole, the guard
   is regex over English-shaped references and would miss invented law written in
   fluent Hindi.

Loom, or an unlisted YouTube upload. Check the share link opens in a private window.

## 5. Form fields

| field | value |
|---|---|
| Problem chosen | **Problem 2 — Build an AI Lawyer** |
| Solution doc | Drive link to the PDF |
| Prototype | GitHub repo URL (and the Streamlit URL if deployed) |
| Raw outputs | Repo `outputs/` folder, or a Drive link |
| Walkthrough | Loom / YouTube link, if recorded |

---

## Last checks before you hit submit

- [ ] Every link opens in a **private browsing window**. This is the single most
      common way a good submission gets marked down.
- [ ] The PDF is **2 pages**, not 3.
- [ ] The GitHub repo is **public**.
- [ ] You can answer *"why this problem?"* and *"where does it fail?"* in your own
      words — both are in the solution doc, and the brief says they will be
      discussed in the interview.
- [ ] You have run `python run.py` yourself at least once. If shortlisted, you
      will be asked to run it on a new input live.

## Running it cold, the way a reviewer will

```bash
git clone <your repo> && cd nyaya-mitra
```

```bash
python run.py
```

That needs no API key, no network and no `pip install`. Expect
`84/84 checks passed`.

```bash
python check_guard.py
```

Expect `15/15 passed`.

```bash
python tests/test_nyaya.py
```

Expect `all checks passed`.

---

## Note on the Problem 1 folder

`promo-finder/` reads Kuku's screenplay pack, which the brief says is
confidential: *"You're welcome to use it with AI tools for your work, but
please don't publish or share it."*

**Its raw outputs quote screenplay dialogue verbatim**, so they are Kuku's
content too, not just the episode files. Its `.gitignore` therefore excludes
both the pack and `outputs/`. If you ever submit Problem 1 instead, send those
raw outputs to Kuku as a **private Drive link**, not in a public repo.

This submission (`ai-lawyer/`) contains **no Kuku content at all** — Problem 2
has no input pack — so the repo and its outputs are safe to make public.
