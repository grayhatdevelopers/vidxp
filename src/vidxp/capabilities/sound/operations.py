from __future__ import annotations

from typing import Any, Mapping

from vidxp.capabilities.contracts import (
    CapabilityContext,
    CapabilityIndexResult,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capabilities.schemas import SearchInput, SearchResult
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
    return search_embeddings(
        cleaned,
        "sound",
        sound_embedding(cleaned, runtime),
        config=config,
        required_metadata=REQUIRED_METADATA,
        top_k=top_k,
        video_id=video_id,
        query_id=query_id,
        filters=filters,
        storage=storage,
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
