from __future__ import annotations

import math
from typing import Any, Mapping

from vidxp.capabilities.contracts import (
    CapabilityContext,
    CapabilityIndexResult,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capabilities.schemas import SearchHit, SearchInput, SearchResult
from vidxp.capabilities.search import search_embeddings
from vidxp.capabilities.sound.config import sound_config
from vidxp.capabilities.sound.indexing import index_sound
from vidxp.capabilities.sound.models import get_sound_model
from vidxp.capabilities.sound.specs import PE_A_FRAME_INTERVAL_SECONDS
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
        "section_index",
        "frame_index",
        "evidence_index",
        "timestamp",
        "frame_end",
        "modality",
    }
)

FRAME_REPRESENTATION = "frame"


def _collapse_evidence_windows(
    result: SearchResult,
    *,
    top_k: int,
) -> SearchResult:
    selected: list[SearchHit] = []
    seen: set[tuple[str, float, float]] = set()
    for hit in result.hits:
        key = (hit.media_id, hit.start, hit.end)
        if key in seen:
            continue
        seen.add(key)
        selected.append(hit.model_copy(update={"rank": len(selected) + 1}))
        if len(selected) == top_k:
            break
    return result.model_copy(update={"hits": tuple(selected)})


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
    explicit_filters = dict(filters or {})
    representation = explicit_filters.get("representation")
    if representation not in {None, FRAME_REPRESENTATION}:
        raise ValueError("Sound search only supports PE-A frame records.")
    explicit_filters["representation"] = FRAME_REPRESENTATION
    settings = sound_config(config)
    frames_per_window = math.ceil(
        settings.evidence_window_seconds / PE_A_FRAME_INTERVAL_SECONDS
    )
    # Each evidence window contains at most this many frame records. Fetching
    # top_k times that bound guarantees top_k distinct windows when they exist.
    ranked_frames = search_embeddings(
        cleaned,
        "sound",
        embedding,
        config=config,
        required_metadata=REQUIRED_METADATA,
        top_k=top_k * frames_per_window,
        video_id=video_id,
        query_id=query_id,
        filters=explicit_filters,
        storage=storage,
    )
    return _collapse_evidence_windows(
        ranked_frames,
        top_k=top_k,
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
