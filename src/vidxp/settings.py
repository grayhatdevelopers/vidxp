from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path

from platformdirs import user_cache_path
from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from vidxp.repository_layout import RepositoryLayout


class ApplicationMode(StrEnum):
    local = "local"
    remote = "remote"
    server = "server"


class HttpAuthMode(StrEnum):
    none = "none"
    static = "static"
    oidc = "oidc"


_HTTP_URL = TypeAdapter(HttpUrl)


class VidXPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIDXP_",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    mode: ApplicationMode = ApplicationMode.local
    repository_root: Path = Path("chroma_data")
    runtime_backend: str = "auto"
    model_cache: Path = Field(
        default_factory=lambda: user_cache_path("vidxp") / "models"
    )
    allow_model_downloads: bool = True
    max_loaded_models: int = Field(default=3, gt=0, le=16)
    max_concurrent_indexing: int = Field(default=1, gt=0, le=16)
    max_concurrent_inference: int = Field(default=2, gt=0, le=64)
    workflow_database_url: str | None = Field(default=None, min_length=1)
    workflow_poll_interval_seconds: float = Field(
        default=0.25,
        gt=0,
        le=10,
    )
    cpu_thread_budget: int = Field(
        default_factory=lambda: min(256, max(1, os.cpu_count() or 1)),
        gt=0,
        le=256,
    )
    minimum_available_memory_mb: int = Field(default=1024, ge=0)
    max_local_import_bytes: int = Field(
        default=50 * 1024 * 1024 * 1024,
        gt=0,
    )
    max_snippet_duration_seconds: float = Field(
        default=300,
        gt=0,
        le=3600,
    )
    http_bind_host: str = Field(default="127.0.0.1", min_length=1)
    http_port: int = Field(default=8000, gt=0, le=65535)
    http_auth_mode: HttpAuthMode = HttpAuthMode.none
    http_static_bearer_token: SecretStr | None = None
    http_oidc_issuer: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    http_oidc_audience: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )
    http_oidc_jwks_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    http_oidc_algorithms: tuple[str, ...] = ("RS256",)
    http_required_scopes: tuple[str, ...] = ()
    http_trusted_hosts: tuple[str, ...] = (
        "127.0.0.1",
        "::1",
        "localhost",
        "testserver",
    )
    http_allowed_origins: tuple[str, ...] = ()
    http_max_json_body_bytes: int = Field(
        default=4 * 1024 * 1024,
        gt=0,
        le=16 * 1024 * 1024,
    )
    http_max_small_upload_bytes: int = Field(
        default=256 * 1024 * 1024,
        gt=0,
        le=256 * 1024 * 1024,
    )
    trusted_local_import_roots: tuple[Path, ...] = ()
    ffprobe_executable: str = Field(default="ffprobe", min_length=1)
    ffmpeg_executable: str = Field(default="ffmpeg", min_length=1)
    external_capabilities: bool = False
    capability_allowlist: tuple[str, ...] = ()

    @field_validator("runtime_backend")
    @classmethod
    def _validate_runtime_backend(cls, value: str) -> str:
        backend = value.strip().lower()
        if not re.fullmatch(r"(auto|cpu|mps|cuda(?::[0-9]+)?)", backend):
            raise ValueError(
                "runtime_backend must be auto, cpu, mps, cuda, "
                "or cuda:<device-index>."
            )
        return backend

    @field_validator("capability_allowlist")
    @classmethod
    def _clean_allowlist(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(
            dict.fromkeys(value.strip() for value in values if value.strip())
        )
        invalid = [
            value
            for value in cleaned
            if value.count(":") != 1
            or not all(part.strip() for part in value.split(":", 1))
        ]
        if invalid:
            raise ValueError(
                "capability_allowlist entries must use "
                "DISTRIBUTION:ENTRY_POINT."
            )
        return cleaned

    @field_validator(
        "http_required_scopes",
        "http_trusted_hosts",
        "http_allowed_origins",
    )
    @classmethod
    def _clean_http_lists(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(value.strip() for value in values if value.strip())
        )

    @field_validator("http_trusted_hosts")
    @classmethod
    def _validate_trusted_hosts(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(value.lower() for value in values)
        for value in normalized:
            if (
                "*" in value[1:]
                or (
                    value.startswith("*")
                    and value != "*"
                    and not value.startswith("*.")
                )
            ):
                raise ValueError(
                    "Trusted-host wildcards must use *.example.com."
                )
        return normalized

    @field_validator("http_oidc_algorithms")
    @classmethod
    def _validate_oidc_algorithms(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        allowed = {
            "RS256",
            "RS384",
            "RS512",
            "ES256",
            "ES384",
            "ES512",
            "EdDSA",
        }
        cleaned = tuple(dict.fromkeys(values))
        if not cleaned or any(value not in allowed for value in cleaned):
            raise ValueError(
                "http_oidc_algorithms must contain supported asymmetric "
                "signature algorithms."
            )
        return cleaned

    @field_validator("http_oidc_issuer", "http_oidc_jwks_url")
    @classmethod
    def _validate_oidc_url(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        if value != value.strip():
            raise ValueError(f"{info.field_name} must not contain whitespace.")
        parsed = _HTTP_URL.validate_python(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(f"{info.field_name} must not contain credentials.")
        if parsed.fragment is not None:
            raise ValueError(f"{info.field_name} must not contain a fragment.")
        if info.field_name == "http_oidc_issuer" and parsed.query is not None:
            raise ValueError("http_oidc_issuer must not contain a query.")
        return value

    @field_validator("trusted_local_import_roots")
    @classmethod
    def _clean_import_roots(
        cls,
        values: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        return tuple(
            dict.fromkeys(path.expanduser() for path in values)
        )

    @model_validator(mode="after")
    def _require_explicit_server_backend(self) -> "VidXPSettings":
        if (
            self.mode == ApplicationMode.server
            and not re.fullmatch(r"(cpu|cuda(?::[0-9]+)?)", self.runtime_backend)
        ):
            raise ValueError(
                "Server mode requires an explicit cpu or cuda runtime backend."
            )
        if self.http_auth_mode == HttpAuthMode.static:
            if self.http_static_bearer_token is None or len(
                self.http_static_bearer_token.get_secret_value()
            ) < 32:
                raise ValueError(
                    "Static HTTP authentication requires a bearer token of "
                    "at least 32 characters."
                )
            if any(
                value is not None
                for value in (
                    self.http_oidc_issuer,
                    self.http_oidc_audience,
                    self.http_oidc_jwks_url,
                )
            ):
                raise ValueError(
                    "Static HTTP authentication cannot include OIDC settings."
                )
        elif self.http_auth_mode == HttpAuthMode.oidc:
            if (
                self.http_oidc_issuer is None
                or self.http_oidc_audience is None
                or self.http_oidc_jwks_url is None
            ):
                raise ValueError(
                    "OIDC HTTP authentication requires issuer, audience, "
                    "and JWKS URL settings."
                )
            if self.http_static_bearer_token is not None:
                raise ValueError(
                    "OIDC HTTP authentication cannot include a static token."
                )
            if not self.http_required_scopes:
                raise ValueError(
                    "OIDC HTTP authentication requires at least one scope."
                )
        elif self.http_static_bearer_token is not None or any(
            value is not None
            for value in (
                self.http_oidc_issuer,
                self.http_oidc_audience,
                self.http_oidc_jwks_url,
            )
        ):
            raise ValueError(
                "HTTP credentials require an explicit authentication mode."
            )
        return self

    def validate_http_server(self) -> None:
        if (
            self.mode == ApplicationMode.server
            and self.http_auth_mode == HttpAuthMode.none
        ):
            raise ValueError(
                "Server-mode HTTP requires static bearer or OIDC "
                "authentication."
            )
        if (
            self.http_auth_mode == HttpAuthMode.none
            and self.http_bind_host not in {"127.0.0.1", "::1", "localhost"}
        ):
            raise ValueError(
                "Unauthenticated HTTP may bind only to a loopback address."
            )
        if not self.http_trusted_hosts:
            raise ValueError("At least one trusted HTTP host is required.")

    @property
    def layout(self) -> RepositoryLayout:
        return RepositoryLayout(root=self.repository_root.expanduser())
