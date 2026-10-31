"""Multi-stage ingredient normalization.

Raw ingredient text becomes a list of ingredients, each traced from what was written to a canonical
concept:

  1. text cleaning           symbols, percentages, E-number forms, whitespace
  2. OCR correction          (label photos only) character confusions such as l/i, rn/m, cl/d,
                             accepted only when exactly one correction lands on a known word
  3. spelling normalization  reviewed regional variants and misspellings (flavor -> flavour)
  4. synonym resolution      exact alias / allergen-term lookup in the knowledge base
  5. fuzzy matching          RapidFuzz similarity against known names, with configurable thresholds
  6. compound ingredients    nested brackets, class names ("emulsifier: ..."), headers
                             ("contains 2% or less of:"), source qualifiers ("lecithins (soya)")
  7. canonical concept       the concept slug (and FoodOn id) the item resolves to
  8. uncertainty kept        unresolved text is never forced into a match

Safety rule for the inference stages (2 and 5): a guessed match may ADD an allergen finding (the
cautious direction, shown with the original text), but it never turns an unknown ingredient into
"no known allergen". When a guess leads to a harmless ingredient, the item stays unresolved and the
guess is kept only as a suggestion.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz, process

from app.core.config import get_settings
from app.engine.knowledge import AllergenPath, ItemResolution, KnowledgeBase
from app.engine.text import is_nonspecific
from app.seed.spelling_data import CLASS_LABELS, HEADER_PATTERNS, SPELLING_VARIANTS

CONFIDENCE = {"exact": 1.0, "spelling": 0.98, "ocr_correction": 0.85, "qualified": 1.0}

# Tesseract's frequent character confusions (written form -> intended form).
OCR_CONFUSIONS = [
    ("l", "i"), ("i", "l"), ("1", "i"), ("1", "l"), ("rn", "m"), ("m", "rn"), ("cl", "d"), ("vv", "w"),
    ("0", "o"), ("5", "s"), ("c", "e"), ("e", "c"), ("ii", "u"), ("u", "ii"), ("li", "h"), ("h", "li"),
]

_HEADERS = [re.compile(p) for p in HEADER_PATTERNS]
_INGREDIENTS_PREFIX = re.compile(r"^\s*(ingredients?|ingr\.?|zutaten|ingrédients)\s*[:\-]\s*", re.I)
_NOISE = re.compile(r"[®™©*•·|~_^#@$]+")
_PERCENT = re.compile(r"\b\d+(?:[.,]\d+)?\s*%")
_ENUMBER = re.compile(r"\be[\s\-]?(\d{3,4}[a-z]?)\b")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class ParsedItem:
    original: str
    role: str  # ingredient | compound | component | class_label
    parent: str | None = None
    children: list["ParsedItem"] = field(default_factory=list)
    incomplete: str = ""  # why this item's component list looks incomplete ("" = no sign of that)


@dataclass
class NormalizedIngredient:
    original: str  # as written (lower-cased)
    text: str  # after cleaning, OCR correction, spelling and fuzzy matching
    # ingredient | compound | component | class_label | qualified | source_qualifier
    role: str
    parent: str | None
    method: str  # exact | spelling | ocr_correction | fuzzy | qualified | none
    confidence: float
    resolution: ItemResolution | None
    corrections: list[str] = field(default_factory=list)
    suggestion: str | None = None  # a guess that was not accepted (see the safety rule)
    candidates: list[str] = field(default_factory=list)  # close matches considered but not accepted
    unresolved_reasons: list[str] = field(default_factory=list)  # why the text could not be identified
    incomplete: str = ""  # the component list of this item looks cut off or open-ended

    @property
    def counts(self) -> bool:
        """Whether this item is evidence on its own (class names, source qualifiers and list notes are not)."""
        return self.role not in ("class_label", "source_qualifier", "list_note")


# ---------- 1. cleaning and 6. parsing ----------


def clean(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    t = _INGREDIENTS_PREFIX.sub("", t)
    t = _NOISE.sub(" ", t)
    t = _PERCENT.sub(" ", t)
    t = _ENUMBER.sub(lambda m: f"e{m.group(1)}", t)
    t = re.sub(r"\s+", " ", t).strip(" .,;:-'\"")
    t = re.sub(r"^(and|or|with|contains?|also)\s+", "", t)
    return t.strip()


def _split_top(text: str, seps: str = ",;") -> list[str]:
    """Split on separators that are outside brackets; '. ' also ends an ingredient."""
    parts, buf, depth = [], [], 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if depth == 0 and (ch in seps or (ch == "." and (i + 1 == len(text) or text[i + 1] == " "))):
            parts.append("".join(buf))
            buf = []
        elif depth == 0 and text.startswith(" and/or ", i):
            parts.append("".join(buf))
            buf = []
            i += len(" and/or ") - 1
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p for p in (p.strip() for p in parts) if p]


def _top_colon(piece: str) -> int:
    depth = 0
    for i, ch in enumerate(piece):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            return i
    return -1


def _head_and_inner(piece: str) -> tuple[str, list[str]]:
    """'chocolate (sugar, cocoa)' -> ('chocolate', ['sugar, cocoa']); 'emulsifier: x (y)' -> ('emulsifier', ['x (y)'])."""
    colon = _top_colon(piece)
    if colon != -1:
        return piece[:colon].strip(), [piece[colon + 1:]] if piece[colon + 1:].strip() else []
    head, inner, depth, buf = [], [], 0, []
    for ch in piece:
        if ch in "([{":
            if depth == 0:
                buf = []
            else:
                buf.append(ch)
            depth += 1
        elif ch in ")]}" and depth:
            depth -= 1
            if depth == 0:
                inner.append("".join(buf))
            else:
                buf.append(ch)
        elif depth:
            buf.append(ch)
        else:
            head.append(ch)
    return "".join(head).strip(), [x for x in inner if x.strip()]


_OPEN_ENDED = re.compile(r"(?:^|\s+)((?:and\s+|&\s+)?(?:etc|others?|more|other\s+(?:ingredients|spices|herbs)))\s*$")
OPEN_ENDED_NOTE = "the list ends with '{0}', so not every ingredient is named"
UNCLOSED_NOTE = "a bracket is not closed, so the list may be cut off"
EMPTY_NOTE = "its brackets are empty, so its components are not given"


def parse_ingredients(text: str, parent: str | None = None) -> list[ParsedItem]:
    top = parent is None
    from app.engine.ablation import current

    if top and current().no_compound:  # ablation: no bracket parsing, one item per top-level piece
        text = _INGREDIENTS_PREFIX.sub("", unicodedata.normalize("NFKC", text))
        return [ParsedItem(clean(p), "ingredient") for p in _split_top(text) if clean(p)]
    if top:
        text = _INGREDIENTS_PREFIX.sub("", unicodedata.normalize("NFKC", text))
        text = re.sub(r"\s*(?:\.{3}|…)", " etc", text)  # "sugar, ..." -> "sugar, etc"
    items: list[ParsedItem] = []
    for piece in _split_top(text):
        open_note = ""
        lowered = " ".join(piece.lower().split()).rstrip(" .,;")
        m = _OPEN_ENDED.search(lowered) if not re.search(r"[)\]}]\s*$", lowered) else None
        if m:
            open_note = OPEN_ENDED_NOTE.format(m.group(1))
            piece = lowered[: m.start()].strip()
            if not piece:
                items.append(ParsedItem("", "list_note", parent, incomplete=open_note))
                continue
        head, inners = _head_and_inner(piece)
        if re.search(r"[(\[{]\s*[)\]}]", piece) or (inners and not any(i.strip() for i in inners)):
            role = "class_label" if apply_spelling(clean(head)) in CLASS_LABELS else ("component" if parent else "ingredient")
            items.append(ParsedItem(clean(head), role, parent, incomplete=EMPTY_NOTE))
            continue
        head_c = clean(head)
        is_header = any(p.match(re.sub(r"\s+", " ", head.lower().strip())) for p in _HEADERS)
        children = [c for inner in inners for c in parse_ingredients(inner, parent=None if is_header else (head_c or parent))]
        if not head_c or is_header:
            items.extend(children)  # "contains 2% or less of: salt, ..." -> just the ingredients
        elif not children:
            items.append(ParsedItem(head_c, "component" if parent else "ingredient", parent, incomplete=open_note))
            if open_note:  # the trailing "etc" belongs to the list, not to this ingredient
                items.append(ParsedItem("", "list_note", parent, incomplete=open_note))
        elif apply_spelling(head_c) in CLASS_LABELS:
            items.append(ParsedItem(head_c, "class_label", parent, children))
            items.extend(_with_parent(children, head_c))
        else:
            note = next((c.incomplete for c in children if c.role == "list_note"), "")
            if open_note:
                items.append(ParsedItem("", "list_note", parent, incomplete=open_note))
            items.append(ParsedItem(head_c, "compound", parent, children, incomplete=note))
            items.extend(_with_parent(children, head_c))
    if top and items and sum(text.count(c) for c in "([{") > sum(text.count(c) for c in ")]}"):
        last = next((i for i in reversed(items) if i.role in ("compound", "class_label")), items[-1])
        last.incomplete = last.incomplete or UNCLOSED_NOTE
    return items


def _with_parent(children: list[ParsedItem], parent: str) -> list[ParsedItem]:
    for c in children:
        if c.parent is None:
            c.parent = parent
        if c.role == "ingredient":
            c.role = "component"
    return children


# ---------- 2/3/5. text normalization stages ----------


def _vocab(kb: KnowledgeBase) -> tuple[frozenset[str], tuple[str, ...]]:
    """Known words and known names (aliases + allergen terms), cached on the knowledge base."""
    cached = getattr(kb, "_norm_vocab", None)
    if cached is None:
        phrases = set(kb.alias_to_concept) | {t for t, _, _ in kb._synonyms}
        words = {w for p in phrases | {t for t, _, _ in kb._exclusions} for w in _WORD.findall(p)}
        words |= set(kb.modifiers) | {w for v in SPELLING_VARIANTS.values() for w in _WORD.findall(v)}
        words |= {w for c in CLASS_LABELS for w in _WORD.findall(c)}
        cached = (frozenset(words), tuple(sorted(phrases)))
        kb._norm_vocab = cached
    return cached


def apply_spelling(text: str) -> str:
    return " ".join(SPELLING_VARIANTS.get(w, w) for w in text.split(" "))


@lru_cache(maxsize=4096)
def _ocr_variants(word: str) -> frozenset[str]:
    """Words reachable from `word` by one or two character confusions."""
    singles = set()
    for wrong, right in OCR_CONFUSIONS:
        start = word.find(wrong)
        while start != -1:
            singles.add(word[:start] + right + word[start + len(wrong):])
            start = word.find(wrong, start + 1)
    doubles = set()
    if len(word) >= 5:
        for w in singles:
            for wrong, right in OCR_CONFUSIONS:
                start = w.find(wrong)
                while start != -1:
                    doubles.add(w[:start] + right + w[start + len(wrong):])
                    start = w.find(wrong, start + 1)
    return frozenset((singles | doubles) - {word})


def ocr_correct(kb: KnowledgeBase, text: str) -> tuple[str, list[str]]:
    words, _ = _vocab(kb)
    out, notes = [], []
    for tok in text.split(" "):
        if len(tok) >= 3 and tok not in words and tok not in SPELLING_VARIANTS:
            hits = [v for v in _ocr_variants(tok) if v in words]
            if len(hits) == 1:
                out.append(hits[0])
                notes.append(f"'{tok}' read as '{hits[0]}' (OCR correction)")
                continue
        out.append(tok)
    return " ".join(out), notes


def fuzzy_candidates(kb: KnowledgeBase, text: str) -> list[str]:
    """Close known names that were NOT accepted because two or more were about equally close."""
    s = get_settings()
    words, phrases = _vocab(kb)
    if len(text) >= s.fuzzy_min_length:
        best = process.extract(text, phrases, scorer=fuzz.ratio, limit=3, score_cutoff=s.fuzzy_phrase_threshold - 5)
        if len(best) > 1 and best[0][1] - best[1][1] < s.fuzzy_margin:
            return [b[0] for b in best]
    return []


def fuzzy_correct(kb: KnowledgeBase, text: str) -> tuple[str, list[str], float]:
    """Best fuzzy replacement for the whole text, else for unknown words. Returns (text, notes, score)."""
    s = get_settings()
    words, phrases = _vocab(kb)
    if len(text) >= s.fuzzy_min_length:
        best = process.extract(text, phrases, scorer=fuzz.ratio, limit=2, score_cutoff=s.fuzzy_phrase_threshold)
        if best and (len(best) == 1 or best[0][1] - best[1][1] >= s.fuzzy_margin):
            return best[0][0], [f"'{text}' matched to '{best[0][0]}' ({best[0][1]:.0f}% similar)"], best[0][1]
    out, notes, scores = [], [], []
    for tok in text.split(" "):
        if len(tok) >= s.fuzzy_min_length and tok not in words:
            best = process.extract(tok, words, scorer=fuzz.ratio, limit=2, score_cutoff=s.fuzzy_word_threshold)
            if best and (len(best) == 1 or best[0][1] - best[1][1] >= s.fuzzy_margin):
                out.append(best[0][0])
                notes.append(f"'{tok}' matched to '{best[0][0]}' ({best[0][1]:.0f}% similar)")
                scores.append(best[0][1])
                continue
        out.append(tok)
    return " ".join(out), notes, (min(scores) if scores else 0.0)


def _resolve(kb: KnowledgeBase, text: str) -> ItemResolution:
    return kb.resolve_item(text, nonspecific=is_nonspecific(text))


_STOP = {"and", "or", "with", "of", "in", "from", "the", "a", "an"}


def _allergens(res: ItemResolution) -> set[str]:
    return {t.allergen_code for t in res.terms} | {p.allergen_code for _, p in res.paths}


def normalize_item(kb: KnowledgeBase, original: str, role: str, parent: str | None, ocr: bool) -> NormalizedIngredient:
    from app.engine.ablation import current

    text = clean(original)
    if role == "class_label":
        return NormalizedIngredient(original, text, role, parent, "exact", 1.0, None)
    if current().no_normalization:  # ablation: exact text only
        res = _resolve(kb, text)
        return NormalizedIngredient(original, text, role, parent, "exact" if res.status != "unresolved" else "none",
                                    1.0 if res.status != "unresolved" else 0.0, res)
    spelled = apply_spelling(text)
    method = "spelling" if spelled != text else "exact"
    corrections = [f"'{text}' normalized to '{spelled}' (spelling)"] if spelled != text else []
    res = _resolve(kb, spelled)
    if res.status != "unresolved":
        if ocr:
            # Unknown words next to a recognised one ("whey powcler"): correct them if that keeps every
            # allergen already found (a correction may add findings, never remove them).
            fixed, notes = ocr_correct(kb, spelled)
            if fixed != spelled:
                r2 = _resolve(kb, apply_spelling(fixed))
                if r2.status != "unresolved" and _allergens(res) <= _allergens(r2):
                    return NormalizedIngredient(original, apply_spelling(fixed), role, parent, "ocr_correction",
                                                CONFIDENCE["ocr_correction"], r2, corrections + notes)
        return NormalizedIngredient(original, spelled, role, parent, method, CONFIDENCE[method], res, corrections)

    attempts: list[tuple[str, str, list[str], float]] = []
    if ocr:
        fixed, notes = ocr_correct(kb, spelled)
        if fixed != spelled:
            attempts.append((apply_spelling(fixed), "ocr_correction", notes, CONFIDENCE["ocr_correction"]))
    fixed, notes, score = fuzzy_correct(kb, spelled)
    if fixed != spelled:
        attempts.append((apply_spelling(fixed), "fuzzy", notes, round(score / 100 * 0.9, 2)))

    suggestion = None
    for cand, how, notes, conf in attempts:
        r = _resolve(kb, cand)
        if r.status in ("allergen_linked", "possible_allergen"):
            return NormalizedIngredient(original, cand, role, parent, how, conf, r, corrections + notes)
        if r.status != "unresolved" and suggestion is None:
            suggestion = cand  # a harmless guess: kept as a suggestion only
    return NormalizedIngredient(original, spelled, role, parent, "none", 0.0, res, corrections, suggestion,
                                candidates=([suggestion] if suggestion else []) + [c for c in fuzzy_candidates(kb, spelled) if c != suggestion],
                                unresolved_reasons=_why_unresolved(kb, spelled, suggestion, fuzzy_candidates(kb, spelled)))


def _why_unresolved(kb: KnowledgeBase, text: str, suggestion: str | None, close: list[str]) -> list[str]:
    words, _ = _vocab(kb)
    unknown = [w for w in _WORD.findall(text) if w not in words and w not in _STOP]
    reasons = []
    if unknown:
        reasons.append("unknown word" + ("s" if len(unknown) > 1 else "") + ": " + ", ".join(unknown))
    if len(close) > 1:
        reasons.append("several close matches, none clearly best: " + ", ".join(close))
    if suggestion:
        reasons.append(f"the closest match '{suggestion}' would mean no allergen, so the guess was not accepted")
    if not close and not suggestion:
        reasons.append("no close match in the knowledge base")
    return reasons


def _qualify(kb: KnowledgeBase, head: NormalizedIngredient, kids: list[NormalizedIngredient]) -> bool:
    """'lecithins (soya)': an ingredient whose source is not stated, followed by its named source(s).

    The source replaces the head's own (possible) source relationships."""
    if not head.resolution or not head.resolution.concepts or not kids:
        return False
    if head.incomplete:  # "spices (cumin, and others)": the named components are not all of them
        return False
    if not any(kb.concepts[c.slug].ambiguous_source for c in head.resolution.concepts):
        return False
    if any(k.resolution is None or not k.resolution.concepts or k.method in ("ocr_correction", "fuzzy") for k in kids):
        return False
    head_concept = kb.concepts[head.resolution.concepts[0].slug]
    if head_concept.concept_type in ("prepared_food", "general_term"):
        # "chocolate (sugar, cocoa mass, milk powder)", "spices (cumin, coriander)": the brackets list the
        # components. They stay components and carry their own evidence; the name's unstated possible
        # ingredients are replaced by them because the full list is given.
        r = head.resolution
        kept = [(cm, p) for cm, p in r.paths if p.certainty == "definite"]
        for k in kids:  # the name carries what its listed components carry
            kept += [(cm, AllergenPath(p.allergen_code, p.certainty, p.nodes, f"{head_concept.name} contains {p.text}"))
                     for cm, p in k.resolution.paths]
        terms = list(r.terms) + [t for k in kids for t in k.resolution.terms]
        definite = {t.allergen_code for t in terms} | {p.allergen_code for _, p in kept if p.certainty == "definite"}
        possible = {p.allergen_code for _, p in kept} - definite
        status = "allergen_linked" if definite else ("possible_allergen" if possible else "no_known_allergen")
        head.resolution = ItemResolution(r.text, status, r.concepts, terms, kept, r.interpretations)
        head.corrections.append("components are listed, so they replace the name's possible ingredients")
        return False
    head_name = head_concept.name
    paths, terms, concepts = [], [], list(head.resolution.concepts)
    for k in kids:
        concepts += k.resolution.concepts
        terms += k.resolution.terms
        for cm, p in k.resolution.paths:
            paths.append((cm, AllergenPath(p.allergen_code, p.certainty, p.nodes, f"{head_name} from {p.text}")))
        k.role = "source_qualifier"
    definite = {t.allergen_code for t in terms} | {p.allergen_code for _, p in paths if p.certainty == "definite"}
    status = "allergen_linked" if definite else ("possible_allergen" if paths else "no_known_allergen")
    head.text = f"{head.text} ({', '.join(k.text for k in kids)})"
    head.role, head.method, head.confidence = "qualified", "qualified", CONFIDENCE["qualified"]
    head.resolution = ItemResolution(head.text, status, concepts, terms, paths)
    return True


NORMALIZATION_CACHE_SIZE = 4096
_cache_stats = {"hits": 0, "misses": 0}
_cache_lock = threading.Lock()


def normalization_cache_stats() -> dict:
    with _cache_lock:
        return dict(_cache_stats)


def normalize_ingredients(kb: KnowledgeBase, text: str, ocr: bool = False) -> list[NormalizedIngredient]:
    """Cached per knowledge-base instance (a new knowledge version gets a fresh cache) and per fuzzy
    settings. Callers get their own copy, so changing a result never affects the cache."""
    s = get_settings()
    from app.engine.ablation import current

    key = (text, ocr, s.fuzzy_phrase_threshold, s.fuzzy_word_threshold, s.fuzzy_min_length, s.fuzzy_margin, current())
    with _cache_lock:
        cache: OrderedDict = kb.__dict__.setdefault("_normalize_cache", OrderedDict())
        hit = cache.get(key)
        if hit is not None:
            cache.move_to_end(key)
            _cache_stats["hits"] += 1
        else:
            _cache_stats["misses"] += 1
    if hit is not None:
        return copy.deepcopy(hit)
    out = _normalize_ingredients(kb, text, ocr)
    with _cache_lock:
        cache[key] = copy.deepcopy(out)
        while len(cache) > NORMALIZATION_CACHE_SIZE:
            cache.popitem(last=False)
    return out


def _normalize_ingredients(kb: KnowledgeBase, text: str, ocr: bool = False) -> list[NormalizedIngredient]:
    parsed = parse_ingredients(text)
    out: list[NormalizedIngredient] = []
    by_item: dict[int, NormalizedIngredient] = {}

    def walk(items: list[ParsedItem]) -> None:
        for it in items:
            if id(it) in by_item:
                continue
            if it.role == "list_note":
                n = NormalizedIngredient(it.original, "", "list_note", it.parent, "none", 0.0, None, incomplete=it.incomplete)
            else:
                n = normalize_item(kb, it.original, it.role, it.parent, ocr)
                n.incomplete = it.incomplete
            by_item[id(it)] = n
            out.append(n)

    walk(parsed)
    # Source qualifiers: decided once heads and their children are normalized.
    for it in parsed:
        if it.role == "compound":
            _qualify(kb, by_item[id(it)], [by_item[id(c)] for c in it.children if id(c) in by_item])
    return out
