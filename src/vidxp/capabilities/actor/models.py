from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vidxp.runtime import ModelKey, ModelRuntime


OPENCV_ZOO_REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
YUNET_FILE = "face_detection_yunet_2026may.onnx"
YUNET_SHA256 = "ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0"
SFACE_FILE = "face_recognition_sface_2021dec.onnx"
SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
_RAW_ROOT = (
    "https://raw.githubusercontent.com/opencv/opencv_zoo/"
    f"{OPENCV_ZOO_REVISION}/models"
)


@dataclass(frozen=True)
class ActorModels:
    detector: Any
    recognizer: Any


def _model_path(
    runtime: ModelRuntime,
    *,
    relative_url: str,
    filename: str,
    sha256: str,
) -> str:
    import pooch

    destination = runtime.settings.model_cache / "opencv-zoo"
    if not runtime.settings.allow_model_downloads:
        path = destination / filename
        if not path.is_file():
            raise FileNotFoundError(
                f"Model {filename} is not cached and downloads are disabled."
            )
        if pooch.file_hash(path, alg="sha256") != sha256:
            raise RuntimeError(f"Cached model checksum failed: {filename}.")
        return str(path)
    return pooch.retrieve(
        url=f"{_RAW_ROOT}/{relative_url}/{filename}",
        known_hash=f"sha256:{sha256}",
        fname=filename,
        path=destination,
        progressbar=False,
    )


def get_actor_models(runtime: ModelRuntime) -> ActorModels:
    key = ModelKey(
        "actor",
        "opencv-zoo",
        "yunet+sface",
        OPENCV_ZOO_REVISION,
        "cpu",
    )

    def load() -> ActorModels:
        import cv2

        detector_path = _model_path(
            runtime,
            relative_url="face_detection_yunet",
            filename=YUNET_FILE,
            sha256=YUNET_SHA256,
        )
        recognizer_path = _model_path(
            runtime,
            relative_url="face_recognition_sface",
            filename=SFACE_FILE,
            sha256=SFACE_SHA256,
        )
        return ActorModels(
            detector=cv2.FaceDetectorYN.create(
                detector_path,
                "",
                (320, 320),
                score_threshold=0.9,
                nms_threshold=0.3,
                top_k=5000,
            ),
            recognizer=cv2.FaceRecognizerSF.create(recognizer_path, ""),
        )

    return runtime.get_or_load(key, load)
