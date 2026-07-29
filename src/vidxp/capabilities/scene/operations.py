from __future__ import annotations

from typing import Any, Mapping

from vidxp.capabilities.contracts import CapabilityContext
from vidxp.capabilities.scene.models import get_scene_model
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.capabilities.search import search_embeddings
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


def scene_embedding(
    query: str,
    runtime: ModelRuntimePort,
) -> list[float]:
    import torch

    provider = get_scene_model(runtime)
    inputs = provider.processor(
        text=[query],
        padding="max_length",
        return_tensors="pt",
    )
    inputs = {name: value.to(provider.device) for name, value in inputs.items()}
    with torch.inference_mode():
        features = provider.model.get_text_features(**inputs).pooler_output
        features = torch.nn.functional.normalize(features, dim=-1)
    return features.cpu().numpy().tolist()[0]


def search_scene(
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
        "scene",
        scene_embedding(cleaned, runtime),
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
    return search_scene(
        request.query,
        config=config,
        top_k=request.top_k,
        video_id=request.media_id or config.video_id,
        runtime=context.runtime,
        storage=context.require_storage(),
    )
