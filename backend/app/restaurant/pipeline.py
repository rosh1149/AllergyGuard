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
    EvidenceSource,
)
from app.models import (
    ReviewKind,
    MenuItem,
    MenuItemStatus,
    Restaurant,
    User,
    VerificationStatus,
)
from app.packaged.images import LoadedImage
from app.packaged.pipeline import Step
from app.restaurant.fallback import RecipeSource, public_recipe_candidates, staff_questions
from app.core.timing import StageTimer
from app.restaurant.recipes import current_version
from app.review.queue import ReviewFlag, queue_flags_later
from app.sources.restaurant import CustomerReportsAdapter, PublicRecipeAdapter, RestaurantRecordAdapter
from app.restaurant.vision import identify_dish

MIN_MATCH = 0.5  # below this, no menu item is treated as the dish
PHOTO_ONLY_CAP = 0.6  # a photo alone never reaches the 0.7 identity threshold
AMBIGUITY_MARGIN = 0.1  # top two matches closer than this: ask the customer which dish it is
AMBIGUOUS_CONFIDENCE_CAP = 0.6


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
    # The dish match is uncertain: the app should ask the customer to pick from `candidates`.
    needs_confirmation: bool = False


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
    flags: list[ReviewFlag] = []
    needs_confirmation = False
    timer = StageTimer()

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
                with timer("dish_recognition"):
                    guess = identify_dish([i.image for i in images], [i.name for i in menu])
                steps.append(Step(
                    name="dish_recognition",
                    status="done" if guess else "skipped",
                    detail=(guess.menu_choice or guess.dish_description or "Not recognised") if guess
                    else "Photo recognition is not configured.",
                ))
            with timer("dish_match"):
                matches = _match_dish(menu, dish_name, guess.menu_choice if guess else "", guess.confidence if guess else 0.0)
            steps.append(Step(
                name="dish_match",
                status="done",
                detail=f"Best match: {matches[0].item.name} ({matches[0].confidence:.0%})." if matches else "No menu item matched.",
            ))
        candidates = [DishCandidate(menu_item_id=m.item.id, name=m.item.name, confidence=m.confidence) for m in matches[:3]]

        chosen = matches[0] if matches and matches[0].confidence >= MIN_MATCH else None
        # Two menu items matched about equally well: ask which one, and don't trust either meanwhile.
        ambiguous = bool(
            chosen and chosen.method != "selected_from_menu" and len(matches) > 1
            and matches[1].confidence >= MIN_MATCH and chosen.confidence - matches[1].confidence < AMBIGUITY_MARGIN
        )
        query = dish_name or (guess.menu_choice or guess.dish_description if guess else "")
        if chosen:
            item, version = chosen.item, current_version(chosen.item)
            conf = min(chosen.confidence, AMBIGUOUS_CONFIDENCE_CAP) if ambiguous else chosen.confidence
            dish = DishInfo(
                menu_item_id=item.id, name=item.name, match_method=chosen.method,
                match_confidence=conf, confident=conf >= MIN_IDENTITY_CONFIDENCE,
                recipe_version=version.version_number, verification_status=version.verification_status,
            )
            identity_conf = conf
            needs_confirmation = not dish.confident
            if ambiguous:
                limitations.append(
                    "Several menu items match about equally well ("
                    + ", ".join(c.name for c in candidates) + "). Choose the dish from the menu for a reliable result."
                )
                flags.append(ReviewFlag(
                    ReviewKind.multiple_dish_matches, query, f"'{query}' matches several menu items.",
                    {"candidates": [c.model_dump() for c in candidates]}, restaurant.id,
                ))
            elif not dish.confident:
                flags.append(ReviewFlag(
                    ReviewKind.low_confidence_dish, query,
                    f"'{query}' was matched to {item.name} with low confidence.",
                    {"matched_item": item.name, "method": chosen.method, "confidence": chosen.confidence}, restaurant.id,
                ))
            dish_label = item.name
            with timer("restaurant_records"):
                records = RestaurantRecordAdapter().fetch(item, version, restaurant.name)
                sources.extend(records.sources)
                reports = CustomerReportsAdapter().fetch(db, item, version)
            sources.extend(reports.sources)
            older_reports = reports.payload or 0
            if reports.status.failed:
                limitations.append("Unreviewed customer reports could not be loaded.")
            if older_reports:
                limitations.append(
                    f"{older_reports} unreviewed customer report(s) refer to an earlier version of this recipe "
                    "and were not used."
                )
            link = AssessmentLink(kind="restaurant", restaurant_id=restaurant.id, menu_item_id=item.id,
                                  recipe_version_id=version.id)
            if not dish.confident and not ambiguous:
                limitations.append(
                    f"We are not sure this is '{item.name}'. Confirm the dish from the menu for a reliable result."
                )
            steps.append(Step(name="restaurant_records", status="done", detail=f"Recipe v{version.version_number} ({version.verification_status.value})."))
        else:
            dish_label = dish_name or (guess.dish_description if guess else "")
            limitations.append(
                f"This dish could not be matched to the {branch.name} menu, so the restaurant's own records were not used."
            )
            needs_confirmation = bool(candidates)
            if dish_name:
                flags.append(ReviewFlag(
                    ReviewKind.unmatched_dish, dish_name,
                    f"A customer asked about '{dish_name}', which did not match the {branch.name} menu.",
                    {"branch": branch.name, "closest": [c.model_dump() for c in candidates]}, restaurant.id,
                ))
    else:
        info = RestaurantInfo(registered=False, name=restaurant_name.strip() or "Unregistered restaurant", location=location.strip())
        if images and use_ai and not dish_name:
            with timer("dish_recognition"):
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
        public = None
        if dish_label and use_ai:
            context = ", ".join(p for p in (info.name if not info.registered or info.restaurant_id else "", info.location) if p)
            with timer("public_recipes"):
                public = PublicRecipeAdapter(public_recipe_candidates).fetch(dish_label, context)
        if public is not None and public.ok:
            recipe_sources = public.payload.sources
            sources.extend(public.sources)
            steps.append(Step(name="public_recipes", status="done", detail=public.detail))
        elif public is not None and public.status.failed:
            steps.append(Step(name="public_recipes", status="failed", detail="Public recipe search failed."))
            limitations.append("Public recipe search failed, so no typical ingredients could be suggested.")
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
        stages=timer.stages(steps),
        started=timer.started,
    )
    result = analyze_for_user(db, user, sources, ctx, record=link)
    queue_flags_later(flags, result.assessment_id)
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
        needs_confirmation=needs_confirmation,
    )
