from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vidxp.ports import ModelRuntimePort
from vidxp.capabilities.actor.specs import (
    SFACE_MODEL,
    YUNET_MODEL,
    actor_model_key,
)


@dataclass(frozen=True)
class ActorModels:
    detector: Any
    recognizer: Any


def get_actor_models(runtime: ModelRuntimePort) -> ActorModels:
    key = actor_model_key(runtime.device_for("actor"))

    def load() -> ActorModels:
        import cv2

        detector_path = runtime.resolve_artifact(YUNET_MODEL)
        recognizer_path = runtime.resolve_artifact(SFACE_MODEL)
        models = ActorModels(
            detector=cv2.FaceDetectorYN.create(
                str(detector_path),
                "",
                (320, 320),
                score_threshold=0.9,
                nms_threshold=0.3,
                top_k=5000,
            ),
            recognizer=cv2.FaceRecognizerSF.create(str(recognizer_path), ""),
        )
        runtime.record_compute_precision(
            YUNET_MODEL.capability,
            YUNET_MODEL.weights_precision,
        )
        runtime.record_compute_precision(
            SFACE_MODEL.capability,
            SFACE_MODEL.weights_precision,
        )
        return models

    return runtime.get_or_load(key, load)
