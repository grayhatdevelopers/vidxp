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
def _require_uuid4_hex(value: str) -> str:
    identifier = UUID(hex=value)
    if identifier.version != 4 or identifier.hex != value:
        raise ValueError("identifier must be lowercase UUID4 hex")
    return value


Uuid4Hex: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{32}$"),
    AfterValidator(_require_uuid4_hex),
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

RepositoryId: TypeAlias = Identifier
MediaId: TypeAlias = Uuid4Hex
VideoId: TypeAlias = MediaId
IndexGenerationId: TypeAlias = Uuid4Hex
IndexSnapshotId: TypeAlias = Uuid4Hex
JobId: TypeAlias = Identifier
ArtifactId: TypeAlias = Uuid4Hex
