from typing import Annotated, TypeAlias
from uuid import UUID

from pydantic import AfterValidator, StringConstraints


Identifier: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ActorClusterId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9._~:%-]+$",
    ),
]


def _require_uuid4_hex(value: str) -> str:
    identifier = UUID(hex=value)
    if identifier.version != 4 or identifier.hex != value:
        raise ValueError("identifier must be lowercase UUID4 hex")
    return value


def _require_workflow_uuid(value: str) -> str:
    identifier = UUID(value)
    if (
        identifier.version not in {4, 7}
        or value not in {identifier.hex, str(identifier)}
    ):
        raise ValueError("identifier must be a lowercase UUID4 or UUID7 string")
    return value


Uuid4Hex: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{32}$"),
    AfterValidator(_require_uuid4_hex),
]
WorkflowUuid: TypeAlias = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^(?:[0-9a-f]{12}[47][0-9a-f]{19}|"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
        )
    ),
    AfterValidator(_require_workflow_uuid),
]
Sha256: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
MimeType: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=127,
        pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$",
    ),
]

MediaId: TypeAlias = Uuid4Hex
VideoId: TypeAlias = MediaId
IndexGenerationId: TypeAlias = Uuid4Hex
IndexSnapshotId: TypeAlias = Uuid4Hex
JobId: TypeAlias = WorkflowUuid
ArtifactId: TypeAlias = Uuid4Hex
UploadIntentId: TypeAlias = Uuid4Hex
