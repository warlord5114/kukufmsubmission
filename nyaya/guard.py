"""The hallucination guard.

The brief names the failure directly: models invent laws, sections and past
cases. Prompting against it does not work reliably, so this is not a prompt.
It is a filter that runs on every piece of model output before a user sees it.

Three rules, in increasing order of strictness:

1.  Citations are id-based. The model may only write ``[[CPA2019_S69]]``, and
    the id must exist in the knowledge base AND be one of the ids supplied for
    this turn. The user-facing citation text is then rendered by us from the
    KB, so the words of the law come from the KB and never from the model.
    An unknown id is removed.

2.  Loose legal references are caught. A model that is told to use ids will
    still sometimes write "under Section 138" in running prose. Any
    section-shaped or Act-shaped reference that is not licensed by the ids
    supplied for this turn is flagged, and in strict mode redacted.

3.  Case law is always refused. The KB deliberately contains no judgments, so
    there is nothing a case citation could be checked against. Every case-shaped
    string is treated as a fabrication regardless of whether it happens to be
    real. This is the only rule with no exceptions, because invented
    "precedent" is the most convincing and most damaging thing the model does.

The guard reports rather than silently repairs: `GuardReport.findings` is shown
in the UI and written to the raw outputs, so a reviewer can see exactly what was
caught. A clean run with zero findings is evidence; a run that quietly stripped
things would not be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .kb import KnowledgeBase

# --- patterns ----------------------------------------------------------------

RE_CITE = re.compile(r"\[\[\s*([A-Za-z0-9_]+)\s*\]\]")

# "Section 73", "Sec. 15(2)", "S.138", "u/s 420", "under section 12"
RE_SECTION = re.compile(
    r"\b(?:u/s|under\s+section|section|sections|sec\.?|s\.)\s*"
    r"(\d+[A-Za-z]?(?:\s*\(\s*\d+[A-Za-z]?\s*\))?)",
    re.I,
)
# "Article 21", "Art. 14"
RE_ARTICLE = re.compile(r"\b(?:article|art\.)\s*(\d+[A-Za-z]?)", re.I)

# "Consumer Protection Act, 2019" / "Indian Penal Code" / "IPC" / "CrPC"
RE_ACT_NAMED = re.compile(
    r"\b((?:[A-Z][\w'’-]*\s+){1,7}(?:Act|Code|Rules|Ordinance|Regulations))"
    r"(?:\s*,?\s*((?:19|20)\d{2}))?",
)
RE_ACT_ABBREV = re.compile(
    r"\b(IPC|CrPC|Cr\.P\.C\.?|CPC|C\.P\.C\.?|BNS|BNSS|BSA|NI\s+Act|N\.I\.\s*Act|"
    r"RERA|SARFAESI|POSH\s+Act|DV\s+Act|MV\s+Act|IT\s+Act)\b"
)

# Case law, in every shape it tends to arrive in.
RE_CASE_VS = re.compile(
    r"\b([A-Z][\w.&'’-]*(?:\s+[A-Z][\w.&'’-]*){0,5})\s+"
    r"(?:v\.?|vs\.?|versus)\s+"
    r"([A-Z][\w.&'’-]*(?:\s+[A-Z][\w.&'’-]*){0,5})\b"
)
RE_CASE_REPORTER = re.compile(
    r"\b(?:AIR\s*(?:19|20)\d{2}|\(\s*(?:19|20)\d{2}\s*\)\s*\d+\s*SCC|"
    r"(?:19|20)\d{2}\s*SCC\s*(?:OnLine)?|\d+\s*SCC\s*\d+|"
    r"SCC\s*OnLine|\(\s*(?:19|20)\d{2}\s*\)\s*\d+\s*SCR|"
    r"MANU/[A-Z]{2}/\d+|ILR\s*(?:19|20)\d{2})",
    re.I,
)
# "the Supreme Court held", "the High Court ruled in"
RE_COURT_HELD = re.compile(
    r"\b(?:Supreme Court|High Court|NCDRC|National Commission|Apex Court)\b[^.]{0,60}?"
    r"\b(?:held|ruled|observed|laid down|decided)\b",
    re.I,
)

# Phrases that promise an outcome. Not hallucination, but not safe either.
RE_GUARANTEE = re.compile(
    r"\b(?:you will (?:definitely|certainly|surely) (?:win|get|receive)|"
    r"guaranteed|i guarantee|100%\s*(?:sure|certain)|you are certain to win|"
    r"there is no chance you (?:will )?lose|cannot lose)\b",
    re.I,
)

SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}


@dataclass
class Finding:
    kind: str
    severity: str
    snippet: str
    message: str
    action: str  # "removed" | "flagged" | "rendered"


@dataclass
class GuardReport:
    text: str = ""
    findings: list[Finding] = field(default_factory=list)
    used_citations: list[str] = field(default_factory=list)
    rejected_citations: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    @property
    def worst(self) -> str:
        if not self.findings:
            return "none"
        return max(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 0)).severity

    @property
    def blocked(self) -> bool:
        """Critical findings mean the answer must not be shown as-is."""
        return any(f.severity == "critical" for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "clean": self.clean,
            "worst_severity": self.worst,
            "blocked": self.blocked,
            "used_citations": self.used_citations,
            "rejected_citations": self.rejected_citations,
            "findings": [
                {
                    "kind": f.kind, "severity": f.severity, "snippet": f.snippet,
                    "message": f.message, "action": f.action,
                }
                for f in self.findings
            ],
        }

    def summary(self) -> str:
        if self.clean:
            return f"Guard: clean. {len(self.used_citations)} citation(s), all resolved against the KB."
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.kind] = counts.get(f.kind, 0) + 1
        bits = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
        return f"Guard: {len(self.findings)} finding(s) [{bits}]; worst={self.worst}"


def _norm_section(s: str) -> str:
    return re.sub(r"\s+", "", s).strip().rstrip(".,")


# Sentence splitter that does not break on "s. 73", "v.", "AIR 1998 SC 1234"
# or decimal amounts -- all of which appear constantly in this domain.
RE_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[_*])")
_ABBREV_TAIL = re.compile(r"\b(?:s|ss|sec|art|v|vs|no|cl|pt|Rs|Mr|Mrs|Ms|Dr|i\.e|e\.g)\.$", re.I)


def _split_sentences(text: str) -> list[str]:
    rough = RE_SENTENCE_END.split(text)
    out: list[str] = []
    for part in rough:
        if out and _ABBREV_TAIL.search(out[-1].rstrip()):
            out[-1] = out[-1] + " " + part
        else:
            out.append(part)
    return out


def check(
    text: str,
    kb: KnowledgeBase,
    allowed_ids: list[str] | None = None,
    *,
    strict: bool = True,
    lang: str = "en",
) -> GuardReport:
    """Validate and render one piece of model output.

    `allowed_ids` is the set of provisions supplied to the model for this turn.
    Anything outside it is treated as unlicensed even if it exists in the KB,
    because the model should not be reaching for law it was not given.
    """
    rep = GuardReport()
    allowed = list(allowed_ids) if allowed_ids is not None else list(kb.provisions)
    allowed_set = set(allowed)

    # --- rule 1: id-based citations -------------------------------------
    def _render_cite(m: re.Match[str]) -> str:
        pid = m.group(1)
        prov = kb.get(pid)
        if prov is None:
            rep.rejected_citations.append(pid)
            rep.findings.append(
                Finding(
                    kind="unknown_citation_id", severity="critical", snippet=pid,
                    message=f"The model cited '{pid}', which is not in the knowledge base. Removed.",
                    action="removed",
                )
            )
            return ""
        if pid not in allowed_set:
            rep.rejected_citations.append(pid)
            rep.findings.append(
                Finding(
                    kind="out_of_scope_citation", severity="high", snippet=pid,
                    message=(
                        f"'{pid}' exists but was not supplied for this question, so it was "
                        f"not shown. The model reached outside the material it was given."
                    ),
                    action="removed",
                )
            )
            return ""
        if pid not in rep.used_citations:
            rep.used_citations.append(pid)
        # The visible words come from the KB, not from the model.
        return f"**{prov.cite_label()}**"

    out = RE_CITE.sub(_render_cite, text)

    # --- rule 3 first: case law is always a finding ----------------------
    case_rules = (
        (RE_CASE_REPORTER, "case_citation",
         "A law-report citation was produced. This system holds no verified judgment "
         "database, so every case citation is treated as fabricated."),
        (RE_CASE_VS, "case_name",
         "A case name was produced. This system holds no verified judgment database, "
         "so every case citation is treated as fabricated."),
        (RE_COURT_HELD, "asserted_precedent",
         "The answer asserts what a court has held. Without a verified judgment "
         "database that claim cannot be checked."),
    )
    # Redact the whole sentence, not the matched span. Snipping "AIR 1998" out of
    # "This follows AIR 1998 SC 1234 which settled the point" leaves a mutilated
    # sentence that still asserts a precedent. A sentence built around a fake
    # case has nothing worth keeping.
    sentences = _split_sentences(out)
    keep: list[str] = []
    for sent in sentences:
        # Record every rule a sentence trips, not just the first. One sentence
        # can carry a fabricated case name AND a fabricated reporter citation,
        # and the report is more useful when it names both.
        hits: list[Finding] = []
        for rx, kind, msg in case_rules:
            m = rx.search(sent)
            if m:
                hits.append(Finding(
                    kind=kind, severity="critical", snippet=m.group(0).strip(),
                    message=msg, action="sentence removed" if strict else "flagged",
                ))
        if hits:
            rep.findings.extend(hits)
            keep.append("_[A claim about case law was removed here: this assistant has no "
                        "verified database of judgments and will not cite them.]_"
                        if strict else sent)
        else:
            keep.append(sent)
    out = " ".join(s for s in keep if s).strip()

    # --- rule 2: loose statutory references ------------------------------
    ok_sections = kb.allowed_sections(allowed)
    ok_acts = kb.allowed_acts(allowed)

    for m in RE_SECTION.finditer(out):
        sec = _norm_section(m.group(1))
        bare = sec.split("(")[0]
        if sec in ok_sections or bare in ok_sections:
            continue
        rep.findings.append(
            Finding(
                kind="unlicensed_section", severity="high", snippet=m.group(0).strip(),
                message=(
                    f"'{m.group(0).strip()}' is not a section in the material supplied "
                    f"for this question. It may not exist, or may not say what the answer claims."
                ),
                action="redacted" if strict else "flagged",
            )
        )
    for m in RE_ARTICLE.finditer(out):
        art = _norm_section(m.group(1))
        if art in ok_sections or f"Schedule,Article{art}" in {_norm_section(s) for s in ok_sections}:
            continue
        # Limitation Act Articles are stored as "Schedule, Article 55".
        if any(art == _norm_section(s).replace("Schedule,Article", "") for s in ok_sections):
            continue
        rep.findings.append(
            Finding(
                kind="unlicensed_article", severity="high", snippet=m.group(0).strip(),
                message=f"'{m.group(0).strip()}' was not supplied for this question.",
                action="redacted" if strict else "flagged",
            )
        )

    for m in RE_ACT_NAMED.finditer(out):
        name = m.group(1).strip()
        year = m.group(2)
        full = f"{name}, {year}".lower() if year else name.lower()
        if full in ok_acts or name.lower() in ok_acts:
            continue
        if any(name.lower() in a or a in name.lower() for a in ok_acts):
            continue
        rep.findings.append(
            Finding(
                kind="unlicensed_act", severity="high", snippet=m.group(0).strip(),
                message=(
                    f"'{m.group(0).strip()}' is not in the knowledge base for this question. "
                    f"The assistant has not been given this law and must not advise on it."
                ),
                action="redacted" if strict else "flagged",
            )
        )
    for m in RE_ACT_ABBREV.finditer(out):
        rep.findings.append(
            Finding(
                kind="unlicensed_act", severity="high", snippet=m.group(0).strip(),
                message=(
                    f"'{m.group(0).strip()}' is outside the three areas this assistant "
                    f"covers. Advice on it would be unverified."
                ),
                action="redacted" if strict else "flagged",
            )
        )

    if strict:
        for rx in (RE_SECTION, RE_ARTICLE, RE_ACT_ABBREV):
            out = _redact_unlicensed(rx, out, ok_sections, ok_acts)
        out = _redact_unlicensed_acts(out, ok_acts)
        # Collapse any sentence left holding a redaction. Snipping the citation
        # out of "under Section 23 of the Tenancy Rules he is liable for a
        # statutory penalty" leaves the invented penalty asserted and only the
        # source removed, which is the more believable half. The claim has to
        # go with its basis.
        out = _collapse_redacted_sentences(out)

    # --- overconfidence ---------------------------------------------------
    for m in RE_GUARANTEE.finditer(out):
        rep.findings.append(
            Finding(
                kind="outcome_guarantee", severity="medium", snippet=m.group(0).strip(),
                message="The answer promises an outcome. No legal outcome is certain.",
                action="flagged",
            )
        )

    rep.text = re.sub(r"[ \t]{2,}", " ", out).strip()
    return rep


def _redact_unlicensed(
    rx: re.Pattern[str], text: str, ok_sections: set[str], ok_acts: set[str]
) -> str:
    def repl(m: re.Match[str]) -> str:
        whole = m.group(0)
        if rx is RE_ACT_ABBREV:
            return "[reference removed: outside this assistant's scope]"
        val = _norm_section(m.group(1))
        bare = val.split("(")[0]
        if val in ok_sections or bare in ok_sections:
            return whole
        if any(val == _norm_section(s).replace("Schedule,Article", "") for s in ok_sections):
            return whole
        return "[reference removed: not in the verified knowledge base]"

    return rx.sub(repl, text)


_REDACTION_MARK = "[reference removed"


def _collapse_redacted_sentences(text: str) -> str:
    """Replace any sentence containing a redaction with a single note."""
    kept: list[str] = []
    for sent in _split_sentences(text):
        if _REDACTION_MARK in sent:
            note = ("_[A claim here rested on law that is not in the verified "
                    "knowledge base, so it was removed.]_")
            if not kept or kept[-1] != note:
                kept.append(note)
        else:
            kept.append(sent)
    return " ".join(s for s in kept if s).strip()


def _redact_unlicensed_acts(text: str, ok_acts: set[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1).strip()
        year = m.group(2)
        full = f"{name}, {year}".lower() if year else name.lower()
        if full in ok_acts or name.lower() in ok_acts:
            return m.group(0)
        if any(name.lower() in a or a in name.lower() for a in ok_acts):
            return m.group(0)
        return "[reference removed: not in the verified knowledge base]"

    return RE_ACT_NAMED.sub(repl, text)


def render_sources(
    kb: KnowledgeBase, ids: list[str], lang: str = "en", *, verbose: bool = False
) -> str:
    """The 'what this is based on' block shown under every substantive answer.

    Compact by default. Showing ten full statutory summaries under every answer
    buries the advice the person came for, and nobody reads it. The caveats are
    always shown, because those are the parts that change the reader's
    behaviour; the full summaries are available on request.
    """
    if not ids:
        return ""
    lines = ["", "---", "", "**What this is based on**", ""]
    for pid in ids:
        p = kb.get(pid)
        if not p:
            continue
        if verbose:
            lines.append(f"- **{p.cite_label()}** — {p.heading}. {p.summary}")
        else:
            lines.append(f"- **{p.cite_label()}** — {p.heading}")
        if p.caveat:
            lines.append(f"  - ⚠️ {p.caveat}")
        if verbose and p.source:
            lines.append(f"  - Verify: {p.source}")
    lines.append("")
    lines.append(
        "_Plain-language summaries written for this tool, not quotations from the "
        "statute. Every one links to the original — check it before you rely on it "
        "in anything you file._"
    )
    return "\n".join(lines)
