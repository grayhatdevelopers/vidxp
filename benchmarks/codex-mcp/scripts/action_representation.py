from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
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


def _generation_metrics(
    index_directory: Path,
    *,
    snapshot_id: str,
    media_id: str,
) -> dict[str, Any]:
    indexes = index_directory / "indexes"
    snapshot = json.loads(
        (indexes / "snapshots" / f"{snapshot_id}.json").read_text(
            encoding="utf-8"
        )
    )
    reference = snapshot["generations"][media_id]
    manifest = json.loads(
        (
            indexes
            / "generations"
            / reference["generation_id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    video = manifest["videos"][media_id]
    created = datetime.fromisoformat(manifest["created_at"])
    completed = datetime.fromisoformat(manifest["completed_at"])
    return {
        "generation_id": reference["generation_id"],
        "generation_wall_seconds": (completed - created).total_seconds(),
        "visual_indexing_seconds": video["stages"]["visual_indexing"]["seconds"],
        "committed_generation_bytes": reference["store_size_bytes_at_commit"],
    }


def _metrics(start: float, end: float, task: dict[str, Any]) -> dict[str, float]:
    expected_start = float(task["expected_start"])
    expected_end = float(task["expected_end"])
    return {
        "temporal_iou": interval_iou(start, end, expected_start, expected_end),
        "start_error_seconds": start - expected_start,
        "end_error_seconds": end - expected_end,
        "duration_error_seconds": (end - start) - (expected_end - expected_start),
    }


def _ranked_records(probe: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(probe["records"], key=lambda record: record["retrieval_rank"])


def _record_metrics(
    record: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "start_seconds": record["start_seconds"],
        "end_seconds": record["end_seconds"],
        "retrieval_rank": record["retrieval_rank"],
        **_metrics(
            float(record["start_seconds"]),
            float(record["end_seconds"]),
            task,
        ),
    }
    if "coarse_parent_ranks" in record:
        result["coarse_parent_ranks"] = record["coarse_parent_ranks"]
    return result


def _candidate_summary(
    records: list[dict[str, Any]],
    task: dict[str, Any],
    *,
    top_k: int,
) -> dict[str, Any]:
    if not records:
        raise RuntimeError("the action comparison has no candidate records")
    top_records = records[:top_k]
    top_metrics = [_record_metrics(record, task) for record in top_records]
    all_metrics = [_record_metrics(record, task) for record in records]
    return {
        "top_retrieved": top_metrics[0],
        "top_k": top_metrics,
        "best_in_top_k": max(
            top_metrics,
            key=lambda item: item["temporal_iou"],
        ),
        "best_candidate_oracle": max(
            all_metrics,
            key=lambda item: item["temporal_iou"],
        ),
        "candidate_count": len(records),
    }


def _coarse_to_fine_summary(
    coarse_probe: dict[str, Any],
    fine_probe: dict[str, Any],
    task: dict[str, Any],
    *,
    top_k: int,
) -> dict[str, Any]:
    coarse = _ranked_records(coarse_probe)[:top_k]
    fine = _ranked_records(fine_probe)
    selected = []
    for record in fine:
        midpoint = (
            float(record["start_seconds"]) + float(record["end_seconds"])
        ) / 2.0
        parent_ranks = [
            parent["retrieval_rank"]
            for parent in coarse
            if float(parent["start_seconds"])
            <= midpoint
            <= float(parent["end_seconds"])
        ]
        if parent_ranks:
            selected.append({**record, "coarse_parent_ranks": parent_ranks})
    summary = _candidate_summary(selected, task, top_k=top_k)
    summary["coarse_gate"] = _candidate_summary(coarse, task, top_k=top_k)
    summary["gate_rule"] = (
        "fine-window midpoint falls inside any of the top-k coarse windows"
    )
    summary["boundary_rule"] = "return one ranked fine window without union"
    return summary


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
            print(
                f"Indexing fine action windows for {task_id}...",
                file=sys.stderr,
                flush=True,
            )
            started = time.perf_counter()
            application.create_index(
                CreateIndexCommand(
                    media_id=media.media_id,
                    modalities=("action",),
                    capability_options={"action": options},
                )
            )
            indexing_seconds = time.perf_counter() - started
            print(
                f"Indexed fine action windows in {indexing_seconds:.3f}s.",
                file=sys.stderr,
                flush=True,
            )
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
    generation_metrics = _generation_metrics(
        index_directory,
        snapshot_id=config.snapshot_id,
        media_id=media.media_id,
    )
    coarse_probe = base_probe["modalities"]["action"]
    comparison = {
        "current_coarse": _candidate_summary(
            _ranked_records(coarse_probe),
            task,
            top_k=top_k,
        ),
        "fine_only": _candidate_summary(
            _ranked_records(action_probe),
            task,
            top_k=top_k,
        ),
        "coarse_to_fine": _coarse_to_fine_summary(
            coarse_probe,
            action_probe,
            task,
            top_k=top_k,
        ),
    }
    output = profile_root / f"{task_id}.json"
    payload = {
        "schema_version": 2,
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
        "multiscale_comparison": comparison,
        "resource_use": {
            "index_reused": reused_index,
            "indexing_seconds_this_run": indexing_seconds,
            "profile_store_bytes": _directory_size(index_directory),
            **generation_metrics,
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
        "multiscale_comparison": comparison,
        "resource_use": payload["resource_use"],
    }


def _method_summary(results: list[dict[str, Any]], method: str) -> dict[str, Any]:
    top = [
        result["multiscale_comparison"][method]["top_retrieved"]
        for result in results
    ]
    best_top_k = [
        result["multiscale_comparison"][method]["best_in_top_k"]
        for result in results
    ]
    oracle = [
        result["multiscale_comparison"][method]["best_candidate_oracle"]
        for result in results
    ]

    def rates(values: list[dict[str, Any]]) -> dict[str, float]:
        return {
            f"tiou_{threshold}": sum(
                item["temporal_iou"] >= threshold for item in values
            )
            / len(values)
            for threshold in (0.3, 0.5, 0.7)
        }

    return {
        "tasks": len(results),
        "mean_top1_iou": sum(item["temporal_iou"] for item in top) / len(top),
        "top1_threshold_rates": rates(top),
        "top_k_candidate_recall": rates(best_top_k),
        "oracle_threshold_rates": rates(oracle),
        "mean_best_in_top_k_iou": sum(
            item["temporal_iou"] for item in best_top_k
        )
        / len(best_top_k),
        "mean_oracle_iou": sum(item["temporal_iou"] for item in oracle)
        / len(oracle),
        "mean_absolute_start_error_seconds": sum(
            abs(item["start_error_seconds"]) for item in top
        )
        / len(top),
        "mean_absolute_end_error_seconds": sum(
            abs(item["end_error_seconds"]) for item in top
        )
        / len(top),
    }


def compare_held_out(
    *,
    sample_fps: float,
    stride_samples: int,
) -> dict[str, Any]:
    _load_environment()
    tasks = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "tasks"
            / "longvale-part9-pilot.json"
        ).read_text(encoding="utf-8")
    )
    selected = [task for task in tasks[2:] if "action" in task["modalities"]]
    missing = [
        task["id"]
        for task in selected
        if not _output_path(task["id"], None).is_file()
    ]
    if missing:
        raise RuntimeError(
            "missing held-out probes; run './benchmarks/codex-mcp/run probe "
            f"TASK_ID' for: {', '.join(missing)}"
        )

    results = []
    for index, task in enumerate(selected, start=1):
        print(
            f"[{index}/{len(selected)}] {task['id']}",
            file=sys.stderr,
            flush=True,
        )
        results.append(
            compare_action_representation(
                task["id"],
                sample_fps=sample_fps,
                stride_samples=stride_samples,
            )
        )
    methods = {
        method: _method_summary(results, method)
        for method in ("current_coarse", "fine_only", "coarse_to_fine")
    }
    unique_video_resources: dict[str, dict[str, Any]] = {}
    for task, result in zip(selected, results):
        unique_video_resources.setdefault(task["video_id"], result["resource_use"])

    profile, settings = _profile(sample_fps, stride_samples)
    evaluation_root = Path(_required_environment("VIDXP_EVAL_DATA_DIR")).parent
    output = evaluation_root / "action-representations" / profile / "held-out.json"
    aggregate = {
        "schema_version": 1,
        "scope": "five frozen held-out action tasks across three videos",
        "task_ids": [task["id"] for task in selected],
        "method": {
            "research_basis": [
                "CTAP (Gao et al., ECCV 2018)",
                (
                    "Localizing Moments in Long Video via Multimodal Guidance "
                    "(Barrios et al., ICCV 2023)"
                ),
            ],
            "vidxp_choices": {
                **settings,
                "coarse_top_k": 3,
                "gate_rule": (
                    "fine-window midpoint falls inside any top-three coarse window"
                ),
                "boundary_rule": "return one ranked fine window without union",
            },
            "excluded": [
                "multimodal fusion",
                "query rewriting",
                "agent or MCP execution",
                "learned boundary prediction",
            ],
        },
        "methods": methods,
        "per_task": [
            {
                "task_id": task["id"],
                "expected_start": task["expected_start"],
                "expected_end": task["expected_end"],
                "current_coarse": result["multiscale_comparison"][
                    "current_coarse"
                ],
                "fine_only": result["multiscale_comparison"]["fine_only"],
                "coarse_to_fine": result["multiscale_comparison"][
                    "coarse_to_fine"
                ],
            }
            for task, result in zip(selected, results)
        ],
        "resource_use": {
            "unique_videos": len(unique_video_resources),
            "fine_indexing_seconds_this_run": sum(
                resource["indexing_seconds_this_run"]
                for resource in unique_video_resources.values()
            ),
            "recorded_generation_wall_seconds": sum(
                resource["generation_wall_seconds"]
                for resource in unique_video_resources.values()
            ),
            "recorded_visual_indexing_seconds": sum(
                resource["visual_indexing_seconds"]
                for resource in unique_video_resources.values()
            ),
            "fine_action_records": sum(
                resource["action_record_count"]
                for resource in unique_video_resources.values()
            ),
            "current_action_records": sum(
                resource["control_action_record_count"]
                for resource in unique_video_resources.values()
            ),
            "fine_generation_bytes": sum(
                resource["committed_generation_bytes"]
                for resource in unique_video_resources.values()
            ),
            "profile_store_bytes": max(
                resource["profile_store_bytes"]
                for resource in unique_video_resources.values()
            ),
            "new_fine_text_embedding_calls": len(results),
            "coarse_probe_results_reused": True,
            "live_product_text_embedding_calls_per_task": 2,
            "codex_calls": 0,
            "api_calls": 0,
        },
    }
    output.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {**aggregate, "output": str(output)}


def _print_held_out(result: dict[str, Any]) -> None:
    print("Held-out VideoPrism multiscale comparison")
    print("Method             top1 IoU   >=.3   >=.5   >=.7   top3@.5  oracle@.5")
    for key, label in (
        ("current_coarse", "Current 8-second"),
        ("fine_only", "Fine-only"),
        ("coarse_to_fine", "Coarse-to-fine"),
    ):
        metrics = result["methods"][key]
        top1 = metrics["top1_threshold_rates"]
        top_k = metrics["top_k_candidate_recall"]
        oracle = metrics["oracle_threshold_rates"]
        print(
            f"{label:<18} {metrics['mean_top1_iou']:>8.4f} "
            f"{top1['tiou_0.3']:>7.3f} {top1['tiou_0.5']:>7.3f} "
            f"{top1['tiou_0.7']:>7.3f} {top_k['tiou_0.5']:>9.3f} "
            f"{oracle['tiou_0.5']:>10.3f}"
        )
    print("\nTask                          current    fine   coarse→fine")
    for task in result["per_task"]:
        print(
            f"{task['task_id'].removeprefix('longvale-part9-'):<29} "
            f"{task['current_coarse']['top_retrieved']['temporal_iou']:>7.4f} "
            f"{task['fine_only']['top_retrieved']['temporal_iou']:>7.4f} "
            f"{task['coarse_to_fine']['top_retrieved']['temporal_iou']:>13.4f}"
        )
    resources = result["resource_use"]
    print(
        "\nResource use: "
        f"{resources['fine_action_records']} fine records versus "
        f"{resources['current_action_records']} current records; "
        f"{resources['recorded_generation_wall_seconds']:.3f}s recorded build time; "
        f"{resources['new_fine_text_embedding_calls']} new local text embeddings; "
        "0 Codex/API calls."
    )
    print(f"Full evidence: {result['output']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Index and compare one isolated overlapping VideoPrism representation."
        )
    )
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--held-out", action="store_true")
    parser.add_argument("--sample-fps", type=float, required=True)
    parser.add_argument("--stride-samples", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.held_out == (arguments.task_id is not None):
        parser.error("provide one task ID or --held-out")
    if arguments.held_out:
        _print_held_out(
            compare_held_out(
                sample_fps=arguments.sample_fps,
                stride_samples=arguments.stride_samples,
            )
        )
    else:
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
