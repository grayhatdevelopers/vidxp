from __future__ import annotations

import platform
from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable

from vidxp.application_models import RuntimeProfile
from vidxp.settings import VidXPSettings


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
        if platform.system() == "Darwin" and mps_available:
            torch_device = "mps"
        else:
            torch_device = "cpu"
    elif normalized == "mps":
        if not mps_available:
            raise RuntimeError("MPS was requested but is unavailable.")
        torch_device = "mps"
    elif normalized.startswith("cuda"):
        if not cuda_available:
            raise RuntimeError("CUDA was requested but is unavailable.")
        if ":" in normalized:
            import torch

            index = int(normalized.split(":", 1)[1])
            if index >= torch.cuda.device_count():
                raise RuntimeError(
                    f"CUDA device {index} was requested but only "
                    f"{torch.cuda.device_count()} device(s) are available."
                )
        torch_device = normalized
    else:
        torch_device = "cpu"
    return RuntimeProfile(
        requested=normalized,
        torch_device=torch_device,
        transcription_device=(
            normalized if normalized.startswith("cuda") else "cpu"
        ),
        mps_available=mps_available,
        cuda_available=cuda_available,
    )


@dataclass(frozen=True)
class ModelKey:
    capability: str
    provider: str
    model_id: str
    revision: str
    device: str


class ModelRuntime:
    """One injected model cache and backend resolver for all capabilities."""

    def __init__(self, settings: VidXPSettings) -> None:
        self.settings = settings
        self.backends = resolve_backends(settings.runtime_backend)
        self._resources: OrderedDict[ModelKey, Any] = OrderedDict()
        self._lock = RLock()

    def device_for(self, capability: str) -> str:
        if capability == "dialogue.transcription":
            return self.backends.transcription_device
        if capability == "actor":
            return self.backends.actor_device
        return self.backends.torch_device

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
            resource = loader()
            self._resources[key] = resource
            while len(self._resources) > self.settings.max_loaded_models:
                self._resources.popitem(last=False)
            return resource

    def clear(self) -> None:
        with self._lock:
            self._resources.clear()
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
            "compute": {
                "scene": "float32",
                "dialogue_embedding": "float32",
                "dialogue_transcription": (
                    "float16"
                    if self.backends.transcription_device.startswith("cuda")
                    else "int8"
                ),
                "actor": "float32",
            },
        }
