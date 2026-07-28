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
RepositoryId: TypeAlias = Identifier
MediaId: TypeAlias = Identifier
VideoId: TypeAlias = Identifier
IndexGenerationId: TypeAlias = Identifier
IndexSnapshotId: TypeAlias = Identifier
JobId: TypeAlias = Identifier
ArtifactId: TypeAlias = Identifier


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
