from __future__ import annotations

from typing import Any, Mapping

from vidxp.capabilities.actor.config import ActorConfig, actor_config
from vidxp.capabilities.actor.indexing import VISUAL_PROCESSOR
from vidxp.capabilities.actor.models import (
    get_actor_models,
)
from vidxp.capabilities.actor.operations import (
    cluster_operation,
    clusters_operation,
    detections_operation,
)
from vidxp.capabilities.actor.specs import SFACE_MODEL, YUNET_MODEL
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    OperationDefinition,
    PreparationContext,
    module_import_check,
)
from vidxp.capabilities.actor.schemas import (
    ActorClusterInput,
    ActorClusterSummary,
    ActorClustersInput,
    ActorClustersOutput,
    ActorDetectionsInput,
    ActorDetectionsOutput,
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
    get_actor_models(context.runtime, download=True, progress=progress)
    return (YUNET_MODEL.filename, SFACE_MODEL.filename)


def model_manifest(
    config: IndexConfig,
    _sources: tuple[VideoSource, ...],
) -> Mapping[str, Any]:
    settings = actor_config(config)
    return {
        "actor": {
            "models": {
                "detector": YUNET_MODEL.identity(),
                "recognizer": SFACE_MODEL.identity(),
            },
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
    model_specs=(YUNET_MODEL, SFACE_MODEL),
    operations={
        "cluster": OperationDefinition(
            input_model=ActorClusterInput,
            output_model=ActorClusterSummary,
            public=False,
        ),
        "clusters": OperationDefinition(
            input_model=ActorClustersInput,
            output_model=ActorClustersOutput,
        ),
        "detections": OperationDefinition(
            input_model=ActorDetectionsInput,
            output_model=ActorDetectionsOutput,
        ),
    },
)


def create_executor() -> CapabilityExecutor:
    return CapabilityExecutor(
        indexer=index_capabilities,
        index_processor=VISUAL_PROCESSOR,
        operations={
            "cluster": cluster_operation,
            "clusters": clusters_operation,
            "detections": detections_operation,
        },
        model_manifest=model_manifest,
        prepare=prepare_models,
        runtime_checks=(
            module_import_check(
                "OpenCV import",
                "cv2",
                "FaceDetectorYN",
                "FaceRecognizerSF",
            ),
            module_import_check("Pooch import", "pooch", "retrieve"),
        ),
    )


PLUGIN = CapabilityPlugin(
    definition=DEFINITION,
    executor_factory=create_executor,
)
