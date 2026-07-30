from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from filelock import FileLock

from vidxp.app_paths import (
    default_config_directory,
    default_data_directory,
    default_repository_directory,
)

REPOSITORY_SCHEMA_VERSION = 1
DEFAULT_REPOSITORY_NAME = "default"
DEFAULT_INDEX_DIRECTORY = default_repository_directory()
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RepositoryConfigError(ValueError):
    """Raised when repository configuration is invalid or unavailable."""


def default_config_path() -> Path:
    configured = os.environ.get("VIDXP_CONFIG_FILE")
    if configured:
        return Path(configured).expanduser()
    return default_config_directory() / "repositories.json"


def _repository_name(value: str) -> str:
    name = str(value).strip()
    if not _NAME_PATTERN.fullmatch(name):
        raise RepositoryConfigError(
            "Repository names must be 1-64 characters, start with a letter "
            "or number, and contain only letters, numbers, '.', '_', or '-'."
        )
    return name


@dataclass(frozen=True)
class RepositoryConfig:
    name: str
    index_directory: Path
    device: str | None = None
    configured: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _repository_name(self.name))
        object.__setattr__(
            self,
            "index_directory",
            Path(self.index_directory).expanduser(),
        )
        if self.device is not None and not str(self.device).strip():
            raise RepositoryConfigError("Repository device must not be empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": "local",
            "index_directory": str(self.index_directory),
            "device": self.device,
            "configured": self.configured,
        }


class RepositoryRegistry:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        default_index_directory: str | Path | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else default_config_path()
        self.default_index_directory = Path(
            default_index_directory
            if default_index_directory is not None
            else default_repository_directory()
        ).expanduser()

    def read(self) -> dict[str, Any]:
        return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "schema_version": REPOSITORY_SCHEMA_VERSION,
                "active_repository": None,
                "repositories": {},
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepositoryConfigError(
                f"Repository configuration is unreadable: {self.path}"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != REPOSITORY_SCHEMA_VERSION
            or not isinstance(payload.get("repositories"), dict)
        ):
            raise RepositoryConfigError(
                f"Unsupported repository configuration: {self.path}"
            )
        self._validate_payload(payload)
        return payload

    def list(self) -> tuple[RepositoryConfig, ...]:
        payload = self.read()
        configured = tuple(
            self._from_entry(name, entry)
            for name, entry in sorted(payload["repositories"].items())
        )
        if any(item.name == DEFAULT_REPOSITORY_NAME for item in configured):
            return configured
        return (
            RepositoryConfig(
                DEFAULT_REPOSITORY_NAME,
                self.default_index_directory,
                configured=False,
            ),
            *configured,
        )

    def resolve(self, name: str | None = None) -> RepositoryConfig:
        payload = self.read()
        selected = (
            _repository_name(name)
            if name is not None
            else payload.get("active_repository") or DEFAULT_REPOSITORY_NAME
        )
        entry = payload["repositories"].get(selected)
        if entry is not None:
            return self._from_entry(selected, entry)
        if selected == DEFAULT_REPOSITORY_NAME:
            return RepositoryConfig(
                DEFAULT_REPOSITORY_NAME,
                self.default_index_directory,
                configured=False,
            )
        raise RepositoryConfigError(
            f"Repository {selected!r} is not configured."
        )

    def add(
        self,
        name: str,
        index_directory: str | Path,
        *,
        device: str | None = None,
        replace: bool = False,
    ) -> RepositoryConfig:
        repository = RepositoryConfig(
            name,
            Path(index_directory).expanduser().resolve(),
            device,
        )
        with self._lock():
            payload = self._read_unlocked()
            if repository.name in payload["repositories"] and not replace:
                raise RepositoryConfigError(
                    f"Repository {repository.name!r} already exists."
                )
            payload["repositories"][repository.name] = {
                "type": "local",
                "index_directory": str(repository.index_directory),
                "device": repository.device,
            }
            self._write_unlocked(payload)
        return repository

    def remove(self, name: str) -> RepositoryConfig:
        selected = _repository_name(name)
        with self._lock():
            payload = self._read_unlocked()
            entry = payload["repositories"].pop(selected, None)
            if entry is None:
                raise RepositoryConfigError(
                    f"Repository {selected!r} is not configured."
                )
            if payload.get("active_repository") == selected:
                payload["active_repository"] = None
            self._write_unlocked(payload)
        return self._from_entry(selected, entry)

    def use(self, name: str) -> RepositoryConfig:
        selected = _repository_name(name)
        with self._lock():
            payload = self._read_unlocked()
            entry = payload["repositories"].get(selected)
            if entry is not None:
                repository = self._from_entry(selected, entry)
                payload["active_repository"] = repository.name
            elif selected == DEFAULT_REPOSITORY_NAME:
                repository = RepositoryConfig(
                    DEFAULT_REPOSITORY_NAME,
                    self.default_index_directory,
                    configured=False,
                )
                payload["active_repository"] = None
            else:
                raise RepositoryConfigError(
                    f"Repository {selected!r} is not configured."
                )
            self._write_unlocked(payload)
        return repository

    def write(self, payload: Mapping[str, Any]) -> None:
        with self._lock():
            self._write_unlocked(payload)

    def _write_unlocked(self, payload: Mapping[str, Any]) -> None:
        validated = dict(payload)
        self._validate_payload(validated)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                validated,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _lock(self) -> FileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.path) + ".lock")

    @staticmethod
    def _from_entry(
        name: str,
        entry: Mapping[str, Any],
    ) -> RepositoryConfig:
        return RepositoryConfig(
            name=name,
            index_directory=Path(str(entry["index_directory"])),
            device=(
                str(entry["device"])
                if entry.get("device") is not None
                else None
            ),
        )

    @staticmethod
    def _validate_payload(payload: Mapping[str, Any]) -> None:
        repositories = payload.get("repositories")
        if not isinstance(repositories, dict):
            raise RepositoryConfigError(
                "Repository configuration must contain a repositories object."
            )
        for name, entry in repositories.items():
            _repository_name(name)
            if (
                not isinstance(entry, dict)
                or entry.get("type") != "local"
                or not str(entry.get("index_directory", "")).strip()
            ):
                raise RepositoryConfigError(
                    f"Repository {name!r} has an invalid local configuration."
                )
        active = payload.get("active_repository")
        if active is not None:
            active = _repository_name(str(active))
            if active not in repositories:
                raise RepositoryConfigError(
                    f"Active repository {active!r} is not configured."
                )


def resolve_repository(
    *,
    registry_path: str | Path | None = None,
    name: str | None = None,
    index_directory: str | Path | None = None,
    data_directory: str | Path | None = None,
    device: str | None = None,
) -> tuple[RepositoryRegistry, RepositoryConfig]:
    active_data_directory = Path(
        data_directory
        if data_directory is not None
        else os.environ.get("VIDXP_DATA_DIR") or default_data_directory()
    ).expanduser()
    registry = RepositoryRegistry(
        registry_path,
        default_index_directory=default_repository_directory(
            active_data_directory
        ),
    )
    selected_name = name or os.environ.get("VIDXP_REPOSITORY")
    repository = registry.resolve(selected_name)
    resolved_index = (
        Path(index_directory)
        if index_directory is not None
        else Path(os.environ["VIDXP_INDEX_DIR"])
        if os.environ.get("VIDXP_INDEX_DIR")
        else default_repository_directory(active_data_directory)
        if not repository.configured
        else repository.index_directory
    )
    if not repository.configured:
        registry.default_index_directory = resolved_index.expanduser()
    resolved_device = (
        device
        if device is not None
        else os.environ.get("VIDXP_DEVICE") or repository.device
    )
    return registry, RepositoryConfig(
        name=repository.name,
        index_directory=resolved_index,
        device=resolved_device,
        configured=repository.configured,
    )
