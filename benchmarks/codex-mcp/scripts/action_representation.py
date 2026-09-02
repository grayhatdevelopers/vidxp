from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from vidxp.application_models import (
    CreateIndexCommand,
    ImportMediaCommand,
    ListMediaCommand,
    MediaState,
    SearchHit,
    SearchResult,
)
from vidxp.benchmarks.agent_ablation_score import interval_iou
from vidxp.capabilities.action.config import videoprism_config
from vidxp.composition import create_local_application
from vidxp.index_state import IndexNotReadyError
from vidxp.search_fusion import fuse_search_results

from modality_probe import (
    _load_environment,
    _output_path,
    _required_environment,
    _search_all,
    _task,
)


def _profile(sample_fps: float, stride_samples: int) -> tuple[str, dict[str, Any]]:
    settings = {
        "sample_fps": sample_fps,
        "clip_stride_samples": stride_samples,
    }
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
    return f"videoprism-{hashlib.sha256(encoded).hexdigest()[:12]}", settings


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _metrics(start: float, end: float, task: dict[str, Any]) -> dict[str, float]:
    expected_start = float(task["expected_start"])
    expected_end = float(task["expected_end"])
    return {
        "temporal_iou": interval_iou(start, end, expected_start, expected_end),
        "start_error_seconds": start - expected_start,
        "end_error_seconds": end - expected_end,
        "duration_error_seconds": (end - start) - (expected_end - expected_start),
    }


def _saved_result(
    modality: str,
    probe: dict[str, Any],
    *,
    top_k: int,
) -> SearchResult:
    records = sorted(
        probe["modalities"][modality]["records"],
        key=lambda record: record["retrieval_rank"],
    )[:top_k]
    hits = tuple(
        SearchHit(
            rank=record["retrieval_rank"],
            media_id=probe["media_id"],
            video_id=probe["media_id"],
            generation_id=record["source_id"].split(":", 1)[0],
            start=record["start_seconds"],
            end=record["end_seconds"],
            score=record["ordering_score"],
            raw_distance=record["raw_distance"],
            modality=modality,
            source_id=record["source_id"],
            metadata=record["metadata"],
        )
        for record in records
    )
    return SearchResult(
        query_id=f"saved:{probe['task_id']}:{modality}",
        query=probe["query"],
        modality=modality,
        hits=hits,
    )


def compare_action_representation(
    task_id: str,
    *,
    sample_fps: float,
    stride_samples: int,
) -> dict[str, Any]:
    if sample_fps <= 0:
        raise ValueError("sample_fps must be positive")
    if not 1 <= stride_samples <= 16:
        raise ValueError("stride_samples must be between 1 and 16")

    _load_environment()
    task = _task(task_id)
    if "action" not in task["modalities"]:
        raise ValueError(f"task does not declare action evidence: {task_id}")
    base_path = _output_path(task_id, None)
    if not base_path.is_file():
        raise RuntimeError(f"run './benchmarks/codex-mcp/run probe {task_id}' first")
    base_probe = json.loads(base_path.read_text(encoding="utf-8"))
    top_k = int(base_probe["current_control"]["candidate_top_k_per_modality"])

    profile, options = _profile(sample_fps, stride_samples)
    evaluation_root = Path(_required_environment("VIDXP_EVAL_DATA_DIR")).parent
    profile_root = evaluation_root / "action-representations" / profile
    data_directory = profile_root / "data"
    index_directory = profile_root / "index"
    source = Path(_required_environment("VIDXP_EVAL_WORKSPACE")) / task["media_relpath"]
    if not source.is_file():
        raise RuntimeError(f"prepared benchmark media is missing: {source}")

    context = create_local_application(
        repository_name=os.environ.get("VIDXP_EVAL_REPOSITORY", "default"),
        index_directory=index_directory,
        data_directory=data_directory,
        device=os.environ.get("VIDXP_EVAL_DEVICE", "cpu"),
    )
    indexing_seconds = 0.0
    reused_index = False
    try:
        application = context.application
        page = application.media.list(
            ListMediaCommand(
                page_size=2,
                filename=source.name,
                state=MediaState.ready,
            )
        )
        if len(page.items) > 1:
            raise RuntimeError(
                f"multiple experimental media records match {source.name}"
            )
        media = (
            page.items[0]
            if page.items
            else application.import_media(ImportMediaCommand(path=source))
        )

        try:
            config = application.index_backend.active_config(
                application.index_directory,
                device=application.device,
            )
        except IndexNotReadyError:
            config = None
        if config is not None:
            effective = videoprism_config(config)
            if (
                effective.sample_fps != sample_fps
                or effective.clip_stride_samples != stride_samples
            ):
                raise RuntimeError(
                    f"experimental profile {profile} has different settings"
                )
            with application.index_backend.open_store(config) as storage:
                reused_index = storage.count_records(
                    "action", video_id=media.media_id
                ) > 0

        if not reused_index:
            started = time.perf_counter()
            application.create_index(
                CreateIndexCommand(
                    media_id=media.media_id,
                    modalities=("action",),
                    capability_options={"action": options},
                )
            )
            indexing_seconds = time.perf_counter() - started
            config = application.index_backend.active_config(
                application.index_directory,
                device=application.device,
            )
        assert config is not None

        with application.index_backend.open_store(config) as storage:
            with application.runtime.scheduler.inference():
                action_result, action_probe = _search_all(
                    "action",
                    task["query"],
                    media.media_id,
                    float(task["expected_start"]),
                    float(task["expected_end"]),
                    config=config,
                    runtime=application.runtime,
                    storage=storage,
                )
    finally:
        context.close()

    normalized_action = action_result.model_copy(
        update={
            "hits": tuple(
                hit.model_copy(
                    update={
                        "media_id": base_probe["media_id"],
                        "video_id": base_probe["media_id"],
                    }
                )
                for hit in action_result.hits[:top_k]
            )
        }
    )
    results = tuple(
        normalized_action
        if modality == "action"
        else _saved_result(modality, base_probe, top_k=top_k)
        for modality in task["modalities"]
        if modality == "action" or modality in base_probe["modalities"]
    )
    fused = fuse_search_results(
        query=task["query"],
        requested_modalities=tuple(task["modalities"]),
        results=results,
        media_id=base_probe["media_id"],
        top_k=top_k,
        snapshot_id=base_probe["snapshot_id"],
    )
    top_moment = fused.moments[0] if fused.moments else None
    control_action_records = int(
        base_probe["modalities"]["action"]["record_count"]
    )
    action_records = int(action_probe["record_count"])
    output = profile_root / f"{task_id}.json"
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "profile": profile,
        "research_role": (
            "overlapping fixed-window control; exact settings are VidXP experimental"
        ),
        "settings": {
            **options,
            "nominal_window_seconds": 16 / sample_fps,
            "nominal_stride_seconds": stride_samples / sample_fps,
        },
        "control": base_probe["current_control"],
        "experimental": {
            "action": action_probe,
            "fused_result": fused.model_dump(mode="json"),
            "top_moment_metrics": (
                _metrics(top_moment.start, top_moment.end, task)
                if top_moment is not None
                else None
            ),
        },
        "resource_use": {
            "index_reused": reused_index,
            "indexing_seconds": indexing_seconds,
            "index_bytes": _directory_size(index_directory),
            "control_action_record_count": control_action_records,
            "action_record_count": action_records,
            "action_record_count_multiplier": action_records / control_action_records,
            "query_seconds": action_probe["elapsed_seconds"],
            "model_calls": {
                "action_video_embedding_batches": (
                    0 if reused_index else action_records
                ),
                "action_text_embedding": 1,
            },
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(output),
        "profile": profile,
        "settings": payload["settings"],
        "control": base_probe["current_control"]["top_moment_metrics"],
        "experimental": payload["experimental"]["top_moment_metrics"],
        "action_top_retrieved": action_probe["top_retrieved"],
        "action_best_individual_interval_oracle": action_probe[
            "best_individual_interval_oracle"
        ],
        "resource_use": payload["resource_use"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Index and compare one isolated overlapping VideoPrism representation."
        )
    )
    parser.add_argument("task_id")
    parser.add_argument("--sample-fps", type=float, required=True)
    parser.add_argument("--stride-samples", type=int, required=True)
    arguments = parser.parse_args()
    print(
        json.dumps(
            compare_action_representation(
                arguments.task_id,
                sample_fps=arguments.sample_fps,
                stride_samples=arguments.stride_samples,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
