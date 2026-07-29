from __future__ import annotations

from pydantic import Field

from vidxp.application_models import SearchHit, SearchQuery, SearchResult
from vidxp.capabilities.contracts import CapabilityInput


class SearchInput(CapabilityInput):
    query: SearchQuery
    top_k: int = Field(default=10, gt=0, le=100)


__all__ = ["SearchHit", "SearchInput", "SearchResult"]
