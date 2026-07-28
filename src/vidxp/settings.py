from __future__ import annotations

import os
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
    max_concurrent_indexing: int = Field(default=1, gt=0, le=16)
    max_concurrent_inference: int = Field(default=2, gt=0, le=64)
    cpu_thread_budget: int = Field(
        default_factory=lambda: min(256, max(1, os.cpu_count() or 1)),
        gt=0,
        le=256,
    )
    minimum_available_memory_mb: int = Field(default=1024, ge=0)
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

    @model_validator(mode="after")
    def _require_explicit_server_backend(self) -> "VidXPSettings":
        if (
            self.mode == ApplicationMode.server
            and not re.fullmatch(r"(cpu|cuda(?::[0-9]+)?)", self.runtime_backend)
        ):
            raise ValueError(
                "Server mode requires an explicit cpu or cuda runtime backend."
            )
        return self

    @property
    def layout(self) -> RepositoryLayout:
        return RepositoryLayout(root=self.repository_root.expanduser())
