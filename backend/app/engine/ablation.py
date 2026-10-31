"""Switches that turn off one engine component at a time, for ablation studies (Stage 4).

Never used by the application: the default turns nothing off. Experiments wrap evaluation in
`ablated(...)`; the setting lives in a context variable, so it cannot leak into other requests.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Ablation:
    no_ontology: bool = False  # no relationship reasoning: only direct allergen terms count
    no_normalization: bool = False  # no spelling/OCR/fuzzy correction; concepts only by their own name
    no_compound: bool = False  # brackets are not parsed: each comma-separated piece is one item
    no_source_reliability: bool = False  # every source trusted and able to rule allergens out
    no_conflicts: bool = False  # disagreements between sources are not detected
    no_uncertainty: bool = False  # unresolved, incomplete, stale or possible evidence never blocks absence

    @property
    def name(self) -> str:
        on = [k for k, v in self.__dict__.items() if v]
        return ", ".join(on) or "full"


_current: ContextVar[Ablation] = ContextVar("ablation", default=Ablation())


def current() -> Ablation:
    return _current.get()


@contextmanager
def ablated(**flags):
    token = _current.set(replace(Ablation(), **flags))
    try:
        yield
    finally:
        _current.reset(token)
