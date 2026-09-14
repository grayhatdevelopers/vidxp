from vidxp.model_contracts import ModelSpec


QWEN3_EMBEDDING_MODEL = ModelSpec(
    capability="speech.embedding",
    provider="sentence-transformers",
    model_id="Qwen/Qwen3-Embedding-0.6B",
    revision="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
    download_size_bytes=1_207_489_041,
    weights_file="model.safetensors",
    weights_sha256=(
        "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd"
    ),
    license="Apache-2.0",
    weights_precision="bfloat16",
)

FASTER_WHISPER_MODEL = ModelSpec(
    capability="speech.transcription",
    provider="faster-whisper",
    model_id="dropbox-dash/faster-whisper-large-v3-turbo",
    revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    download_size_bytes=1_621_668_947,
    weights_file="model.bin",
    weights_sha256=(
        "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"
    ),
    license="MIT",
    weights_precision="float16",
)


def whisper_compute_type(device: str) -> str:
    return "float16" if device.startswith("cuda") else "int8"
