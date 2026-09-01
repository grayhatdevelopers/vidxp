from vidxp.model_contracts import ArtifactSpec, ModelSpec


FINELAP_MODEL = ModelSpec(
    capability="sound.embedding",
    provider="transformers",
    model_id="AndreasXi/FineLAP",
    revision="b419aa22947d29907a5567f21b81bf3b39a40449",
    download_size_bytes=980_404_741,
    weights_file="model.safetensors",
    weights_sha256=(
        "13b9646c9f9d48513c0145bed75e654179e83f0fd8d49ed4ffc5d6b8f3353fb4"
    ),
    license="MIT (model card)",
    weights_precision="float32",
)

ROBERTA_REVISION = "e2da8e2f811d1448a5b465c236feacd80ffbac7b"
ROBERTA_CONFIG = ArtifactSpec(
    capability="sound.text_encoder.config",
    provider="finelap-tokenizer",
    model_id="FacebookAI/roberta-base config",
    revision=ROBERTA_REVISION,
    download_size_bytes=481,
    url=(
        "https://huggingface.co/FacebookAI/roberta-base/resolve/"
        f"{ROBERTA_REVISION}/config.json"
    ),
    filename="config.json",
    sha256=(
        "ef0185e2aae6e06c5f105a285006952c340e20c7dbf43c86ec82601b13fc45e9"
    ),
    license="MIT",
    weights_precision="not applicable",
)
ROBERTA_VOCAB = ArtifactSpec(
    capability="sound.tokenizer.vocab",
    provider="finelap-tokenizer",
    model_id="FacebookAI/roberta-base vocab",
    revision=ROBERTA_REVISION,
    download_size_bytes=898_823,
    url=(
        "https://huggingface.co/FacebookAI/roberta-base/resolve/"
        f"{ROBERTA_REVISION}/vocab.json"
    ),
    filename="vocab.json",
    sha256=(
        "9e7f63c2d15d666b52e21d250d2e513b87c9b713cfa6987a82ed89e5e6e50655"
    ),
    license="MIT",
    weights_precision="not applicable",
)
ROBERTA_MERGES = ArtifactSpec(
    capability="sound.tokenizer.merges",
    provider="finelap-tokenizer",
    model_id="FacebookAI/roberta-base merges",
    revision=ROBERTA_REVISION,
    download_size_bytes=456_318,
    url=(
        "https://huggingface.co/FacebookAI/roberta-base/resolve/"
        f"{ROBERTA_REVISION}/merges.txt"
    ),
    filename="merges.txt",
    sha256=(
        "1ce1664773c50f3e0cc8842619a93edc4624525b728b188a9e0be33b7726adc5"
    ),
    license="MIT",
    weights_precision="not applicable",
)

SOUND_MODEL_SPECS = (
    FINELAP_MODEL,
    ROBERTA_CONFIG,
    ROBERTA_VOCAB,
    ROBERTA_MERGES,
)
