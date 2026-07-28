from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from platformdirs import user_cache_path
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vidxp.repository_layout import RepositoryLayout


class ApplicationMode(StrEnum):
    local = "local"
    remote = "remote"
    server = "server"


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
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def _require_explicit_server_backend(self) -> "VidXPSettings":
        if self.mode == ApplicationMode.server and self.runtime_backend == "auto":
            raise ValueError(
                "Server mode requires an explicit cpu or cuda runtime backend."
            )
        return self

    @property
    def layout(self) -> RepositoryLayout:
        return RepositoryLayout(root=self.repository_root.expanduser())
