"""Allergen knowledge base: maps ingredient text to allergen categories.

Two layers work together:
- allergen terms: synonyms and look-alike exclusions per allergen category (whole-word matching);
- ingredient concepts: recognised ingredients with typed relationships (derived_from, is_a,
  contains_component, is_source_of, associated_with_allergen), so an allergen can be reached through an
  explicit chain such as sodium caseinate -> casein -> milk protein -> milk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.engine.text import term_pattern

RELATION_WORDS = {
    "derived_from": "derived from",
    "is_a": "is a",
    "contains_component": "contains",
    "associated_with_allergen": "belongs to",
}
_STOPWORDS = {"and", "or", "with", "of", "in", "from", "the", "a", "an", "contains", "contain"}
MAX_DEPTH = 6


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


@dataclass(frozen=True)
class ConceptInfo:
    slug: str
    name: str
    ambiguous_source: bool = False
    foodon_id: str | None = None


@dataclass(frozen=True)
class ConceptMatch:
    slug: str
    alias: str
    start: int
    end: int


@dataclass(frozen=True)
class AllergenPath:
    """How a concept reaches an allergen: the concept slugs visited and a readable chain."""

    allergen_code: str
    certainty: str  # definite | possible
    nodes: tuple[str, ...]  # concept slugs from the matched concept to the allergen's source
    text: str  # "sodium caseinate, derived from casein, is a milk protein, derived from milk"


@dataclass
class ItemResolution:
    """What the knowledge base could tell about one ingredient item."""

    text: str
    status: str  # allergen_linked | possible_allergen | ambiguous_source | no_known_allergen | unresolved
    concepts: list[ConceptMatch] = field(default_factory=list)
    terms: list[TermMatch] = field(default_factory=list)  # allergen-term matches kept after suppression
    paths: list[tuple[ConceptMatch, AllergenPath]] = field(default_factory=list)


class KnowledgeBase:
    def __init__(
        self,
        allergens: list[AllergenInfo],
        synonyms: list[tuple[str, str]],  # (term, allergen_code)
        exclusions: list[tuple[str, str]],  # (term, allergen_code)
        concepts: list[ConceptInfo] | None = None,
        aliases: list[tuple[str, str]] | None = None,  # (alias, concept slug)
        relations: list[tuple[str, str, str, str]] | None = None,  # (subject, relation, object, certainty)
        hierarchy: list[tuple[str, str]] | None = None,  # (child allergen, parent allergen)
        regulated: dict[str, list[str]] | None = None,  # allergen code -> jurisdictions
        modifiers: set[str] | None = None,
        version: int = 0,
    ):
        self.allergens = {a.code: a for a in allergens}
        self._synonyms = [(t, c, term_pattern(t)) for t, c in synonyms]
        self._exclusions = [(t, c, term_pattern(t)) for t, c in exclusions]
        # Lets claim phrases like "dairy" in "dairy-free" resolve to an allergen.
        self._name_terms = [(a.name.lower(), a.code, term_pattern(a.name.lower())) for a in allergens]

        self.concepts = {c.slug: c for c in concepts or []}
        self._aliases = [(a, s, term_pattern(a)) for a, s in aliases or [] if s in self.concepts]
        self.alias_to_concept = {a: s for a, s in aliases or [] if s in self.concepts}
        # Forward edges: subject -> [(relation, object, certainty)]; is_source_of(a, b) == derived_from(b, a).
        self._edges: dict[str, list[tuple[str, str, str]]] = {}
        for subj, rel, obj, cert in relations or []:
            if rel == "is_source_of":
                subj, rel, obj = obj, "derived_from", subj
            self._edges.setdefault(subj, []).append((rel, obj, cert))
        self.parents: dict[str, list[str]] = {}
        for child, parent in hierarchy or []:
            self.parents.setdefault(child, []).append(parent)
        self.regulated = regulated or {}
        self.modifiers = modifiers or set()
        self.version = version
        self._path_cache: dict[str, list[AllergenPath]] = {}

    def name(self, code: str) -> str:
        return self.allergens[code].name if code in self.allergens else code

    # ---------- allergen terms ----------

    def match(self, text: str) -> list[TermMatch]:
        """Allergen-term matches in `text`, after removing matches covered by an exclusion.

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

    # ---------- concepts ----------

    def match_concepts(self, text: str) -> list[ConceptMatch]:
        """Longest non-overlapping concept-alias matches ("soy lecithin" wins over "lecithin")."""
        cands = [ConceptMatch(s, a, m.start(), m.end()) for a, s, pat in self._aliases for m in pat.finditer(text)]
        chosen: list[ConceptMatch] = []
        for c in sorted(cands, key=lambda c: (-(c.end - c.start), c.start)):
            if all(c.end <= o.start or c.start >= o.end for o in chosen):
                chosen.append(c)
        return sorted(chosen, key=lambda c: c.start)

    def allergen_paths(self, slug: str) -> list[AllergenPath]:
        """Every allergen reachable from a concept, with the chain that reaches it (cached)."""
        if slug in self._path_cache:
            return self._path_cache[slug]
        found: list[AllergenPath] = []

        def walk(node: str, nodes: tuple[str, ...], words: list[str], certainty: str) -> None:
            if len(nodes) > MAX_DEPTH:
                return
            for rel, obj, cert in self._edges.get(node, []):
                cert_here = "possible" if "possible" in (certainty, cert) else "definite"
                rel_words = RELATION_WORDS.get(rel, rel) + (" (possibly)" if cert == "possible" else "")
                if obj.startswith("allergen:"):
                    code = obj.split(":", 1)[1]
                    found.append(AllergenPath(code, cert_here, nodes, ", ".join(words)))
                elif obj not in nodes and obj in self.concepts:
                    walk(obj, nodes + (obj,), words + [f"{rel_words} {self.concepts[obj].name}"], cert_here)

        walk(slug, (slug,), [self.concepts[slug].name], "definite")
        # One path per (allergen, certainty), preferring the shortest.
        best: dict[tuple[str, str], AllergenPath] = {}
        for p in sorted(found, key=lambda p: len(p.nodes)):
            best.setdefault((p.allergen_code, p.certainty), p)
        # A definite path makes a possible one for the same allergen redundant.
        paths = [p for p in best.values() if not (p.certainty == "possible" and (p.allergen_code, "definite") in best)]
        self._path_cache[slug] = paths
        return paths

    def concept_reaches(self, slug: str, target: str) -> bool:
        """Whether `target` is the concept itself or any concept on its relationship chains."""
        if slug == target:
            return True
        seen, stack = {slug}, [slug]
        while stack:
            for _, obj, _ in self._edges.get(stack.pop(), []):
                if obj == target:
                    return True
                if obj in self.concepts and obj not in seen:
                    seen.add(obj)
                    stack.append(obj)
        return False

    def resolve_item(self, item: str, nonspecific: bool = False) -> ItemResolution:
        """Combine allergen terms and concept reasoning for one ingredient item."""
        concepts = self.match_concepts(item)
        terms = self.match(item)
        # Look-alike exclusions apply to concept matches too: "cream of tartar" contains the concept
        # "cream", but the exclusion says it is not milk.
        excluded = [(code, m.start(), m.end()) for _, code, pat in self._exclusions for m in pat.finditer(item)]
        reach: dict[ConceptMatch, set[str]] = {}
        paths: list[tuple[ConceptMatch, AllergenPath]] = []
        for cm in concepts:
            ps = [p for p in self.allergen_paths(cm.slug)
                  if not any(c == p.allergen_code and s <= cm.start and cm.end <= e for c, s, e in excluded)]
            reach[cm] = {p.allergen_code for p in ps}
            paths.extend((cm, p) for p in ps)

        # A recognised concept covering a term match decides that term: "cocoa butter" (a cocoa
        # product with no milk relationship) suppresses milk("butter").
        kept = [t for t in terms if not any(cm.start <= t.start and t.end <= cm.end and t.allergen_code not in reach[cm]
                                            for cm in concepts)]

        definite = {t.allergen_code for t in kept} | {p.allergen_code for _, p in paths if p.certainty == "definite"}
        possible = {p.allergen_code for _, p in paths if p.certainty == "possible"} - definite
        ambiguous = nonspecific or any(self.concepts[cm.slug].ambiguous_source for cm in concepts)
        covered = bool(concepts) and self._covered(item, concepts)
        if definite:
            status = "allergen_linked"
        elif possible:
            status = "possible_allergen"
        elif not covered and not nonspecific:
            # Unknown words remain ("penaut oil": "oil" is known, "penaut" is not). Calling this
            # "ambiguous" or "no known allergen" would hide a possibly misspelled allergen.
            status = "unresolved"
        elif ambiguous:
            status = "ambiguous_source"
        else:
            status = "no_known_allergen"
        return ItemResolution(item, status, concepts, kept, paths)

    def _covered(self, item: str, concepts: list[ConceptMatch]) -> bool:
        """True when everything outside the matched concepts is descriptive (organic, roasted, 38%...)."""
        rest = item
        for cm in sorted(concepts, key=lambda c: -c.start):
            rest = rest[: cm.start] + " " + rest[cm.end :]
        words = re.findall(r"[a-z]+", rest)
        return all(w in self.modifiers or w in _STOPWORDS for w in words)

    def regulated_in(self, code: str) -> list[str]:
        return self.regulated.get(code, [])


def custom_match(term: str, text: str) -> re.Match[str] | None:
    return term_pattern(term).search(text)
