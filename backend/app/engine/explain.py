"""Turn a finished AnalysisResult into a readable explanation.

The template is always available. If an Anthropic API key is configured, Claude rewrites the
explanation from the structured result only; its output is checked and discarded (falling back to
the template) if it drops one of the user's allergens or makes a safety claim.
"""

from __future__ import annotations

import json
import logging
import re

from app.core.llm import FALLBACK_BETA, get_client, model_name
from app.engine.types import AnalysisResult, Outcome

log = logging.getLogger(__name__)

_HEADLINES = {
    Outcome.PRESENT: "One or more of your allergens appears to be present.",
    Outcome.POSSIBLE_CROSS_CONTACT: "There is a possible cross-contact risk for one or more of your allergens.",
    Outcome.UNKNOWN: "There isn't enough reliable information to assess all of your allergens.",
    Outcome.NOT_LISTED_IN_VERIFIED_RECORDS: (
        "None of your allergens were found in the verified information available. "
        "This is not a guarantee that they are absent."
    ),
}


def template_explanation(result: AnalysisResult) -> str:
    lines = [_HEADLINES[result.overall_outcome]]
    if result.identity_label:
        lines.append(f"Checked: {result.identity_label}.")
    lines.append("")
    lines.append("Your allergens:")
    for a in result.your_allergens:
        lines.append(f"- {a.name}: {a.outcome_label}. {a.reasons[0] if a.reasons else ''}".rstrip())
    if result.other_allergens:
        lines.append("")
        lines.append(
            "Other allergens found: "
            + ", ".join(f"{a.name} ({a.outcome_label.lower()})" for a in result.other_allergens)
            + "."
        )
    if result.conflicts:
        lines.append("")
        lines.append("Conflicting information:")
        lines.extend(f"- {c.description}" for c in result.conflicts)
    if result.limitations:
        lines.append("")
        lines.append("Limitations:")
        lines.extend(f"- {lim}" for lim in result.limitations)
    return "\n".join(lines)


_SYSTEM_PROMPT = """You explain food-allergen analysis results to a person with food allergies.

You receive a JSON analysis produced by a rule-based engine. Write a short, plain-language explanation (under 180 words) of what was found for the person's own allergens, the evidence behind each finding, and what remains uncertain.

Hard rules:
- Use only facts in the JSON. Do not add ingredients, allergens, sources or general knowledge about the food.
- Keep every outcome exactly as given. Mention each of the person's allergens by its name, with its outcome.
- "Not listed in verified records" means the allergen was not found in the records available. It is not proof of absence; say so.
- Never say or imply the food is safe, allergen-free, or fine to eat, and do not give medical advice.
- If there are conflicts or limitations, mention them.
- End by suggesting they confirm with the manufacturer or restaurant staff.
- Plain text only, no markdown headings."""

_FORBIDDEN = re.compile(
    r"\b(is|are|it's|looks|seems|should be|perfectly|completely|totally)\s+(safe|fine|ok|okay)\b"
    r"|\bsafe\s+(to|for)\s+(eat|consume|you)\b|\ballergen[\s-]free\b|\bno\s+risk\b|(?<!not a )(?<!no )\bguarantee",
    re.I,
)


def _llm_payload(result: AnalysisResult) -> str:
    def brief(a):
        return {"allergen": a.name, "outcome": a.outcome_label, "reasons": a.reasons}

    return json.dumps(
        {
            "checked_item": result.identity_label or None,
            "overall_outcome": result.overall_label,
            "your_allergens": [brief(a) for a in result.your_allergens],
            "other_allergens_found": [brief(a) for a in result.other_allergens],
            "conflicts": [c.description for c in result.conflicts],
            "limitations": result.limitations,
            "sources": [{"name": s.name, "type": s.type_label} for s in result.sources],
        },
        indent=1,
    )


def _acceptable(text: str, result: AnalysisResult) -> bool:
    if not text or _FORBIDDEN.search(text):
        return False
    lowered = text.lower()
    return all(a.name.lower() in lowered for a in result.your_allergens)


def llm_explanation(result: AnalysisResult) -> str | None:
    client = get_client()
    if client is None:
        return None
    import anthropic

    try:
        response = client.beta.messages.create(
            model=model_name(),
            max_tokens=4000,
            system=_SYSTEM_PROMPT,
            output_config={"effort": "low"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=[{"role": "user", "content": _llm_payload(result)}],
        )
    except anthropic.APIConnectionError as e:
        log.warning("LLM explanation: connection error: %s", e)
        return None
    except anthropic.RateLimitError:
        log.warning("LLM explanation: rate limited")
        return None
    except anthropic.APIStatusError as e:
        log.warning("LLM explanation: API error %s: %s", e.status_code, e.message)
        return None

    if response.stop_reason != "end_turn":
        log.warning("LLM explanation: stop_reason=%s", response.stop_reason)
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not _acceptable(text, result):
        log.warning("LLM explanation rejected by output checks; using template")
        return None
    return text


def add_explanation(result: AnalysisResult, use_llm: bool = True) -> AnalysisResult:
    text = llm_explanation(result) if use_llm else None
    if text:
        result.explanation, result.explanation_source = text, "llm"
    else:
        result.explanation, result.explanation_source = template_explanation(result), "template"
    return result
