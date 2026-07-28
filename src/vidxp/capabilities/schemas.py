from __future__ import annotations

from typing import Any

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityInput, CapabilityOutput
from vidxp.core.contracts import INDEX_SCHEMA_VERSION


class SearchInput(CapabilityInput):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, gt=0, le=100)


class SearchHit(CapabilityOutput):
    rank: int = Field(gt=0)
    video_id: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    score: float
    raw_distance: float
    modality: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SearchResult(CapabilityOutput):
    schema_version: int = INDEX_SCHEMA_VERSION
    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    modality: str = Field(min_length=1)
    hits: tuple[SearchHit, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_prediction(self) -> dict[str, list[dict[str, Any]]]:
        return {
            self.query_id: [
                hit.model_dump(mode="json") for hit in self.hits
            ]
        }
