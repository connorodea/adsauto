"""Merchant Center policy pre-check — deterministic, no model.

Google's "Healthcare and medicines" policy restricts or disallows
unapproved pharmaceuticals and supplements, prescription drugs, and any
listing that makes health claims or gives human-use instructions. A
research-peptide catalogue is squarely in the risk zone: the product names
alone (somatropin, tirzepatide, semaglutide) match Google's regulated
drug lists, and marketing copy routinely drifts into "fat loss" and
"recovery".

This module scans titles and descriptions for the words that trigger
that policy and reports them as WARNINGS with the offending text. It is a
pre-check, not a verdict: it does not predict approval and nothing here
can promise that Merchant Center will accept the catalogue. A clean scan
means only that the text contains none of the listed patterns.

Codes:
  P001  health / efficacy claim          ("treats", "cures", "fat loss", "anti-aging")
  P002  human-use or dosing instruction  ("inject", "dosage", "mg/kg", "per day")
  P003  regulated or prescription drug   ("somatropin", "tirzepatide", "not approved by the FDA")
  P004  placeholder description          (import-time stub text, not a product description)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..models import Product
from .validate import Issue

_W = r"\b"


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile(_W + "(?:" + "|".join(patterns) + ")" + _W, re.IGNORECASE)


CLAIM_PATTERNS = _rx(
    r"treats?", r"treating", r"treatment(?:s)? (?:of|for)", r"cures?", r"curing",
    r"heals?", r"healing", r"prevents?", r"preventing", r"reverses?",
    r"weight[- ]loss", r"lose weight", r"fat[- ](?:loss|burn\w*)", r"slimming",
    r"muscle (?:growth|gain|mass|building)", r"bodybuilding", r"lean mass",
    r"anti[- ]?ag(?:e|ing)", r"longevity", r"life ?extension", r"rejuvenat\w*",
    r"boosts?", r"enhances?", r"improves?", r"increases? (?:energy|libido|strength|stamina)",
    r"libido", r"erectile", r"sexual (?:performance|function)",
    r"cognitive (?:enhancement|boost)", r"nootropic", r"anxiety relief", r"depression",
    r"pain relief", r"injury recovery", r"faster recovery", r"wound healing",
    r"immune (?:boost\w*|support)", r"detox\w*", r"clinically proven", r"fda[- ]approved",
    r"safe and effective", r"side[- ]effect free", r"no side effects",
)

HUMAN_USE_PATTERNS = _rx(
    r"inject(?:s|ed|ing|ion|ions|able)?", r"subcutaneous(?:ly)?", r"intramuscular(?:ly)?",
    r"dosage", r"dosing", r"dose (?:per|for|of)", r"recommended dose",
    r"mg\s*/\s*kg", r"mcg\s*/\s*kg", r"iu\s*/\s*kg",
    r"per day", r"daily dose", r"twice daily", r"once daily", r"a day",
    r"before (?:bed|meals?)", r"with food", r"on an empty stomach",
    r"take \d", r"take (?:one|two|three)", r"administer(?:ed|ing)?",
    r"cycle (?:length|of)", r"\d+[- ]week cycle", r"weeks? on", r"loading phase",
    r"oral(?:ly)? (?:use|dose|administration)", r"nasal spray", r"sublingual",
    r"for human (?:use|consumption)", r"human (?:dosing|dose|trial dose)",
    r"patients?", r"users? report", r"self[- ]administ\w*",
)

REGULATED_PATTERNS = _rx(
    r"somatropin", r"human growth hormone", r"growth hormone", r"hgh",
    r"tirzepatide", r"semaglutide", r"retatrutide", r"liraglutide", r"cagrilintide",
    r"insulin", r"testosterone", r"hcg", r"hmg", r"menotropins?", r"gonadotropins?",
    r"melanotan", r"pt[- ]141", r"bremelanotide", r"oxytocin", r"tesamorelin",
    r"not (?:yet )?approved by the fda", r"fda has not approved", r"unapproved",
    r"investigational (?:drug|compound|new drug)", r"prescription",
    r"glp[- ]?1", r"ozempic", r"wegovy", r"mounjaro", r"zepbound",
)

PLACEHOLDER_PATTERNS = _rx(
    r"listed in our sourcing catalog",
    r"strength and pack labels are preserved",
    r"stock and lot documentation are not yet confirmed",
    r"lorem ipsum", r"description coming soon", r"to be added",
)


@dataclass(frozen=True)
class Hit:
    pattern: str
    field: str
    excerpt: str


def _excerpt(text: str, start: int, end: int, radius: int = 40) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return prefix + text[lo:hi].replace("\n", " ") + suffix


def scan_text(text: str, field: str, rx: re.Pattern[str]) -> list[Hit]:
    hits: list[Hit] = []
    seen: set[str] = set()
    for m in rx.finditer(text or ""):
        key = m.group(0).lower()
        if key in seen:
            continue
        seen.add(key)
        hits.append(Hit(m.group(0), field, _excerpt(text, m.start(), m.end())))
    return hits


def _issues_for(p: Product, code: str, rx: re.Pattern[str], label: str, fix: str) -> Iterable[Issue]:
    for field in ("title", "description"):
        for hit in scan_text(getattr(p, field), field, rx):
            yield Issue(
                code, "warning", p.offer_id,
                f"{label} in {hit.field}: {hit.pattern!r} — {hit.excerpt}",
                fix,
            )


def check_product(p: Product) -> list[Issue]:
    out: list[Issue] = []
    out += _issues_for(
        p, "P001", CLAIM_PATTERNS, "Health/efficacy claim",
        "Describe the compound (identity, formula, research areas); remove outcome language",
    )
    out += _issues_for(
        p, "P002", HUMAN_USE_PATTERNS, "Human-use or dosing wording",
        "Remove dosing/administration text; state 'laboratory research use only'",
    )
    out += _issues_for(
        p, "P003", REGULATED_PATTERNS, "Regulated/prescription drug reference",
        "Expect Healthcare & medicines policy review; consider certification or "
        "excluding the item from Shopping ads",
    )
    out += _issues_for(
        p, "P004", PLACEHOLDER_PATTERNS, "Placeholder description",
        "Write a real description on the site; Merchant Center compares feed text to the page",
    )
    return out


@dataclass
class PolicyReport:
    issues: list[Issue]
    total: int

    @property
    def flagged_offer_ids(self) -> set[str]:
        return {i.offer_id for i in self.issues}

    def by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for i in self.issues:
            counts[i.code] = counts.get(i.code, 0) + 1
        return dict(sorted(counts.items()))

    def summary(self) -> str:
        lines = [
            f"Policy pre-check (Healthcare & medicines): {len(self.issues)} warnings "
            f"on {len(self.flagged_offer_ids)} of {self.total} items",
        ]
        lines += [f"  {code}: {count}" for code, count in self.by_code().items()]
        return "\n".join(lines)


def check(products: Sequence[Product]) -> PolicyReport:
    issues: list[Issue] = []
    for p in products:
        issues.extend(check_product(p))
    return PolicyReport(issues=issues, total=len(products))
