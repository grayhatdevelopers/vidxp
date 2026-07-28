from __future__ import annotations

from pydantic import Field, model_validator

from vidxp.capabilities.contracts import CapabilityInput, CapabilityOutput
from vidxp.core.identifiers import IndexGenerationId, MediaId


class ActorClustersInput(CapabilityInput):
    page_size: int = Field(default=50, gt=0, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


class ActorClusterSummary(CapabilityOutput):
    cluster_id: str = Field(min_length=1)
    media_id: MediaId
    generation_id: IndexGenerationId
    detection_count: int = Field(ge=0)
    first_timestamp: float = Field(ge=0)
    last_timestamp: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate_interval(self) -> "ActorClusterSummary":
        if self.last_timestamp < self.first_timestamp:
            raise ValueError("last_timestamp must not precede first_timestamp")
        return self

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class ActorClustersOutput(CapabilityOutput):
    clusters: tuple[ActorClusterSummary, ...] = ()
    total: int = Field(ge=0)
    next_cursor: str | None = None


class ActorDetectionsInput(CapabilityInput):
    cluster_id: str = Field(min_length=1)
    page_size: int = Field(default=50, gt=0, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


class ActorDetection(CapabilityOutput):
    detection_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    timestamp: float = Field(ge=0)
    bbox: tuple[int, int, int, int]
    dataset: str
    split: str
    run_id: str
    media_id: MediaId
    generation_id: IndexGenerationId
    modality: str
    source_id: str


class ActorDetectionsOutput(CapabilityOutput):
    cluster_id: str = Field(min_length=1)
    detections: tuple[ActorDetection, ...] = ()
    total: int | None = Field(default=None, ge=0)
    next_cursor: str | None = None
