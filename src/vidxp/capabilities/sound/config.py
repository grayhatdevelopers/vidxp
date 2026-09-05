from __future__ import annotations

from pydantic import Field, model_validator

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


DEFAULT_SOUND_BATCH_SIZE = 1
DEFAULT_SOUND_INFERENCE_WINDOW_SECONDS = 10.0
DEFAULT_SOUND_INFERENCE_OVERLAP_SECONDS = 2.0
DEFAULT_SOUND_EVIDENCE_WINDOW_SECONDS = 10.0
SOUND_VECTOR_DISTANCE = "ip"


class SoundConfig(CapabilityConfig):
    batch_size: int = Field(
        default=DEFAULT_SOUND_BATCH_SIZE,
        gt=0,
        description="PE-A inference sections processed in one model call.",
    )
    inference_window_seconds: float = Field(
        default=DEFAULT_SOUND_INFERENCE_WINDOW_SECONDS,
        gt=0,
        description="Length of each bounded PE-A inference section.",
    )
    inference_overlap_seconds: float = Field(
        default=DEFAULT_SOUND_INFERENCE_OVERLAP_SECONDS,
        ge=0,
        description="Audio shared by adjacent inference sections.",
    )
    evidence_window_seconds: float = Field(
        default=DEFAULT_SOUND_EVIDENCE_WINDOW_SECONDS,
        gt=0,
        description="Fixed interval returned for each ranked sound match.",
    )

    @model_validator(mode="after")
    def _valid_overlap(self) -> "SoundConfig":
        if self.inference_overlap_seconds >= self.inference_window_seconds:
            raise ValueError(
                "inference_overlap_seconds must be smaller than "
                "inference_window_seconds."
            )
        return self


def sound_config(config: IndexConfig) -> SoundConfig:
    if config.vector_distance != SOUND_VECTOR_DISTANCE:
        raise ValueError(
            "PE-A sound indexes require inner-product vector distance."
        )
    return SoundConfig.model_validate(config.options_for("sound"))
