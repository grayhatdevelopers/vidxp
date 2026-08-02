from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from jwt import ExpiredSignatureError, PyJWTError

from vidxp.application_models import (
    ApplicationError,
    Artifact,
    ErrorCategory,
)
from vidxp.capability_security import (
    decode_capability,
    encode_capability,
    repository_binding,
)
from vidxp.core.artifacts import artifact_file_identity
from vidxp.core.media import utc_now
from vidxp.ports import LocalFileResource
from vidxp.settings import VidXPSettings


_LINK_AUDIENCE = "vidxp-artifact-download-link"
_SESSION_AUDIENCE = "vidxp-artifact-download-session"
_LINK_PURPOSE = "artifact-download-link-v1"
_SESSION_PURPOSE = "artifact-download-session-v1"
@dataclass(frozen=True)
class ArtifactBinding:
    artifact_id: str
    filename: str
    mime_type: str
    extension: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class IssuedArtifactDownload:
    url: str
    expires_at: datetime


def artifact_binding(artifact: Artifact) -> ArtifactBinding:
    try:
        filename, extension = artifact_file_identity(
            kind=artifact.kind,
            artifact_id=artifact.artifact_id,
            mime_type=artifact.mime_type,
        )
    except ValueError as exc:
        raise ApplicationError(
            "artifact_type_unsupported",
            ErrorCategory.validation,
            "Only completed PNG, MP4, and Matroska artifacts can be delivered.",
            details={"mime_type": artifact.mime_type},
        ) from exc
    return ArtifactBinding(
        artifact_id=artifact.artifact_id,
        filename=filename,
        mime_type=artifact.mime_type,
        extension=extension,
        byte_size=artifact.byte_size,
        sha256=artifact.sha256,
    )


def require_resource_binding(
    binding: ArtifactBinding,
    resource: LocalFileResource,
) -> None:
    if (
        resource.filename != binding.filename
        or resource.mime_type != binding.mime_type
        or resource.byte_size != binding.byte_size
        or resource.etag != binding.sha256
    ):
        raise ApplicationError(
            "artifact_download_binding_mismatch",
            ErrorCategory.conflict,
            "The registered artifact metadata does not match its content.",
        )


class ArtifactDownloadCapabilities:
    """Issue and validate stateless repository-bound browser downloads."""

    def __init__(self, settings: VidXPSettings) -> None:
        self.settings = settings

    def issue(self, artifact: Artifact) -> IssuedArtifactDownload:
        public_url = self.settings.artifact_download_public_url
        if public_url is None or self.settings.artifact_download_secret is None:
            raise ApplicationError(
                "public_download_origin_unavailable",
                ErrorCategory.unavailable,
                "Public artifact downloads are not configured for this deployment.",
            )
        binding = artifact_binding(artifact)
        now = utc_now()
        expires_at = datetime.fromtimestamp(
            int(
                (
                    now + timedelta(seconds=self.settings.artifact_download_ttl_seconds)
                ).timestamp()
            ),
            tz=now.tzinfo,
        )
        capability = self._encode(
            binding,
            audience=_LINK_AUDIENCE,
            purpose=_LINK_PURPOSE,
            issued_at=now,
            expires_at=expires_at,
        )
        return IssuedArtifactDownload(
            url=(
                f"{public_url}/{binding.artifact_id}#capability="
                f"{quote(capability, safe='')}"
            ),
            expires_at=expires_at,
        )

    def exchange(self, artifact: Artifact, capability: str) -> tuple[str, datetime]:
        binding = artifact_binding(artifact)
        claims = self._decode(
            capability,
            audience=_LINK_AUDIENCE,
            purpose=_LINK_PURPOSE,
        )
        self._require_binding(claims, binding)
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=utc_now().tzinfo)
        session_token = self._encode(
            binding,
            audience=_SESSION_AUDIENCE,
            purpose=_SESSION_PURPOSE,
            issued_at=utc_now(),
            expires_at=expires_at,
        )
        return session_token, expires_at

    def authorize(self, artifact: Artifact, session_token: str | None) -> None:
        if session_token is None:
            raise self._invalid_capability()
        binding = artifact_binding(artifact)
        claims = self._decode(
            session_token,
            audience=_SESSION_AUDIENCE,
            purpose=_SESSION_PURPOSE,
        )
        self._require_binding(claims, binding)

    def _encode(
        self,
        binding: ArtifactBinding,
        *,
        audience: str,
        purpose: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> str:
        return encode_capability(
            {
                "aud": audience,
                "sub": binding.artifact_id,
                "purpose": purpose,
                "version": 1,
                "repository": repository_binding(self.settings.repository_root),
                "mime_type": binding.mime_type,
                "extension": binding.extension,
                "sha256": binding.sha256,
                "iat": int(issued_at.timestamp()),
                "exp": int(expires_at.timestamp()),
            },
            secret=self._secret(),
        )

    def _decode(
        self,
        token: str,
        *,
        audience: str,
        purpose: str,
    ) -> dict:
        try:
            claims = decode_capability(
                token,
                secret=self._secret(),
                audience=audience,
                required_claims=(
                    "purpose",
                    "version",
                    "repository",
                    "mime_type",
                    "extension",
                    "sha256",
                ),
            )
        except ExpiredSignatureError as exc:
            raise ApplicationError(
                "artifact_download_capability_expired",
                ErrorCategory.authentication,
                "The artifact download capability expired.",
            ) from exc
        except PyJWTError as exc:
            raise self._invalid_capability() from exc
        if claims.get("purpose") != purpose or claims.get("version") != 1:
            raise self._invalid_capability()
        return claims

    def _require_binding(self, claims: dict, binding: ArtifactBinding) -> None:
        if (
            claims.get("sub") != binding.artifact_id
            or claims.get("repository")
            != repository_binding(self.settings.repository_root)
            or claims.get("mime_type") != binding.mime_type
            or claims.get("extension") != binding.extension
            or claims.get("sha256") != binding.sha256
        ):
            raise ApplicationError(
                "artifact_download_binding_mismatch",
                ErrorCategory.authorization,
                "The artifact download capability does not match this artifact.",
            )

    def _secret(self) -> str:
        secret = self.settings.artifact_download_secret
        if secret is None:
            raise ApplicationError(
                "public_download_origin_unavailable",
                ErrorCategory.unavailable,
                "Public artifact downloads are not configured for this deployment.",
            )
        return secret.get_secret_value()

    @staticmethod
    def _invalid_capability() -> ApplicationError:
        return ApplicationError(
            "artifact_download_capability_invalid",
            ErrorCategory.authentication,
            "The artifact download capability is invalid.",
        )


def verified_local_path(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise _local_path_unavailable() from exc
    if not resolved.is_file():
        raise _local_path_unavailable()
    return resolved


def _local_path_unavailable() -> ApplicationError:
    return ApplicationError(
        "local_path_unavailable",
        ErrorCategory.unavailable,
        "The completed artifact is not available as a local file.",
    )
