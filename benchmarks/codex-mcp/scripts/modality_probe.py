from __future__ import annotations

import argparse
import json
import os
import shlex
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from vidxp.application_models import ListMediaCommand, MediaState, SearchResult
from vidxp.benchmarks.agent_ablation_score import interval_iou
from vidxp.capabilities.action.operations import search_videoprism
from vidxp.capabilities.action.specs import VIDEOPRISM_MODEL
from vidxp.capabilities.scene.operations import search_scene
from vidxp.capabilities.scene.specs import SIGLIP2_MODEL
from vidxp.capabilities.sound.operations import search_sound
from vidxp.capabilities.sound.specs import FINELAP_MODEL
from vidxp.capabilities.speech.operations import search_speech
from vidxp.capabilities.speech.specs import QWEN3_EMBEDDING_MODEL
from vidxp.composition import create_local_application
from vidxp.core.contracts import IndexConfig
from vidxp.ports import IndexStore, ModelRuntimePort
from vidxp.search_fusion import fuse_search_results


BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = BENCHMARK_ROOT / "tasks" / "longvale-part9-pilot.json"
SearchFunction = Callable[..., SearchResult]
SEARCHERS: dict[str, SearchFunction] = {
    "action": search_videoprism,
    "scene": search_scene,
    "sound": search_sound,
    "speech": search_speech,
}
MODELS = {
    "action": VIDEOPRISM_MODEL,
    "scene": SIGLIP2_MODEL,
    "sound": FINELAP_MODEL,
    "speech": QWEN3_EMBEDDING_MODEL,
}


def _load_environment() -> None:
    path = BENCHMARK_ROOT / ".env"
    if not path.is_file():
        raise RuntimeError("run benchmark setup before probing indexed evidence")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        values = shlex.split(raw_value, posix=True)
        if len(values) != 1:
            raise RuntimeError(f"invalid value for {name} in benchmark .env")
        os.environ.setdefault(name, values[0])


def _task(task_id: str) -> dict[str, Any]:
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    matches = [task for task in tasks if task.get("id") == task_id]
    if len(matches) != 1:
        raise ValueError(f"unknown task id: {task_id}")
    return matches[0]


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is missing from benchmark .env")
    return value


def _output_path(task_id: str, requested: Path | None) -> Path:
    if requested is not None:
        return requested.resolve()
    data_directory = Path(_required_environment("VIDXP_EVAL_DATA_DIR"))
    return data_directory.parent / "localization" / f"{task_id}.probe.json"


def _search_all(
    modality: str,
    query: str,
    media_id: str,
    expected_start: float,
    expected_end: float,
    *,
    config: IndexConfig,
    runtime: ModelRuntimePort,
    storage: IndexStore,
    filters: Mapping[str, Any] | None = None,
) -> tuple[SearchResult, dict[str, Any]]:
    record_count = storage.count_records(
        modality,
        video_id=media_id,
        filters=filters,
    )
    if record_count == 0:
        raise RuntimeError(f"no {modality} records are indexed for this task")
    started = time.perf_counter()
    result = SEARCHERS[modality](
        query,
        config=config,
        runtime=runtime,
        top_k=record_count,
        video_id=media_id,
        filters=filters,
        storage=storage,
    )
    elapsed_seconds = time.perf_counter() - started
    records = [
        {
            "start_seconds": hit.start,
            "end_seconds": hit.end,
            "retrieval_rank": hit.rank,
            "ordering_score": hit.score,
            "raw_distance": hit.raw_distance,
            "source_id": hit.source_id,
            "metadata": hit.metadata,
        }
        for hit in sorted(result.hits, key=lambda item: (item.start, item.end))
    ]
    top_hit = result.hits[0]
    best_hit = max(
        result.hits,
        key=lambda hit: interval_iou(
            hit.start,
            hit.end,
            expected_start,
            expected_end,
        ),
    )
    return result, {
        "model": MODELS[modality].identity(),
        "record_count": len(records),
        "elapsed_seconds": elapsed_seconds,
        "model_calls": {"text_embedding": 1},
        "top_retrieved": {
            "start_seconds": top_hit.start,
            "end_seconds": top_hit.end,
            "temporal_iou": interval_iou(
                top_hit.start,
                top_hit.end,
                expected_start,
                expected_end,
            ),
        },
        "best_individual_interval_oracle": {
            "start_seconds": best_hit.start,
            "end_seconds": best_hit.end,
            "retrieval_rank": best_hit.rank,
            "temporal_iou": interval_iou(
                best_hit.start,
                best_hit.end,
                expected_start,
                expected_end,
            ),
        },
        "records": records,
    }


def export_probe(
    task_id: str,
    output: Path | None = None,
    *,
    current_top_k: int = 3,
) -> dict[str, Any]:
    if current_top_k <= 0:
        raise ValueError("current_top_k must be positive")
    _load_environment()
    task = _task(task_id)
    context = create_local_application(
        repository_name=os.environ.get("VIDXP_EVAL_REPOSITORY", "default"),
        index_directory=_required_environment("VIDXP_EVAL_INDEX_DIR"),
        data_directory=_required_environment("VIDXP_EVAL_DATA_DIR"),
        device=os.environ.get("VIDXP_EVAL_DEVICE", "cpu"),
    )
    application = context.application
    filename = Path(task["media_relpath"]).name
    page = application.media.list(
        ListMediaCommand(
            page_size=2,
            filename=filename,
            state=MediaState.ready,
        )
    )
    if len(page.items) != 1:
        raise RuntimeError(f"expected one ready media record for {filename}")
    media_id = page.items[0].media_id
    config = application.index_backend.active_config(
        application.index_directory,
        device=application.device,
    )
    modalities = tuple(
        modality
        for modality in task["modalities"]
        if modality in SEARCHERS and modality in config.enabled_modalities
    )
    if not modalities:
        raise RuntimeError("the task has no indexed searchable modalities")

    started = time.perf_counter()
    with application.index_backend.open_store(config) as storage:
        with application.runtime.scheduler.inference():
            searched = {
                modality: _search_all(
                    modality,
                    task["query"],
                    media_id,
                    float(task["expected_start"]),
                    float(task["expected_end"]),
                    config=config,
                    runtime=application.runtime,
                    storage=storage,
                )
                for modality in modalities
            }
    elapsed_seconds = time.perf_counter() - started
    full_results = tuple(searched[name][0] for name in modalities)
    probe_results = {name: searched[name][1] for name in modalities}
    current_inputs = tuple(
        result.model_copy(update={"hits": result.hits[:current_top_k]})
        for result in full_results
    )
    current_fusion = fuse_search_results(
        query=task["query"],
        requested_modalities=modalities,
        results=current_inputs,
        media_id=media_id,
        top_k=current_top_k,
        snapshot_id=config.snapshot_id,
    )
    top_moment = current_fusion.moments[0] if current_fusion.moments else None
    current_metrics = (
        {
            "temporal_iou": interval_iou(
                top_moment.start,
                top_moment.end,
                float(task["expected_start"]),
                float(task["expected_end"]),
            ),
            "start_error_seconds": top_moment.start - float(task["expected_start"]),
            "end_error_seconds": top_moment.end - float(task["expected_end"]),
            "duration_error_seconds": (
                top_moment.end
                - top_moment.start
                - float(task["expected_end"])
                + float(task["expected_start"])
            ),
        }
        if top_moment is not None
        else None
    )
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "video_id": task["video_id"],
        "media_id": media_id,
        "query": task["query"],
        "expected_start": task["expected_start"],
        "expected_end": task["expected_end"],
        "snapshot_id": config.snapshot_id,
        "vector_distance": config.vector_distance,
        "score_definition": "ordering_score is negative raw_distance",
        "modalities": probe_results,
        "current_control": {
            "candidate_top_k_per_modality": current_top_k,
            "output_top_k": current_top_k,
            "top_moment_metrics": current_metrics,
            "result": current_fusion.model_dump(mode="json"),
        },
        "elapsed_seconds": elapsed_seconds,
    }
    destination = _output_path(task_id, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(destination),
        "expected_interval": {
            "start_seconds": task["expected_start"],
            "end_seconds": task["expected_end"],
        },
        "current_control": {
            "top_interval": (
                {
                    "start_seconds": top_moment.start,
                    "end_seconds": top_moment.end,
                }
                if top_moment is not None
                else None
            ),
            "metrics": current_metrics,
        },
        "modalities": {
            name: {
                "records": result["record_count"],
                "elapsed_seconds": result["elapsed_seconds"],
                "model_calls": result["model_calls"],
                "top_retrieved": result["top_retrieved"],
                "best_individual_interval_oracle": result[
                    "best_individual_interval_oracle"
                ],
            }
            for name, result in probe_results.items()
        },
        "model_calls": {
            "text_embedding": sum(
                result["model_calls"]["text_embedding"]
                for result in probe_results.values()
            )
        },
        "elapsed_seconds": elapsed_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export all indexed evidence scores for one benchmark task."
    )
    parser.add_argument("task_id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--top-k", type=int, default=3)
    arguments = parser.parse_args()
    print(
        json.dumps(
            export_probe(
                arguments.task_id,
                arguments.output,
                current_top_k=arguments.top_k,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
