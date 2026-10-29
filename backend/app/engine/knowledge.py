"""Allergen knowledge base: maps ingredient text to allergen categories."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.engine.text import term_pattern


@dataclass(frozen=True)
class AllergenInfo:
    code: str
    name: str


@dataclass(frozen=True)
class TermMatch:
    allergen_code: str
    term: str
    start: int
    end: int


class KnowledgeBase:
    def __init__(
        self,
        allergens: list[AllergenInfo],
        synonyms: list[tuple[str, str]],  # (term, allergen_code)
        exclusions: list[tuple[str, str]],  # (term, allergen_code)
    ):
        self.allergens = {a.code: a for a in allergens}
        self._synonyms = [(t, c, term_pattern(t)) for t, c in synonyms]
        self._exclusions = [(t, c, term_pattern(t)) for t, c in exclusions]
        # Lets claim phrases like "dairy" in "dairy-free" resolve to an allergen.
        self._name_terms = [(a.name.lower(), a.code, term_pattern(a.name.lower())) for a in allergens]

    def name(self, code: str) -> str:
        return self.allergens[code].name if code in self.allergens else code

    def match(self, text: str) -> list[TermMatch]:
        """Allergen matches in `text`, after removing matches covered by an exclusion.

        An exclusion only suppresses matches of its own allergen that it fully covers:
        "peanut butter" suppresses milk("butter") but not peanut("peanut butter").
        """
        excluded: list[tuple[str, int, int]] = []
        for _, code, pat in self._exclusions:
            excluded.extend((code, m.start(), m.end()) for m in pat.finditer(text))

        found: dict[tuple[str, int, int], TermMatch] = {}
        for term, code, pat in self._synonyms:
            for m in pat.finditer(text):
                if any(c == code and s <= m.start() and m.end() <= e for c, s, e in excluded):
                    continue
                key = (code, m.start(), m.end())
                found.setdefault(key, TermMatch(code, term, m.start(), m.end()))
        # Keep the longest match per allergen per position (e.g. "whey protein" over "whey").
        result: list[TermMatch] = []
        for tm in sorted(found.values(), key=lambda t: (t.start, -(t.end - t.start))):
            if any(r.allergen_code == tm.allergen_code and r.start <= tm.start and tm.end <= r.end for r in result):
                continue
            result.append(tm)
        return result

    def codes_in(self, text: str) -> set[str]:
        codes = {m.allergen_code for m in self.match(text)}
        for _, code, pat in self._name_terms:
            if pat.search(text):
                codes.add(code)
        return codes


def custom_match(term: str, text: str) -> re.Match[str] | None:
    return term_pattern(term).search(text)
