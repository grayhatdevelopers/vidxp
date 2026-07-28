from __future__ import annotations

from pathlib import Path

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityInput, CapabilityOutput


class ActorClustersInput(CapabilityInput):
    pass


class ActorClusterSummary(CapabilityOutput):
    cluster_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    detection_count: int = Field(ge=0)
    first_timestamp: float = Field(ge=0)
    last_timestamp: float = Field(ge=0)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class ActorClustersOutput(CapabilityOutput):
    clusters: tuple[ActorClusterSummary, ...] = ()


class ActorDetectionsInput(CapabilityInput):
    cluster_id: str = Field(min_length=1)


class ActorDetection(CapabilityOutput):
    detection_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    timestamp: float = Field(ge=0)
    bbox: tuple[int, int, int, int]
    dataset: str
    split: str
    run_id: str
    video_id: str
    generation_id: str | None = None
    modality: str
    source_id: str


class ActorDetectionsOutput(CapabilityOutput):
    cluster_id: str = Field(min_length=1)
    detections: tuple[ActorDetection, ...] = ()


class ActorRenderInput(CapabilityInput):
    cluster_id: str = Field(min_length=1)
    input_path: Path
    output_path: Path


class ActorRenderResult(CapabilityOutput):
    output_path: Path
    detection_count: int = Field(gt=0)
