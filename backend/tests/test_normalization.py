"""Step B: multi-stage ingredient normalization."""

import pytest

from app.core.config import get_settings
from app.engine import AllergyProfile, EvidenceSource, Outcome, SourceType, assess
from app.engine.normalize import clean, normalize_ingredients, parse_ingredients
from app.seed.seed import DEMO_CUSTOMER_EMAIL
from tests.helpers import login
from tests.kb_fixture import build_kb

P, U, N = Outcome.PRESENT, Outcome.UNKNOWN, Outcome.NOT_LISTED_IN_VERIFIED_RECORDS


@pytest.fixture(scope="module")
def kb():
    return build_kb()


def norm(kb, text, ocr=False):
    return {n.original: n for n in normalize_ingredients(kb, text, ocr=ocr)}


def allergens(n):
    r = n.resolution
    return {t.allergen_code for t in r.terms} | {p.allergen_code for _, p in r.paths if p.certainty == "definite"}


def outcomes(kb, text, codes, src_type=SourceType.label_ocr):
    src = EvidenceSource(id="l", type=src_type, name="Label", ingredients_text=text)
    r = assess(kb, [src], AllergyProfile(set(codes)))
    return {a.code: a for a in r.your_allergens}, r


# ---- 1. cleaning ----


@pytest.mark.parametrize(
    "raw, cleaned",
    [
        ("Sugar®", "sugar"),
        ("E 322", "e322"),
        ("e-621", "e621"),
        ("Hazelnuts 13%", "hazelnuts"),
        ("  and Salt. ", "salt"),
        ("Whey•Powder", "whey powder"),
    ],
)
def test_cleaning(raw, cleaned):
    assert clean(raw) == cleaned


# ---- 6. compound ingredients ----


def test_parse_compound_class_and_header():
    items = [(p.role, p.original, p.parent) for p in parse_ingredients(
        "Ingredients: chocolate (sugar, cocoa mass, milk powder), emulsifier: lecithins (soya), "
        "contains 2% or less of: salt, thickener (guar gum)")]
    assert items == [
        ("compound", "chocolate", None),
        ("component", "sugar", "chocolate"),
        ("component", "cocoa mass", "chocolate"),
        ("component", "milk powder", "chocolate"),
        ("class_label", "emulsifier", None),
        ("compound", "lecithins", "emulsifier"),
        ("component", "soya", "lecithins"),
        ("ingredient", "salt", None),
        ("class_label", "thickener", None),
        ("component", "guar gum", "thickener"),
    ]


def test_source_qualifier_replaces_possible_sources(kb):
    # Step A had to report egg as possible for "lecithins"; the label names the source.
    mine, r = outcomes(kb, "sugar, emulsifier: lecithins (soya)", {"soy", "eggs"})
    assert mine["soy"].outcome == P and mine["eggs"].outcome == N
    q = next(i for i in r.ingredients if i.role == "qualified")
    assert q.text == "lecithins (soya)" and q.allergens == ["soy"] and q.relationship_path.startswith("lecithin from soybean")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("flour (wheat)", {"gluten", "wheat"}),
        ("vegetable oil (sunflower, rapeseed)", set()),
        ("vegetable oil (peanut)", {"peanuts"}),
        ("starch (maize)", set()),
    ],
)
def test_qualified_sources(kb, text, expected):
    head = next(n for n in normalize_ingredients(kb, text) if n.role == "qualified")
    assert allergens(head) == expected
    assert head.resolution.status == ("allergen_linked" if expected else "no_known_allergen")


def test_compound_keeps_components_and_skips_class_names(kb):
    mine, r = outcomes(kb, "chocolate (sugar, cocoa mass, milk powder), thickener (guar gum)", {"milk"})
    assert mine["milk"].outcome == P
    roles = {i.text: (i.role, i.parent) for i in r.ingredients}
    assert roles["milk powder"] == ("component", "chocolate")
    assert roles["thickener"][0] == "class_label"
    assert "thickener" not in r.nonspecific_ingredients


# ---- 2. OCR correction ----


@pytest.mark.parametrize("ocr_text, fixed, allergen", [("caseln", "casein", "milk"), ("ca5ein", "casein", "milk"),
                                                       ("rnilk", "milk", "milk"), ("aimond", "almond", "tree_nuts")])
def test_ocr_correction(kb, ocr_text, fixed, allergen):
    n = norm(kb, ocr_text, ocr=True)[ocr_text]
    assert n.text == fixed and n.method == "ocr_correction" and n.confidence == 0.85
    assert allergen in allergens(n)
    assert any("OCR correction" in c for c in n.corrections)


def test_ocr_correction_only_for_photos(kb):
    photo, _ = outcomes(kb, "rice, caseln", {"milk"}, SourceType.label_ocr)
    assert photo["milk"].outcome == P
    assert "(read as 'casein', OCR correction)" in photo["milk"].reasons[0]
    typed = norm(kb, "caseln", ocr=False)["caseln"]
    assert typed.resolution.status == "unresolved" and typed.method == "none"


def test_ocr_correction_leaves_known_words_alone(kb):
    for word in ("sugar", "milk", "salt", "lime"):
        n = norm(kb, word, ocr=True)[word]
        assert n.text == word and n.method == "exact"


# ---- 3. spelling ----


@pytest.mark.parametrize(
    "written, normalized, allergen",
    [("mayonaise", "mayonnaise", "eggs"), ("sesami seeds", "sesame seeds", "sesame"),
     ("worcester sauce", "worcestershire sauce", "fish"), ("caseine", "casein", "milk")],
)
def test_spelling(kb, written, normalized, allergen):
    n = norm(kb, written)[written]
    assert n.text == normalized and n.method == "spelling" and allergen in allergens(n)


def test_us_spelling_class_label(kb):
    items = {p.original: p.role for p in parse_ingredients("flavor enhancer (msg)")}
    assert items["flavor enhancer"] == "class_label"


# ---- 5. fuzzy matching ----


@pytest.mark.parametrize("written, allergen", [("hazelnutt", "tree_nuts"), ("worcestershir sauce", "fish"),
                                              ("almonde", "tree_nuts"), ("penaut oil", "peanuts")])
def test_fuzzy_match_adds_allergen(kb, written, allergen):
    n = norm(kb, written)[written]
    assert n.method == "fuzzy" and allergen in allergens(n) and 0 < n.confidence < 0.9


def test_fuzzy_threshold_is_configurable(kb, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "fuzzy_phrase_threshold", 99.5)
    monkeypatch.setattr(s, "fuzzy_word_threshold", 99.5)
    n = norm(kb, "hazelnutt")["hazelnutt"]
    assert n.resolution.status == "unresolved" and n.method == "none"


# ---- 8. safety rule: guesses never make an ingredient "harmless" ----


@pytest.mark.parametrize("written, guess", [("cocoa mas", "cocoa mass"), ("sunflwer oil", "sunflower oil")])
def test_harmless_guess_stays_unresolved(kb, written, guess):
    n = norm(kb, written)[written]
    assert n.resolution.status == "unresolved"
    assert n.suggestion == guess and n.method == "none"


def test_unresolved_text_is_preserved(kb):
    _, r = outcomes(kb, "rice, zzqx extract", {"milk"})
    item = next(i for i in r.ingredients if i.original_text == "zzqx extract")
    assert item.status == "unresolved" and item.text == "zzqx extract"
    assert any("zzqx extract" in lim for lim in r.limitations)


# ---- every normalized ingredient keeps its trace ----


def test_resolve_endpoint_reports_each_stage(client):
    h = login(client, DEMO_CUSTOMER_EMAIL)
    rows = client.post("/api/knowledge/resolve", headers=h,
                       json={"text": "Caseln, mayonaise, emulsifier: lecithins (soya), cocoa mas", "ocr": True}).json()
    by = {r["original_text"]: r for r in rows}
    assert (by["caseln"]["text"], by["caseln"]["match_method"], by["caseln"]["concept"]) == ("casein", "ocr_correction", "casein")
    assert by["caseln"]["foodon_id"] == "FOODON:03420180" and by["caseln"]["match_confidence"] == 0.85
    assert (by["mayonaise"]["text"], by["mayonaise"]["match_method"]) == ("mayonnaise", "spelling")
    assert by["emulsifier"]["status"] == "class_name"
    assert by["lecithins"]["role"] == "qualified" and by["lecithins"]["allergens"] == ["soy"]
    assert by["soya"]["role"] == "source_qualifier"
    assert by["cocoa mas"]["status"] == "unresolved" and by["cocoa mas"]["suggestion"] == "cocoa mass"
