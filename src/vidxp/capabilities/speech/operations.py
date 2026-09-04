from __future__ import annotations

import re
from typing import Any, Mapping

from vidxp.capabilities.contracts import (
    CapabilityContext,
    CapabilityIndexResult,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.capabilities.speech.config import speech_config
from vidxp.capabilities.speech.indexing import index_speech
from vidxp.capabilities.speech.models import get_embedder
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.capabilities.search import hits_from_rows, stable_query_id
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
        "text",
        "phrase_id",
        "modality",
    }
)

_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_KEYWORD_PAGE_SIZE = 256
# Exact lexical matches outrank typical semantic distances (lower is better).
_EXACT_DISTANCE = 0.0
_KEYWORD_DISTANCE = 0.05


def index_capability(
    source: VideoSource,
    *,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
    registry: CapabilityRegistry,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None = None,
    modalities: tuple[str, ...] = ("speech",),
) -> CapabilityIndexResult:
    if modalities != ("speech",):
        raise ValueError("The speech indexer only accepts speech.")
    return CapabilityIndexResult(
        summary=index_speech(
            source,
            config=config,
            storage=storage,
            cancellation=cancellation,
            progress=progress,
            runtime=runtime,
        )
    )


def speech_embedding(
    query: str,
    config: IndexConfig,
    runtime: ModelRuntimePort,
) -> list[float]:
    settings = speech_config(config)
    encoder = get_embedder(runtime)
    encoded = encoder.encode_query(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=settings.normalize_embeddings,
    )
    return encoded[0].tolist()


def _query_tokens(query: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _TOKEN_PATTERN.findall(query.casefold())
        if len(token) > 1
    )


def _keyword_distance(query: str, text: str) -> float | None:
    """Return a lexical distance, or None when the text does not match."""

    query_tokens = _query_tokens(query)
    if not query_tokens:
        return None
    text_tokens = _query_tokens(text)
    if not text_tokens:
        return None
    window = len(query_tokens)
    for offset in range(len(text_tokens) - window + 1):
        if text_tokens[offset:offset + window] == query_tokens:
            return _EXACT_DISTANCE
    if set(query_tokens) <= set(text_tokens):
        return _KEYWORD_DISTANCE
    return None


def _iter_speech_metadata(
    storage: IndexStore,
    *,
    video_id: str | None,
    filters: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = storage.records(
            "speech",
            video_id=video_id,
            filters=filters,
            limit=_KEYWORD_PAGE_SIZE,
            offset=offset,
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < _KEYWORD_PAGE_SIZE:
            break
        offset += len(batch)
    return rows


def keyword_search_rows(
    query: str,
    *,
    storage: IndexStore,
    video_id: str | None = None,
    filters: Mapping[str, Any] | None = None,
    top_k: int,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for metadata in _iter_speech_metadata(
        storage,
        video_id=video_id,
        filters=filters,
    ):
        text = metadata.get("text")
        if not isinstance(text, str):
            continue
        distance = _keyword_distance(query, text)
        if distance is None:
            continue
        source_id = str(metadata.get("source_id") or "")
        if not source_id:
            continue
        annotated = dict(metadata)
        annotated["match_kind"] = (
            "exact" if distance == _EXACT_DISTANCE else "keyword"
        )
        matches.append(
            {
                "source_id": source_id,
                "metadata": annotated,
                "raw_distance": distance,
            }
        )
    matches.sort(key=lambda row: (row["raw_distance"], row["source_id"]))
    return matches[:top_k]


def _merge_search_rows(
    *groups: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    preferred = {"exact", "keyword"}
    for group in groups:
        for row in group:
            source_id = str(row["source_id"])
            current = best.get(source_id)
            if current is None or float(row["raw_distance"]) < float(
                current["raw_distance"]
            ):
                best[source_id] = row
                continue
            if float(row["raw_distance"]) != float(current["raw_distance"]):
                continue
            if (
                row["metadata"].get("match_kind") in preferred
                and current["metadata"].get("match_kind") not in preferred
            ):
                best[source_id] = row
    ordered = sorted(
        best.values(),
        key=lambda row: (row["raw_distance"], row["source_id"]),
    )
    return ordered[:top_k]


def search_speech(
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
    if "speech" not in config.enabled_modalities:
        raise ValueError(
            "The speech modality is not present in this index run."
        )
    semantic_rows = storage.query(
        "speech",
        speech_embedding(cleaned, config, runtime),
        top_k=top_k,
        video_id=video_id,
        filters=filters,
    )
    for row in semantic_rows:
        metadata = dict(row["metadata"])
        metadata.setdefault("match_kind", "semantic")
        row["metadata"] = metadata

    lexical_rows = keyword_search_rows(
        cleaned,
        storage=storage,
        video_id=video_id,
        filters=filters,
        top_k=top_k,
    )
    merged = _merge_search_rows(semantic_rows, lexical_rows, top_k=top_k)
    return SearchResult(
        query_id=query_id or stable_query_id(cleaned, "speech", config),
        query=cleaned,
        modality="speech",
        hits=hits_from_rows("speech", merged, REQUIRED_METADATA),
    )


def search_operation(
    context: CapabilityContext,
    request: SearchInput,
) -> SearchResult:
    config = context.require_config()
    return search_speech(
        request.query,
        config=config,
        top_k=request.top_k,
        video_id=request.media_id or config.video_id,
        runtime=context.runtime,
        storage=context.require_storage(),
    )
