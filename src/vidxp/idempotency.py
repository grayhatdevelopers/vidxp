from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal, TypeAlias
from uuid import UUID

from pydantic import StringConstraints

from vidxp.application_models import Principal


IdempotencyKey: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=8,
        max_length=200,
        pattern=r"^[\x21-\x7e]+$",
    ),
]
RequestTransport: TypeAlias = Literal["http", "mcp"]
_SINGLE_REPOSITORY_SCOPE = "default"


def scoped_request_key(
    *,
    principal: Principal,
    transport: RequestTransport,
    operation: str,
    idempotency_key: str,
) -> str:
    """Derive a non-reversible request identity within one adapter namespace."""

    material = "\0".join(
        (
            f"vidxp-{transport}-request-v1",
            _SINGLE_REPOSITORY_SCOPE,
            principal.subject,
            operation,
            idempotency_key,
        )
    ).encode()
    return sha256(material).hexdigest()


def scoped_job_id(
    *,
    principal: Principal,
    transport: RequestTransport,
    operation: str,
    idempotency_key: str,
) -> str:
    """Project a scoped request identity into a valid deterministic job UUID."""

    digest = scoped_request_key(
        principal=principal,
        transport=transport,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    value = bytearray(bytes.fromhex(digest)[:16])
    value[6] = (value[6] & 0x0F) | 0x40
    value[8] = (value[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(value)).hex
