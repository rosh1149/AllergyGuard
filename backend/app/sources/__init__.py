"""Source adapters: every external or internal evidence source is fetched through an adapter that turns
its data into the engine's common `EvidenceSource` format.

Adapters only fetch and convert. They do not normalize ingredients or decide outcomes (that is the
engine's job), so a new source can be added by writing one adapter, without touching the engine.
"""

from app.sources.base import ADAPTERS, AdapterResult, AdapterStatus, SourceAdapter, adapter_health

__all__ = ["ADAPTERS", "AdapterResult", "AdapterStatus", "SourceAdapter", "adapter_health"]
