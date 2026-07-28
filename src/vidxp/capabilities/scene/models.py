from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vidxp.runtime import ModelKey, ModelRuntime


@dataclass(frozen=True)
class SceneModel:
    model: Any
    processor: Any
    device: str


def get_scene_model(
    runtime: ModelRuntime,
    model_id: str,
    revision: str,
) -> SceneModel:
    device = runtime.device_for("scene")
    key = ModelKey("scene", "transformers", model_id, revision, device)

    def load() -> SceneModel:
        from transformers import AutoModel, AutoProcessor

        common = {
            "cache_dir": str(runtime.settings.model_cache),
            "revision": revision,
            "local_files_only": not runtime.settings.allow_model_downloads,
        }
        model = AutoModel.from_pretrained(model_id, **common).to(device)
        model.eval()
        return SceneModel(
            model=model,
            processor=AutoProcessor.from_pretrained(model_id, **common),
            device=device,
        )

    return runtime.get_or_load(key, load)
