from __future__ import annotations

import platform
import hashlib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock, RLock
from time import monotonic
from typing import Any, Callable, Iterator
from pathlib import Path

from vidxp.application_models import RuntimeProfile
from vidxp.model_contracts import (
    ArtifactSpec,
    ModelArtifactUnavailableError,
    ModelKey,
    ModelSpec,
)
from vidxp.settings import VidXPSettings


class RuntimeBackendUnavailableError(RuntimeError):
    """Raised when an explicitly requested compute backend cannot be used."""


def _torch_accelerators() -> tuple[bool, bool]:
    try:
        import torch
    except ModuleNotFoundError:
        return False, False
    mps = bool(
        getattr(torch.backends, "mps", None)
        and torch.backends.mps.is_available()
    )
    return mps, bool(torch.cuda.is_available())


def resolve_backends(requested: str) -> RuntimeProfile:
    mps_available, cuda_available = _torch_accelerators()
    normalized = requested.lower()
    if normalized == "auto":
        torch_device = "cpu"
    elif normalized == "mps":
        if not mps_available:
            raise RuntimeBackendUnavailableError(
                "MPS was requested but is unavailable."
            )
        torch_device = "mps"
    elif normalized.startswith("cuda"):
        if not cuda_available:
            raise RuntimeBackendUnavailableError(
                "CUDA was requested but is unavailable."
            )
        if ":" in normalized:
            import torch

            index = int(normalized.split(":", 1)[1])
            if index >= torch.cuda.device_count():
                raise RuntimeBackendUnavailableError(
                    f"CUDA device {index} was requested but only "
                    f"{torch.cuda.device_count()} device(s) are available."
                )
        torch_device = normalized
    elif normalized == "cpu":
        torch_device = "cpu"
    else:
        raise RuntimeBackendUnavailableError(
            f"Unsupported runtime backend: {requested!r}."
        )
    return RuntimeProfile(
        requested=normalized,
        torch_device=torch_device,
        transcription_device=(
            normalized if normalized.startswith("cuda") else "cpu"
        ),
        mps_available=mps_available,
        cuda_available=cuda_available,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ResourceScheduler:
    """Bound concurrent model work without owning workflow state."""

    def __init__(
        self,
        *,
        indexing_slots: int,
        inference_slots: int,
    ) -> None:
        self._indexing = BoundedSemaphore(indexing_slots)
        self._inference = BoundedSemaphore(inference_slots)

    @contextmanager
    def indexing(self) -> Iterator[None]:
        with self._indexing:
            yield

    @contextmanager
    def inference(self) -> Iterator[None]:
        with self._inference:
            yield


class ModelRuntime:
    """One injected model cache and backend resolver for all capabilities."""

    def __init__(
        self,
        settings: VidXPSettings,
        *,
        allowed_specs: tuple[ModelSpec | ArtifactSpec, ...] = (),
    ) -> None:
        self.settings = settings
        self._allowed_specs = frozenset(allowed_specs)
        self.backends = resolve_backends(settings.runtime_backend)
        self.scheduler = ResourceScheduler(
            indexing_slots=settings.max_concurrent_indexing,
            inference_slots=settings.max_concurrent_inference,
        )
        self._resources: OrderedDict[ModelKey, Any] = OrderedDict()
        self._resolved_models: dict[str, dict[str, Any]] = {}
        self._compute_precision: dict[str, str] = {}
        self._load_locks: dict[ModelKey, Lock] = {}
        self._lock = RLock()

    @property
    def model_cache(self) -> Path:
        return self.settings.model_cache

    @property
    def cpu_thread_budget(self) -> int:
        return self.settings.cpu_thread_budget

    def _configure_cpu_threads(self) -> None:
        try:
            import torch
        except ModuleNotFoundError:
            pass
        else:
            torch.set_num_threads(self.cpu_thread_budget)
        try:
            import cv2
        except ModuleNotFoundError:
            pass
        else:
            cv2.setNumThreads(self.cpu_thread_budget)

    def device_for(self, capability: str) -> str:
        if capability == "dialogue.transcription":
            return self.backends.transcription_device
        if capability == "actor":
            return self.backends.actor_device
        return self.backends.torch_device

    @staticmethod
    def _download_snapshot(
        spec: ModelSpec,
        *,
        cache: Path,
        progress: Callable[[dict[str, Any]], None] | None,
    ) -> Path:
        from huggingface_hub import constants, snapshot_download
        from tqdm.auto import tqdm

        # Xet can remain parked at zero bytes without surfacing an error.
        # The regular HTTP path has bounded read timeouts and reports bytes
        # through tqdm, which is required for durable preparation progress.
        constants.HF_HUB_DISABLE_XET = True
        state_lock = Lock()
        state: dict[str, Any] = {
            "current": 0,
            "total": spec.download_size_bytes,
            "message": f"Connecting to download {spec.model_id}.",
        }

        class ReportingTqdm(tqdm):
            def display(self, msg=None, pos=None) -> None:
                return None

            def update(self, n=1):
                result = super().update(n)
                if self.unit == "B":
                    with state_lock:
                        state.update(
                            {
                                "current": int(self.n),
                                "total": (
                                    int(self.total)
                                    if self.total
                                    else None
                                ),
                                "message": f"Downloading {spec.model_id}.",
                            }
                        )
                return result

        def download() -> str:
            return snapshot_download(
                repo_id=spec.model_id,
                revision=spec.revision,
                cache_dir=str(cache),
                local_files_only=False,
                tqdm_class=ReportingTqdm,
            )

        reported_at = 0.0
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(download)
            while True:
                try:
                    snapshot = future.result(timeout=0.5)
                    if progress is not None:
                        with state_lock:
                            event = dict(state)
                        progress(
                            {
                                "state": "preparing",
                                "stage": "downloading_model",
                                **event,
                            }
                        )
                    break
                except FutureTimeout:
                    now = monotonic()
                    if progress is None or now - reported_at < 1:
                        continue
                    with state_lock:
                        event = dict(state)
                    progress(
                        {
                            "state": "preparing",
                            "stage": "downloading_model",
                            **event,
                        }
                    )
                    reported_at = now
        return Path(snapshot)

    def resolve_model(
        self,
        spec: ModelSpec,
        *,
        download: bool = False,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path:
        if spec not in self._allowed_specs:
            raise ModelArtifactUnavailableError(spec.capability)
        from huggingface_hub import snapshot_download

        try:
            try:
                snapshot = Path(
                    snapshot_download(
                        repo_id=spec.model_id,
                        revision=spec.revision,
                        cache_dir=str(self.settings.model_cache),
                        local_files_only=True,
                    )
                )
            except Exception:
                if not download or not self.settings.allow_model_downloads:
                    raise ModelArtifactUnavailableError(spec.capability)
                snapshot = self._download_snapshot(
                    spec,
                    cache=self.settings.model_cache,
                    progress=progress,
                )
            weights = snapshot / spec.weights_file
            if not weights.is_file() or _sha256(weights) != spec.weights_sha256:
                raise ModelArtifactUnavailableError(spec.capability)
        except ModelArtifactUnavailableError:
            raise
        except Exception as exc:
            raise ModelArtifactUnavailableError(spec.capability) from exc
        with self._lock:
            self._resolved_models[spec.capability] = spec.identity(cached=True)
        return snapshot

    def resolve_artifact(
        self,
        spec: ArtifactSpec,
        *,
        download: bool = False,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> Path:
        if spec not in self._allowed_specs:
            raise ModelArtifactUnavailableError(spec.capability)
        try:
            destination = self.settings.model_cache / spec.provider
            path = destination / spec.filename
            if not path.is_file() or _sha256(path) != spec.sha256:
                if not download or not self.settings.allow_model_downloads:
                    raise ModelArtifactUnavailableError(spec.capability)
                if progress is not None:
                    progress(
                        {
                            "state": "preparing",
                            "stage": "downloading_model",
                            "message": f"Downloading {spec.model_id}.",
                        }
                    )
                import pooch

                resolved = Path(
                    pooch.retrieve(
                        url=spec.url,
                        known_hash=f"sha256:{spec.sha256}",
                        fname=spec.filename,
                        path=destination,
                        progressbar=False,
                    )
                )
            else:
                resolved = path
            if not resolved.is_file() or _sha256(resolved) != spec.sha256:
                raise ModelArtifactUnavailableError(spec.capability)
        except ModelArtifactUnavailableError:
            raise
        except Exception as exc:
            raise ModelArtifactUnavailableError(spec.capability) from exc
        with self._lock:
            self._resolved_models[spec.capability] = spec.identity(cached=True)
        return resolved

    def record_compute_precision(
        self,
        capability: str,
        precision: str,
    ) -> None:
        with self._lock:
            self._compute_precision[capability] = precision

    def get_or_load(
        self,
        key: ModelKey,
        loader: Callable[[], Any],
    ) -> Any:
        with self._lock:
            if key in self._resources:
                resource = self._resources.pop(key)
                self._resources[key] = resource
                return resource
            key_lock = self._load_locks.setdefault(key, Lock())

        with key_lock:
            with self._lock:
                if key in self._resources:
                    resource = self._resources.pop(key)
                    self._resources[key] = resource
                    return resource
            self._configure_cpu_threads()
            resource = loader()
            with self._lock:
                existing = self._resources.pop(key, None)
                if existing is not None:
                    return existing
                self._resources[key] = resource
                while len(self._resources) > self.settings.max_loaded_models:
                    self._resources.popitem(last=False)
                self._load_locks.pop(key, None)
                return resource

    def clear(self) -> None:
        with self._lock:
            self._resources.clear()
            self._load_locks.clear()
        try:
            import torch
        except ModuleNotFoundError:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        if (
            getattr(torch.backends, "mps", None)
            and torch.backends.mps.is_available()
        ):
            torch.mps.empty_cache()

    def describe(self) -> dict[str, Any]:
        return {
            "platform": platform.system().lower(),
            "architecture": platform.machine().lower(),
            **self.backends.model_dump(mode="json"),
            "model_cache": str(self.settings.model_cache),
            "allow_model_downloads": self.settings.allow_model_downloads,
            "limits": {
                "max_loaded_models": self.settings.max_loaded_models,
                "max_concurrent_indexing": (
                    self.settings.max_concurrent_indexing
                ),
                "max_concurrent_inference": (
                    self.settings.max_concurrent_inference
                ),
                "cpu_thread_budget": self.settings.cpu_thread_budget,
            },
            "resolved_models": dict(self._resolved_models),
            "compute_precision": dict(self._compute_precision),
        }
