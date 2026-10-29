"""Shared allergen analysis engine (used by both the packaged-food and restaurant-food pipelines).

Pipelines turn their inputs into `EvidenceSource` objects; `assess()` applies deterministic rules
and returns an `AnalysisResult`. An LLM may only rephrase the finished result (see `explain.py`).
"""

from app.engine.assess import assess
from app.engine.knowledge import KnowledgeBase
from app.engine.types import (
    AllergyProfile,
    AnalysisContext,
    AnalysisResult,
    CrossContactDecl,
    EvidenceSource,
    Outcome,
    SourceType,
)

__all__ = [
    "AllergyProfile",
    "AnalysisContext",
    "AnalysisResult",
    "CrossContactDecl",
    "EvidenceSource",
    "KnowledgeBase",
    "Outcome",
    "SourceType",
    "assess",
]
