# Nyaya Mitra — an AI lawyer that cannot invent law

**Kuku AI Generalist Assignment — Problem 2.**

A conversational assistant for three everyday legal problems in India, in
English, Hindi or Hinglish:

| | |
|---|---|
| 🏠 | a **security deposit** a landlord will not return |
| 💼 | **salary** that stopped |
| 🛒 | a **refund** for defective goods or a service that failed |

It builds the case from a messy story, works out the deadline and the forum,
drafts a legal notice, and lets the person rehearse the argument against the
other side.

---

## The one idea

The brief names the failure directly: *"AI models confidently invent laws,
sections and past cases, so tell us how yours avoids that."*

Prompting a model not to hallucinate does not work. So in this system the
model is **never the source of law**. It has three narrow jobs — read a messy
story into a form, say the situation back warmly, and write one factual
paragraph of a notice — and a filter sits between it and the user.

```
    the person                     ┌──────────────────────────┐
         │                         │  kb/provisions.json      │
         ▼                         │  30 human-written entries│
    ┌─────────┐   messy story      │  every one source-linked │
    │ extract │ ◄─── model ────┐   └────────────┬─────────────┘
    └────┬────┘                │                │ only these ids
         │ structured facts    │                ▼ may be cited
         ▼                     │        ┌───────────────┐
    ┌─────────────────┐        └────────│   guard.py    │◄── model prose
    │  casefile.py    │                 └───────┬───────┘
    │  NO MODEL HERE  │                         │ stripped + reported
    │  deadline       │                         ▼
    │  forum + fee    │─────────────────►  what the person sees
    │  escalation     │
    └─────────────────┘
```

**Everything that must be right is computed, not generated.** The deadline, the
days remaining, the forum, the filing fee, and the decision to send someone to
a real lawyer are all arithmetic over the dates the person gave. You can
recompute every number by hand.

**Everything the model writes passes the guard.** Three rules:

1. **Citations are ids.** The model may only write `[[CPA2019_S69]]`. The id
   must exist in the knowledge base *and* be one of the ids supplied for that
   turn. The visible citation text is then rendered by us from the KB, so the
   words of the law never come from the model.
2. **Loose references are caught.** A section number, Act name or Article in
   running prose that was not supplied is redacted — and so is the sentence
   around it, because "under Section 23 he owes a penalty" with only the
   citation removed leaves the invented penalty standing.
3. **Case law is always refused.** The KB deliberately contains no judgments,
   so there is nothing to check a case against. Every case-shaped string is
   treated as fabricated whether or not it happens to be real.

### Does it work?

`check_guard.py` runs the guard against 15 realistic model outputs about Indian
law — invented sections, a fabricated Supreme Court judgment, a real Act
applied to the wrong problem, foreign law, constitutional Articles in a money
dispute, and two correct answers that must survive untouched.

```
15/15 passed; guard intervened on 13, left 2 clean answers alone
```

The full before/after for every item is in
[`outputs/guard_report.md`](outputs/guard_report.md). For example:

> **The model produced:** *"In Sharma v. Gupta (2018) 7 SCC 412, the Supreme
> Court held that a landlord withholding a security deposit without itemised
> proof commits an unfair trade practice. You can rely on this directly."*
>
> **The user saw:** *"[A claim about case law was removed here: this assistant
> has no verified database of judgments and will not cite them.] You can rely
> on this directly."*

---

## Run it

No API key, no network, no account. The whole legal pipeline is standard
library only.

```bash
python run.py                 # all 15 test consultations
```

```bash
python check_guard.py         # the hallucination corpus
```

```bash
python tests/test_nyaya.py    # 126 unit checks
```

Talk to it in the terminal:

```bash
python run.py --chat
```

Or run the chat UI (`pip install -r requirements.txt` first):

```bash
streamlit run app.py
```

### Swapping the model

The brief says a stronger model may be connected at interview time. It is a
flag, and no vendor SDK is imported anywhere except in its own adapter file.

```bash
python run.py --model gemini:gemini-2.0-flash     # GEMINI_API_KEY
```

```bash
python run.py --model groq:llama-3.3-70b-versatile    # GROQ_API_KEY
```

| spec | what it is |
|---|---|
| `template` | **default.** No model. Regex extraction plus templates over the computed assessment. Correct and complete, just stiff. |
| `manual:<dir>` | Writes prompts to disk, reads answers back. For when the model is a chat session; every prompt and reply is committed and auditable. |
| `gemini:<model>` | Google AI Studio free tier. |
| `groq:` / `openai:` / `openrouter:` | Any OpenAI-compatible endpoint, including a local one. |

Adding a provider is one file implementing one method
([`nyaya/llm/base.py`](nyaya/llm/base.py)).

---

## What it does in a consultation

1. **Works out the problem** from what the person wrote (weighted keyword
   signals, three languages). If it is not one of the three, it says so and
   hands them to free legal aid rather than guessing.
2. **Collects the facts** one question at a time, in the script they are
   writing in. It never asks the same thing a third time: if an answer will not
   parse it records the gap, moves on, and says what is missing.
3. **Computes the deadline** from the real cause-of-action date, and says
   plainly when it has already passed.
4. **Picks the forum and the fee**, cheapest and least formal first — the
   National Consumer Helpline before the District Commission, a written demand
   before a notice before a court.
5. **Escalates to a human** on rules, not on the model's judgement: a safety
   disclosure, a police complaint, an expired limitation period, a notice
   already received, a large amount, or anything out of scope. Safety outranks
   the money question and is printed first.
6. **Drafts a legal notice** — skeleton fixed, one narrative paragraph from the
   model, and it asks who to address before drafting so the output is actually
   sendable.
7. **Plays the other side** so the person can practise, then gives feedback.
   The roleplayed landlord goes through the guard too: a fake section quoted by
   a bluffing opponent is just as likely to make someone give up.

---

## Honest limits

- **Three problems only**, and it refuses everything else. It has no
  criminal, family, property-title or cheque-bounce knowledge and will not
  pretend otherwise.
- **No case law at all.** This is a deliberate capability gap. A real lawyer
  would cite precedent; this cannot, because it has no way to verify one.
- **State law is the weak spot.** Tenancy and shops-and-establishments law is
  state-specific, and the KB holds none of it. The system names the route
  ("check whether your state's Rent Act covers deposits") and refuses to state
  a section or limit. The Model Tenancy Act is carried with an explicit warning
  that it binds nobody unless the state has enacted it.
- **Volatile numbers go stale.** Pecuniary limits, filing fees and the wage
  ceiling are flagged `volatility: high` in the KB and dated
  (`last_reviewed`). They need a human to re-check, not a model.
- **The KB is summaries, not statute.** Every entry is plain-language text
  written by hand with a link to the original. The app never presents them as
  quotations.
- **Extraction is imperfect.** Regex handles the common shapes in all three
  languages; a model improves it. Anything it gets wrong is visible in the case
  file in the sidebar and can be corrected by typing the correction.

---

## Layout

```
kb/provisions.json     30 citable provisions, each source-linked and dated
kb/routes.json         forums, fees, limitation periods, escalation rules
nyaya/kb.py            loads and validates the KB; a dangling id is fatal
nyaya/guard.py         ← the hallucination filter
nyaya/casefile.py      slots, deadline arithmetic, forum choice, escalation
nyaya/extract.py       messy story → structured facts (regex, then model)
nyaya/lang.py          English / Hindi / Hinglish detection and strings
nyaya/advise.py        renders the assessment; model writes only the opening
nyaya/draft.py         legal notice, opponent roleplay, coaching
nyaya/session.py       the consultation state machine
nyaya/llm/             the swappable model adapters
testcases/             15 consultations + 15 adversarial model outputs
outputs/               every raw output, committed
```

~4,600 lines of Python, ~830 lines of knowledge base.

---

## Where the numbers in the write-up come from

| claim | reproduce with |
|---|---|
| 15/15 adversarial outputs blocked | `python check_guard.py` → `outputs/guard_report.md` |
| 84/84 functional checks across 15 consultations | `python run.py` → `outputs/report.md` |
| 126 unit checks | `python tests/test_nyaya.py` |
| the consultations themselves | `outputs/*.md` |

Every test fixes `today`, so the deadline arithmetic is reproducible and the
committed outputs do not rot.

---

**This is not a lawyer and not legal advice.** Free legal aid in India is a
right for many people: **15100** (NALSA), or the District Legal Services
Authority at any district court.
