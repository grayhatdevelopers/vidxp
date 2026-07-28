from __future__ import annotations

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


class SceneConfig(CapabilityConfig):
    batch_size: int = Field(default=32, gt=0)
    model: str = Field(
        default="google/siglip2-base-patch16-224",
        min_length=1,
    )
    revision: str = Field(
        default="75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2",
        min_length=1,
    )


def scene_config(config: IndexConfig) -> SceneConfig:
    return SceneConfig.model_validate(config.options_for("scene"))
