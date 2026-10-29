"""Dish recognition from a photo with Claude vision (optional; needs ANTHROPIC_API_KEY)."""

from __future__ import annotations

import base64
import logging

from pydantic import BaseModel, Field

from app.core.llm import FALLBACK_BETA, get_client, model_name
from app.packaged.images import to_jpeg_bytes

log = logging.getLogger(__name__)


class DishGuess(BaseModel):
    is_food: bool = Field(description="True if the photo shows a prepared dish or meal.")
    dish_description: str = Field(description="Short name of the dish as commonly known, e.g. 'butter chicken'.")
    menu_choice: str = Field(description="The menu item name, copied exactly, that best matches the photo, or empty if none match.")
    confidence: float = Field(description="0 to 1: how sure you are about menu_choice (or the dish name if no menu).")


def identify_dish(images, menu_names: list[str]) -> DishGuess | None:
    client = get_client(timeout=45.0)
    if client is None or not images:
        return None
    import anthropic

    menu = "\n".join(f"- {n}" for n in menu_names) if menu_names else "(no menu available)"
    prompt = (
        "Identify the dish in the photo.\n\nRestaurant menu:\n" + menu + "\n\n"
        "If one menu item clearly matches, copy its name exactly into menu_choice; otherwise leave it empty. "
        "Do not list ingredients. Many dishes look alike, so keep confidence moderate unless the match is unmistakable."
    )
    content: list[dict] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(to_jpeg_bytes(img)).decode("ascii")},
        }
        for img in images[:3]
    ]
    content.append({"type": "text", "text": prompt})
    try:
        response = client.beta.messages.parse(
            model=model_name(),
            max_tokens=4000,
            output_config={"effort": "low"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
            output_format=DishGuess,
        )
    except anthropic.APIConnectionError as e:
        log.warning("Dish recognition: connection error: %s", e)
        return None
    except anthropic.RateLimitError:
        log.warning("Dish recognition: rate limited")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Dish recognition: API error %s: %s", e.status_code, e.message)
        return None
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        log.warning("Dish recognition: stop_reason=%s", response.stop_reason)
        return None
    guess = response.parsed_output
    guess.confidence = min(max(guess.confidence, 0.0), 1.0)
    return guess
