"""Ingredient text normalization and splitting."""

import re
import unicodedata

_PREFIX = re.compile(r"^\s*(ingredients?|ingr\.?|zutaten|ingrédients)\s*[:\-]\s*", re.I)

# Precautionary ("may contain") statements -> cross-contact, not ingredient presence.
_PRECAUTION = re.compile(
    r"(may\s+(also\s+)?contain|may\s+be\s+present|(contains?\s+)?traces?\s+of|"
    r"(produced|made|manufactured|processed|prepared|packed|packaged)\s+(in|on|at)\s+"
    r"(a\s+)?(factory|facility|site|kitchen|premises|line|equipment|environment)"
    r"|not\s+suitable\s+for\s+(people|those|customers)\s+with)"
    r"[^.;\n]*",
    re.I,
)

# Words that aren't ingredients but appear in ingredient statements.
_NOISE = re.compile(r"\b\d+(\.\d+)?\s*%|\*+|\bmin(imum)?\b\.?", re.I)

# "gluten-free", "dairy free", "free from milk", "free of nuts"
_FREE_SUFFIX = re.compile(r"\b([a-z][a-z' ]{1,30}?)[\s-]free\b")
_FREE_PREFIX = re.compile(r"\bfree\s+(?:from|of)\s+([a-z ,&']{2,60})")

# Ingredients that name a mixture without listing its components.
NONSPECIFIC_TERMS = [
    "spices", "spice", "spice mix", "seasoning", "seasonings", "flavouring", "flavourings",
    "flavoring", "flavorings", "natural flavour", "natural flavours", "natural flavor",
    "natural flavors", "sauce", "stock", "broth", "dressing", "marinade", "masala", "curry paste",
    "lecithin", "emulsifier", "emulsifiers", "thickener", "stabiliser", "stabilizer", "starch",
    "modified starch", "hydrolysed protein", "hydrolyzed protein", "vegetable protein",
    "mixed fruit", "filling", "coating", "topping", "glaze",
]
_NONSPECIFIC = {t for t in NONSPECIFIC_TERMS}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_precautions(text: str) -> tuple[str, list[str]]:
    """Return (text without precautionary statements, the statements)."""
    statements = [m.group(0).strip() for m in _PRECAUTION.finditer(text)]
    return _PRECAUTION.sub(" ", text), statements


def split_items(text: str) -> list[str]:
    """Split an ingredient statement into items, keeping both compound names and their parts.

    "chocolate (sugar, cocoa butter, milk powder), salt" ->
    ["chocolate", "sugar", "cocoa butter", "milk powder", "salt"]
    """
    text = _PREFIX.sub("", text)
    text = _NOISE.sub(" ", text)
    parts = re.split(r"[,;:()\[\]{}]|\.(?=\s|$)|\s+-\s+|\band/or\b|\s&\s", text)
    items: list[str] = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip(" .-'\"")
        p = re.sub(r"^(and|or|with|contains?)\s+", "", p)
        if len(p) >= 2 and p not in items:
            items.append(p)
    return items


def free_from_claims(item: str) -> list[str]:
    """Phrases inside an item that claim absence, e.g. 'gluten' in 'gluten-free oats'."""
    claims = [m.group(1).strip() for m in _FREE_SUFFIX.finditer(item)]
    for m in _FREE_PREFIX.finditer(item):
        claims.extend(c.strip() for c in re.split(r",|&|\band\b", m.group(1)) if c.strip())
    return claims


# Generic heads that make an item non-specific however it is qualified ("natural flavourings").
_NONSPECIFIC_HEADS = (
    "flavouring", "flavourings", "flavoring", "flavorings", "flavour", "flavours", "flavor",
    "flavors", "seasoning", "seasonings", "spices", "spice mix", "spice blend",
)


def is_nonspecific(item: str) -> bool:
    return item in _NONSPECIFIC or any(item.endswith(" " + h) for h in _NONSPECIFIC_HEADS)


def word_set(items: list[str]) -> set[str]:
    words: set[str] = set()
    for it in items:
        words.update(w.rstrip("s") for w in re.findall(r"[a-z]{3,}", it))
    return words


def term_pattern(term: str) -> re.Pattern[str]:
    """Whole-word, plural-tolerant pattern; spaces and hyphens are interchangeable."""
    words = [re.escape(w) for w in re.split(r"[\s-]+", term.strip()) if w]
    body = r"[\s-]+".join(words)
    return re.compile(rf"(?<![a-z0-9]){body}(?:s|es)?(?![a-z0-9])")
