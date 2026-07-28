from __future__ import annotations

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


class DialogueConfig(CapabilityConfig):
    words_per_phrase: int = Field(default=5, gt=0)
    embedding_batch_size: int = Field(default=128, gt=0)
    transcription_batch_size: int = Field(default=16, gt=0)
    normalize_embeddings: bool = True
    sentence_model: str = Field(
        default="Qwen/Qwen3-Embedding-0.6B",
        min_length=1,
    )
    sentence_revision: str = Field(
        default="97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        min_length=1,
    )
    whisper_model: str = Field(
        default="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        min_length=1,
    )
    whisper_revision: str = Field(
        default="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        min_length=1,
    )


def dialogue_config(config: IndexConfig) -> DialogueConfig:
    return DialogueConfig.model_validate(config.options_for("dialogue"))
