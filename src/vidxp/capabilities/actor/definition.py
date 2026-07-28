from __future__ import annotations

from typing import Any, Mapping

from vidxp.capabilities.actor.config import ActorConfig, actor_config
from vidxp.capabilities.actor.indexing import VISUAL_PROCESSOR
from vidxp.capabilities.actor.models import (
    OPENCV_ZOO_REVISION,
    SFACE_FILE,
    SFACE_SHA256,
    YUNET_FILE,
    YUNET_SHA256,
    get_actor_models,
)
from vidxp.capabilities.actor.operations import (
    clusters_operation,
    detections_operation,
    render_operation,
)
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    OperationDefinition,
    PreparationContext,
)
from vidxp.capabilities.actor.schemas import (
    ActorClustersInput,
    ActorClustersOutput,
    ActorDetectionsInput,
    ActorDetectionsOutput,
    ActorRenderInput,
    ActorRenderResult,
)
from vidxp.capabilities.visual import index_capabilities
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.indexing_common import ProgressCallback


def prepare_models(
    context: PreparationContext,
    progress: ProgressCallback | None,
) -> tuple[str, ...]:
    if progress is not None:
        progress(
            {
                "state": "preparing",
                "stage": "actor_models",
                "message": "Preparing OpenCV Zoo YuNet and SFace models.",
            }
        )
    get_actor_models(context.runtime)
    return (YUNET_FILE, SFACE_FILE)

def model_manifest(
    config: IndexConfig,
    _sources: tuple[VideoSource, ...],
) -> Mapping[str, Any]:
    settings = actor_config(config)
    return {
        "actor": {
            "provider": "opencv-zoo",
            "revision": OPENCV_ZOO_REVISION,
            "detector": {"file": YUNET_FILE, "sha256": YUNET_SHA256},
            "recognizer": {"file": SFACE_FILE, "sha256": SFACE_SHA256},
            "match_threshold": settings.match_threshold,
            "detection_threshold": settings.detection_threshold,
            "minimum_detections": settings.minimum_detections,
        }
    }


DEFINITION = CapabilityDefinition(
    name="actor",
    description="Index, inspect, and render actor clusters.",
    extra="actor",
    config_model=ActorConfig,
    collection_name="actor",
    index_stage="visual_indexing",
    execution_group="visual",
    prepares_models=True,
    operations={
        "clusters": OperationDefinition(
            input_model=ActorClustersInput,
            output_model=ActorClustersOutput,
        ),
        "detections": OperationDefinition(
            input_model=ActorDetectionsInput,
            output_model=ActorDetectionsOutput,
        ),
        "render": OperationDefinition(
            input_model=ActorRenderInput,
            output_model=ActorRenderResult,
        ),
    },
)


def create_executor() -> CapabilityExecutor:
    return CapabilityExecutor(
        indexer=index_capabilities,
        index_processor=VISUAL_PROCESSOR,
        operations={
            "clusters": clusters_operation,
            "detections": detections_operation,
            "render": render_operation,
        },
        model_manifest=model_manifest,
        prepare=prepare_models,
    )


PLUGIN = CapabilityPlugin(
    definition=DEFINITION,
    executor_factory=create_executor,
)
