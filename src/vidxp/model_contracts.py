from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any


_IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def loaded_compute_precision(model: Any, *, fallback: str) -> str:
    try:
        value = next(model.parameters()).dtype
    except (AttributeError, StopIteration, TypeError):
        return fallback
    return str(value).removeprefix("torch.")


class ModelArtifactUnavailableError(RuntimeError):
    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"Model artifacts for {capability} are unavailable.")


class ModelArtifactDownloadError(RuntimeError):
    def __init__(
        self,
        capability: str,
        model_id: str,
        *,
        attempts: int,
        reason: str,
        resumable: bool,
        retryable: bool,
    ) -> None:
        self.capability = capability
        self.model_id = model_id
        self.attempts = attempts
        self.reason = reason
        self.resumable = resumable
        self.retryable = retryable
        super().__init__(
            f"Downloading {model_id} failed after {attempts} attempt(s): "
            f"{reason}."
        )


@dataclass(frozen=True)
class ModelKey:
    capability: str
    provider: str
    model_id: str
    revision: str
    device: str


@dataclass(frozen=True)
class ModelSpec:
    capability: str
    provider: str
    model_id: str
    revision: str
    download_size_bytes: int
    weights_file: str
    weights_sha256: str
    license: str
    weights_precision: str

    def __post_init__(self) -> None:
        if not _IMMUTABLE_REVISION.fullmatch(self.revision):
            raise ValueError("Model revisions must be immutable commit hashes.")
        if self.download_size_bytes <= 0:
            raise ValueError("Model download sizes must be positive.")
        if not _SHA256.fullmatch(self.weights_sha256):
            raise ValueError("Model weight checksums must be SHA-256 hashes.")

    def key(self, device: str) -> ModelKey:
        return ModelKey(
            capability=self.capability,
            provider=self.provider,
            model_id=self.model_id,
            revision=self.revision,
            device=device,
        )

    def identity(self, *, cached: bool | None = None) -> dict[str, Any]:
        identity = {
            "provider": self.provider,
            "model": self.model_id,
            "revision": self.revision,
            "download_size_bytes": self.download_size_bytes,
            "weights": {
                "file": self.weights_file,
                "sha256": self.weights_sha256,
            },
            "license": self.license,
            "weights_precision": self.weights_precision,
        }
        if cached is not None:
            identity["cached"] = cached
        return identity


@dataclass(frozen=True)
class ArtifactSpec:
    capability: str
    provider: str
    model_id: str
    revision: str
    download_size_bytes: int
    url: str
    filename: str
    sha256: str
    license: str
    weights_precision: str

    def __post_init__(self) -> None:
        if not _IMMUTABLE_REVISION.fullmatch(self.revision):
            raise ValueError("Artifact revisions must be immutable commit hashes.")
        if self.download_size_bytes <= 0:
            raise ValueError("Artifact download sizes must be positive.")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError("Artifact checksums must be SHA-256 hashes.")
        if not self.url.startswith("https://"):
            raise ValueError("Artifact URLs must use HTTPS.")

    def key(self, device: str) -> ModelKey:
        return ModelKey(
            capability=self.capability,
            provider=self.provider,
            model_id=self.model_id,
            revision=self.revision,
            device=device,
        )

    def identity(self, *, cached: bool | None = None) -> dict[str, Any]:
        identity = {
            "provider": self.provider,
            "model": self.model_id,
            "revision": self.revision,
            "download_size_bytes": self.download_size_bytes,
            "artifact": {
                "file": self.filename,
                "sha256": self.sha256,
                "url": self.url,
            },
            "license": self.license,
            "weights_precision": self.weights_precision,
        }
        if cached is not None:
            identity["cached"] = cached
        return identity


def model_artifact_path(
    cache: Path,
    spec: ModelSpec | ArtifactSpec,
) -> Path:
    if isinstance(spec, ModelSpec):
        repository = "models--" + spec.model_id.replace("/", "--")
        return (
            cache
            / repository
            / "snapshots"
            / spec.revision
            / spec.weights_file
        )
    return cache / spec.provider / spec.filename


def model_artifact_cached(
    cache: Path,
    spec: ModelSpec | ArtifactSpec,
) -> bool:
    return model_artifact_valid(model_artifact_path(cache, spec), spec)


def model_artifact_valid(
    path: Path,
    spec: ModelSpec | ArtifactSpec,
) -> bool:
    if not path.is_file():
        return False
    expected = (
        spec.weights_sha256 if isinstance(spec, ModelSpec) else spec.sha256
    )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return False
    return digest.hexdigest() == expected
