from vidxp.model_contracts import ModelSpec


SIGLIP2_MODEL = ModelSpec(
    capability="scene",
    provider="transformers",
    model_id="google/siglip2-base-patch16-224",
    revision="75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2",
    download_size_bytes=1_539_458_338,
    weights_file="model.safetensors",
    weights_sha256=(
        "612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b"
    ),
    license="Apache-2.0",
    weights_precision="float32",
)
