from __future__ import annotations

from typing import Any

from pydantic import Field, JsonValue, field_validator, model_validator

from vidxp.capabilities.contracts import CapabilityInput, CapabilityOutput
from vidxp.core.contracts import INDEX_SCHEMA_VERSION
from vidxp.core.identifiers import IndexGenerationId, MediaId, VideoId


class SearchInput(CapabilityInput):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, gt=0, le=100)


class SearchHit(CapabilityOutput):
    rank: int = Field(gt=0)
    media_id: MediaId
    video_id: VideoId
    generation_id: IndexGenerationId
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    score: float
    raw_distance: float
    modality: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _reject_internal_metadata(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        forbidden: set[str] = set()

        def inspect(item: JsonValue) -> None:
            if isinstance(item, dict):
                for key, nested in item.items():
                    if (
                        key == "path"
                        or key == "storage_key"
                        or key.endswith("_path")
                        or key.endswith("_directory")
                    ):
                        forbidden.add(key)
                    inspect(nested)
            elif isinstance(item, list):
                for nested in item:
                    inspect(nested)

        inspect(value)
        if forbidden:
            raise ValueError(
                "Search metadata contains internal location fields."
            )
        return value

    @model_validator(mode="after")
    def _validate_interval(self) -> "SearchHit":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self

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
