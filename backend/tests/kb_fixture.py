"""Build the full seeded knowledge base in memory (no database), for engine tests."""

from app.evaluation.seed_kb import build_kb

__all__ = ["build_kb"]
