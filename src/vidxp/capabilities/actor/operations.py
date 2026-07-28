from __future__ import annotations

from vidxp.capabilities.actor.results import (
    actor_clusters,
    actor_detections,
)
from vidxp.capabilities.contracts import CapabilityContext
from vidxp.capabilities.actor.schemas import (
    ActorClustersInput,
    ActorClustersOutput,
    ActorDetectionsInput,
    ActorDetectionsOutput,
)


def clusters_operation(
    context: CapabilityContext,
    _request: ActorClustersInput,
) -> ActorClustersOutput:
    return actor_clusters(
        context.require_config(),
        storage=context.require_storage(),
        page_size=_request.page_size,
        cursor=_request.cursor,
    )


def detections_operation(
    context: CapabilityContext,
    request: ActorDetectionsInput,
) -> ActorDetectionsOutput:
    config = context.require_config()
    return actor_detections(
        config,
        request.cluster_id,
        storage=context.require_storage(),
        page_size=request.page_size,
        cursor=request.cursor,
    )
