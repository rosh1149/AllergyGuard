"""Help for dishes without restaurant records: public-recipe candidates and questions for staff.

Public recipes describe how a dish is commonly made. They are passed to the engine as
`public_recipe` evidence (candidates only), are never saved, and never count as the restaurant's data.
"""

from __future__ import annotations

import re

import logging

from pydantic import BaseModel, Field

from app.core.llm import FALLBACK_BETA, get_client, model_name
from app.engine.types import AnalysisResult, Outcome

log = logging.getLogger(__name__)


class RecipeSource(BaseModel):
    title: str
    url: str


class RecipeCandidates(BaseModel):
    dish: str = Field(description="The dish the recipes are for.")
    ingredients: list[str] = Field(description="Ingredients that commonly appear in recipes for this dish, including sauces, oils, garnishes and stocks.")
    sources: list[RecipeSource] = Field(description="Recipe pages actually consulted (title and URL).")


def public_recipe_candidates(dish: str, context: str) -> RecipeCandidates | None:
    """Search public recipe pages for a dish (needs ANTHROPIC_API_KEY). Returns None if unavailable."""
    client = get_client(timeout=120.0)
    if client is None or not dish.strip():
        return None
    import anthropic

    prompt = (
        f"Search the web for 2-3 public recipes for the dish '{dish}'"
        + (f" ({context})" if context else "")
        + ". List the ingredients that commonly appear across them, including those in sauces, marinades, "
        "cooking fats, stocks and garnishes. Use only ingredients found in the recipes you read, and list "
        "the recipe pages you used. These are general recipes, not the restaurant's own."
    )
    try:
        response = client.beta.messages.parse(
            model=model_name(),
            max_tokens=8000,
            output_config={"effort": "low"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
            output_format=RecipeCandidates,
        )
    except anthropic.APIConnectionError as e:
        log.warning("Public recipe search: connection error: %s", e)
        return None
    except anthropic.RateLimitError:
        log.warning("Public recipe search: rate limited")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Public recipe search: API error %s: %s", e.status_code, e.message)
        return None
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        log.warning("Public recipe search: stop_reason=%s", response.stop_reason)
        return None
    out = response.parsed_output
    out.ingredients = clean_candidate_ingredients(out.ingredients)
    out.sources = [s for s in out.sources if s.url.startswith(("http://", "https://"))][:5]
    return out if out.ingredients else None


# Common places each allergen hides in restaurant food, used to make staff questions concrete.
HIDDEN_SOURCES = {
    "milk": "butter, ghee, cream, cheese, yogurt or milk powder",
    "gluten": "wheat flour, breadcrumbs, batter, soy sauce, malt or thickened sauces",
    "eggs": "mayonnaise, egg wash, batter, aioli or fresh pasta",
    "peanuts": "peanut oil, satay sauce, peanut butter or crushed peanuts",
    "tree_nuts": "cashew or almond paste, pesto, praline or nut oils",
    "sesame": "sesame oil, tahini, sesame seeds or hummus",
    "soy": "soy sauce, tofu, miso, soy oil or soy lecithin",
    "fish": "fish sauce, anchovies, Worcestershire sauce or fish stock",
    "crustaceans": "shrimp paste, prawns, crab or shellfish stock",
    "molluscs": "oyster sauce, squid or mussels",
    "mustard": "mustard seeds, mustard oil, dressings or marinades",
    "celery": "celery, celery salt, stock or soup bases",
    "sulphites": "wine, vinegar, dried fruit or pickles",
    "lupin": "lupin flour in breads or batters",
}


def staff_questions(result: AnalysisResult, dish: str, registered: bool) -> list[str]:
    """Deterministic checklist of questions for the customer to ask restaurant staff."""
    dish = dish or "this dish"
    qs: list[str] = []
    for a in result.your_allergens:
        name = a.name if not a.is_custom else a.name
        lower = name.lower()
        if a.outcome == Outcome.PRESENT:
            qs.append(f"{dish} appears to contain {lower}. Can it be prepared without it, and would anything else change?")
        elif a.outcome == Outcome.POSSIBLE_CROSS_CONTACT:
            qs.append(
                f"How do you prevent cross-contact with {lower} for {dish} "
                "(shared fryers, grills, surfaces, utensils)?"
            )
        else:
            hidden = HIDDEN_SOURCES.get(a.code)
            qs.append(
                f"Does {dish} contain {lower}"
                + (f", for example {hidden}" if hidden else "")
                + ", including in sauces, marinades, oils or garnishes?"
            )
    if registered:
        qs.append("Has the recipe or any supplier changed recently?")
    else:
        qs.append("Do you have an allergen menu or ingredient list for this dish that I can see?")
    qs.append("Can you tell the kitchen about my allergies when the order is placed?")
    return qs


_INGREDIENT_TEXT = re.compile(r"^[a-z0-9][a-z0-9 ,'()&/%.+-]{0,79}$")
_NOT_AN_INGREDIENT = re.compile(r"https?://|www\.|\b(ignore|instruction|assistant|system|prompt|safe|allergen[- ]free)\b")


def clean_candidate_ingredients(items: list[str]) -> list[str]:
    """Public-recipe ingredients are untrusted text: keep short plain ingredient names only.

    Anything that looks like a link, an instruction or a safety claim is dropped. Kept items are still
    only candidates: they can raise a warning, never confirm or rule out an allergen."""
    out: list[str] = []
    for raw in items[:80]:
        t = " ".join(str(raw).lower().split())
        if _INGREDIENT_TEXT.match(t) and not _NOT_AN_INGREDIENT.search(t) and t not in out:
            out.append(t)
    return out[:60]
