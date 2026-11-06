# Nyaya Mitra — an AI lawyer that cannot invent law

**Problem 2 · Kuku AI Generalist Assignment**

---

### Why this problem

Problem 1 is a ranking task with a human already in the loop: an editor sees
every promo before it ships, so a bad suggestion costs a few minutes. Problem 2
has no such backstop. Someone with no money and no lawyer will act on what the
tool says — send a notice, miss a deadline, walk into the wrong forum — and a
confident wrong answer does real harm. That asymmetry is what made it the more
interesting problem to build carefully, and the brief points straight at the
hard part: *"AI models confidently invent laws, sections and past cases, so
tell us how yours avoids that."*

I treated that sentence as the whole specification.

### Assumptions I made

| | |
|---|---|
| **Scope** | Three problems, in depth, rather than many in breadth: landlord security deposit, unpaid salary, consumer refund. Depth is what lets the tool name a forum and a deadline instead of giving general encouragement. |
| **Language** | English, Devanagari Hindi, and Romanised Hinglish — because Hinglish is how most people actually type. Not other languages: I would be generating legal phrasing I cannot verify, which contradicts the point of the build. |
| **The user** | Has a phone, not a lawyer. Wants to know *what to do on Monday*, not what the law is. So the output is a deadline, a place to go, a fee, and a next step. |
| **Success** | The person takes one correct concrete step they would not otherwise have taken. Usually that is a written demand or a notice, not litigation. |
| **No case law** | A deliberate capability gap, explained below. |

### How it works

The system is split by what *must* be right.

**Computed, never generated.** The limitation deadline, days remaining, the
forum, the filing-fee slab, and the decision to escalate to a human are all
arithmetic over the dates the person gave, driven by `kb/routes.json`. A model
is confidently wrong about exactly these things — it will invent a pecuniary
limit or miscount twelve months — so it never touches them. Every number on
screen can be recomputed by hand.

**The model does three narrow jobs**: read a messy story into a structured form
(it is genuinely better than regex at *"maine 10 August ko ghar khaali kiya"*),
say the situation back in the person's own language, and write one factual
paragraph of a legal notice. It is given no legal authority in any of them.

**Everything it writes passes a guard** (`nyaya/guard.py`):

1. *Citations are ids.* The model may only emit `[[CPA2019_S69]]`, the id must
   exist in the knowledge base **and** have been supplied for that turn, and
   the visible citation text is rendered by us from the KB. The words of the
   law never come from the model.
2. *Loose references are caught.* A section, Act or Article in prose that was
   not supplied is redacted — **and so is the sentence around it**. Removing
   only the citation from *"under Section 23 of the Tenancy Rules he owes a
   penalty"* leaves the invented penalty standing, which is the more believable
   half.
3. *Case law is always refused.* The KB holds no judgments, so there is nothing
   to check one against. Every case-shaped string is treated as fabricated
   whether or not it happens to be real.

The knowledge base is 30 hand-written provisions, each with a plain-language
summary, a source URL, a confidence and a volatility flag. It is validated on
load: a dangling citation id is a fatal error, not a degraded answer.

### How I know it is good, and where it fails

Three suites, all reproducible, all committed:

| | result |
|---|---|
| **Adversarial corpus** — 15 realistic model outputs about Indian law | **15/15 blocked or passed correctly** |
| **Functional consultations** — 15 scripted cases | **84/84 checks** |
| **Unit tests** — arithmetic, parsing, escalation, guard | **126 checks** |

The adversarial corpus is the real evidence. It contains the failures a general
model actually produces: an invented section for a rule with no statutory
basis, a fabricated judgment with a real-looking citation, the Negotiable
Instruments Act applied to a tenancy dispute, IPC sections for a civil debt,
UK housing law, Article 21 for a money claim — plus **two correct answers that
must survive untouched**, because a filter that mangles good output gets
switched off in a week. `outputs/guard_report.md` shows what the model produced
and what the user saw for all 15.

**Where it fails, honestly:**

- **State law is the hole.** Tenancy and shops-and-establishments law is
  state-specific and the KB has none of it. The system names the route and
  refuses to state a section or a limit. That is correct but unsatisfying: a
  user in Maharashtra gets "check your state's Rent Act", not an answer.
- **No case law at all**, by design. A real lawyer cites precedent. This
  cannot, and says so.
- **Volatile numbers go stale.** Pecuniary limits, fees and the wage ceiling
  are flagged `volatility: high` and dated. They need a human to re-check.
- **Extraction is the weakest component.** Building it surfaced two bugs worth
  naming: Hindi and Hinglish are verb-final, so every date arrived *before* the
  verb and my English-ordered patterns dropped all of them — and no date means
  no deadline, the single most important output. Separately, Python's `\b` does
  not work around Devanagari (matras are non-spacing marks, not word
  characters), so every Hindi keyword silently scored zero. Both were caught by
  test cases, not by reading the code.
- **The guard is regex over English-shaped legal references.** A model writing
  invented law in fluent Hindi prose would evade it. That is the first thing I
  would fix.

### How it scales

Cost is near zero and flat: ~900 input / ~350 output tokens per grounded answer,
so a full consultation is under 10k tokens — inside the Gemini free tier, a
fraction of a paisa on paid Flash. The expensive part is not inference, it is
the knowledge base, and that is a human writing 30 entries once per domain.

That is the honest scaling story: **adding a problem is a day of careful
writing, not a prompt change.** A legal-aid NGO could own `provisions.json`
without touching code, and the load-time validation means a mistake in it stops
the program rather than reaching a user. The whole pipeline is standard library
only, so it runs offline on a cheap machine with no model at all — it loses the
conversational polish and keeps every legal guarantee.

Humans stay in the loop in two places that matter: the escalation rules hand
off to a real lawyer on safety, criminal matters, expired limitation and
anything out of scope; and every notice is marked a draft for the user to
check before sending.

### AI tools, and where they got it wrong

Built with Claude (Claude Code) throughout: architecture, the knowledge base
drafting, and the code. I used it hard and checked it harder.

**Where it got it wrong:** asked to draft the knowledge base, it produced
confident, fluent, wrongly-numbered provisions — a "Section 17 of the Model
Tenancy Act" for deposit refund timelines, and a Supreme Court judgment that
does not exist. That is not a footnote to this project; it *is* the project.
I kept those outputs, put them in `testcases/hallucinations.json`, and the
guard is built to catch exactly them. The tool that wrote the system also
generated its adversarial test set, by failing.

A second, smaller one: it initially wrote the Hindi interface strings in
Romanised Hindi for Devanagari users. Fluent, friendly, and the small
discourtesy that tells someone a tool was not built for them.

---

**Repo:** `ai-lawyer/` · **Run:** `python run.py` (no API key, no network)
**Evidence:** `outputs/guard_report.md`, `outputs/report.md`, `outputs/*.md`

*This is not a lawyer and not legal advice. Free legal aid in India: **15100**
(NALSA), or the District Legal Services Authority at any district court.*
