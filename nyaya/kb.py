"""The knowledge base: a closed set of things the assistant is allowed to say.

Everything legal the assistant can assert lives in kb/provisions.json and
kb/routes.json. Nothing is retrieved from the open web and nothing is recalled
from the model's own memory of Indian law, because that memory is exactly what
produces confident, invented section numbers.

The KB is validated on load. A broken KB stops the program rather than
degrading quietly into a system that cites nothing and nobody notices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_KB_DIR = Path(__file__).resolve().parent.parent / "kb"

DOMAINS = ("deposit", "salary", "consumer", "general")


@dataclass(frozen=True)
class Provision:
    """One citable statement of law, written by a human."""

    id: str
    act: str
    act_short: str
    section: str
    heading: str
    summary: str
    plain: str
    domains: tuple[str, ...]
    use_when: str = ""
    plain_hi: str = ""
    confidence: str = "medium"
    volatility: str = "low"
    caveat: str = ""
    source: str = ""

    def cite_label(self) -> str:
        sec = (self.section or "").strip()
        if not sec or sec in ("-", "varies"):
            return self.act_short
        # "Schedule, Article 55" and "Rules 3-5" read wrongly after "s."
        if sec.lower().startswith(("schedule", "rule", "art", "order", "part")):
            return f"{self.act_short}, {sec}"
        return f"{self.act_short}, s. {sec}"

    def render(self, lang: str = "en") -> str:
        """How a provision is shown to the user. Never claims to be a quotation."""
        body = self.plain_hi if (lang == "hi" and self.plain_hi) else self.plain
        out = f"**{self.cite_label()} — {self.heading}**\n\n{body}\n\n_Summary of the provision: {self.summary}_"
        if self.caveat:
            out += f"\n\n⚠️ _{self.caveat}_"
        if self.source:
            out += f"\n\n[Check the original]({self.source})"
        return out


@dataclass
class LimitationRule:
    key: str
    months: int
    label: str
    runs_from: str
    cites: list[str]
    condonable: bool
    condonation_note: str
    warn_at_months_left: int


@dataclass
class Forum:
    id: str
    name: str
    stage: str
    applies_if: dict
    how: str
    cites: list[str]
    lawyer_needed: bool = False
    typical_time: str = ""
    cost: str = ""
    cost_slabs: list[dict] = field(default_factory=list)
    caveat: str = ""

    def fee_for(self, amount: float | None) -> tuple[int | None, str]:
        """Filing fee for a claim of this size, from the slab table."""
        if not self.cost_slabs:
            return None, self.cost or "not recorded"
        if amount is None:
            return None, "depends on the amount claimed"
        for slab in self.cost_slabs:
            cap = slab.get("up_to")
            if cap is None or amount <= cap:
                fee = slab.get("fee", 0)
                return fee, slab.get("label") or (f"₹{fee:,}" if fee else "No fee")
        last = self.cost_slabs[-1]
        return last.get("fee"), f"₹{last.get('fee', 0):,}"

    def matches(self, *, amount: float | None = None, monthly_wage: float | None = None) -> bool:
        c = self.applies_if or {}
        if amount is not None:
            if "amount_max" in c and amount > c["amount_max"]:
                return False
            if "amount_min" in c and amount <= c["amount_min"]:
                return False
        if monthly_wage is not None:
            if "monthly_wage_max" in c and monthly_wage > c["monthly_wage_max"]:
                return False
            if "monthly_wage_min" in c and monthly_wage <= c["monthly_wage_min"]:
                return False
        elif "monthly_wage_max" in c or "monthly_wage_min" in c:
            # Wage unknown: keep the forum as a possibility rather than hiding it.
            return True
        return True


class KBError(RuntimeError):
    """The knowledge base is malformed. Never recoverable by guessing."""


class KnowledgeBase:
    def __init__(self, kb_dir: str | Path = DEFAULT_KB_DIR) -> None:
        self.dir = Path(kb_dir)
        prov_path = self.dir / "provisions.json"
        routes_path = self.dir / "routes.json"
        for p in (prov_path, routes_path):
            if not p.exists():
                raise KBError(f"missing knowledge base file: {p}")

        raw = json.loads(prov_path.read_text(encoding="utf-8"))
        self.meta: dict = raw.get("_about", {})
        self.provisions: dict[str, Provision] = {}
        for entry in raw.get("provisions", []):
            pid = entry.get("id")
            if not pid:
                raise KBError(f"provision without an id: {entry!r:.120}")
            if pid in self.provisions:
                raise KBError(f"duplicate provision id: {pid}")
            missing = [
                k for k in ("act", "act_short", "section", "heading", "summary", "plain", "domains")
                if not entry.get(k)
            ]
            if missing:
                raise KBError(f"provision {pid} is missing {missing}")
            bad = [d for d in entry["domains"] if d not in DOMAINS]
            if bad:
                raise KBError(f"provision {pid} has unknown domain(s) {bad}")
            self.provisions[pid] = Provision(
                id=pid,
                act=entry["act"],
                act_short=entry["act_short"],
                section=entry["section"],
                heading=entry["heading"],
                summary=entry["summary"],
                plain=entry["plain"],
                plain_hi=entry.get("plain_hi", ""),
                domains=tuple(entry["domains"]),
                use_when=entry.get("use_when", ""),
                confidence=entry.get("confidence", "medium"),
                volatility=entry.get("volatility", "low"),
                caveat=entry.get("caveat", ""),
                source=entry.get("source", ""),
            )

        routes = json.loads(routes_path.read_text(encoding="utf-8"))
        self.limitation: dict[str, LimitationRule] = {
            k: LimitationRule(
                key=k,
                months=v["months"],
                label=v["label"],
                runs_from=v["runs_from"],
                cites=v["cites"],
                condonable=v.get("condonable", False),
                condonation_note=v.get("condonation_note", ""),
                warn_at_months_left=v.get("warn_at_months_left", 3),
            )
            for k, v in routes.get("limitation", {}).items()
        }
        self.forums: dict[str, list[Forum]] = {
            domain: [
                Forum(
                    id=f["id"], name=f["name"], stage=f["stage"],
                    applies_if=f.get("applies_if", {}), how=f.get("how", ""),
                    cites=f.get("cites", []), lawyer_needed=f.get("lawyer_needed", False),
                    typical_time=f.get("typical_time", ""), cost=f.get("cost", ""),
                    cost_slabs=f.get("cost_slabs", []), caveat=f.get("caveat", ""),
                )
                for f in flist
            ]
            for domain, flist in routes.get("forums", {}).items()
        }
        self.escalation_rules: list[dict] = routes.get("escalate_to_human", {}).get("rules", [])

        self._validate_cross_references()

    def _validate_cross_references(self) -> None:
        """Every id referenced anywhere must exist. Catches typos at load."""
        known = set(self.provisions)
        problems: list[str] = []
        for rule in self.limitation.values():
            for cid in rule.cites:
                if cid not in known:
                    problems.append(f"limitation.{rule.key} cites unknown provision {cid}")
        for domain, flist in self.forums.items():
            for f in flist:
                for cid in f.cites:
                    if cid not in known:
                        problems.append(f"forum {domain}/{f.id} cites unknown provision {cid}")
        if problems:
            raise KBError("knowledge base has dangling references:\n  " + "\n  ".join(problems))

    # --- lookup ----------------------------------------------------------

    def get(self, pid: str) -> Provision | None:
        return self.provisions.get(pid)

    def for_domain(self, domain: str, include_general: bool = True) -> list[Provision]:
        wanted = {domain} | ({"general"} if include_general else set())
        return [p for p in self.provisions.values() if wanted & set(p.domains)]

    def ids_for_domain(self, domain: str, include_general: bool = True) -> list[str]:
        return [p.id for p in self.for_domain(domain, include_general)]

    def forums_for(
        self, domain: str, *, amount: float | None = None, monthly_wage: float | None = None
    ) -> list[Forum]:
        return [
            f for f in self.forums.get(domain, [])
            if f.matches(amount=amount, monthly_wage=monthly_wage)
        ]

    def limitation_for(self, key: str) -> LimitationRule | None:
        return self.limitation.get(key)

    # --- facts the guard needs -------------------------------------------

    def allowed_sections(self, ids: list[str] | None = None) -> set[str]:
        """Section numbers the assistant may mention, given what it may cite."""
        pool = [self.provisions[i] for i in ids if i in self.provisions] if ids else list(self.provisions.values())
        out: set[str] = set()
        for p in pool:
            sec = p.section.strip()
            if not sec or sec in ("-", "varies"):
                continue
            # "15(2) proviso" should also license a bare "15"
            out.add(sec)
            head = sec.split("(")[0].split()[0].strip().rstrip(",.")
            if head:
                out.add(head)
            if "," in sec:
                for part in sec.split(","):
                    part = part.strip()
                    if part:
                        out.add(part)
                        out.add(part.split("(")[0].strip())
        return out

    def allowed_acts(self, ids: list[str] | None = None) -> set[str]:
        pool = [self.provisions[i] for i in ids if i in self.provisions] if ids else list(self.provisions.values())
        out: set[str] = set()
        for p in pool:
            out.add(p.act.lower())
            out.add(p.act_short.lower())
        return out

    def summary(self) -> str:
        by_domain: dict[str, int] = {}
        for p in self.provisions.values():
            for d in p.domains:
                by_domain[d] = by_domain.get(d, 0) + 1
        bits = ", ".join(f"{d}: {n}" for d, n in sorted(by_domain.items()))
        return (
            f"{len(self.provisions)} provisions ({bits}); "
            f"{sum(len(v) for v in self.forums.values())} forums; "
            f"{len(self.limitation)} limitation rules; "
            f"last reviewed {self.meta.get('last_reviewed', 'unknown')}"
        )


_CACHE: KnowledgeBase | None = None


def load_kb(kb_dir: str | Path = DEFAULT_KB_DIR, *, reload: bool = False) -> KnowledgeBase:
    global _CACHE
    if _CACHE is None or reload:
        _CACHE = KnowledgeBase(kb_dir)
    return _CACHE
