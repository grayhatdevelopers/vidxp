from __future__ import annotations

from pydantic import Field

from vidxp.application_models import SearchHit, SearchQuery, SearchResult
from vidxp.capabilities.contracts import CapabilityInput
from vidxp.core.identifiers import MediaId


class SearchInput(CapabilityInput):
    query: SearchQuery
    top_k: int = Field(default=10, gt=0, le=100)
    media_id: MediaId | None = None


__all__ = ["SearchHit", "SearchInput", "SearchResult"]
