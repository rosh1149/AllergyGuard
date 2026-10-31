"""Restaurant-food pipeline: restaurant/branch + dish photo/name -> menu match -> branch records
(or public-recipe fallback) -> shared engine -> result + questions for staff."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.similarity import name_similarity
from app.engine.service import AssessmentLink, analyze_for_user
from app.engine.types import (
    MIN_IDENTITY_CONFIDENCE,
    AnalysisContext,
    AnalysisResult,
    CrossContactDecl,
    EvidenceSource,
    SourceType,
)
from app.models import (
    Branch,
    FeedbackSuggestion,
    MenuItem,
    MenuItemStatus,
    RecipeVersion,
    Restaurant,
    SuggestionStatus,
    SuggestionType,
    User,
    VerificationStatus,
)
from app.packaged.images import LoadedImage
from app.packaged.pipeline import Step
from app.restaurant.fallback import RecipeSource, public_recipe_candidates, staff_questions
from app.restaurant.recipes import current_version, to_evidence
from app.restaurant.vision import identify_dish

MIN_MATCH = 0.5  # below this, no menu item is treated as the dish
PHOTO_ONLY_CAP = 0.6  # a photo alone never reaches the 0.7 identity threshold


class RestaurantInfo(BaseModel):
    registered: bool
    restaurant_id: int | None = None
    name: str
    branch_id: int | None = None
    branch_name: str = ""
    location: str = ""


class DishCandidate(BaseModel):
    menu_item_id: int
    name: str
    confidence: float


class DishInfo(BaseModel):
    menu_item_id: int
    name: str
    match_method: str  # selected_from_menu | dish_name | photo | dish_name+photo
    match_confidence: float
    confident: bool
    recipe_version: int | None
    verification_status: VerificationStatus | None


class RestaurantAnalysisOut(BaseModel):
    result: AnalysisResult
    restaurant: RestaurantInfo
    dish: DishInfo | None
    # Other likely menu items, so the app can ask the customer to confirm the dish.
    candidates: list[DishCandidate]
    questions_for_staff: list[str]
    public_recipe_sources: list[RecipeSource]
    steps: list[Step]
    can_submit_feedback: bool


@dataclass
class _Match:
    item: MenuItem
    confidence: float
    method: str


def _match_dish(
    menu: list[MenuItem], dish_name: str, photo_choice: str, photo_conf: float
) -> list[_Match]:
    matches: list[_Match] = []
    for item in menu:
        name_score = name_similarity(dish_name, item.name) if dish_name else 0.0
        photo_hit = bool(photo_choice) and photo_choice.strip().lower() == item.name.strip().lower()
        photo_score = min(photo_conf, PHOTO_ONLY_CAP) if photo_hit else 0.0
        if name_score >= MIN_MATCH and photo_hit:
            conf, method = min(1.0, max(name_score, photo_conf) + 0.1), "dish_name+photo"
        elif name_score >= photo_score:
            conf, method = name_score, "dish_name"
        else:
            conf, method = photo_score, "photo"
        if conf >= 0.3:
            matches.append(_Match(item, round(conf, 2), method))
    return sorted(matches, key=lambda m: -m.confidence)


def _suggestion_source(db: Session, item: MenuItem, version: RecipeVersion) -> tuple[EvidenceSource | None, int]:
    """Unreviewed customer reports about the current recipe version. Reports made against an
    earlier version are not used (the recipe has changed since); their count is returned."""
    pending = db.scalars(
        select(FeedbackSuggestion).where(
            FeedbackSuggestion.menu_item_id == item.id, FeedbackSuggestion.status == SuggestionStatus.unverified
        )
    ).all()
    older = [s for s in pending if s.recipe_version_id is not None and s.recipe_version_id != version.id]
    pending = [s for s in pending if s not in older]
    ingredients, declared, cross = [], [], []
    for s in pending:
        if s.suggestion_type == SuggestionType.missing_ingredient and s.ingredient_name:
            ingredients.append(s.ingredient_name)
        elif s.suggestion_type == SuggestionType.missing_allergen and s.allergen:
            declared.append(s.allergen.code)
        elif s.suggestion_type == SuggestionType.cross_contact and s.allergen:
            cross.append(CrossContactDecl(s.allergen.code, "possible", s.message or "Reported by a customer",
                                          kind="other", status="reported"))
    if not (ingredients or declared or cross):
        return None, len(older)
    return EvidenceSource(
        id=f"suggestions:{item.id}",
        type=SourceType.customer_suggestion,
        name=f"Unreviewed customer reports for {item.name}",
        ingredients=ingredients,
        declared_allergens=declared,
        cross_contact=cross,
        complete=False,
        reference=f"{len(pending)} customer report(s) on recipe v{version.version_number}",
        verification="customer",
        as_of=max(s.created_at for s in pending),
    ), len(older)


def run_restaurant_pipeline(
    db: Session,
    user: User,
    *,
    restaurant_id: int | None,
    branch_id: int | None,
    restaurant_name: str,
    location: str,
    dish_name: str,
    menu_item_id: int | None,
    images: list[LoadedImage],
    use_ai: bool = True,
) -> RestaurantAnalysisOut:
    dish_name = " ".join(dish_name.split())
    steps: list[Step] = []
    limitations: list[str] = []
    for i, img in enumerate(images, 1):
        for issue in img.issues:
            limitations.append(f"Photo {i}: {issue}" if len(images) > 1 else issue)

    sources: list[EvidenceSource] = []
    dish: DishInfo | None = None
    candidates: list[DishCandidate] = []
    recipe_sources: list[RecipeSource] = []
    identity_conf: float | None = None
    guess = None
    dish_label = dish_name
    link = AssessmentLink(kind="restaurant", restaurant_id=restaurant_id)

    if restaurant_id is not None:
        restaurant = db.get(Restaurant, restaurant_id)
        if restaurant is None or not restaurant.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Restaurant not found.")
        branches = [b for b in restaurant.branches if b.is_active]
        if branch_id is None:
            if len(branches) != 1:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose the restaurant branch.")
            branch = branches[0]
        else:
            branch = next((b for b in branches if b.id == branch_id), None)
            if branch is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Branch not found.")
        info = RestaurantInfo(
            registered=True, restaurant_id=restaurant.id, name=restaurant.name,
            branch_id=branch.id, branch_name=branch.name, location=branch.city,
        )
        menu = [
            i for i in db.scalars(select(MenuItem).where(MenuItem.branch_id == branch.id)).all()
            if i.status == MenuItemStatus.available and current_version(i) is not None
        ]

        matches: list[_Match] = []
        if menu_item_id is not None:
            item = next((i for i in menu if i.id == menu_item_id), None)
            if item is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Menu item not found at this branch.")
            matches = [_Match(item, 1.0, "selected_from_menu")]
            steps.append(Step(name="dish_match", status="done", detail=f"Selected from menu: {item.name}."))
        else:
            if images and use_ai:
                guess = identify_dish([i.image for i in images], [i.name for i in menu])
                steps.append(Step(
                    name="dish_recognition",
                    status="done" if guess else "skipped",
                    detail=(guess.menu_choice or guess.dish_description or "Not recognised") if guess
                    else "Photo recognition is not configured.",
                ))
            matches = _match_dish(menu, dish_name, guess.menu_choice if guess else "", guess.confidence if guess else 0.0)
            steps.append(Step(
                name="dish_match",
                status="done",
                detail=f"Best match: {matches[0].item.name} ({matches[0].confidence:.0%})." if matches else "No menu item matched.",
            ))
        candidates = [DishCandidate(menu_item_id=m.item.id, name=m.item.name, confidence=m.confidence) for m in matches[:3]]

        chosen = matches[0] if matches and matches[0].confidence >= MIN_MATCH else None
        if chosen:
            item, version = chosen.item, current_version(chosen.item)
            dish = DishInfo(
                menu_item_id=item.id, name=item.name, match_method=chosen.method,
                match_confidence=chosen.confidence, confident=chosen.confidence >= MIN_IDENTITY_CONFIDENCE,
                recipe_version=version.version_number, verification_status=version.verification_status,
            )
            identity_conf = chosen.confidence
            dish_label = item.name
            sources.append(to_evidence(item, version, restaurant.name))
            suggestion_src, older_reports = _suggestion_source(db, item, version)
            if suggestion_src:
                sources.append(suggestion_src)
            if older_reports:
                limitations.append(
                    f"{older_reports} unreviewed customer report(s) refer to an earlier version of this recipe "
                    "and were not used."
                )
            link = AssessmentLink(kind="restaurant", restaurant_id=restaurant.id, menu_item_id=item.id,
                                  recipe_version_id=version.id)
            if not dish.confident:
                limitations.append(
                    f"We are not sure this is '{item.name}'. Confirm the dish from the menu for a reliable result."
                )
            steps.append(Step(name="restaurant_records", status="done", detail=f"Recipe v{version.version_number} ({version.verification_status.value})."))
        else:
            dish_label = dish_name or (guess.dish_description if guess else "")
            limitations.append(
                f"This dish could not be matched to the {branch.name} menu, so the restaurant's own records were not used."
            )
    else:
        info = RestaurantInfo(registered=False, name=restaurant_name.strip() or "Unregistered restaurant", location=location.strip())
        if images and use_ai and not dish_name:
            guess = identify_dish([i.image for i in images], [])
            steps.append(Step(
                name="dish_recognition",
                status="done" if guess else "skipped",
                detail=(guess.dish_description or "Not recognised") if guess else "Photo recognition is not configured.",
            ))
        dish_label = dish_name or (guess.dish_description if guess and guess.is_food else "")
        limitations.append(
            "This restaurant is not registered with AllergyGuard, so no restaurant-verified information is available."
        )

    if dish is None:
        candidates_found = None
        if dish_label and use_ai:
            context = ", ".join(p for p in (info.name if not info.registered or info.restaurant_id else "", info.location) if p)
            candidates_found = public_recipe_candidates(dish_label, context)
        if candidates_found:
            recipe_sources = candidates_found.sources
            sources.append(EvidenceSource(
                id="public_recipes",
                type=SourceType.public_recipe,
                name=f"Public recipes for '{candidates_found.dish or dish_label}'",
                ingredients=candidates_found.ingredients,
                complete=False,
                url=recipe_sources[0].url if recipe_sources else None,
                reference=", ".join(r.url for r in recipe_sources[:3] if r.url),
                verification="public",
            ))
            steps.append(Step(name="public_recipes", status="done", detail=f"{len(candidates_found.ingredients)} candidate ingredients."))
        else:
            steps.append(Step(
                name="public_recipes", status="skipped",
                detail="Public recipe search is not configured." if use_ai else "Not requested.",
            ))
            if not dish_label:
                limitations.append("No dish name was given or recognised.")

    ctx = AnalysisContext(
        identity_confidence=identity_conf,
        identity_label=f"{dish_label} at {info.name}" + (f" ({info.branch_name})" if info.branch_name else "") if dish_label else info.name,
        limitations=limitations,
    )
    result = analyze_for_user(db, user, sources, ctx, record=link)
    steps.append(Step(name="analysis", status="done", detail=result.overall_label))
    return RestaurantAnalysisOut(
        result=result,
        restaurant=info,
        dish=dish,
        candidates=candidates,
        questions_for_staff=staff_questions(result, dish_label, info.registered and dish is not None),
        public_recipe_sources=recipe_sources,
        steps=steps,
        can_submit_feedback=dish is not None,
    )
