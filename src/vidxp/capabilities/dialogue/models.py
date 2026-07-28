from __future__ import annotations

from typing import Any

from vidxp.runtime import ModelKey, ModelRuntime


def get_embedder(
    runtime: ModelRuntime,
    model_id: str,
    revision: str,
) -> Any:
    device = runtime.device_for("dialogue.embedding")
    key = ModelKey("dialogue", "sentence-transformers", model_id, revision, device)

    def load() -> Any:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(
            model_id,
            device=device,
            cache_folder=str(runtime.settings.model_cache),
            revision=revision,
            local_files_only=not runtime.settings.allow_model_downloads,
        )

    return runtime.get_or_load(key, load)


def get_whisper_model(
    runtime: ModelRuntime,
    model_id: str,
    revision: str,
) -> Any:
    device = runtime.device_for("dialogue.transcription")
    compute_type = "float16" if device.startswith("cuda") else "int8"
    key = ModelKey(
        "dialogue",
        "faster-whisper",
        model_id,
        revision,
        f"{device}:{compute_type}",
    )

    def load() -> Any:
        from faster_whisper import WhisperModel

        return WhisperModel(
            model_id,
            device=device.split(":", 1)[0],
            device_index=(
                int(device.split(":", 1)[1])
                if ":" in device
                else 0
            ),
            compute_type=compute_type,
            download_root=str(runtime.settings.model_cache),
            local_files_only=not runtime.settings.allow_model_downloads,
            revision=revision,
        )

    return runtime.get_or_load(key, load)
