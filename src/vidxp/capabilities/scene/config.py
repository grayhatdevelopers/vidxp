from __future__ import annotations

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


class SceneConfig(CapabilityConfig):
    batch_size: int = Field(default=32, gt=0)
    sample_fps: float = Field(default=1.0, gt=0)


def scene_config(config: IndexConfig) -> SceneConfig:
    return SceneConfig.model_validate(config.options_for("scene"))
