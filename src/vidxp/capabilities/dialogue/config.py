from __future__ import annotations

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


class DialogueConfig(CapabilityConfig):
    words_per_phrase: int = Field(default=5, gt=0)
    embedding_batch_size: int = Field(default=128, gt=0)
    transcription_batch_size: int = Field(default=16, gt=0)
    normalize_embeddings: bool = True


def dialogue_config(config: IndexConfig) -> DialogueConfig:
    return DialogueConfig.model_validate(config.options_for("dialogue"))
