from vidxp.model_contracts import ModelSpec


VIDEOPRISM_MODEL = ModelSpec(
    capability="action",
    provider="transformers",
    model_id="google/videoprism-lvt-base-f16r288",
    revision="fb6de9f0eb7bc285be86bdca1cf7daa3e3ef51ff",
    download_size_bytes=993_993_146,
    weights_file="model.safetensors",
    weights_sha256=(
        "7d64ac2364d3473c0dd9fde35fb09e3cfb3b43153c3e9af79d7e49f1c8387cf5"
    ),
    license="Apache-2.0",
    weights_precision="float32",
)
