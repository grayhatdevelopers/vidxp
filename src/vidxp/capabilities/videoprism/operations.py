from __future__ import annotations

from typing import Any, Mapping

from vidxp.capabilities.contracts import CapabilityContext
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.capabilities.search import search_embeddings
from vidxp.capabilities.videoprism.models import (
    get_videoprism_model,
    normalize_pooled_output,
)
from vidxp.core.contracts import IndexConfig
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
        "frame_index",
        "timestamp",
        "fps",
        "duration",
        "modality",
    }
)


def videoprism_embedding(
    query: str,
    runtime: ModelRuntimePort,
) -> list[float]:
    import torch

    provider = get_videoprism_model(runtime)
    inputs = provider.processor(
        text=[query],
        padding="max_length",
        max_length=64,
        truncation=True,
        return_tensors="pt",
    )
    inputs = {name: value.to(provider.device) for name, value in inputs.items()}
    with torch.inference_mode():
        features = provider.model.get_text_features(**inputs).pooler_output
        features = normalize_pooled_output(features)
    return features.cpu().numpy().tolist()[0]


def search_videoprism(
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
        "videoprism",
        videoprism_embedding(cleaned, runtime),
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
    return search_videoprism(
        request.query,
        config=config,
        top_k=request.top_k,
        video_id=request.media_id or config.video_id,
        runtime=context.runtime,
        storage=context.require_storage(),
    )
