from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeFloat,
    NonNegativeInt,
    model_validator,
)

from vidxp.core.contracts import (
    INDEX_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)
from vidxp.core.identifiers import Identifier, IndexGenerationId, Sha256


class _GenerationManifestModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class _GenerationInput(_GenerationManifestModel):
    sha256: Sha256
    checksums: dict[Identifier, Sha256]
    size: NonNegativeInt | None
    source_name: str | None
    path: str | None
    metadata: dict[str, JsonValue]


class _GenerationStage(_GenerationManifestModel):
    video_id: Identifier
    stage: Identifier
    seconds: NonNegativeFloat
    stats: dict[str, JsonValue]
    recorded_at: AwareDatetime


class _CompletedGenerationVideo(_GenerationManifestModel):
    state: Literal["complete"]
    started_at: AwareDatetime
    stages: dict[Identifier, _GenerationStage]
    completed_at: AwareDatetime
    summary: dict[str, JsonValue]


class CompletedGenerationManifest(_GenerationManifestModel):
    manifest_schema_version: Literal[MANIFEST_SCHEMA_VERSION]
    index_schema_version: Literal[INDEX_SCHEMA_VERSION]
    dataset: Identifier
    split: Identifier
    run_id: Identifier
    generation_id: IndexGenerationId
    state: Literal["complete"]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime
    config_fingerprint: Sha256
    execution_fingerprint: Sha256
    configuration: dict[str, JsonValue]
    models: dict[str, JsonValue]
    git: dict[str, JsonValue]
    environment: dict[str, JsonValue]
    inputs: dict[Identifier, _GenerationInput] = Field(min_length=1, max_length=1)
    videos: dict[Identifier, _CompletedGenerationVideo] = Field(
        min_length=1,
        max_length=1,
    )
    completed_videos: tuple[Identifier, ...] = Field(
        min_length=1,
        max_length=1,
    )
    failed_videos: tuple[Identifier, ...] = Field(max_length=0)
    interrupted_videos: tuple[Identifier, ...] = Field(max_length=0)
    processed_frames: NonNegativeInt
    record_counts: dict[Identifier, NonNegativeInt]
    store_size_bytes_at_commit: NonNegativeInt | None

    @model_validator(mode="after")
    def _validate_completed_generation(self) -> CompletedGenerationManifest:
        input_ids = set(self.inputs)
        video_ids = set(self.videos)
        completed_ids = set(self.completed_videos)
        if input_ids != video_ids or input_ids != completed_ids:
            raise ValueError(
                "inputs, videos, and completed_videos must identify the same "
                "single media item"
            )

        configured = self.configuration.get("enabled_modalities")
        if (
            not isinstance(configured, list)
            or not configured
            or any(not isinstance(value, str) or not value for value in configured)
            or len(configured) != len(set(configured))
        ):
            raise ValueError(
                "configuration.enabled_modalities must be a non-empty list "
                "of unique identifiers"
            )
        if set(self.record_counts) != set(configured):
            raise ValueError(
                "record_counts keys must exactly match enabled_modalities"
            )
        return self
