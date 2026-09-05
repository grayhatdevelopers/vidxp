from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from vidxp.core.identifiers import (
    ActorClusterId,
    IndexGenerationId,
    MediaId,
    MimeType,
    PersonId,
    PersonReferenceId,
    Sha256,
)
from vidxp.core.storage_keys import validate_storage_key


PEOPLE_SCHEMA_VERSION = 1


class _PersonModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        strict=True,
    )


class PersonRecord(_PersonModel):
    """A repository-specific, user-reviewed person identity.

    A PersonRecord is independent of any single index generation or
    actor cluster: it represents a durable, user-approved identity
    that survives re-indexing (see PersonClusterLink for the evidence
    that ties it to specific anonymous face clusters).
    """

    schema_version: Literal[PEOPLE_SCHEMA_VERSION] = PEOPLE_SCHEMA_VERSION
    person_id: PersonId
    display_name: str = Field(min_length=1, max_length=255)
    notes: str | None = Field(default=None, max_length=10_000)
    biography: str | None = Field(default=None, max_length=10_000)
    created_at: AwareDatetime


class PersonAlias(_PersonModel):
    """An alternate name a user has attached to a reviewed person."""

    person_id: PersonId
    alias: str = Field(min_length=1, max_length=255)


class StagedPersonReference(_PersonModel):
    """A reference image written to a temporary path, not yet published."""

    reference_id: PersonReferenceId
    person_id: PersonId
    path: Path


class StoredPersonReference(_PersonModel):
    """A reference image that has been published into managed storage."""

    sha256: Sha256
    byte_size: int = Field(gt=0)
    storage_key: str = Field(min_length=1)
    local_path: Path

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str) -> str:
        return validate_storage_key(value)


class PersonReference(_PersonModel):
    """A user-supplied reference image attached to a reviewed person.

    Deliberately independent of ArtifactRecord: artifacts are
    job-generated, generation-scoped, and may expire, while a
    reference image is durable, user-supplied, and outlives any
    single index generation.
    """

    reference_id: PersonReferenceId
    person_id: PersonId
    storage_key: str = Field(min_length=1)
    sha256: Sha256
    byte_size: int = Field(gt=0)
    mime_type: MimeType
    created_at: AwareDatetime

    @field_validator("storage_key")
    @classmethod
    def _validate_storage_key(cls, value: str) -> str:
        return validate_storage_key(value)


class PersonClusterLink(_PersonModel):
    """Evidence linking a reviewed person to an anonymous actor cluster.

    The cluster_id is scoped to one media item and one index
    generation: it is evidence, not identity. A person may accumulate
    multiple links across different videos and across re-indexing
    generations. Removing a link (to correct an accidental merge or
    split) never touches the underlying video, media, or index data.
    """

    person_id: PersonId
    cluster_id: ActorClusterId
    media_id: MediaId
    generation_id: IndexGenerationId
    created_at: AwareDatetime
