"""Customer ingredient suggestions and the staff verification workflow.

A suggestion is stored as unverified and never changes trusted records by itself. Only an
authorized staff decision can: confirming creates a new recipe version with the change applied;
rejecting changes nothing; staff can also ask the customer a question first. Corrections that would
make a record less cautious (removing an allergen) are never applied automatically. Every step is
written to the audit log, and the original message is never edited.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.knowledge import KnowledgeBase
from app.models import (
    PENDING_STATUSES,
    Allergen,
    Assessment,
    AuditRecord,
    FeedbackSuggestion,
    MenuItem,
    MenuItemStatus,
    RecipeVersion,
    SuggestionStatus,
    SuggestionType,
    User,
    VerificationStatus,
)
from app.models.common import utcnow
from app.restaurant.recipes import audit, create_version, current_version, snapshot, version_changes
from app.schemas.restaurant import (
    CrossContactIn,
    FeedbackEvent,
    FeedbackImpact,
    FeedbackIn,
    FeedbackOut,
    IngredientIn,
)

NEEDS_INGREDIENT = {SuggestionType.missing_ingredient, SuggestionType.incorrect_ingredient}
NEEDS_ALLERGEN = {SuggestionType.missing_allergen, SuggestionType.incorrect_allergen, SuggestionType.cross_contact}
NEEDS_MESSAGE = {SuggestionType.other, SuggestionType.outdated_recipe}


def _422(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, msg)


def submit(db: Session, customer: User, data: FeedbackIn) -> FeedbackSuggestion:
    item = db.get(MenuItem, data.menu_item_id)
    if item is None or item.status == MenuItemStatus.archived or not item.branch.restaurant.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Menu item not found.")
    ingredient = " ".join(data.ingredient_name.split())
    if data.suggestion_type in NEEDS_INGREDIENT and not ingredient:
        raise _422("Enter the ingredient this suggestion is about.")
    allergen = None
    if data.allergen_code:
        allergen = db.scalar(select(Allergen).where(Allergen.code == data.allergen_code))
        if allergen is None:
            raise _422(f"Unknown allergen code: {data.allergen_code}")
    if data.suggestion_type in NEEDS_ALLERGEN and allergen is None:
        raise _422("Choose the allergen this suggestion is about.")
    if data.suggestion_type in NEEDS_MESSAGE and not data.message.strip():
        raise _422("Describe the issue in the message.")

    duplicate = db.scalar(
        select(FeedbackSuggestion.id).where(
            FeedbackSuggestion.customer_id == customer.id,
            FeedbackSuggestion.menu_item_id == item.id,
            FeedbackSuggestion.suggestion_type == data.suggestion_type,
            FeedbackSuggestion.ingredient_name == ingredient,
            FeedbackSuggestion.allergen_id == (allergen.id if allergen else None),
            FeedbackSuggestion.status.in_(PENDING_STATUSES),
        )
    )
    if duplicate:
        raise HTTPException(status.HTTP_409_CONFLICT, "You already submitted this suggestion; it is awaiting review.")

    cur = current_version(item)
    suggestion = FeedbackSuggestion(
        restaurant_id=item.restaurant_id,
        branch_id=item.branch_id,
        menu_item_id=item.id,
        recipe_version_id=cur.id if cur else None,
        customer_id=customer.id,
        suggestion_type=data.suggestion_type,
        ingredient_name=ingredient,
        allergen_id=allergen.id if allergen else None,
        message=data.message.strip(),
    )
    db.add(suggestion)
    db.flush()
    audit(
        db, item.restaurant_id, customer.id, "feedback.submitted", "feedback_suggestion", suggestion.id,
        {"menu_item_id": item.id, "type": data.suggestion_type.value},
    )
    return suggestion


def _apply(kb: KnowledgeBase, s: FeedbackSuggestion, recipe, allergen_code: str | None) -> str | None:
    """Apply the suggestion to a recipe snapshot in place. Returns a description, or None if no change."""
    names = {i.name.lower(): i for i in recipe.ingredients}
    if s.suggestion_type == SuggestionType.missing_ingredient:
        if s.ingredient_name.lower() in names:
            return None
        codes = sorted({m.allergen_code for m in kb.match(s.ingredient_name.lower())} | ({allergen_code} if allergen_code else set()))
        recipe.ingredients.append(IngredientIn(name=s.ingredient_name, allergen_codes=codes, notes="Added from confirmed customer feedback"))
        return f"Added ingredient '{s.ingredient_name}'" + (f" ({', '.join(codes)})" if codes else "")
    if s.suggestion_type == SuggestionType.incorrect_ingredient:
        if s.ingredient_name.lower() not in names:
            raise _422(
                f"'{s.ingredient_name}' is not in the current recipe. Edit the recipe directly, or reject the suggestion."
            )
        recipe.ingredients = [i for i in recipe.ingredients if i.name.lower() != s.ingredient_name.lower()]
        return f"Removed ingredient '{s.ingredient_name}'"
    if s.suggestion_type == SuggestionType.missing_allergen:
        declared = set(recipe.dish_allergen_codes) | {c for i in recipe.ingredients for c in i.allergen_codes}
        if allergen_code in declared:
            return None
        recipe.dish_allergen_codes.append(allergen_code)
        return f"Declared allergen {allergen_code}"
    if s.suggestion_type == SuggestionType.cross_contact:
        if any(c.allergen_code == allergen_code for c in recipe.cross_contacts):
            return None
        recipe.cross_contacts.append(CrossContactIn(allergen_code=allergen_code, description=s.message[:1000]))
        return f"Added possible cross-contact with {allergen_code}"
    # "incorrect_allergen": removing an allergen makes the record less cautious, so it is never applied
    # automatically; staff edit the recipe themselves. "other": reviewed and logged only.
    return None


def decide(
    db: Session, kb: KnowledgeBase, restaurant_id: int, actor: User, s: FeedbackSuggestion,
    decision: SuggestionStatus, note: str,
) -> FeedbackSuggestion:
    if s.status not in PENDING_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This suggestion was already {s.status.value}.")
    if decision == SuggestionStatus.clarification_requested:
        if s.status == SuggestionStatus.clarification_requested:
            raise HTTPException(status.HTTP_409_CONFLICT, "A question is already waiting for the customer's answer.")
        if not note.strip():
            raise _422("Enter the question for the customer.")
        s.status = decision
        s.clarification_request = note.strip()
        s.clarification_response = ""
        audit(db, restaurant_id, actor.id, "feedback.clarification_requested", "feedback_suggestion", s.id,
              {"menu_item_id": s.menu_item_id, "note": note})
        db.flush()
        return s

    change = None
    if decision == SuggestionStatus.confirmed:
        item = db.get(MenuItem, s.menu_item_id)
        cur = current_version(item)
        recipe = snapshot(cur)
        if s.suggestion_type == SuggestionType.outdated_recipe:
            # Staff agree the records no longer match the dish: publish the same recipe as an unverified
            # version, so it can no longer rule allergens out until staff update and verify it.
            if cur is not None and cur.verification_status == VerificationStatus.verified:
                change = "Recipe marked as needing re-verification"
                recipe.mark_verified = False
        else:
            change = _apply(kb, s, recipe, s.allergen.code if s.allergen else None)
            recipe.mark_verified = True  # an authorized staff member confirmed this change
        if change:
            recipe.change_note = f"Customer suggestion #{s.id} confirmed: {change}" + (f". {note}" if note else "")
            version = create_version(db, item, recipe, actor)
            s.resulting_recipe_version_id = version.id
    s.status = decision
    s.decided_by_id = actor.id
    s.decided_at = utcnow()
    s.decision_note = note
    audit(
        db, restaurant_id, actor.id, f"feedback.{decision.value}", "feedback_suggestion", s.id,
        {"menu_item_id": s.menu_item_id, "change": change, "note": note,
         "resulting_recipe_version_id": s.resulting_recipe_version_id},
    )
    db.flush()
    return s


def clarify(db: Session, customer: User, s: FeedbackSuggestion, message: str) -> FeedbackSuggestion:
    """The customer answers the staff question; the suggestion goes back to the review list."""
    if s.status != SuggestionStatus.clarification_requested:
        raise HTTPException(status.HTTP_409_CONFLICT, "No question is waiting for your answer.")
    if not message.strip():
        raise _422("Enter your answer.")
    s.clarification_response = message.strip()
    s.clarified_at = utcnow()
    s.status = SuggestionStatus.unverified
    audit(db, s.restaurant_id, customer.id, "feedback.clarified", "feedback_suggestion", s.id,
          {"menu_item_id": s.menu_item_id})
    db.flush()
    return s


def impact(db: Session, s: FeedbackSuggestion) -> FeedbackImpact | None:
    """What a confirmed correction changed, and how many later assessments used the corrected recipe."""
    if s.resulting_recipe_version_id is None:
        return None
    v = db.get(RecipeVersion, s.resulting_recipe_version_id)
    if v is None:
        return None
    rows = db.execute(select(Assessment.outcomes).where(Assessment.recipe_version_id == v.id)).scalars().all()
    code = s.allergen.code if s.allergen else None
    flagged = sum(1 for o in rows if code and o.get(code) in ("PRESENT", "POSSIBLE_CROSS_CONTACT"))
    return FeedbackImpact(
        resulting_version_number=v.version_number,
        version_is_current=current_version(v.menu_item) is v,
        changes=version_changes(v),
        assessments_since=len(rows),
        assessments_flagging_allergen=flagged if code else None,
    )


def history(db: Session, s: FeedbackSuggestion) -> list[FeedbackEvent]:
    rows = db.scalars(
        select(AuditRecord).where(AuditRecord.entity_type == "feedback_suggestion", AuditRecord.entity_id == s.id)
        .order_by(AuditRecord.created_at, AuditRecord.id)
    ).all()
    return [FeedbackEvent(action=r.action.removeprefix("feedback."), at=r.created_at, note=(r.details or {}).get("note") or "")
            for r in rows]


def feedback_out(db: Session, s: FeedbackSuggestion) -> FeedbackOut:
    item = db.get(MenuItem, s.menu_item_id)
    v = db.get(RecipeVersion, s.recipe_version_id) if s.recipe_version_id else None
    rv = db.get(RecipeVersion, s.resulting_recipe_version_id) if s.resulting_recipe_version_id else None
    return FeedbackOut(
        id=s.id,
        restaurant_id=s.restaurant_id,
        branch_id=s.branch_id,
        menu_item_id=s.menu_item_id,
        menu_item_name=item.name if item else "",
        recipe_version_number=v.version_number if v else None,
        suggestion_type=s.suggestion_type,
        ingredient_name=s.ingredient_name,
        allergen_code=s.allergen.code if s.allergen else None,
        message=s.message,
        status=s.status,
        created_at=s.created_at,
        decided_at=s.decided_at,
        decision_note=s.decision_note,
        resulting_version_number=rv.version_number if rv else None,
        clarification_request=s.clarification_request,
        clarification_response=s.clarification_response,
        clarified_at=s.clarified_at,
        impact=impact(db, s),
        history=history(db, s),
    )
