from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any

from vidxp.benchmarks.common import (
    append_failure,
    benchmark_generation_id,
    benchmark_media_id,
    ensure_adapter_outputs,
    record_adapter_manifest,
)
from vidxp.benchmarks.modality_metrics import (
    RetrievalQuery,
    TemporalQuery,
    retrieval_metrics,
    temporal_retrieval_metrics,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capabilities.schemas import SearchResult
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.manifest import ManifestStore, sha256_file, write_json_atomic
from vidxp.core.runner import run_index
from vidxp.core.storage import IndexStorage
from vidxp.infrastructure.local_index import LOCAL_INDEX_RUNTIME_CHECKS
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


SearchFunction = Callable[..., SearchResult]


def _validate_queries(
    queries: Sequence[RetrievalQuery] | Sequence[TemporalQuery],
) -> None:
    query_ids = [query.query_id for query in queries]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("Benchmark query IDs must be unique.")


def input_artifact(path: str | Path, *, name: str, source: str) -> dict[str, Any]:
    artifact = Path(path)
    if not artifact.is_file():
        raise FileNotFoundError(f"{name} not found: {artifact}")
    return {
        "name": name,
        "path": str(artifact.resolve()),
        "source": source,
        "revision": "recorded_from_supplied_file",
        "sha256": sha256_file(artifact),
        "size_bytes": artifact.stat().st_size,
    }


def resolve_media(
    media_directory: str | Path,
    media_id: str,
    *,
    extensions: Sequence[str],
) -> Path:
    root = Path(media_directory)
    exact = root / media_id
    candidates = [exact]
    if exact.suffix == "":
        candidates.extend(root / f"{media_id}{extension}" for extension in extensions)
    matches = [candidate for candidate in candidates if candidate.is_file()]
    if len(matches) != 1:
        detail = "not found" if not matches else "ambiguous"
        raise FileNotFoundError(
            f"Media {media_id!r} is {detail} under {root.resolve()}."
        )
    return matches[0]


def _runtime(
    config: IndexConfig,
) -> tuple[Any, ModelRuntime]:
    registry = create_capability_registry(
        platform_runtime_checks=LOCAL_INDEX_RUNTIME_CHECKS
    )
    runtime = ModelRuntime(
        VidXPSettings(
            repository_root=config.run_directory,
            runtime_backend=config.device,
        ),
        allowed_specs=registry.model_specs(),
    )
    return registry, runtime


def _sources(
    benchmark: str,
    media: Mapping[str, Path],
) -> tuple[list[VideoSource], dict[str, str]]:
    reverse: dict[str, str] = {}
    sources = []
    for official_id, path in sorted(media.items()):
        internal_id = benchmark_media_id(benchmark, official_id)
        reverse[internal_id] = official_id
        sources.append(
            VideoSource(
                video_id=internal_id,
                path=path,
                source_name=path.name,
            )
        )
    return sources, reverse


def _write_timing(
    path: Path,
    *,
    query_id: str,
    elapsed_seconds: float,
) -> None:
    with path.open("a", encoding="utf-8") as destination:
        destination.write(
            json.dumps(
                {
                    "stage": "query",
                    "query_id": query_id,
                    "elapsed_seconds": elapsed_seconds,
                },
                sort_keys=True,
            )
            + "\n"
        )


def run_indexed_retrieval(
    *,
    benchmark: str,
    split: str,
    modality: str,
    media: Mapping[str, Path],
    queries: Sequence[RetrievalQuery],
    search: SearchFunction,
    run_id: str,
    artifacts: Sequence[Mapping[str, Any]],
    capability_options: Mapping[str, Any] | None = None,
    search_filters: Mapping[str, Any] | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
    reset: bool = False,
    result_classification: str,
) -> dict[str, Any]:
    if not media or not queries:
        raise ValueError("Corpus retrieval requires media and queries.")
    _validate_queries(queries)
    config = IndexConfig(
        dataset=benchmark,
        split=split,
        run_id=run_id,
        enabled_modalities=(modality,),
        capability_options={modality: dict(capability_options or {})},
        device=device,
        output_root=output_root,
        generation_id=benchmark_generation_id(benchmark, split, run_id),
    )
    run_directory = config.run_directory
    registry, runtime = _runtime(config)
    ensure_adapter_outputs(run_directory)
    subset = {
        "query_count": len(queries),
        "media_count": len(media),
        "split": split,
    }
    sources, reverse_ids = _sources(benchmark, media)
    try:
        with IndexStorage(config) as storage:
            run_index(
                sources,
                config,
                reset=reset,
                storage=storage,
                manifest_store=ManifestStore(
                    config,
                    registry=registry,
                    runtime=runtime,
                ),
                registry=registry,
                runtime=runtime,
            )
            record_count = storage.count_records(
                modality,
                filters=search_filters,
            )
            if record_count < len(media):
                raise RuntimeError(
                    f"The {modality} index contains {record_count} records for "
                    f"{len(media)} media items."
                )
            rankings: dict[str, list[str]] = {}
            prediction_records: dict[str, list[dict[str, Any]]] = {}
            for query in queries:
                started = perf_counter()
                result = search(
                    query.text,
                    config=config,
                    runtime=runtime,
                    storage=storage,
                    top_k=record_count,
                    query_id=query.query_id,
                    filters=search_filters,
                )
                _write_timing(
                    run_directory / "timings.jsonl",
                    query_id=query.query_id,
                    elapsed_seconds=perf_counter() - started,
                )
                ranking = []
                records = []
                seen = set()
                for hit in result.hits:
                    official_id = reverse_ids[hit.media_id]
                    if official_id not in seen:
                        seen.add(official_id)
                        ranking.append(official_id)
                        records.append(
                            {
                                "media_id": official_id,
                                "score": hit.score,
                                "raw_distance": hit.raw_distance,
                                "start": hit.start,
                                "end": hit.end,
                                "source_id": hit.source_id,
                            }
                        )
                if seen != set(media):
                    raise RuntimeError(
                        f"Query {query.query_id!r} did not rank the complete gallery."
                    )
                rankings[query.query_id] = ranking
                prediction_records[query.query_id] = records

        metrics = {
            **retrieval_metrics(queries, rankings),
            "media_count": len(media),
            "indexed_record_count": record_count,
        }
        write_json_atomic(
            run_directory / "ground_truth.subset.json",
            [
                {
                    "query_id": query.query_id,
                    "text": query.text,
                    "relevant_media_ids": list(query.relevant_media_ids),
                }
                for query in queries
            ],
        )
        write_json_atomic(
            run_directory / "predictions.json",
            prediction_records,
        )
        write_json_atomic(run_directory / "metrics.json", metrics)
        (run_directory / "evaluator.log").write_text(
            "VidXP computed rank metrics from the complete indexed gallery.\n",
            encoding="utf-8",
        )
        record_adapter_manifest(
            run_directory,
            benchmark=benchmark,
            subset=subset,
            artifacts=artifacts,
            state="complete",
            details={
                "result_classification": result_classification,
                "ranking_unit": "best_indexed_record_per_media",
                "complete_gallery_ranked": True,
                "prediction_count": len(rankings),
            },
        )
        return metrics
    except BaseException as error:
        append_failure(run_directory, stage=f"{benchmark}_adapter", error=error)
        record_adapter_manifest(
            run_directory,
            benchmark=benchmark,
            subset=subset,
            artifacts=artifacts,
            state="failed",
        )
        raise


def run_indexed_temporal(
    *,
    benchmark: str,
    split: str,
    modality: str,
    media: Mapping[str, Path],
    queries: Sequence[TemporalQuery],
    search: SearchFunction,
    run_id: str,
    artifacts: Sequence[Mapping[str, Any]],
    capability_options: Mapping[str, Any] | None = None,
    search_filters: Mapping[str, Any] | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
    reset: bool = False,
    result_classification: str,
) -> dict[str, Any]:
    if not media or not queries:
        raise ValueError("Temporal retrieval requires media and queries.")
    _validate_queries(queries)
    config = IndexConfig(
        dataset=benchmark,
        split=split,
        run_id=run_id,
        enabled_modalities=(modality,),
        capability_options={modality: dict(capability_options or {})},
        device=device,
        output_root=output_root,
        generation_id=benchmark_generation_id(benchmark, split, run_id),
    )
    run_directory = config.run_directory
    registry, runtime = _runtime(config)
    ensure_adapter_outputs(run_directory)
    subset = {
        "query_count": len(queries),
        "media_count": len(media),
        "split": split,
    }
    sources, _reverse_ids = _sources(benchmark, media)
    internal_ids = {
        official_id: benchmark_media_id(benchmark, official_id)
        for official_id in media
    }
    try:
        with IndexStorage(config) as storage:
            run_index(
                sources,
                config,
                reset=reset,
                storage=storage,
                manifest_store=ManifestStore(
                    config,
                    registry=registry,
                    runtime=runtime,
                ),
                registry=registry,
                runtime=runtime,
            )
            predictions: dict[str, list[tuple[float, float]]] = {}
            prediction_records: dict[str, list[dict[str, Any]]] = {}
            for query in queries:
                internal_id = internal_ids[query.media_id]
                record_count = storage.count_records(
                    modality,
                    video_id=internal_id,
                    filters=search_filters,
                )
                if record_count == 0:
                    raise RuntimeError(
                        f"No {modality} records exist for {query.media_id!r}."
                    )
                started = perf_counter()
                result = search(
                    query.text,
                    config=config,
                    runtime=runtime,
                    storage=storage,
                    top_k=record_count,
                    video_id=internal_id,
                    query_id=query.query_id,
                    filters=search_filters,
                )
                _write_timing(
                    run_directory / "timings.jsonl",
                    query_id=query.query_id,
                    elapsed_seconds=perf_counter() - started,
                )
                predictions[query.query_id] = [
                    (hit.start, hit.end) for hit in result.hits
                ]
                prediction_records[query.query_id] = [
                    {
                        "media_id": query.media_id,
                        "start": hit.start,
                        "end": hit.end,
                        "score": hit.score,
                        "raw_distance": hit.raw_distance,
                        "source_id": hit.source_id,
                    }
                    for hit in result.hits
                ]

        metrics = temporal_retrieval_metrics(queries, predictions)
        write_json_atomic(
            run_directory / "ground_truth.subset.json",
            [
                {
                    "query_id": query.query_id,
                    "media_id": query.media_id,
                    "text": query.text,
                    "intervals": [list(interval) for interval in query.intervals],
                }
                for query in queries
            ],
        )
        write_json_atomic(
            run_directory / "predictions.json",
            prediction_records,
        )
        write_json_atomic(run_directory / "metrics.json", metrics)
        (run_directory / "evaluator.log").write_text(
            "VidXP computed ranked temporal-IoU diagnostics from every indexed "
            "record in the known media item.\n",
            encoding="utf-8",
        )
        record_adapter_manifest(
            run_directory,
            benchmark=benchmark,
            subset=subset,
            artifacts=artifacts,
            state="complete",
            details={
                "result_classification": result_classification,
                "ranking_unit": "indexed_interval",
                "prediction_count": len(predictions),
            },
        )
        return metrics
    except BaseException as error:
        append_failure(run_directory, stage=f"{benchmark}_adapter", error=error)
        record_adapter_manifest(
            run_directory,
            benchmark=benchmark,
            subset=subset,
            artifacts=artifacts,
            state="failed",
        )
        raise
