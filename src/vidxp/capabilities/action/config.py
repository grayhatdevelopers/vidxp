from __future__ import annotations

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


class VideoPrismConfig(CapabilityConfig):
    batch_size: int = Field(default=1, gt=0)
    sample_fps: float = Field(default=2.0, gt=0)


def videoprism_config(config: IndexConfig) -> VideoPrismConfig:
    return VideoPrismConfig.model_validate(config.options_for("action"))
