from __future__ import annotations

from vidxp.capabilities.actor.results import (
    actor_clusters,
    actor_detections,
    render_actor_result,
)
from vidxp.capabilities.contracts import CapabilityContext
from vidxp.capabilities.actor.schemas import (
    ActorClustersInput,
    ActorClustersOutput,
    ActorDetectionsInput,
    ActorDetectionsOutput,
    ActorRenderInput,
    ActorRenderResult,
)


def clusters_operation(
    context: CapabilityContext,
    _request: ActorClustersInput,
) -> ActorClustersOutput:
    return ActorClustersOutput(
        clusters=actor_clusters(
            context.require_config(),
            storage=context.require_storage(),
        )
    )


def detections_operation(
    context: CapabilityContext,
    request: ActorDetectionsInput,
) -> ActorDetectionsOutput:
    config = context.require_config()
    return ActorDetectionsOutput(
        cluster_id=request.cluster_id,
        detections=actor_detections(
            config,
            request.cluster_id,
            storage=context.require_storage(),
        ),
    )


def render_operation(
    context: CapabilityContext,
    request: ActorRenderInput,
) -> ActorRenderResult:
    config = context.require_config()
    return render_actor_result(
        config,
        request.cluster_id,
        request.input_path,
        request.output_path,
        storage=context.require_storage(),
    )
