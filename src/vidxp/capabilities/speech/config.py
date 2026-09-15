from __future__ import annotations

from typing import Literal

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig

SegmentationMode = Literal[
    "fixed_words",
    "overlapping_windows",
    "sentence",
]


class SpeechConfig(CapabilityConfig):
    """Speech indexing and search settings.

    Changing segmentation fields changes ``IndexConfig.fingerprint()`` and
    therefore requires a new index generation rather than silently rewriting
    existing segment IDs.
    """

    words_per_phrase: int = Field(default=5, gt=0)
    window_stride_words: int = Field(default=2, gt=0)
    segmentation_mode: SegmentationMode = "fixed_words"
    embedding_batch_size: int = Field(default=128, gt=0)
    transcription_batch_size: int = Field(default=16, gt=0)
    normalize_embeddings: bool = True


def speech_config(config: IndexConfig) -> SpeechConfig:
    return SpeechConfig.model_validate(config.options_for("speech"))
