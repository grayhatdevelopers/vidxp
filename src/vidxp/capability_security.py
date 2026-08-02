from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

import jwt


CAPABILITY_ISSUER = "vidxp"
CAPABILITY_ALGORITHM = "HS256"
_REQUIRED_CLAIMS = ("iss", "aud", "sub", "iat", "exp")


def repository_binding(repository_root: Path) -> str:
    """Return the stable repository binding used by bearer capabilities."""

    root = str(repository_root.resolve()).replace("\\", "/")
    # Preserve the deployed upload-session binding contract while sharing it
    # with other repository-scoped browser capabilities.
    return hashlib.sha256(
        f"vidxp-upload-repository-v1\0{root}".encode("utf-8")
    ).hexdigest()


def encode_capability(claims: Mapping[str, Any], *, secret: str) -> str:
    return jwt.encode(
        {"iss": CAPABILITY_ISSUER, **claims},
        secret,
        algorithm=CAPABILITY_ALGORITHM,
    )


def decode_capability(
    token: str,
    *,
    secret: str,
    audience: str,
    required_claims: Sequence[str] = (),
    verify_exp: bool = True,
) -> dict[str, Any]:
    required = tuple(dict.fromkeys((*_REQUIRED_CLAIMS, *required_claims)))
    return jwt.decode(
        token,
        secret,
        algorithms=[CAPABILITY_ALGORITHM],
        audience=audience,
        issuer=CAPABILITY_ISSUER,
        options={"verify_exp": verify_exp, "require": list(required)},
    )
