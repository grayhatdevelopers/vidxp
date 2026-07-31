from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from vidxp.core.identifiers import (
    Identifier,
    IndexGenerationId,
    IndexSnapshotId,
    Sha256,
)


INDEX_SNAPSHOT_SCHEMA_VERSION = 1
ACTIVE_SNAPSHOT_POINTER_SCHEMA_VERSION = 1

class _SnapshotModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class GenerationReference(_SnapshotModel):
    generation_id: IndexGenerationId
    media_id: Identifier
    manifest_sha256: Sha256
    input_sha256: Sha256
    config_fingerprint: Sha256
    modalities: tuple[Identifier, ...] = Field(min_length=1)
    record_counts: dict[Identifier, int]
    store_size_bytes_at_commit: int | None = Field(ge=0)

    @model_validator(mode="after")
    def _validate_record_counts(self) -> GenerationReference:
        if set(self.record_counts) != set(self.modalities):
            raise ValueError(
                "record_counts keys must exactly match modalities"
            )
        if any(count < 0 for count in self.record_counts.values()):
            raise ValueError("record_counts values must be nonnegative")
        return self


class IndexSnapshot(_SnapshotModel):
    schema_version: Literal[INDEX_SNAPSHOT_SCHEMA_VERSION] = (
        INDEX_SNAPSHOT_SCHEMA_VERSION
    )
    snapshot_id: IndexSnapshotId
    created_at: AwareDatetime
    config_fingerprint: Sha256
    configuration: dict[str, JsonValue]
    generations: dict[Identifier, GenerationReference]

    @model_validator(mode="after")
    def _validate_generation_keys(self) -> IndexSnapshot:
        mismatched = sorted(
            media_id
            for media_id, reference in self.generations.items()
            if media_id != reference.media_id
        )
        if mismatched:
            raise ValueError(
                "generation mapping keys must match reference media_id values"
            )
        return self


class ActiveSnapshotPointer(_SnapshotModel):
    schema_version: Literal[ACTIVE_SNAPSHOT_POINTER_SCHEMA_VERSION] = (
        ACTIVE_SNAPSHOT_POINTER_SCHEMA_VERSION
    )
    snapshot_id: IndexSnapshotId
    snapshot_sha256: Sha256
    updated_at: AwareDatetime
