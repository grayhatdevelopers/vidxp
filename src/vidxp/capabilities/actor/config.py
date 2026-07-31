from __future__ import annotations

from pydantic import Field

from vidxp.capabilities.contracts import CapabilityConfig
from vidxp.core.contracts import IndexConfig


class ActorConfig(CapabilityConfig):
    batch_size: int = Field(default=16, gt=0)
    match_threshold: float = Field(default=0.363, gt=0, lt=1)
    detection_threshold: float = Field(default=0.9, gt=0, lt=1)
    minimum_detections: int = Field(default=4, gt=0)


def actor_config(config: IndexConfig) -> ActorConfig:
    return ActorConfig.model_validate(config.options_for("actor"))
