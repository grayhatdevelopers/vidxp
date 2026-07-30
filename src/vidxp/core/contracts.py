from __future__ import annotations

import hashlib
import json
import re
import string
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import Event
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote


INDEX_SCHEMA_VERSION = 6
MANIFEST_SCHEMA_VERSION = 2


class IndexCancelledError(RuntimeError):
    """Raised after a cooperative indexing cancellation request."""


class IndexSchemaError(RuntimeError):
    """Raised when an index cannot satisfy the current result contract."""


def _require_identifier(label: str, value: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError(f"{label} must not be empty.")
    return value


def _require_sha256(label: str, value: str) -> str:
    checksum = str(value).lower()
    if (
        len(checksum) != 64
        or any(character not in string.hexdigits for character in checksum)
    ):
        raise ValueError(f"{label} must be a 64-character SHA-256 value.")
    return checksum


def _filesystem_component(value: str) -> str:
    value = _require_identifier("path component", value)
    if value in {".", ".."}:
        raise ValueError("Run path components cannot be '.' or '..'.")
    windows_stem = value.rstrip(" .").split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{index}" for index in range(1, 10))
    reserved.update(f"LPT{index}" for index in range(1, 10))
    if windows_stem in reserved:
        raise ValueError(
            f"Run path component {value!r} is reserved on Windows."
        )
    return quote(value, safe="._-")


def stable_source_id(
    run_id: str,
    video_id: str,
    modality: str,
    local_id: str | int,
    *,
    generation_id: str | None = None,
) -> str:
    """Return an escaped, deterministic record ID.

    Each component is percent-encoded independently, so delimiters inside an
    official dataset ID cannot collide with the separators in the stored ID.
    """

    values = (run_id, video_id, modality, str(local_id))
    labels = ("run_id", "video_id", "modality", "local_id")
    if generation_id is not None:
        values = (generation_id, *values)
        labels = ("generation_id", *labels)
    encoded = [
        quote(_require_identifier(label, value), safe="")
        for label, value in zip(labels, values)
    ]
    return ":".join(encoded)


@dataclass(frozen=True)
class IndexConfig:
    dataset: str = "local"
    split: str = "local"
    run_id: str = "default"
    video_id: str | None = None
    enabled_modalities: tuple[str, ...] = ()
    frame_stride: int = 1
    storage_batch_size: int = 256
    vector_distance: str = "l2"
    device: str = "cpu"
    capability_options: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    output_root: str | Path = "benchmark_runs"
    storage_directory: str | Path | None = None
    generation_directory: str | Path | None = None
    generation_id: str | None = None
    snapshot_id: str | None = None
    snapshot_sha256: str | None = None
    collection_names: Mapping[str, str] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_root", str(self.output_root))
        if self.storage_directory is not None:
            object.__setattr__(
                self,
                "storage_directory",
                str(self.storage_directory),
            )
        if self.generation_directory is not None:
            object.__setattr__(
                self,
                "generation_directory",
                str(self.generation_directory),
            )
        for label in ("dataset", "split", "run_id"):
            _require_identifier(label, getattr(self, label))
        if self.video_id is not None:
            _require_identifier("video_id", self.video_id)
        if self.generation_id is not None:
            _require_identifier("generation_id", self.generation_id)
        if self.snapshot_id is not None:
            _require_identifier("snapshot_id", self.snapshot_id)
        if self.snapshot_sha256 is not None:
            object.__setattr__(
                self,
                "snapshot_sha256",
                _require_sha256("snapshot_sha256", self.snapshot_sha256),
            )

        modalities = tuple(dict.fromkeys(self.enabled_modalities))
        if not modalities:
            raise ValueError("At least one indexing modality must be enabled.")
        object.__setattr__(self, "enabled_modalities", modalities)
        collection_names = {
            str(capability): str(name)
            for capability, name in self.collection_names.items()
        }
        if not collection_names:
            collection_names = {
                capability: capability
                for capability in modalities
            }
        missing_collections = sorted(
            set(modalities) - set(collection_names)
        )
        if missing_collections:
            raise ValueError(
                "Missing collection names for capabilities: "
                + ", ".join(missing_collections)
            )
        object.__setattr__(self, "collection_names", collection_names)
        capability_options = {
            str(capability): dict(options)
            for capability, options in self.capability_options.items()
        }
        unknown_options = sorted(
            set(capability_options) - set(modalities)
        )
        if unknown_options:
            raise ValueError(
                "Options were supplied for disabled capabilities: "
                + ", ".join(unknown_options)
            )
        object.__setattr__(
            self,
            "capability_options",
            capability_options,
        )

        for label in (
            "frame_stride",
            "storage_batch_size",
        ):
            if getattr(self, label) <= 0:
                raise ValueError(f"{label} must be greater than zero.")
        if self.vector_distance not in {"l2", "cosine", "ip"}:
            raise ValueError(
                "vector_distance must be one of: l2, cosine, ip."
            )
        collection_pattern = re.compile(
            r"^[A-Za-z0-9][A-Za-z0-9._-]{1,510}[A-Za-z0-9]$"
        )
        invalid_names = [
            name
            for name in self.collection_names.values()
            if not collection_pattern.fullmatch(str(name))
        ]
        if invalid_names:
            raise ValueError(
                "collection_names must be 3-512 characters, begin and end "
                "with an alphanumeric character, and contain only "
                "letters, numbers, periods, underscores, or hyphens."
            )
        if len(set(self.collection_names.values())) != len(
            self.collection_names
        ):
            raise ValueError("collection_names must be distinct.")

    @classmethod
    def local(cls, **changes: Any) -> "IndexConfig":
        """Return the default configuration for a local repository operation."""

        defaults = {
            "storage_directory": "chroma_data",
        }
        if "enabled_modalities" not in changes:
            defaults["enabled_modalities"] = ("dialogue", "scene", "actor")
        defaults.update(changes)
        return cls(**defaults)

    @property
    def run_directory(self) -> Path:
        if self.generation_directory is not None:
            return Path(self.generation_directory)
        if self.storage_directory is not None:
            return Path(self.storage_directory)
        return (
            Path(self.output_root)
            / _filesystem_component(self.dataset)
            / _filesystem_component(self.run_id)
        )

    @property
    def index_directory(self) -> Path:
        if self.storage_directory is not None:
            return Path(self.storage_directory)
        return self.run_directory / "index"

    def for_video(self, video_id: str) -> "IndexConfig":
        return replace(self, video_id=_require_identifier("video_id", video_id))

    def record_identity(
        self,
        modality: str,
        source_id: str,
    ) -> dict[str, str]:
        if self.video_id is None:
            raise ValueError("IndexConfig.video_id is required for record metadata.")
        if modality not in self.enabled_modalities:
            raise ValueError(
                f"Capability {modality!r} is not enabled for this run."
            )
        identity = {
            "dataset": self.dataset,
            "split": self.split,
            "run_id": self.run_id,
            "video_id": self.video_id,
            "modality": modality,
            "source_id": source_id,
        }
        if self.generation_id is not None:
            identity["generation_id"] = self.generation_id
        return identity

    def options_for(self, capability: str) -> dict[str, Any]:
        if capability not in self.enabled_modalities:
            raise ValueError(
                f"Capability {capability!r} is not enabled for this run."
            )
        return dict(self.capability_options.get(capability, {}))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["enabled_modalities"] = list(self.enabled_modalities)
        payload["collection_names"] = dict(self.collection_names)
        payload["run_directory"] = str(self.run_directory)
        payload["index_directory"] = str(self.index_directory)
        return payload

    def fingerprint(self) -> str:
        payload = asdict(self)
        for excluded in (
            "video_id",
            "device",
            "output_root",
            "storage_directory",
            "generation_directory",
            "generation_id",
            "snapshot_id",
            "snapshot_sha256",
        ):
            payload.pop(excluded, None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class VideoSource:
    video_id: str | None = None
    path: str | Path | None = None
    source_name: str | None = None
    transcript: Sequence[Mapping[str, Any]] | None = None
    checksum: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.video_id is not None:
            _require_identifier("video_id", self.video_id)
        if self.path is None and self.transcript is None:
            raise ValueError("A video path or timestamped transcript is required.")
        if self.checksum is not None:
            object.__setattr__(
                self,
                "checksum",
                _require_sha256("checksum", self.checksum),
            )


@dataclass(frozen=True)
class StorageRecord:
    source_id: str
    metadata: Mapping[str, Any]
    embedding: Sequence[float] | None = None
    document: str | None = None


class CancellationToken:
    """A small cooperative cancellation token checked between work batches."""

    def __init__(self, event: Any | None = None):
        self._event = event or Event()

    @property
    def cancelled(self) -> bool:
        return bool(self._event.is_set())

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise IndexCancelledError("Indexing was cancelled between batches.")


def batched(items: Sequence[Any], batch_size: int) -> Iterable[Sequence[Any]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero.")
    for start in range(0, len(items), batch_size):
        yield items[start:start + batch_size]
