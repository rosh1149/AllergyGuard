"""Engine rule checks. Pure Python: the knowledge base is built from the seed data, no database."""

import pytest

from app.engine import (
    AllergyProfile,
    AnalysisContext,
    CrossContactDecl,
    EvidenceSource,
    KnowledgeBase,
    Outcome,
    SourceType,
    assess,
)
from app.engine.explain import _acceptable, template_explanation
from app.engine.knowledge import AllergenInfo
from app.seed.allergen_data import ALLERGENS

P, X, U, N = (
    Outcome.PRESENT,
    Outcome.POSSIBLE_CROSS_CONTACT,
    Outcome.UNKNOWN,
    Outcome.NOT_LISTED_IN_VERIFIED_RECORDS,
)


@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase(
        [AllergenInfo(a["code"], a["name"]) for a in ALLERGENS],
        [(t, a["code"]) for a in ALLERGENS for t in a["synonyms"]],
        [(t, a["code"]) for a in ALLERGENS for t, _ in a["exclusions"]],
    )


def label(text, **kw):
    return EvidenceSource(id="label", type=SourceType.label_ocr, name="Label", ingredients_text=text, **kw)


def outcomes(kb, sources, codes, custom=(), ctx=None):
    r = assess(kb, sources, AllergyProfile(set(codes), list(custom)), ctx)
    return {a.code: a.outcome for a in r.your_allergens}, r


# ---- knowledge-base matching ----


@pytest.mark.parametrize(
    "text, expected",
    [
        ("whey powder", {"milk"}),
        ("sodium caseinate", {"milk"}),
        ("ghee", {"milk"}),
        ("paneer", {"milk"}),
        ("semolina", {"gluten"}),
        ("tahini", {"sesame"}),
        ("peanut butter", {"peanuts"}),  # not milk
        ("cocoa butter", set()),  # not milk
        ("coconut milk", set()),
        ("almond milk", {"tree_nuts"}),  # not milk
        ("nutmeg", set()),  # not a tree nut
        ("eggplant", set()),
        ("buckwheat", set()),
        ("rice flour", set()),
        ("wheat flour", {"gluten"}),
        ("butternut squash", set()),
        ("oyster mushrooms", set()),
        ("oyster sauce", {"molluscs"}),
        ("shellfish", {"crustaceans", "molluscs"}),
        ("soy sauce", {"soy", "gluten"}),
        ("hazelnuts", {"tree_nuts"}),
        ("prawns", {"crustaceans"}),
        ("cream of tartar", set()),
        ("butter beans", set()),
        ("e220", {"sulphites"}),
        ("maltodextrin", set()),
        ("mustard seeds", {"mustard"}),
    ],
)
def test_term_matching(kb, text, expected):
    assert {m.allergen_code for m in kb.match(text)} == expected


# ---- rules ----


def test_present_from_label(kb):
    o, r = outcomes(kb, [label("Ingredients: wheat flour, sugar, butter (milk), salt.")], {"milk", "peanuts"})
    assert o == {"milk": P, "peanuts": N}
    assert r.overall_outcome == P
    assert any("butter" in reason for reason in r.your_allergens[0].reasons)


def test_compound_ingredient_parts_are_checked(kb):
    o, _ = outcomes(kb, [label("chocolate (sugar, cocoa mass, whole milk powder), hazelnuts 13%")], {"milk", "tree_nuts"})
    assert o == {"milk": P, "tree_nuts": P}


def test_may_contain_is_cross_contact_not_presence(kb):
    o, _ = outcomes(kb, [label("oats, sugar, salt. May contain traces of peanuts and sesame.")], {"peanuts", "sesame"})
    assert o == {"peanuts": X, "sesame": X}


def test_factory_statement_is_cross_contact(kb):
    o, _ = outcomes(kb, [label("rice, salt. Made in a factory that also handles milk.")], {"milk"})
    assert o == {"milk": X}


def test_presence_beats_cross_contact(kb):
    o, _ = outcomes(kb, [label("milk chocolate. May contain milk.")], {"milk"})
    assert o == {"milk": P}


def test_no_sources_is_unknown_never_not_listed(kb):
    o, r = outcomes(kb, [], {"milk"})
    assert o == {"milk": U}
    assert r.overall_outcome == U


def test_low_ocr_confidence_cannot_establish_absence(kb):
    o, r = outcomes(kb, [label("rice, salt, sugar", confidence=0.4)], {"milk"})
    assert o == {"milk": U}
    assert any("could not be read reliably" in lim for lim in r.limitations)


def test_low_ocr_confidence_still_reports_presence(kb):
    o, _ = outcomes(kb, [label("rice, milk, sugar", confidence=0.4)], {"milk"})
    assert o == {"milk": P}


def test_incomplete_source_cannot_establish_absence(kb):
    o, _ = outcomes(kb, [label("rice, salt", complete=False)], {"milk"})
    assert o == {"milk": U}


def test_uncertain_identity_downgrades_product_database(kb):
    db_src = EvidenceSource(id="off", type=SourceType.product_database, name="Open Food Facts",
                            ingredients_text="rice, milk powder, salt")
    ctx = AnalysisContext(identity_confidence=0.4)
    o, r = outcomes(kb, [db_src], {"milk", "peanuts"}, ctx=ctx)
    # Possibly the wrong product: its milk is only a candidate, and its silence proves nothing.
    assert o == {"milk": U, "peanuts": U}
    assert any("identified with confidence" in lim for lim in r.limitations)


def test_uncertain_identity_does_not_affect_label_photo(kb):
    ctx = AnalysisContext(identity_confidence=0.4)
    o, _ = outcomes(kb, [label("rice, salt, sugar")], {"milk"}, ctx=ctx)
    assert o == {"milk": N}


def test_public_recipe_is_candidate_only(kb):
    recipe = EvidenceSource(id="web", type=SourceType.public_recipe, name="Recipe site",
                            ingredients=["chicken", "cream", "tomato"])
    o, r = outcomes(kb, [recipe], {"milk", "peanuts"})
    assert o == {"milk": U, "peanuts": U}  # never PRESENT, never NOT LISTED
    assert any("not how this restaurant" in lim for lim in r.limitations)


def test_customer_suggestion_is_candidate_only(kb):
    rv = EvidenceSource(id="rv", type=SourceType.restaurant_verified, name="Spice Route",
                        ingredients=["rice", "salt"], declared_allergens=[])
    sugg = EvidenceSource(id="fb", type=SourceType.customer_suggestion, name="Customer report",
                          ingredients=["ghee"])
    o, _ = outcomes(kb, [rv, sugg], {"milk", "eggs"})
    assert o == {"milk": U, "eggs": N}


def test_restaurant_verified_records(kb):
    rv = EvidenceSource(
        id="rv", type=SourceType.restaurant_verified, name="Spice Route",
        ingredients=["chicken", "butter", "cashew paste"], declared_allergens=["milk", "tree_nuts"],
        cross_contact=[CrossContactDecl("peanuts", "possible", "Shared kitchen")],
    )
    o, r = outcomes(kb, [rv], {"milk", "peanuts", "eggs"})
    assert o == {"milk": P, "peanuts": X, "eggs": N}
    assert not r.conflicts


def test_unverified_restaurant_record_cannot_establish_absence(kb):
    rv = EvidenceSource(id="ru", type=SourceType.restaurant_unverified, name="Spice Route",
                        ingredients=["rice", "butter"])
    o, _ = outcomes(kb, [rv], {"milk", "eggs"})
    assert o == {"milk": P, "eggs": U}


def test_declaration_mismatch_detected(kb):
    rv = EvidenceSource(id="rv", type=SourceType.restaurant_verified, name="Spice Route",
                        ingredients=["chicken", "ghee"], declared_allergens=[])
    o, r = outcomes(kb, [rv], {"milk"})
    assert o == {"milk": P}  # the ingredient wins over the missing declaration
    assert [c.kind for c in r.conflicts] == ["declaration_mismatch"]


def test_disagreement_between_sources_keeps_present(kb):
    ocr = label("oat flakes, sugar, sunflower oil, salt, honey")
    off = EvidenceSource(id="off", type=SourceType.product_database, name="Open Food Facts",
                         ingredients_text="oat flakes, sugar, sunflower oil, salt, honey, skimmed milk powder")
    o, r = outcomes(kb, [ocr, off], {"milk"}, ctx=AnalysisContext(identity_confidence=0.95))
    assert o == {"milk": P}
    assert any(c.kind == "allergen_disagreement" and c.allergen_code == "milk" for c in r.conflicts)


def test_very_different_lists_block_absence(kb):
    ocr = label("rice, salt, sugar, vinegar")
    off = EvidenceSource(id="off", type=SourceType.product_database, name="Open Food Facts",
                         ingredients_text="potato, sunflower oil, paprika, onion powder")
    o, r = outcomes(kb, [ocr, off], {"milk"}, ctx=AnalysisContext(identity_confidence=0.9))
    assert o == {"milk": U}
    assert any(c.kind == "ingredient_list_mismatch" for c in r.conflicts)


def test_free_from_claim_is_unknown_not_absent(kb):
    o, _ = outcomes(kb, [label("gluten-free oats, sugar")], {"gluten"})
    assert o == {"gluten": U}


def test_dairy_free_claim(kb):
    o, _ = outcomes(kb, [label("dairy-free spread (palm oil, water)")], {"milk"})
    assert o == {"milk": U}


def test_custom_allergy(kb):
    o, _ = outcomes(kb, [label("apple, kiwi, banana")], set(), custom=["kiwi"])
    assert o == {"custom:kiwi": P}
    o, r = outcomes(kb, [label("apple, banana")], set(), custom=["kiwi"])
    assert o == {"custom:kiwi": N}
    assert any("custom allergy" in reason for reason in r.your_allergens[0].reasons)
    o, _ = outcomes(kb, [label("apple. May contain kiwi.")], set(), custom=["Kiwi "])
    assert o == {"custom:kiwi": X}


def test_other_allergens_are_reported_separately(kb):
    _, r = outcomes(kb, [label("wheat flour, egg, sesame seeds")], {"milk"})
    assert [a.code for a in r.your_allergens] == ["milk"]
    assert {a.code for a in r.other_allergens} == {"gluten", "eggs", "sesame"}


def test_overall_is_most_severe(kb):
    o, r = outcomes(kb, [label("rice, egg. May contain peanuts.")], {"eggs", "peanuts", "milk"})
    assert o == {"eggs": P, "peanuts": X, "milk": N}
    assert r.overall_outcome == P
    assert [a.code for a in r.your_allergens] == ["eggs", "peanuts", "milk"]


def test_nonspecific_ingredients_listed(kb):
    _, r = outcomes(kb, [label("rice, spices, natural flavourings")], {"celery"})
    assert set(r.nonspecific_ingredients) == {"spices", "natural flavourings"}


# ---- explanation ----


def test_template_never_claims_safety(kb):
    _, r = outcomes(kb, [label("rice, salt")], {"milk"})
    text = template_explanation(r)
    assert "not a guarantee" in text
    assert _acceptable(text, r)


@pytest.mark.parametrize(
    "bad",
    ["This product is safe for you.", "It looks fine to eat Milk.", "Milk: allergen-free.", "No risk from Milk."],
)
def test_llm_output_checks_reject_safety_claims(kb, bad):
    _, r = outcomes(kb, [label("rice, salt")], {"milk"})
    assert not _acceptable(bad, r)


def test_llm_output_checks_require_every_allergen(kb):
    _, r = outcomes(kb, [label("rice, salt")], {"milk", "peanuts"})
    assert not _acceptable("Milk was not listed in verified records.", r)
    assert _acceptable("Milk and Peanuts were not listed in the verified records; this is not proof.", r)
