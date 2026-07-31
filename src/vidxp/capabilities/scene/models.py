from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from vidxp.ports import ModelRuntimePort
from vidxp.model_contracts import loaded_compute_precision
from vidxp.capabilities.scene.specs import SIGLIP2_MODEL


@dataclass(frozen=True)
class SceneModel:
    model: Any
    processor: Any
    device: str


def get_scene_model(
    runtime: ModelRuntimePort,
    *,
    download: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> SceneModel:
    device = runtime.device_for("scene")
    key = SIGLIP2_MODEL.key(device)

    def load() -> SceneModel:
        from transformers import AutoModel, AutoProcessor

        snapshot = runtime.resolve_model(
            SIGLIP2_MODEL,
            download=download,
            progress=progress,
        )
        if progress is not None:
            progress(
                {
                    "state": "preparing",
                    "stage": "loading_model",
                    "message": f"Loading {SIGLIP2_MODEL.model_id}.",
                }
            )
        common = {
            "cache_dir": str(runtime.model_cache),
            "local_files_only": True,
        }
        model = AutoModel.from_pretrained(snapshot, **common).to(device)
        model.eval()
        runtime.record_compute_precision(
            SIGLIP2_MODEL.capability,
            loaded_compute_precision(
                model,
                fallback=SIGLIP2_MODEL.weights_precision,
            ),
        )
        return SceneModel(
            model=model,
            processor=AutoProcessor.from_pretrained(snapshot, **common),
            device=device,
        )

    return runtime.get_or_load(key, load)
