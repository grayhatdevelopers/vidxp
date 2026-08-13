from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from vidxp.capabilities.videoprism.specs import VIDEOPRISM_MODEL
from vidxp.core.indexing_common import report_preparation
from vidxp.model_contracts import loaded_compute_precision
from vidxp.ports import ModelRuntimePort


@dataclass(frozen=True)
class VideoPrismModel:
    model: Any
    processor: Any
    device: str


def normalize_pooled_output(features: Any) -> Any:
    import torch

    return torch.nn.functional.normalize(features.flatten(start_dim=1), dim=-1)


def get_videoprism_model(
    runtime: ModelRuntimePort,
    *,
    download: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> VideoPrismModel:
    device = runtime.device_for("videoprism")
    key = VIDEOPRISM_MODEL.key(device)

    def load() -> VideoPrismModel:
        from transformers import VideoPrismClipModel, VideoPrismProcessor

        snapshot = runtime.resolve_model(
            VIDEOPRISM_MODEL,
            download=download,
            progress=progress,
        )
        report_preparation(
            progress,
            "loading_model",
            f"Loading {VIDEOPRISM_MODEL.model_id}.",
        )
        common = {
            "local_files_only": True,
        }
        model = VideoPrismClipModel.from_pretrained(snapshot, **common).to(
            device
        )
        model.eval()
        runtime.record_compute_precision(
            VIDEOPRISM_MODEL.capability,
            loaded_compute_precision(
                model,
                fallback=VIDEOPRISM_MODEL.weights_precision,
            ),
        )
        return VideoPrismModel(
            model=model,
            processor=VideoPrismProcessor.from_pretrained(snapshot, **common),
            device=device,
        )

    return runtime.get_or_load(key, load)
