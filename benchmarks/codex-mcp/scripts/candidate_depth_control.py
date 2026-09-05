from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from modality_probe import _load_environment, _output_path
from vidxp.application_models import SearchHit, SearchResult
from vidxp.benchmarks.agent_ablation_score import interval_iou
from vidxp.search_fusion import fuse_search_results


TASKS_PATH = (
    Path(__file__).resolve().parent.parent
    / "tasks"
    / "longvale-part9-pilot.json"
)
DEPTHS = (1, 3, 5, 10, 20, 50, 100, 250, 500, 1000)
THRESHOLDS = (0.3, 0.5, 0.7)
OUTPUT_TOP_K = 10
BOARD_TOP_K = 3
EVALUATION_TOP_K = 5


def _saved_result(
    probe: dict[str, Any],
    modality: str,
    depth: int,
) -> SearchResult:
    records = sorted(
        probe["modalities"][modality]["records"],
        key=lambda record: record["retrieval_rank"],
    )[:depth]
    return SearchResult(
        query_id=f"candidate-depth:{probe['task_id']}:{modality}:{depth}",
        query=probe["query"],
        modality=modality,
        hits=tuple(
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
        ),
    )


def _moment_metrics(
    moments: tuple[Any, ...],
    expected_start: float,
    expected_end: float,
) -> dict[str, Any]:
    ious = [
        interval_iou(moment.start, moment.end, expected_start, expected_end)
        for moment in moments
    ]
    best_index = max(range(len(ious)), key=ious.__getitem__) if ious else None
    return {
        "top_interval": (
            {
                "start_seconds": moments[0].start,
                "end_seconds": moments[0].end,
            }
            if moments
            else None
        ),
        "top_1_iou": ious[0] if ious else 0.0,
        "best_board_iou": max(ious[:BOARD_TOP_K], default=0.0),
        "best_top_5_iou": max(ious[:EVALUATION_TOP_K], default=0.0),
        "best_output_iou": max(ious, default=0.0),
        "best_output_rank": best_index + 1 if best_index is not None else None,
        "top_candidates": [
            {
                "rank": moment.rank,
                "start_seconds": moment.start,
                "end_seconds": moment.end,
                "iou": iou,
                "score": moment.score,
                "evidence": [
                    {
                        "modality": hit.modality,
                        "rank": hit.rank,
                        "start_seconds": hit.start,
                        "end_seconds": hit.end,
                    }
                    for hit in moment.hits
                ],
            }
            for moment, iou in zip(moments, ious)
        ],
        "returned_moments": len(moments),
    }


def _task_depth_result(probe: dict[str, Any], depth: int) -> dict[str, Any]:
    modalities = tuple(probe["modalities"])
    fused = fuse_search_results(
        query=probe["query"],
        requested_modalities=modalities,
        results=tuple(
            _saved_result(probe, modality, depth) for modality in modalities
        ),
        media_id=probe["media_id"],
        top_k=OUTPUT_TOP_K,
        snapshot_id=probe["snapshot_id"],
    )
    return _moment_metrics(
        fused.moments,
        float(probe["expected_start"]),
        float(probe["expected_end"]),
    )


def _aggregate(task_results: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(task_results)
    return {
        "tasks": count,
        "mean_top_1_iou": sum(item["top_1_iou"] for item in task_results)
        / count,
        "mean_best_board_iou": sum(
            item["best_board_iou"] for item in task_results
        )
        / count,
        "mean_best_top_5_iou": sum(
            item["best_top_5_iou"] for item in task_results
        )
        / count,
        "mean_best_output_iou": sum(
            item["best_output_iou"] for item in task_results
        )
        / count,
        "recall_at_1": {
            str(threshold): sum(
                item["top_1_iou"] >= threshold for item in task_results
            )
            / count
            for threshold in THRESHOLDS
        },
        "recall_at_board_3": {
            str(threshold): sum(
                item["best_board_iou"] >= threshold for item in task_results
            )
            / count
            for threshold in THRESHOLDS
        },
        "recall_at_5": {
            str(threshold): sum(
                item["best_top_5_iou"] >= threshold for item in task_results
            )
            / count
            for threshold in THRESHOLDS
        },
        "recall_at_output_10": {
            str(threshold): sum(
                item["best_output_iou"] >= threshold for item in task_results
            )
            / count
            for threshold in THRESHOLDS
        },
    }


def compare_candidate_depths(output: Path | None = None) -> dict[str, Any]:
    _load_environment()
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    probes = []
    for task in tasks:
        path = _output_path(task["id"], None)
        if not path.is_file():
            raise RuntimeError(
                "saved full-list probe is missing; run "
                f"'./benchmarks/codex-mcp/run probe {task['id']}' first"
            )
        probes.append(json.loads(path.read_text(encoding="utf-8")))

    maximum_records = max(
        modality["record_count"]
        for probe in probes
        for modality in probe["modalities"].values()
    )
    depths = (*DEPTHS, maximum_records)
    per_depth: dict[str, Any] = {}
    for depth in depths:
        task_results = [
            {
                "task_id": probe["task_id"],
                **_task_depth_result(probe, depth),
            }
            for probe in probes
        ]
        label = "all" if depth == maximum_records else str(depth)
        per_depth[label] = {
            "candidate_depth_per_modality": (
                "all available records" if label == "all" else depth
            ),
            "aggregate": _aggregate(task_results),
            "tasks": task_results,
        }

    payload = {
        "schema_version": 2,
        "control_id": "candidate-depth-direct-overlap-control-v2",
        "control": (
            "Replay saved full-query modality rankings through the production "
            "rank-anchored direct-overlap RRF implementation. Vary only the maximum "
            "number of candidates retained per modality."
        ),
        "task_count": len(probes),
        "output_top_k": OUTPUT_TOP_K,
        "evidence_board_top_k": BOARD_TOP_K,
        "depths": per_depth,
        "notes": [
            "Depth values are curve samples, not proposed product defaults.",
            "No model inference, agent run, or API call is made.",
            "The all-records point checks that additional candidates do not "
            "expand a result through transitive overlap.",
        ],
    }
    destination = output
    if destination is None:
        destination = _output_path("candidate-depth-control", None).with_name(
            "candidate-depth-direct-overlap-control.json"
        )
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _print_summary(payload: dict[str, Any]) -> None:
    print(
        "depth  mean@1  R1@.3/.5/.7  "
        "Rboard3@.3/.5/.7  R5@.3/.5/.7  R10@.3/.5/.7"
    )
    for label, result in payload["depths"].items():
        aggregate = result["aggregate"]
        r1 = aggregate["recall_at_1"]
        board = aggregate["recall_at_board_3"]
        top_5 = aggregate["recall_at_5"]
        output = aggregate["recall_at_output_10"]
        print(
            f"{label:>5}  {aggregate['mean_top_1_iou']:.4f}  "
            f"{r1['0.3']:.2f}/{r1['0.5']:.2f}/{r1['0.7']:.2f}  "
            f"{board['0.3']:.2f}/{board['0.5']:.2f}/{board['0.7']:.2f}  "
            f"{top_5['0.3']:.2f}/{top_5['0.5']:.2f}/{top_5['0.7']:.2f}  "
            f"{output['0.3']:.2f}/{output['0.5']:.2f}/{output['0.7']:.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay saved rankings at independent candidate depths."
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    payload = compare_candidate_depths(arguments.output)
    _print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
