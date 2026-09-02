from __future__ import annotations

from typing import Any, Mapping

from vidxp.capabilities.contracts import (
    CapabilityContext,
    CapabilityIndexResult,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capabilities.schemas import SearchHit, SearchInput, SearchResult
from vidxp.capabilities.search import search_embeddings
from vidxp.capabilities.sound.indexing import index_sound
from vidxp.capabilities.sound.models import get_sound_model
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    VideoSource,
)
from vidxp.core.indexing_common import ProgressCallback
from vidxp.ports import IndexStore, ModelRuntimePort


REQUIRED_METADATA = frozenset(
    {
        "dataset",
        "split",
        "run_id",
        "video_id",
        "source_id",
        "start",
        "end",
        "representation",
        "window_index",
        "modality",
    }
)

GLOBAL_REPRESENTATION = "window"
LOCAL_REPRESENTATION = "activation"


def _activation_scope(
    windows: tuple[SearchHit, ...],
    *,
    video_id: str | None,
) -> dict[str, Any]:
    selected = tuple(
        dict.fromkeys(
            (
                hit.media_id,
                int(hit.metadata["window_index"]),
            )
            for hit in windows
        )
    )
    filters: dict[str, Any] = {"representation": LOCAL_REPRESENTATION}
    media_ids = {media_id for media_id, _window_index in selected}
    if len(media_ids) == 1:
        selected_media_id = next(iter(media_ids))
        if video_id is None:
            filters["video_id"] = selected_media_id
        window_indices = [window_index for _media_id, window_index in selected]
        filters["window_index"] = (
            window_indices[0]
            if len(window_indices) == 1
            else {"$in": window_indices}
        )
        return filters
    filters["$or"] = [
        {
            "$and": [
                {"video_id": selected_media_id},
                {"window_index": window_index},
            ]
        }
        for selected_media_id, window_index in selected
    ]
    return filters


def _attach_window_context(
    activations: SearchResult,
    windows: tuple[SearchHit, ...],
) -> SearchResult:
    by_window = {
        (hit.media_id, int(hit.metadata["window_index"])): hit for hit in windows
    }
    hits = []
    for activation in activations.hits:
        key = (
            activation.media_id,
            int(activation.metadata["window_index"]),
        )
        window = by_window[key]
        hits.append(
            activation.model_copy(
                update={
                    "metadata": {
                        **activation.metadata,
                        "context_source_id": window.source_id,
                        "context_start": window.start,
                        "context_end": window.end,
                        "context_rank": window.rank,
                    }
                }
            )
        )
    return activations.model_copy(update={"hits": tuple(hits)})


def index_capability(
    source: VideoSource,
    *,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
    registry: CapabilityRegistry,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None = None,
    modalities: tuple[str, ...] = ("sound",),
) -> CapabilityIndexResult:
    if modalities != ("sound",):
        raise ValueError("The sound indexer only accepts sound.")
    return CapabilityIndexResult(
        summary=index_sound(
            source,
            config=config,
            storage=storage,
            cancellation=cancellation,
            runtime=runtime,
            progress=progress,
        )
    )


def sound_embedding(query: str, runtime: ModelRuntimePort) -> list[float]:
    return get_sound_model(runtime).encode_text(query)


def search_sound(
    query: str,
    *,
    config: IndexConfig,
    runtime: ModelRuntimePort,
    top_k: int = 10,
    video_id: str | None = None,
    query_id: str | None = None,
    filters: Mapping[str, Any] | None = None,
    storage: IndexStore,
) -> SearchResult:
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("Search query must not be empty.")
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero.")
    embedding = sound_embedding(cleaned, runtime)
    if filters:
        explicit_filters = dict(filters)
        explicit_filters.setdefault("representation", GLOBAL_REPRESENTATION)
        return search_embeddings(
            cleaned,
            "sound",
            embedding,
            config=config,
            required_metadata=REQUIRED_METADATA,
            top_k=top_k,
            video_id=video_id,
            query_id=query_id,
            filters=explicit_filters,
            storage=storage,
        )

    # FineLAP Sections 3.2–3.3 train global and local audio outputs separately.
    # Global matches select regions; only local distances rank the final hits.
    windows = search_embeddings(
        cleaned,
        "sound",
        embedding,
        config=config,
        required_metadata=REQUIRED_METADATA,
        top_k=top_k,
        video_id=video_id,
        query_id=query_id,
        filters={"representation": GLOBAL_REPRESENTATION},
        storage=storage,
    )
    if not windows.hits:
        return windows
    activations = search_embeddings(
        cleaned,
        "sound",
        embedding,
        config=config,
        required_metadata=REQUIRED_METADATA,
        top_k=top_k,
        video_id=video_id,
        query_id=windows.query_id,
        filters=_activation_scope(windows.hits, video_id=video_id),
        storage=storage,
    )
    return (
        _attach_window_context(activations, windows.hits)
        if activations.hits
        else windows
    )


def search_operation(
    context: CapabilityContext,
    request: SearchInput,
) -> SearchResult:
    config = context.require_config()
    return search_sound(
        request.query,
        config=config,
        top_k=request.top_k,
        video_id=request.media_id or config.video_id,
        runtime=context.runtime,
        storage=context.require_storage(),
    )
