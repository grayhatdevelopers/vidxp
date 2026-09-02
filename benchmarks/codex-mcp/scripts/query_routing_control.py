from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from vidxp.application_models import ListMediaCommand, MediaState
from vidxp.benchmarks.agent_ablation_score import interval_iou
from vidxp.composition import create_local_application
from vidxp.core.contracts import IndexConfig
from vidxp.ports import IndexStore, ModelRuntimePort

from modality_probe import (
    BENCHMARK_ROOT,
    _load_environment,
    _output_path,
    _required_environment,
    _search_all,
    _task,
)


QUERY_PLAN_PATH = (
    BENCHMARK_ROOT / "tasks" / "longvale-part9-modality-queries.json"
)


def _rank_metrics(
    records: list[dict[str, Any]],
    *,
    expected_start: float,
    expected_end: float,
    top_k: int = 3,
) -> dict[str, Any]:
    ranked = sorted(records, key=lambda item: int(item["retrieval_rank"]))
    scored = [
        (
            item,
            interval_iou(
                float(item["start_seconds"]),
                float(item["end_seconds"]),
                expected_start,
                expected_end,
            ),
        )
        for item in ranked
    ]
    overlapping = [
        (item, iou)
        for item, iou in scored
        if iou > 0
    ]
    first_overlapping = min(
        overlapping,
        key=lambda pair: int(pair[0]["retrieval_rank"]),
        default=None,
    )
    best_iou = max((iou for _, iou in scored), default=0.0)
    best_iou_rank = min(
        (
            int(item["retrieval_rank"])
            for item, iou in scored
            if abs(iou - best_iou) <= 1e-12
        ),
        default=None,
    )
    top_k_best_iou = max(
        (iou for item, iou in scored if int(item["retrieval_rank"]) <= top_k),
        default=0.0,
    )
    return {
        "first_overlapping_rank": (
            int(first_overlapping[0]["retrieval_rank"])
            if first_overlapping is not None
            else None
        ),
        "first_overlapping_interval": (
            {
                "start_seconds": float(first_overlapping[0]["start_seconds"]),
                "end_seconds": float(first_overlapping[0]["end_seconds"]),
                "temporal_iou": first_overlapping[1],
                "representation": first_overlapping[0]["metadata"].get(
                    "representation"
                ),
            }
            if first_overlapping is not None
            else None
        ),
        "best_interval_iou": best_iou,
        "best_interval_rank": best_iou_rank,
        "top_k": top_k,
        "top_k_best_interval_iou": top_k_best_iou,
        "top_k_contains_best_interval": (
            best_iou_rank is not None and best_iou_rank <= top_k
        ),
        "top_k_contains_overlapping_interval": (
            first_overlapping is not None
            and int(first_overlapping[0]["retrieval_rank"]) <= top_k
        ),
    }


def _change(before: int | None, after: int | None) -> str:
    if before is None or after is None:
        return "unavailable"
    if after < before:
        return "improved"
    if after > before:
        return "worse"
    return "unchanged"


def _report_path() -> Path:
    data_directory = Path(_required_environment("VIDXP_EVAL_DATA_DIR"))
    return data_directory.parent / "localization" / "query-routing-held-out.json"


def _search_metrics(
    modality: str,
    query: str,
    media_id: str,
    expected_start: float,
    expected_end: float,
    *,
    config: IndexConfig,
    runtime: ModelRuntimePort,
    storage: IndexStore,
    representation: str | None = None,
) -> dict[str, Any]:
    filters = (
        {"representation": representation}
        if representation is not None
        else None
    )
    _, result = _search_all(
        modality,
        query,
        media_id,
        expected_start,
        expected_end,
        config=config,
        runtime=runtime,
        storage=storage,
        filters=filters,
    )
    return {
        "query": query,
        "record_count": result["record_count"],
        "metrics": _rank_metrics(
            result["records"],
            expected_start=expected_start,
            expected_end=expected_end,
        ),
        "elapsed_seconds": result["elapsed_seconds"],
    }


def compare_query_routing() -> dict[str, Any]:
    _load_environment()
    query_plan = json.loads(QUERY_PLAN_PATH.read_text(encoding="utf-8"))
    planned_tasks = query_plan["tasks"]
    context = create_local_application(
        repository_name=os.environ.get("VIDXP_EVAL_REPOSITORY", "default"),
        index_directory=_required_environment("VIDXP_EVAL_INDEX_DIR"),
        data_directory=_required_environment("VIDXP_EVAL_DATA_DIR"),
        device=os.environ.get("VIDXP_EVAL_DEVICE", "cpu"),
    )
    application = context.application
    config = application.index_backend.active_config(
        application.index_directory,
        device=application.device,
    )
    comparisons: list[dict[str, Any]] = []
    sound_stream_comparisons: list[dict[str, Any]] = []
    started = time.perf_counter()
    model_calls = 0

    with application.index_backend.open_store(config) as storage:
        with application.runtime.scheduler.inference():
            for task_id, routed_queries in planned_tasks.items():
                task = _task(task_id)
                baseline_path = _output_path(task_id, None)
                if not baseline_path.is_file():
                    raise RuntimeError(
                        f"saved full-query probe is missing for {task_id}"
                    )
                baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                if baseline["query"] != task["query"]:
                    raise RuntimeError(f"saved query does not match {task_id}")
                if baseline["snapshot_id"] != config.snapshot_id:
                    raise RuntimeError(f"saved snapshot is stale for {task_id}")
                filename = Path(task["media_relpath"]).name
                page = application.media.list(
                    ListMediaCommand(
                        page_size=2,
                        filename=filename,
                        state=MediaState.ready,
                    )
                )
                if len(page.items) != 1:
                    raise RuntimeError(
                        f"expected one ready media record for {filename}"
                    )
                media_id = page.items[0].media_id
                expected_modalities = set(task["modalities"])
                if set(routed_queries) != expected_modalities:
                    raise RuntimeError(
                        f"query plan does not cover task modalities for {task_id}"
                    )

                for modality in task["modalities"]:
                    routed_query = str(routed_queries[modality])
                    routed = _search_metrics(
                        modality,
                        routed_query,
                        media_id,
                        float(task["expected_start"]),
                        float(task["expected_end"]),
                        config=config,
                        runtime=application.runtime,
                        storage=storage,
                    )
                    model_calls += 1
                    baseline_metrics = _rank_metrics(
                        baseline["modalities"][modality]["records"],
                        expected_start=float(task["expected_start"]),
                        expected_end=float(task["expected_end"]),
                    )
                    routed_metrics = routed["metrics"]
                    comparisons.append(
                        {
                            "task_id": task_id,
                            "modality": modality,
                            "full_query": task["query"],
                            "routed_query": routed_query,
                            "full_query_metrics": baseline_metrics,
                            "routed_query_metrics": routed_metrics,
                            "best_interval_rank_change": _change(
                                baseline_metrics["best_interval_rank"],
                                routed_metrics["best_interval_rank"],
                            ),
                            "first_overlap_rank_change": _change(
                                baseline_metrics["first_overlapping_rank"],
                                routed_metrics["first_overlapping_rank"],
                            ),
                            "elapsed_seconds": routed["elapsed_seconds"],
                        }
                    )
                    if modality == "sound":
                        stream_specs = {
                            "window_caption_query": (
                                "window",
                                str(task["query"]),
                            ),
                            "window_phrase_query": ("window", routed_query),
                            "activation_full_query": (
                                "activation",
                                str(task["query"]),
                            ),
                            "activation_phrase_query": (
                                "activation",
                                routed_query,
                            ),
                        }
                        streams = {
                            name: _search_metrics(
                                modality,
                                query,
                                media_id,
                                float(task["expected_start"]),
                                float(task["expected_end"]),
                                config=config,
                                runtime=application.runtime,
                                storage=storage,
                                representation=representation,
                            )
                            for name, (representation, query) in stream_specs.items()
                        }
                        model_calls += len(stream_specs)
                        sound_stream_comparisons.append(
                            {
                                "task_id": task_id,
                                "current_mixed_full_query": baseline_metrics,
                                **streams,
                            }
                        )

    elapsed_seconds = time.perf_counter() - started
    change_counts = {
        name: sum(
            item["best_interval_rank_change"] == name for item in comparisons
        )
        for name in ("improved", "unchanged", "worse", "unavailable")
    }
    baseline_top3 = sum(
        item["full_query_metrics"]["top_k_contains_best_interval"]
        for item in comparisons
    )
    routed_top3 = sum(
        item["routed_query_metrics"]["top_k_contains_best_interval"]
        for item in comparisons
    )
    baseline_overlap_top3 = sum(
        item["full_query_metrics"]["top_k_contains_overlapping_interval"]
        for item in comparisons
    )
    routed_overlap_top3 = sum(
        item["routed_query_metrics"]["top_k_contains_overlapping_interval"]
        for item in comparisons
    )
    overlap_change_counts = {
        name: sum(item["first_overlap_rank_change"] == name for item in comparisons)
        for name in ("improved", "unchanged", "worse", "unavailable")
    }
    current_sound_overlap_top3 = sum(
        item["current_mixed_full_query"]["top_k_contains_overlapping_interval"]
        for item in sound_stream_comparisons
    )
    separated_sound_overlap_top3 = sum(
        item[stream]["metrics"]["top_k_contains_overlapping_interval"]
        for item in sound_stream_comparisons
        for stream in ("window_caption_query", "activation_phrase_query")
    )
    separated_sound_tasks_with_overlap_top3 = sum(
        any(
            item[stream]["metrics"]["top_k_contains_overlapping_interval"]
            for stream in ("window_caption_query", "activation_phrase_query")
        )
        for item in sound_stream_comparisons
    )
    one_phrase_sound_tasks_with_overlap_top3 = sum(
        any(
            item[stream]["metrics"]["top_k_contains_overlapping_interval"]
            for stream in ("window_phrase_query", "activation_phrase_query")
        )
        for item in sound_stream_comparisons
    )
    report = {
        "schema_version": 1,
        "control": {
            "id": query_plan["method"],
            "type": "benchmark-only manual wording ceiling",
            "constraints": query_plan["constraints"],
            "research_relationship": (
                "Luo et al. (WACV 2024) and TFVTG (ECCV 2024) motivate "
                "compound-query decomposition; per-modality phrases are a "
                "VidXP diagnostic and are not either paper's method."
            ),
        },
        "task_count": len(planned_tasks),
        "task_modality_pairs": len(comparisons),
        "summary": {
            "best_interval_rank_changes": change_counts,
            "first_overlap_rank_changes": overlap_change_counts,
            "full_query_top3_contains_best_interval": baseline_top3,
            "routed_query_top3_contains_best_interval": routed_top3,
            "full_query_top3_contains_target_overlap": baseline_overlap_top3,
            "routed_query_top3_contains_target_overlap": routed_overlap_top3,
            "current_sound_tasks_with_target_overlap_top3": (
                current_sound_overlap_top3
            ),
            "separated_sound_stream_hits_in_top3": separated_sound_overlap_top3,
            "separated_sound_tasks_with_target_overlap_top3": (
                separated_sound_tasks_with_overlap_top3
            ),
            "one_phrase_separated_sound_tasks_with_target_overlap_top3": (
                one_phrase_sound_tasks_with_overlap_top3
            ),
        },
        "resource_use": {
            "local_text_embedding_calls": model_calls,
            "elapsed_seconds": elapsed_seconds,
            "codex_or_api_calls": 0,
            "index_bytes_written": 0,
        },
        "comparisons": comparisons,
        "sound_stream_comparisons": sound_stream_comparisons,
    }
    destination = _report_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report["output"] = str(destination)
    return report


def _print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    changes = summary["best_interval_rank_changes"]
    overlap_changes = summary["first_overlap_rank_changes"]
    print("Modality-query ranking control")
    print(
        "Target-overlapping evidence in top 3: "
        f"full query {summary['full_query_top3_contains_target_overlap']}/"
        f"{report['task_modality_pairs']}; routed query "
        f"{summary['routed_query_top3_contains_target_overlap']}/"
        f"{report['task_modality_pairs']}"
    )
    print(
        "First target-overlap rank: "
        f"{overlap_changes['improved']} improved, "
        f"{overlap_changes['unchanged']} unchanged, "
        f"{overlap_changes['worse']} worse"
    )
    print(
        "Best-boundary record in top 3: "
        f"full query {summary['full_query_top3_contains_best_interval']}/"
        f"{report['task_modality_pairs']}; routed query "
        f"{summary['routed_query_top3_contains_best_interval']}/"
        f"{report['task_modality_pairs']}"
    )
    print(
        "Best-boundary rank: "
        f"{changes['improved']} improved, {changes['unchanged']} unchanged, "
        f"{changes['worse']} worse"
    )
    print()
    print(
        f"{'Task / modality':39} {'target rank':>20} "
        f"{'boundary rank':>20}  query"
    )
    for item in report["comparisons"]:
        task_name = item["task_id"].removeprefix("longvale-part9-")
        label = f"{task_name} / {item['modality']}"
        full_target = item["full_query_metrics"]["first_overlapping_rank"]
        routed_target = item["routed_query_metrics"]["first_overlapping_rank"]
        full_boundary = item["full_query_metrics"]["best_interval_rank"]
        routed_boundary = item["routed_query_metrics"]["best_interval_rank"]
        print(
            f"{label:39} {f'{full_target} -> {routed_target}':>20} "
            f"{f'{full_boundary} -> {routed_boundary}':>20}  "
            f"{item['routed_query']}"
        )
    print()
    print("FineLAP paths kept separate")
    print(
        f"{'Task':27} {'mixed':>7} {'window':>17} {'activation':>21}"
    )
    print(f"{'':27} {'':>7} {'full / phrase':>17} {'full / phrase':>21}")
    for item in report["sound_stream_comparisons"]:
        label = item["task_id"].removeprefix("longvale-part9-")
        mixed_rank = item["current_mixed_full_query"]["first_overlapping_rank"]
        window_rank = item["window_caption_query"]["metrics"][
            "first_overlapping_rank"
        ]
        window_phrase_rank = item["window_phrase_query"]["metrics"][
            "first_overlapping_rank"
        ]
        activation_full_rank = item["activation_full_query"]["metrics"][
            "first_overlapping_rank"
        ]
        activation_rank = item["activation_phrase_query"]["metrics"][
            "first_overlapping_rank"
        ]
        print(
            f"{label:27} {str(mixed_rank):>7} "
            f"{f'{window_rank} / {window_phrase_rank}':>17} "
            f"{f'{activation_full_rank} / {activation_rank}':>21}"
        )
    print(
        "Sound tasks with target evidence in a top 3: "
        f"mixed {summary['current_sound_tasks_with_target_overlap_top3']}/"
        f"{len(report['sound_stream_comparisons'])}; separated "
        f"{summary['separated_sound_tasks_with_target_overlap_top3']}/"
        f"{len(report['sound_stream_comparisons'])}"
    )
    print(
        "Using the short phrase for both separated streams: "
        f"{summary['one_phrase_separated_sound_tasks_with_target_overlap_top3']}/"
        f"{len(report['sound_stream_comparisons'])} sound tasks"
    )
    resources = report["resource_use"]
    print()
    print(
        f"Local embeddings: {resources['local_text_embedding_calls']}; "
        f"time: {resources['elapsed_seconds']:.3f}s; "
        "Codex/API calls: 0"
    )
    print(f"Full evidence: {report['output']}")


def main() -> int:
    report = compare_query_routing()
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
