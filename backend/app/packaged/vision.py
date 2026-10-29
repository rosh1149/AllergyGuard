"""Product identification from photos with Claude vision (optional; needs ANTHROPIC_API_KEY)."""

from __future__ import annotations

import base64
import logging

from pydantic import BaseModel, Field

from app.core.llm import FALLBACK_BETA, get_client, model_name
from app.packaged.images import to_jpeg_bytes

log = logging.getLogger(__name__)


class ProductGuess(BaseModel):
    is_packaged_food: bool = Field(description="True if the photos show a packaged food or drink product.")
    brand: str = Field(description="Brand exactly as printed, or empty if not visible.")
    product_name: str = Field(description="Product name as printed, or empty if not visible.")
    variant: str = Field(description="Flavour/variant as printed, or empty.")
    size: str = Field(description="Net quantity as printed, e.g. '200 g', or empty.")
    barcode_digits: str = Field(description="Barcode digits if clearly legible, else empty.")
    confidence: float = Field(description="0 to 1: how sure you are the brand and product name are right.")


_PROMPT = """Identify the packaged food product in these photos for a product-database lookup.

Report only what is printed and visible: brand, product name, variant and size. Leave a field empty rather than guess. Do not list ingredients or allergens. Set confidence low if the packaging is partly hidden, blurry, or could match several products."""


def identify_product(images) -> ProductGuess | None:
    client = get_client(timeout=45.0)
    if client is None:
        return None
    import anthropic

    content: list[dict] = []
    for img in images[:3]:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(to_jpeg_bytes(img)).decode("ascii"),
                },
            }
        )
    content.append({"type": "text", "text": _PROMPT})
    try:
        response = client.beta.messages.parse(
            model=model_name(),
            max_tokens=4000,
            output_config={"effort": "low"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
            output_format=ProductGuess,
        )
    except anthropic.APIConnectionError as e:
        log.warning("Vision identification: connection error: %s", e)
        return None
    except anthropic.RateLimitError:
        log.warning("Vision identification: rate limited")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Vision identification: API error %s: %s", e.status_code, e.message)
        return None
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        log.warning("Vision identification: stop_reason=%s", response.stop_reason)
        return None
    guess = response.parsed_output
    guess.confidence = min(max(guess.confidence, 0.0), 1.0)
    return guess
