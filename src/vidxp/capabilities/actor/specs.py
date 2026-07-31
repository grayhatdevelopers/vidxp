from vidxp.model_contracts import ArtifactSpec, ModelKey


OPENCV_ZOO_REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
_MODEL_ROOT = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    f"{OPENCV_ZOO_REVISION}/models"
)

YUNET_MODEL = ArtifactSpec(
    capability="actor.detector",
    provider="opencv-zoo",
    model_id="yunet",
    revision=OPENCV_ZOO_REVISION,
    download_size_bytes=229_738,
    url=f"{_MODEL_ROOT}/face_detection_yunet/"
    "face_detection_yunet_2026may.onnx",
    filename="face_detection_yunet_2026may.onnx",
    sha256="ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0",
    license="MIT",
    weights_precision="float32",
)

SFACE_MODEL = ArtifactSpec(
    capability="actor.recognizer",
    provider="opencv-zoo",
    model_id="sface",
    revision=OPENCV_ZOO_REVISION,
    download_size_bytes=38_696_353,
    url=f"{_MODEL_ROOT}/face_recognition_sface/"
    "face_recognition_sface_2021dec.onnx",
    filename="face_recognition_sface_2021dec.onnx",
    sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    license="Apache-2.0",
    weights_precision="float32",
)


def actor_model_key(device: str) -> ModelKey:
    specs = (YUNET_MODEL, SFACE_MODEL)
    providers = {spec.provider for spec in specs}
    revisions = {spec.revision for spec in specs}
    if len(providers) != 1 or len(revisions) != 1:
        raise RuntimeError("Actor model artifacts must share provider and revision.")
    return ModelKey(
        capability=YUNET_MODEL.capability.split(".", 1)[0],
        provider=YUNET_MODEL.provider,
        model_id="+".join(spec.model_id for spec in specs),
        revision=YUNET_MODEL.revision,
        device=device,
    )
