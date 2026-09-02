from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any

# PySceneDetect eagerly imports every installed backend. Keep its optional PyAV
# backend unloaded so macOS does not load PyAV and OpenCV FFmpeg libraries into
# this process together; this control explicitly uses the OpenCV backend.
sys.modules["av"] = None

from scenedetect import ContentDetector, detect  # noqa: E402

from vidxp.benchmarks.agent_ablation_score import interval_iou
from vidxp.benchmarks.shot_proposals import (
    DIWAN_CONTENT_THRESHOLD,
    DIWAN_PAPER_URL,
    TemporalShot,
    rank_shots_from_scene_records,
    rank_shots_with_rrf_evidence,
)
from vidxp.search_fusion import RRF_RANK_CONSTANT


BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = BENCHMARK_ROOT / "tasks" / "longvale-part9-pilot.json"
BOUNDARY_TOLERANCE_SECONDS = 0.05


def _load_environment() -> None:
    path = BENCHMARK_ROOT / ".env"
    if not path.is_file():
        raise RuntimeError("run benchmark setup before comparing shot proposals")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        values = shlex.split(raw_value, posix=True)
        if len(values) != 1:
            raise RuntimeError(f"invalid value for {name} in benchmark .env")
        os.environ.setdefault(name, values[0])


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is missing from benchmark .env")
    return value


def _tasks() -> list[dict[str, Any]]:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def _task(task_id: str) -> dict[str, Any]:
    tasks = _tasks()
    matches = [task for task in tasks if task.get("id") == task_id]
    if len(matches) != 1:
        raise ValueError(f"unknown task id: {task_id}")
    return matches[0]


def _probe_path(task_id: str) -> Path:
    root = Path(_required_environment("VIDXP_EVAL_DATA_DIR")).parent
    return root / "localization" / f"{task_id}.probe.json"


def _detect_shots(source: Path) -> tuple[tuple[TemporalShot, ...], float]:
    started = time.perf_counter()
    detected = detect(
        str(source),
        ContentDetector(threshold=DIWAN_CONTENT_THRESHOLD),
        show_progress=False,
    )
    detection_seconds = time.perf_counter() - started
    return (
        tuple(
            TemporalShot(start=start.seconds, end=end.seconds)
            for start, end in detected
        ),
        detection_seconds,
    )


def compare_shot_proposals(
    task_id: str,
    *,
    detected_shots: tuple[TemporalShot, ...] | None = None,
    detection_seconds: float | None = None,
    detection_reused: bool = False,
) -> dict:
    _load_environment()
    task = _task(task_id)
    base_path = _probe_path(task_id)
    if not base_path.is_file():
        raise RuntimeError(f"run './benchmarks/codex-mcp/run probe {task_id}' first")
    probe = json.loads(base_path.read_text(encoding="utf-8"))
    source = Path(_required_environment("VIDXP_EVAL_WORKSPACE")) / task["media_relpath"]
    if not source.is_file():
        raise RuntimeError(f"prepared benchmark media is missing: {source}")

    if detected_shots is None:
        shots, measured_detection_seconds = _detect_shots(source)
        detection_seconds = measured_detection_seconds
    else:
        shots = detected_shots
    if detection_seconds is None:
        raise ValueError("detection_seconds is required with detected_shots")
    if not shots:
        raise RuntimeError("PySceneDetect produced no proposals")
    scene_result = probe["modalities"].get("scene")
    ranked = (
        rank_shots_from_scene_records(shots, scene_result["records"])
        if scene_result
        else ()
    )
    if scene_result and not ranked:
        raise RuntimeError(
            "PySceneDetect produced no proposal containing a scene sample"
        )
    candidate_top_k = int(
        probe["current_control"]["candidate_top_k_per_modality"]
    )
    fused = rank_shots_with_rrf_evidence(
        shots,
        {
            modality: result["records"]
            for modality, result in probe["modalities"].items()
        },
        scene_ranking=ranked,
        candidate_top_k=candidate_top_k,
        rank_constant=RRF_RANK_CONSTANT,
    )
    if not fused:
        raise RuntimeError("no proposal overlaps the saved top-k evidence")

    expected_start = float(task["expected_start"])
    expected_end = float(task["expected_end"])
    top = ranked[0] if ranked else None
    top_fused = fused[0]
    oracle = max(
        shots,
        key=lambda shot: interval_iou(
            shot.start,
            shot.end,
            expected_start,
            expected_end,
        ),
    )

    def metrics(shot) -> dict:
        return {
            "start_seconds": shot.start,
            "end_seconds": shot.end,
            "temporal_iou": interval_iou(
                shot.start,
                shot.end,
                expected_start,
                expected_end,
            ),
            "start_error_seconds": shot.start - expected_start,
            "end_error_seconds": shot.end - expected_end,
        }

    output = base_path.with_name(base_path.name.replace(".probe.json", ".shots.json"))
    oracle_metrics = metrics(oracle)
    internal_boundaries = sorted(
        shot.end
        for shot in shots[:-1]
        if expected_start + BOUNDARY_TOLERANCE_SECONDS
        < shot.end
        < expected_end - BOUNDARY_TOLERANCE_SECONDS
    )

    def fused_payload(shot) -> dict:
        return {
            **metrics(shot),
            "score": shot.score,
            "scene_rank": shot.scene_rank,
            "best_ranks": dict(shot.best_ranks),
            "evidence": [
                {
                    "modality": item.modality,
                    "rank": item.rank,
                    "source_id": item.source_id,
                    "proposal_overlap_count": item.proposal_overlap_count,
                }
                for item in shot.evidence
            ],
        }

    adaptations = [
        (
            "rank fixed shot candidates with VidXP RRF using the best "
            "overlapping top-k evidence rank per non-scene modality"
        )
    ]
    if scene_result:
        adaptations[:0] = [
            "reuse VidXP one-fps SigLIP2 records instead of CLIP-ViT-B/32",
            "reuse globally sampled frames instead of sampling within each shot",
            "rank each shot by its maximum contained scene ordering score",
        ]

    payload = {
        "schema_version": 3,
        "task_id": task_id,
        "declared_modalities": task["modalities"],
        "ground_truth": {
            "start_seconds": expected_start,
            "end_seconds": expected_end,
            "detected_boundaries_inside": internal_boundaries,
            "spans_multiple_detected_shots": bool(internal_boundaries),
            "boundary_tolerance_seconds": BOUNDARY_TOLERANCE_SECONDS,
        },
        "method": {
            "paper": DIWAN_PAPER_URL,
            "component": "ShotDetect proposals without SimpleWatershed",
            "published_content_threshold": DIWAN_CONTENT_THRESHOLD,
            "pyscenedetect_version": "0.7",
            "rrf_non_scene_evidence_top_k": candidate_top_k,
            "rrf_rank_constant": RRF_RANK_CONSTANT,
            "rrf_boundary_rule": "keep the selected shot interval unchanged",
            "scene_matcher_applied": bool(scene_result),
            "adaptations": adaptations,
            "excluded": [
                "SimpleWatershed and its QVHighlights-tuned similarity threshold",
                "video captioning matcher",
            ],
        },
        "control": probe["current_control"],
        "top_retrieved": metrics(top) if top else None,
        "top_rrf_proposal": fused_payload(top_fused),
        "best_proposal_oracle": {
            **oracle_metrics,
            "scene_retrieval_rank": next(
                (
                    shot.rank
                    for shot in ranked
                    if shot.start == oracle.start and shot.end == oracle.end
                ),
                None,
            ),
        },
        "recall": {
            f"tiou_{threshold}": oracle_metrics["temporal_iou"] >= threshold
            for threshold in (0.3, 0.5, 0.7)
        },
        "resource_use": {
            "detection_seconds": detection_seconds,
            "detection_reused": detection_reused,
            "detected_proposals": len(shots),
            "scene_scored_proposals": len(ranked),
            "rrf_scored_proposals": len(fused),
            "scene_records_reused": len(scene_result["records"]) if scene_result else 0,
            "model_calls": 0,
            "stored_bytes": 0,
        },
        "probe_resource_use": {
            "elapsed_seconds": probe["elapsed_seconds"],
            "text_embedding_calls": sum(
                result["model_calls"]["text_embedding"]
                for result in probe["modalities"].values()
            ),
        },
        "proposals": [
            {
                "rank": shot.rank,
                "start_seconds": shot.start,
                "end_seconds": shot.end,
                "ordering_score": shot.score,
                "source_ids": shot.source_ids,
            }
            for shot in ranked
        ],
        "rrf_proposals": [
            {
                "rank": shot.rank,
                "scene_rank": shot.scene_rank,
                "start_seconds": shot.start,
                "end_seconds": shot.end,
                "score": shot.score,
                "best_ranks": dict(shot.best_ranks),
                "source_ids": shot.source_ids,
                "evidence": [
                    {
                        "modality": item.modality,
                        "rank": item.rank,
                        "source_id": item.source_id,
                        "proposal_overlap_count": item.proposal_overlap_count,
                    }
                    for item in shot.evidence
                ],
            }
            for shot in fused
        ],
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(output),
        "task_id": task_id,
        "declared_modalities": task["modalities"],
        "ground_truth": payload["ground_truth"],
        "control": probe["current_control"]["top_moment_metrics"],
        "top_retrieved": payload["top_retrieved"],
        "top_rrf_proposal": payload["top_rrf_proposal"],
        "best_proposal_oracle": payload["best_proposal_oracle"],
        "recall": payload["recall"],
        "resource_use": payload["resource_use"],
        "probe_resource_use": payload["probe_resource_use"],
    }


def _method_summary(results: list[dict], key: str) -> dict:
    values = [result[key] for result in results if result[key] is not None]
    return {
        "tasks": len(values),
        "mean_temporal_iou": sum(item["temporal_iou"] for item in values)
        / len(values),
        "threshold_rates": {
            f"tiou_{threshold}": sum(
                item["temporal_iou"] >= threshold for item in values
            )
            / len(values)
            for threshold in (0.3, 0.5, 0.7)
        },
        "mean_absolute_start_error_seconds": sum(
            abs(item["start_error_seconds"]) for item in values
        )
        / len(values),
        "mean_absolute_end_error_seconds": sum(
            abs(item["end_error_seconds"]) for item in values
        )
        / len(values),
    }


def compare_held_out() -> dict:
    _load_environment()
    tasks = _tasks()[2:]
    missing = [task["id"] for task in tasks if not _probe_path(task["id"]).is_file()]
    if missing:
        raise RuntimeError(
            "missing held-out probes; run './benchmarks/codex-mcp/run probe TASK_ID' "
            f"for: {', '.join(missing)}"
        )

    detected: dict[str, tuple[tuple[TemporalShot, ...], float]] = {}
    results = []
    for task in tasks:
        source = (
            Path(_required_environment("VIDXP_EVAL_WORKSPACE"))
            / task["media_relpath"]
        )
        cache_key = str(source)
        reused = cache_key in detected
        if not reused:
            detected[cache_key] = _detect_shots(source)
        shots, seconds = detected[cache_key]
        results.append(
            compare_shot_proposals(
                task["id"],
                detected_shots=shots,
                detection_seconds=seconds,
                detection_reused=reused,
            )
        )

    scene_comparisons = []
    for result in results:
        scene = result["top_retrieved"]
        if scene is None:
            continue
        fused = result["top_rrf_proposal"]
        same = (scene["start_seconds"], scene["end_seconds"]) == (
            fused["start_seconds"],
            fused["end_seconds"],
        )
        delta = fused["temporal_iou"] - scene["temporal_iou"]
        if same:
            outcome = "unchanged"
        elif delta > 1e-12:
            outcome = "changed_helped"
        elif delta < -1e-12:
            outcome = "changed_hurt"
        else:
            outcome = "changed_same_iou"
        scene_comparisons.append(
            {
                "task_id": result["task_id"],
                "outcome": outcome,
                "iou_delta": delta,
            }
        )

    ambiguous = [
        {
            "task_id": result["task_id"],
            "evidence": [
                item
                for item in result["top_rrf_proposal"]["evidence"]
                if item["proposal_overlap_count"] > 1
            ],
        }
        for result in results
    ]
    ambiguous = [item for item in ambiguous if item["evidence"]]
    failure_split = {
        "boundary_limited": [
            result["task_id"]
            for result in results
            if result["best_proposal_oracle"]["temporal_iou"] < 0.5
        ],
        "ranking_limited": [
            result["task_id"]
            for result in results
            if result["best_proposal_oracle"]["temporal_iou"] >= 0.5
            and result["top_rrf_proposal"]["temporal_iou"] < 0.5
        ],
    }
    scene_results = [result for result in results if result["top_retrieved"]]
    no_scene_results = [result for result in results if not result["top_retrieved"]]
    aggregate = {
        "schema_version": 1,
        "scope": "held-out tasks 3-10 from the Codex MCP pilot manifest",
        "tasks": len(results),
        "scene_comparable_tasks": len(scene_comparisons),
        "methods": {
            "current_connected_union": _method_summary(results, "control"),
            "scene_ranked_shot": _method_summary(results, "top_retrieved"),
            "proposal_preserving_rrf": _method_summary(
                results,
                "top_rrf_proposal",
            ),
            "current_union_scene_comparable": _method_summary(
                scene_results,
                "control",
            ),
            "rrf_scene_comparable": _method_summary(
                scene_results,
                "top_rrf_proposal",
            ),
            "current_union_without_scene": _method_summary(
                no_scene_results,
                "control",
            ),
            "rrf_without_scene": _method_summary(
                no_scene_results,
                "top_rrf_proposal",
            ),
            "best_single_shot_oracle": _method_summary(
                results,
                "best_proposal_oracle",
            ),
        },
        "scene_vs_rrf": {
            "unchanged": sum(
                item["outcome"] == "unchanged" for item in scene_comparisons
            ),
            "changed_helped": sum(
                item["outcome"] == "changed_helped" for item in scene_comparisons
            ),
            "changed_hurt": sum(
                item["outcome"] == "changed_hurt" for item in scene_comparisons
            ),
            "changed_same_iou": sum(
                item["outcome"] == "changed_same_iou"
                for item in scene_comparisons
            ),
            "tasks": scene_comparisons,
        },
        "top_rrf_ambiguous_evidence": {
            "tasks": len(ambiguous),
            "details": ambiguous,
        },
        "failure_split_at_tiou_0_5": failure_split,
        "ground_truth_spans_multiple_detected_shots": [
            result["task_id"]
            for result in results
            if result["ground_truth"]["spans_multiple_detected_shots"]
        ],
        "unique_videos": len(detected),
        "resource_use": {
            "shot_detection_seconds": sum(
                seconds for _, seconds in detected.values()
            ),
            "probe_elapsed_seconds": sum(
                result["probe_resource_use"]["elapsed_seconds"]
                for result in results
            ),
            "local_text_embedding_calls": sum(
                result["probe_resource_use"]["text_embedding_calls"]
                for result in results
            ),
            "shot_model_calls": 0,
            "codex_calls": 0,
            "new_index_bytes": 0,
            "peak_memory_bytes": None,
        },
        "results": results,
    }
    output = _probe_path("held-out").with_name("shots-held-out.json")
    output.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(output),
        "scope": aggregate["scope"],
        "tasks": aggregate["tasks"],
        "scene_comparable_tasks": aggregate["scene_comparable_tasks"],
        "methods": aggregate["methods"],
        "scene_vs_rrf": aggregate["scene_vs_rrf"],
        "failure_split_at_tiou_0_5": failure_split,
        "ambiguous_evidence_tasks": [
            item["task_id"] for item in ambiguous
        ],
        "ground_truth_spans_multiple_detected_shots": aggregate[
            "ground_truth_spans_multiple_detected_shots"
        ],
        "resource_use": aggregate["resource_use"],
        "per_task": [
            {
                "task_id": result["task_id"],
                "current_iou": result["control"]["temporal_iou"],
                "scene_iou": (
                    result["top_retrieved"]["temporal_iou"]
                    if result["top_retrieved"]
                    else None
                ),
                "rrf_iou": result["top_rrf_proposal"]["temporal_iou"],
                "best_shot_iou": result["best_proposal_oracle"]["temporal_iou"],
            }
            for result in results
        ],
    }


def _print_held_out(result: dict) -> None:
    methods = result["methods"]
    rows = (
        ("Current union (8)", methods["current_connected_union"]),
        ("Best single shot (8)", methods["best_single_shot_oracle"]),
        ("Scene-ranked shot (6)", methods["scene_ranked_shot"]),
        ("RRF, same scene tasks (6)", methods["rrf_scene_comparable"]),
        ("RRF, no-scene tasks (2)", methods["rrf_without_scene"]),
    )
    print("Held-out shot comparison")
    print("Method                     mean IoU   >=.3   >=.5   >=.7")
    for label, metrics in rows:
        recall = metrics["threshold_rates"]
        print(
            f"{label:<27} {metrics['mean_temporal_iou']:>7.4f} "
            f"{recall['tiou_0.3']:>7.3f} {recall['tiou_0.5']:>7.3f} "
            f"{recall['tiou_0.7']:>7.3f}"
        )

    comparison = result["scene_vs_rrf"]
    print(
        "\nScene vs RRF: "
        f"{comparison['unchanged']} unchanged, "
        f"{comparison['changed_helped']} helped, "
        f"{comparison['changed_hurt']} hurt, "
        f"{comparison['changed_same_iou']} changed with equal IoU."
    )
    print("\nTask                          current   scene     RRF   best shot")
    for task in result["per_task"]:
        label = task["task_id"].removeprefix("longvale-part9-")
        scene = (
            "n/a"
            if task["scene_iou"] is None
            else f"{task['scene_iou']:.4f}"
        )
        print(
            f"{label:<29} {task['current_iou']:>7.4f} {scene:>7} "
            f"{task['rrf_iou']:>7.4f} {task['best_shot_iou']:>11.4f}"
        )

    failures = result["failure_split_at_tiou_0_5"]
    def short(values: list[str]) -> str:
        return ", ".join(
            value.removeprefix("longvale-part9-") for value in values
        )
    print(f"\nBoundary-limited at tIoU .5: {short(failures['boundary_limited'])}")
    print(f"Ranking-limited at tIoU .5: {short(failures['ranking_limited'])}")
    print(
        "Ambiguous evidence: "
        f"{len(result['ambiguous_evidence_tasks'])}/{result['tasks']} tasks"
    )
    print(
        "References crossing detected cuts: "
        f"{len(result['ground_truth_spans_multiple_detected_shots'])}"
    )
    resources = result["resource_use"]
    print(
        "Resource use: "
        f"{resources['local_text_embedding_calls']} local text embeddings, "
        f"{resources['probe_elapsed_seconds']:.3f}s probe time, "
        f"{resources['shot_detection_seconds']:.3f}s shot detection, "
        f"{resources['codex_calls']} Codex calls."
    )
    print(f"Full evidence: {result['output']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare disjoint shot proposals on saved modality probes."
    )
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--held-out", action="store_true")
    arguments = parser.parse_args()
    if arguments.held_out == (arguments.task_id is not None):
        parser.error("provide one task ID or --held-out")
    if arguments.held_out:
        _print_held_out(compare_held_out())
    else:
        print(
            json.dumps(
                compare_shot_proposals(arguments.task_id),
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
