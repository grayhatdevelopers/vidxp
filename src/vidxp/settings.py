from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from vidxp.app_paths import (
    default_data_directory,
    default_model_directory,
    default_repository_directory,
)
from vidxp.media_runtime import default_media_executable
from vidxp.repository_layout import RepositoryLayout


DEFAULT_HTTP_PORT = 32191
_TUSD_EXACT_ORIGIN = re.compile(
    r"(?P<scheme>https|http)://(?P<host>"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\\\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r")(?::(?P<port>[0-9]{1,5}))?"
)


def _tusd_cors_origins(pattern: str) -> tuple[str, ...]:
    """Parse the deliberately small regex subset shared with Go's RE2."""
    if not (pattern.startswith("^(") and pattern.endswith(")$")):
        raise ValueError(
            "The upload CORS origin regex must use the RE2-safe exact-origin "
            r"form ^(https://api\.example|https://app\.example)$."
        )
    alternatives = pattern[2:-2].split("|")
    if not alternatives or any(not value for value in alternatives):
        raise ValueError("The upload CORS origin regex has an empty origin.")
    origins: list[str] = []
    for value in alternatives:
        match = _TUSD_EXACT_ORIGIN.fullmatch(value)
        if match is None:
            raise ValueError(
                "The upload CORS origin regex may contain only exact HTTPS "
                "origins or loopback HTTP origins with escaped dots, grouped "
                r"as ^(origin|origin)$."
            )
        decoded_host = match.group("host").replace(r"\.", ".").lower()
        if match.group("scheme") == "http" and decoded_host not in {
            "localhost",
            "127.0.0.1",
        }:
            raise ValueError(
                "The upload CORS origin regex may use HTTP only for loopback."
            )
        port_text = match.group("port")
        if port_text is not None and not 1 <= int(port_text) <= 65535:
            raise ValueError(
                "The upload CORS origin regex contains an invalid port."
            )
        origins.append(value.replace(r"\.", ".").lower())
    if len(set(origins)) != len(origins):
        raise ValueError(
            "The upload CORS origin regex contains a duplicate origin."
        )
    return tuple(origins)


def _validate_browser_public_url(
    value: str | None,
    *,
    field_name: str,
    required_path: str,
) -> str | None:
    if value is None:
        return None
    if value != value.strip() or "\\" in value:
        raise ValueError(f"{field_name} contains unsafe characters.")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != required_path
    ):
        raise ValueError(
            f"{field_name} must be an HTTP(S) URL ending in {required_path}."
        )
    if parsed.scheme != "https" and parsed.hostname.lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError(f"{field_name} must use HTTPS outside loopback.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid port.") from exc
    return value.rstrip("/")


class ApplicationMode(StrEnum):
    local = "local"
    remote = "remote"
    server = "server"


class HttpAuthMode(StrEnum):
    none = "none"
    static = "static"
    oidc = "oidc"


class VidXPSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIDXP_",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    mode: ApplicationMode = ApplicationMode.local
    data_dir: Path = Field(default_factory=default_data_directory)
    repository_root: Path = Field(
        default_factory=default_repository_directory
    )
    runtime_backend: str = "auto"
    model_cache: Path = Field(
        default_factory=default_model_directory
    )
    allow_model_downloads: bool = True
    max_loaded_models: int = Field(default=3, gt=0, le=16)
    max_concurrent_indexing: int = Field(default=1, gt=0, le=16)
    max_concurrent_inference: int = Field(default=2, gt=0, le=64)
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
    http_port: int = Field(default=DEFAULT_HTTP_PORT, gt=0, le=65535)
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
    mcp_public_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    mcp_max_request_body_bytes: int = Field(
        default=4 * 1024 * 1024,
        gt=0,
        le=16 * 1024 * 1024,
    )
    mcp_max_resource_bytes: int = Field(
        default=16 * 1024 * 1024,
        gt=0,
        le=256 * 1024 * 1024,
    )
    mcp_allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:*",
        "[::1]:*",
        "localhost:*",
        "testserver",
    )
    mcp_allowed_origins: tuple[str, ...] = ()
    mcp_stdio_filesystem_accessible: bool = True
    artifact_download_public_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    artifact_download_secret: SecretStr | None = None
    artifact_download_ttl_seconds: int = Field(
        default=15 * 60,
        ge=60,
        le=24 * 60 * 60,
    )
    upload_public_endpoint: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    upload_internal_endpoint: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    upload_max_bytes: int = Field(
        default=50 * 1024 * 1024 * 1024,
        gt=0,
    )
    upload_quota_bytes: int = Field(
        default=100 * 1024 * 1024 * 1024,
        gt=0,
    )
    upload_intent_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        ge=300,
        le=7 * 24 * 60 * 60,
    )
    upload_recovery_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=3600,
    )

    @field_validator(
        "http_static_bearer_token",
        "http_oidc_issuer",
        "http_oidc_audience",
        "http_oidc_jwks_url",
        "mcp_public_url",
        mode="before",
    )
    @classmethod
    def _empty_auth_values_are_unset(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    upload_quarantine_root: Path | None = None
    upload_cleanup_token: SecretStr | None = None
    upload_handoff_public_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    upload_handoff_secret: SecretStr | None = None
    upload_cors_origin_regex: str | None = Field(
        default=None,
        min_length=3,
        max_length=2048,
    )
    upload_session_max_files: int = Field(
        default=10,
        ge=1,
        le=100,
    )
    upload_session_max_bytes: int = Field(
        default=100 * 1024 * 1024 * 1024,
        gt=0,
    )
    upload_session_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        ge=300,
        le=7 * 24 * 60 * 60,
    )
    slm_base_url: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
    )
    slm_model: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    slm_timeout_seconds: float = Field(default=60, gt=0, le=600)
    slm_output_retries: int = Field(default=1, ge=0, le=3)
    trusted_local_import_roots: tuple[Path, ...] = ()
    ffprobe_executable: str = Field(
        default_factory=lambda: default_media_executable("ffprobe"),
        min_length=1,
    )
    ffmpeg_executable: str = Field(
        default_factory=lambda: default_media_executable("ffmpeg"),
        min_length=1,
    )
    external_capabilities: bool = False
    capability_allowlist: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _derive_storage_paths(cls, value):
        if not isinstance(value, dict):
            return value
        configured = dict(value)
        data_directory = Path(
            configured.get("data_dir") or default_data_directory()
        ).expanduser()
        configured.setdefault("data_dir", data_directory)
        configured.setdefault(
            "repository_root",
            default_repository_directory(data_directory),
        )
        configured.setdefault(
            "model_cache",
            default_model_directory(data_directory),
        )
        return configured

    @field_validator("slm_base_url", "slm_model", mode="before")
    @classmethod
    def _normalize_empty_optional_slm(
        cls,
        value: str | None,
    ) -> str | None:
        return None if value == "" else value

    @field_validator(
        "artifact_download_public_url",
        "artifact_download_secret",
        "upload_handoff_public_url",
        "upload_handoff_secret",
        "upload_cors_origin_regex",
        mode="before",
    )
    @classmethod
    def _normalize_empty_handoff_setting(cls, value):
        return None if value == "" else value

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
        "mcp_allowed_hosts",
        "mcp_allowed_origins",
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

    @field_validator("mcp_allowed_hosts")
    @classmethod
    def _validate_mcp_hosts(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(value.lower() for value in values)
        for value in normalized:
            if "*" in value and not value.endswith(":*"):
                raise ValueError(
                    "MCP host wildcards are supported only as host:*."
                )
            if "/" in value or "://" in value:
                raise ValueError(
                    "MCP allowed hosts must be Host header values."
                )
        return normalized

    @field_validator("mcp_allowed_origins")
    @classmethod
    def _validate_mcp_origins(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        for value in values:
            candidate = value[:-2] if value.endswith(":*") else value
            parsed = urlsplit(candidate)
            if (
                value == "null"
                or parsed.scheme not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "MCP allowed origins must be serialized HTTP origins."
                )
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError(
                    "An MCP allowed origin contains an invalid port."
                ) from exc
            if "*" in value and not value.endswith(":*"):
                raise ValueError(
                    "MCP origin wildcards are supported only as origin:*."
                )
        return values

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
        if "\\" in value or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in value
        ):
            raise ValueError(
                f"{info.field_name} contains an unsafe URL character."
            )
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError(f"{info.field_name} must be an HTTP URL.")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError(
                f"{info.field_name} contains an invalid port."
            ) from exc
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(f"{info.field_name} must not contain credentials.")
        if "#" in value:
            raise ValueError(f"{info.field_name} must not contain a fragment.")
        if parsed.scheme != "https" and parsed.hostname.lower() not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError(
                f"{info.field_name} must use HTTPS outside loopback."
            )
        if info.field_name == "http_oidc_issuer" and "?" in value:
            raise ValueError("http_oidc_issuer must not contain a query.")
        return value

    @field_validator(
        "upload_public_endpoint",
        "upload_internal_endpoint",
        "slm_base_url",
    )
    @classmethod
    def _validate_service_url(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        if value is None:
            return None
        if value != value.strip() or "\\" in value:
            raise ValueError(f"{info.field_name} contains unsafe characters.")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{info.field_name} must be a plain HTTP URL.")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError(
                f"{info.field_name} contains an invalid port."
            ) from exc
        if (
            info.field_name == "upload_public_endpoint"
            and parsed.scheme != "https"
            and parsed.hostname.lower() not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
        ):
            raise ValueError(
                "upload_public_endpoint must use HTTPS outside loopback."
            )
        if info.field_name == "slm_base_url":
            if parsed.path.rstrip("/") != "/v1":
                raise ValueError("slm_base_url must end with /v1.")
            if parsed.hostname.lower() in {"ollama.com", "www.ollama.com"}:
                raise ValueError(
                    "slm_base_url must use a self-hosted Ollama service."
                )
        if (
            info.field_name != "slm_base_url"
            and not value.endswith("/")
        ):
            raise ValueError(f"{info.field_name} must end with a slash.")
        return value

    @field_validator("mcp_public_url")
    @classmethod
    def _validate_mcp_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or "\\" in value:
            raise ValueError("mcp_public_url contains unsafe characters.")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/mcp"
        ):
            raise ValueError(
                "mcp_public_url must be a plain HTTP URL ending in /mcp."
            )
        if (
            parsed.scheme != "https"
            and parsed.hostname.lower()
            not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError(
                "mcp_public_url must use HTTPS outside loopback."
            )
        return value.rstrip("/")

    @field_validator("upload_handoff_public_url")
    @classmethod
    def _validate_upload_handoff_public_url(
        cls,
        value: str | None,
    ) -> str | None:
        return _validate_browser_public_url(
            value,
            field_name="upload_handoff_public_url",
            required_path="/upload-handoff",
        )

    @field_validator("artifact_download_public_url")
    @classmethod
    def _validate_artifact_download_public_url(
        cls,
        value: str | None,
    ) -> str | None:
        return _validate_browser_public_url(
            value,
            field_name="artifact_download_public_url",
            required_path="/artifact-download",
        )

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
        if self.mode == ApplicationMode.remote:
            raise ValueError(
                "Remote client mode is not available in this release. "
                "Connect agents to the remote MCP endpoint or use the HTTP "
                "API directly."
            )
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
        if self.upload_public_endpoint is not None:
            if (
                self.upload_internal_endpoint is None
                or self.upload_cleanup_token is None
                or len(self.upload_cleanup_token.get_secret_value()) < 32
            ):
                raise ValueError(
                    "Remote uploads require an internal tusd endpoint and "
                    "a cleanup token of at least 32 characters."
                )
        handoff_configured = any(
            value is not None
            for value in (
                self.upload_handoff_public_url,
                self.upload_handoff_secret,
            )
        )
        if handoff_configured and (
            self.upload_handoff_public_url is None
            or self.upload_handoff_secret is None
            or len(self.upload_handoff_secret.get_secret_value()) < 32
        ):
            raise ValueError(
                "Upload handoffs require an HTTPS public handoff URL (or "
                "loopback HTTP) and a dedicated secret of at least 32 characters."
            )
        if (
            handoff_configured
            and self.mode == ApplicationMode.server
            and (
                self.upload_public_endpoint is None
                or self.upload_cors_origin_regex is None
            )
        ):
            raise ValueError(
                "Server upload handoffs require the resumable upload endpoint "
                "and matching tusd CORS origin policy."
            )
        download_configured = any(
            value is not None
            for value in (
                self.artifact_download_public_url,
                self.artifact_download_secret,
            )
        )
        if download_configured and (
            self.artifact_download_public_url is None
            or self.artifact_download_secret is None
            or len(self.artifact_download_secret.get_secret_value()) < 32
        ):
            raise ValueError(
                "Public artifact downloads require an HTTPS download URL "
                "(or loopback HTTP) and a dedicated secret of at least 32 "
                "characters."
            )
        if self.upload_session_max_bytes < self.upload_max_bytes:
            raise ValueError(
                "The upload session aggregate limit must be at least the "
                "per-file upload limit."
            )
        if self.upload_cors_origin_regex is not None:
            allowed_origins = _tusd_cors_origins(
                self.upload_cors_origin_regex
            )
            if self.upload_handoff_public_url is not None:
                parsed_handoff = urlsplit(self.upload_handoff_public_url)
                handoff_origin = (
                    f"{parsed_handoff.scheme}://{parsed_handoff.netloc}"
                ).lower()
                if handoff_origin not in allowed_origins:
                    raise ValueError(
                        "The upload CORS origin regex must allow the handoff origin."
                    )
        if (self.slm_base_url is None) != (self.slm_model is None):
            raise ValueError(
                "slm_base_url and slm_model must be configured together."
            )
        if self.slm_model is not None and self.slm_model.endswith("-cloud"):
            raise ValueError("slm_model must be a self-hosted Ollama model.")
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
        if not self.mcp_allowed_hosts:
            raise ValueError("At least one allowed MCP host is required.")
        if (
            self.http_auth_mode == HttpAuthMode.oidc
            and self.mcp_public_url is None
        ):
            raise ValueError(
                "OIDC MCP authentication requires mcp_public_url."
            )

    @property
    def layout(self) -> RepositoryLayout:
        return RepositoryLayout(root=self.repository_root.expanduser())

    @property
    def quarantine_root(self) -> Path:
        return (
            self.upload_quarantine_root
            if self.upload_quarantine_root is not None
            else self.repository_root / "upload-quarantine"
        ).expanduser()


class LocalExecutionSettings(BaseModel):
    """Non-secret settings allowed to cross into local execution processes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path
    repository_root: Path
    runtime_backend: str
    model_cache: Path
    allow_model_downloads: bool
    max_loaded_models: int
    max_concurrent_indexing: int
    max_concurrent_inference: int
    workflow_poll_interval_seconds: float
    cpu_thread_budget: int
    max_local_import_bytes: int
    max_snippet_duration_seconds: float
    trusted_local_import_roots: tuple[Path, ...]
    ffprobe_executable: str
    ffmpeg_executable: str
    external_capabilities: bool
    capability_allowlist: tuple[str, ...]
    slm_base_url: str | None
    slm_model: str | None
    slm_timeout_seconds: float
    slm_output_retries: int

    @classmethod
    def from_settings(
        cls,
        settings: VidXPSettings,
    ) -> "LocalExecutionSettings":
        return cls(
            **settings.model_dump(
                include=set(cls.model_fields),
                mode="python",
            )
        )

    def application_settings(self) -> VidXPSettings:
        defaults = VidXPSettings.model_construct().model_dump(mode="python")
        defaults.update(self.model_dump(mode="python"))
        defaults["mode"] = ApplicationMode.local
        defaults["upload_public_endpoint"] = None
        defaults["upload_internal_endpoint"] = None
        defaults["upload_cleanup_token"] = None
        defaults["upload_handoff_public_url"] = None
        defaults["upload_handoff_secret"] = None
        defaults["upload_cors_origin_regex"] = None
        defaults["http_auth_mode"] = HttpAuthMode.none
        defaults["http_static_bearer_token"] = None
        defaults["http_oidc_issuer"] = None
        defaults["http_oidc_audience"] = None
        defaults["http_oidc_jwks_url"] = None
        defaults["http_required_scopes"] = ()
        return VidXPSettings(**defaults)
