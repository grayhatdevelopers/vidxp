from __future__ import annotations

from typing import Any, Callable

from vidxp.ports import ModelRuntimePort
from vidxp.model_contracts import loaded_compute_precision
from vidxp.capabilities.dialogue.specs import (
    FASTER_WHISPER_MODEL,
    QWEN3_EMBEDDING_MODEL,
    whisper_compute_type,
)


def get_embedder(
    runtime: ModelRuntimePort,
    *,
    download: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    device = runtime.device_for("dialogue.embedding")
    key = QWEN3_EMBEDDING_MODEL.key(device)

    def load() -> Any:
        from sentence_transformers import SentenceTransformer

        snapshot = runtime.resolve_model(
            QWEN3_EMBEDDING_MODEL,
            download=download,
            progress=progress,
        )
        if progress is not None:
            progress(
                {
                    "state": "preparing",
                    "stage": "loading_model",
                    "message": (
                        f"Loading {QWEN3_EMBEDDING_MODEL.model_id}."
                    ),
                }
            )
        model = SentenceTransformer(
            str(snapshot),
            device=device,
            cache_folder=str(runtime.model_cache),
            local_files_only=True,
        )
        runtime.record_compute_precision(
            QWEN3_EMBEDDING_MODEL.capability,
            loaded_compute_precision(
                model,
                fallback=QWEN3_EMBEDDING_MODEL.weights_precision,
            ),
        )
        return model

    return runtime.get_or_load(key, load)


def get_whisper_model(
    runtime: ModelRuntimePort,
    *,
    download: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    device = runtime.device_for("dialogue.transcription")
    compute_type = whisper_compute_type(device)
    key = FASTER_WHISPER_MODEL.key(f"{device}:{compute_type}")

    def load() -> Any:
        from faster_whisper import WhisperModel

        snapshot = runtime.resolve_model(
            FASTER_WHISPER_MODEL,
            download=download,
            progress=progress,
        )
        if progress is not None:
            progress(
                {
                    "state": "preparing",
                    "stage": "loading_model",
                    "message": (
                        f"Loading {FASTER_WHISPER_MODEL.model_id}."
                    ),
                }
            )
        model = WhisperModel(
            str(snapshot),
            device=device.split(":", 1)[0],
            device_index=(
                int(device.split(":", 1)[1])
                if ":" in device
                else 0
            ),
            compute_type=compute_type,
            cpu_threads=runtime.cpu_thread_budget,
            download_root=str(runtime.model_cache),
            local_files_only=True,
        )
        runtime.record_compute_precision(
            FASTER_WHISPER_MODEL.capability,
            compute_type,
        )
        return model

    return runtime.get_or_load(key, load)
