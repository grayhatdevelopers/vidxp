from __future__ import annotations

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


class SoundConfig(CapabilityConfig):
    batch_size: int = Field(default=1, gt=0)
    window_seconds: float = Field(default=10.0, gt=0, le=10.0)


def sound_config(config: IndexConfig) -> SoundConfig:
    return SoundConfig.model_validate(config.options_for("sound"))
