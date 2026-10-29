import re
from difflib import SequenceMatcher


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def name_similarity(query: str, candidate: str) -> float:
    """0-1 similarity between two names (token overlap or character similarity, whichever is higher)."""
    a, b = query.lower().strip(), candidate.lower().strip()
    if not a or not b:
        return 0.0
    ta, tb = _tokens(a), _tokens(b)
    token_score = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return max(token_score, SequenceMatcher(None, a, b).ratio())
