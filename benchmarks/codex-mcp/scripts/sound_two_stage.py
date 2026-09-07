from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import mean
from typing import Any

from vidxp.application_models import ListMediaCommand, MediaState
from vidxp.benchmarks.agent_ablation_score import interval_iou
from vidxp.capabilities.search import search_embeddings
from vidxp.capabilities.sound.operations import (
    GLOBAL_REPRESENTATION,
    LOCAL_REPRESENTATION,
    REQUIRED_METADATA,
    _activation_scope,
    search_sound,
    sound_embedding,
)
from vidxp.capabilities.sound.specs import FINELAP_MODEL
from vidxp.composition import create_local_application
from vidxp.search_fusion import fuse_search_results

from modality_probe import (
    BENCHMARK_ROOT,
    TASKS_PATH,
    _load_environment,
    _required_environment,
)
from query_routing_control import QUERY_PLAN_PATH


TOP_K = 3


def _package_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _git_state() -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[3]

    def run(*arguments: str) -> str:
        return subprocess.check_output(
            ("git", *arguments),
            cwd=repository,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()

    try:
        return {
            "revision": run("rev-parse", "HEAD"),
            "working_tree_dirty": bool(run("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "working_tree_dirty": None}


def _hit_metrics(
    hits: tuple[Any, ...], expected_start: float, expected_end: float
) -> dict[str, Any]:
    rows = []
    for hit in hits:
        iou = interval_iou(hit.start, hit.end, expected_start, expected_end)
        rows.append(
            {
                "rank": hit.rank,
                "start_seconds": hit.start,
                "end_seconds": hit.end,
                "temporal_iou": iou,
                "overlaps_target": iou > 0,
                "raw_distance": hit.raw_distance,
                "source_id": hit.source_id,
                "window_index": hit.metadata.get("window_index"),
                "activation_index": hit.metadata.get("activation_index"),
                "context_rank": hit.metadata.get("context_rank"),
            }
        )
    first_overlap = next((row["rank"] for row in rows if row["overlaps_target"]), None)
    return {
        "target_covered": first_overlap is not None,
        "first_overlap_rank": first_overlap,
        "best_temporal_iou": max((row["temporal_iou"] for row in rows), default=0.0),
        "hits": rows,
    }


def _moment_metrics(
    moment: Any, expected_start: float, expected_end: float
) -> dict[str, Any]:
    if moment is None:
        return {
            "start_seconds": None,
            "end_seconds": None,
            "temporal_iou": 0.0,
            "start_absolute_error_seconds": None,
            "end_absolute_error_seconds": None,
            "duration_absolute_error_seconds": None,
        }
    return {
        "start_seconds": moment.start,
        "end_seconds": moment.end,
        "temporal_iou": interval_iou(
            moment.start, moment.end, expected_start, expected_end
        ),
        "start_absolute_error_seconds": abs(moment.start - expected_start),
        "end_absolute_error_seconds": abs(moment.end - expected_end),
        "duration_absolute_error_seconds": abs(
            (moment.end - moment.start) - (expected_end - expected_start)
        ),
    }


def run_benchmark() -> dict[str, Any]:
    _load_environment()
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    query_plan = json.loads(QUERY_PLAN_PATH.read_text(encoding="utf-8"))
    held_out = [
        task
        for task in tasks[2:]
        if "sound" in task["modalities"] and task["id"] in query_plan["tasks"]
    ]
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
    records = []
    started = time.perf_counter()

    with application.index_backend.open_store(config) as storage:
        with application.runtime.scheduler.inference():
            for task in held_out:
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
                query = str(task["query"])
                expected_start = float(task["expected_start"])
                expected_end = float(task["expected_end"])

                product_started = time.perf_counter()
                product = search_sound(
                    query,
                    config=config,
                    runtime=application.runtime,
                    top_k=TOP_K,
                    video_id=media_id,
                    storage=storage,
                )
                product_elapsed = time.perf_counter() - product_started
                fused = fuse_search_results(
                    query=query,
                    requested_modalities=("sound",),
                    results=(product,),
                    media_id=media_id,
                    top_k=TOP_K,
                    snapshot_id=config.snapshot_id,
                )

                diagnostic_started = time.perf_counter()
                embedding = sound_embedding(query, application.runtime)
                windows = search_embeddings(
                    query,
                    "sound",
                    embedding,
                    config=config,
                    required_metadata=REQUIRED_METADATA,
                    top_k=TOP_K,
                    video_id=media_id,
                    filters={"representation": GLOBAL_REPRESENTATION},
                    storage=storage,
                )
                activation_scope = _activation_scope(windows.hits, video_id=media_id)
                activation_count = storage.count_records(
                    "sound", video_id=media_id, filters=activation_scope
                )
                gated_activations = search_embeddings(
                    query,
                    "sound",
                    embedding,
                    config=config,
                    required_metadata=REQUIRED_METADATA,
                    top_k=max(1, activation_count),
                    video_id=media_id,
                    filters=activation_scope,
                    storage=storage,
                )
                diagnostic_elapsed = time.perf_counter() - diagnostic_started
                if [hit.source_id for hit in product.hits] != [
                    hit.source_id for hit in gated_activations.hits[:TOP_K]
                ]:
                    raise RuntimeError(
                        f"product and diagnostic rankings differ for {task['id']}"
                    )

                records.append(
                    {
                        "task_id": task["id"],
                        "video_file": filename,
                        "query": query,
                        "expected": {
                            "start_seconds": expected_start,
                            "end_seconds": expected_end,
                        },
                        "global_gate_top3": _hit_metrics(
                            windows.hits, expected_start, expected_end
                        ),
                        "product_activation_top3": _hit_metrics(
                            product.hits, expected_start, expected_end
                        ),
                        "gated_activation_full_ranking": _hit_metrics(
                            gated_activations.hits, expected_start, expected_end
                        ),
                        "final_sound_only_top1": _moment_metrics(
                            fused.moments[0] if fused.moments else None,
                            expected_start,
                            expected_end,
                        ),
                        "counts": {
                            "gated_activation_records": activation_count,
                            "product_text_embeddings": 1,
                            "product_vector_queries": 2,
                            "diagnostic_text_embeddings": 1,
                            "diagnostic_vector_queries": 2,
                        },
                        "runtime_seconds": {
                            "product": product_elapsed,
                            "diagnostic": diagnostic_elapsed,
                        },
                    }
                )

    final = [record["final_sound_only_top1"] for record in records]
    count = len(records)
    summary = {
        "task_count": count,
        "global_gate_top3_coverage": sum(
            record["global_gate_top3"]["target_covered"] for record in records
        )
        / count,
        "product_activation_top1_coverage": sum(
            record["product_activation_top3"]["hits"][0]["overlaps_target"]
            for record in records
        )
        / count,
        "product_activation_top3_coverage": sum(
            record["product_activation_top3"]["target_covered"] for record in records
        )
        / count,
        "full_gated_activation_coverage": sum(
            record["gated_activation_full_ranking"]["target_covered"]
            for record in records
        )
        / count,
        "mean_final_top1_temporal_iou": mean(item["temporal_iou"] for item in final),
        "final_top1_recall_at_iou_0_3": (
            sum(item["temporal_iou"] >= 0.3 for item in final) / count
        ),
        "final_top1_recall_at_iou_0_5": (
            sum(item["temporal_iou"] >= 0.5 for item in final) / count
        ),
        "final_top1_recall_at_iou_0_7": (
            sum(item["temporal_iou"] >= 0.7 for item in final) / count
        ),
        "mean_start_absolute_error_seconds": mean(
            item["start_absolute_error_seconds"] for item in final
        ),
        "mean_end_absolute_error_seconds": mean(
            item["end_absolute_error_seconds"] for item in final
        ),
        "mean_duration_absolute_error_seconds": mean(
            item["duration_absolute_error_seconds"] for item in final
        ),
        "total_runtime_seconds": time.perf_counter() - started,
    }
    return {
        "schema_version": 1,
        "benchmark": "finelap-two-stage-held-out",
        "scope": {
            "task_manifest": str(TASKS_PATH.relative_to(BENCHMARK_ROOT)),
            "task_manifest_sha256": hashlib.sha256(TASKS_PATH.read_bytes()).hexdigest(),
            "split": "frozen pilot tasks 3-10; sound-tagged tasks only",
            "query_input": "full frozen application query; no rewrite",
            "top_k": TOP_K,
        },
        "system": {
            "sound_model": FINELAP_MODEL.identity(),
            "global_representation": GLOBAL_REPRESENTATION,
            "local_representation": LOCAL_REPRESENTATION,
            "snapshot_id": config.snapshot_id,
            "vector_distance": config.vector_distance,
            "device": str(application.device),
            "git": _git_state(),
            "machine": {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "packages": {
                    package: _package_version(package)
                    for package in ("torch", "transformers", "chromadb", "numpy")
                },
            },
        },
        "measurement": {
            "product_path_per_task": (
                "one text embedding, global top-3 gate, local top-3 activation ranking"
            ),
            "diagnostic_overhead_per_task": (
                "one extra text embedding and two vector queries expose the complete "
                "gated activation ranking"
            ),
            "final_interval": "top sound-only moment after production fusion",
        },
        "summary": summary,
        "tasks": records,
    }


def main() -> int:
    report = run_benchmark()
    data_directory = Path(_required_environment("VIDXP_EVAL_DATA_DIR"))
    output = data_directory.parent / "localization" / "sound-two-stage-held-out.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary = report["summary"]
    print("FineLAP two-stage held-out status")
    print(f"Tasks: {summary['task_count']} (full frozen queries; top_k={TOP_K})")
    print(
        "Coverage: "
        f"global gate {summary['global_gate_top3_coverage']:.0%}, "
        f"activation top-1 {summary['product_activation_top1_coverage']:.0%}, "
        f"activation top-3 {summary['product_activation_top3_coverage']:.0%}, "
        f"full gated list {summary['full_gated_activation_coverage']:.0%}"
    )
    print(
        "Final top-1: "
        f"mean IoU {summary['mean_final_top1_temporal_iou']:.4f}; "
        f"R@0.3 {summary['final_top1_recall_at_iou_0_3']:.0%}; "
        f"R@0.5 {summary['final_top1_recall_at_iou_0_5']:.0%}; "
        f"R@0.7 {summary['final_top1_recall_at_iou_0_7']:.0%}"
    )
    print("Per task:")
    for record in report["tasks"]:
        final = record["final_sound_only_top1"]
        gated = record["gated_activation_full_ranking"]
        gate_status = (
            "hit" if record["global_gate_top3"]["target_covered"] else "miss"
        )
        top3_status = (
            "hit" if record["product_activation_top3"]["target_covered"] else "miss"
        )
        print(
            f"- {record['task_id']}: "
            f"gate={gate_status}, "
            f"top3={top3_status}, "
            f"first gated target rank={gated['first_overlap_rank'] or 'none'}, "
            f"final={final['start_seconds']:.3f}-{final['end_seconds']:.3f}, "
            f"IoU={final['temporal_iou']:.4f}"
        )
    print(f"Report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
