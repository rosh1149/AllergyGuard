"""Restaurant adapters: the restaurant's own recipe records, unreviewed customer reports and public recipes."""

from __future__ import annotations

from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.types import CrossContactDecl, EvidenceSource, SourceType
from app.models import FeedbackSuggestion, MenuItem, RecipeVersion, SuggestionType
from app.models.feedback import PENDING_STATUSES
from app.restaurant.fallback import RecipeCandidates
from app.restaurant.recipes import to_evidence
from app.sources.base import AdapterResult, AdapterStatus, SourceAdapter, register


@register
class RestaurantRecordAdapter(SourceAdapter):
    name = "restaurant_records"
    source_type = SourceType.restaurant_verified
    description = "The registered restaurant's recipe version for the dish (verified or not yet verified)."

    def fetch(self, item: MenuItem, version: RecipeVersion, restaurant_name: str) -> AdapterResult:
        def go():
            return (AdapterStatus.ok, [to_evidence(item, version, restaurant_name)], version,
                    f"Recipe v{version.version_number} ({version.verification_status.value}).")
        return self.run(go)


@register
class CustomerReportsAdapter(SourceAdapter):
    name = "customer_reports"
    source_type = SourceType.customer_suggestion
    description = "Unreviewed customer reports about the current recipe version (candidates only)."

    def fetch(self, db: Session, item: MenuItem, version: RecipeVersion) -> AdapterResult:
        """Payload: the number of pending reports made against an earlier recipe version (not used)."""
        def go():
            pending = db.scalars(
                select(FeedbackSuggestion).where(
                    FeedbackSuggestion.menu_item_id == item.id, FeedbackSuggestion.status.in_(PENDING_STATUSES)
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
                return AdapterStatus.not_found, [], len(older), "No unreviewed reports that affect allergens."
            src = EvidenceSource(
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
            )
            return AdapterStatus.ok, [src], len(older), f"{len(pending)} unreviewed report(s)."
        return self.run(go)


@register
class PublicRecipeAdapter(SourceAdapter):
    name = "public_recipes"
    source_type = SourceType.public_recipe
    description = "Typical ingredients from public web recipes for a dish (candidates only; needs the AI key)."

    def __init__(self, finder: Callable[[str, str], RecipeCandidates | None]):
        self.finder = finder

    def fetch(self, dish: str, context: str) -> AdapterResult:
        def go():
            found = self.finder(dish, context)
            if not found:
                return AdapterStatus.skipped, [], None, "Public recipe search is not configured or found nothing."
            src = EvidenceSource(
                id="public_recipes",
                type=SourceType.public_recipe,
                name=f"Public recipes for '{found.dish or dish}'",
                ingredients=found.ingredients,
                complete=False,
                url=found.sources[0].url if found.sources else None,
                reference=", ".join(r.url for r in found.sources[:3] if r.url),
                verification="public",
            )
            return AdapterStatus.ok, [src], found, f"{len(found.ingredients)} candidate ingredients."
        return self.run(go)
