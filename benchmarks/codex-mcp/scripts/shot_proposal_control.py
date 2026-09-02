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


def _task(task_id: str) -> dict[str, Any]:
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    matches = [task for task in tasks if task.get("id") == task_id]
    if len(matches) != 1:
        raise ValueError(f"unknown task id: {task_id}")
    return matches[0]


def _probe_path(task_id: str) -> Path:
    root = Path(_required_environment("VIDXP_EVAL_DATA_DIR")).parent
    return root / "localization" / f"{task_id}.probe.json"


def compare_shot_proposals(task_id: str) -> dict:
    _load_environment()
    task = _task(task_id)
    base_path = _probe_path(task_id)
    if not base_path.is_file():
        raise RuntimeError(f"run './benchmarks/codex-mcp/run probe {task_id}' first")
    probe = json.loads(base_path.read_text(encoding="utf-8"))
    if "scene" not in probe["modalities"]:
        raise ValueError(f"task has no saved scene curve: {task_id}")
    source = Path(_required_environment("VIDXP_EVAL_WORKSPACE")) / task["media_relpath"]
    if not source.is_file():
        raise RuntimeError(f"prepared benchmark media is missing: {source}")

    started = time.perf_counter()
    detected = detect(
        str(source),
        ContentDetector(threshold=DIWAN_CONTENT_THRESHOLD),
        show_progress=False,
    )
    detection_seconds = time.perf_counter() - started
    shots = tuple(
        TemporalShot(start=start.seconds, end=end.seconds)
        for start, end in detected
    )
    ranked = rank_shots_from_scene_records(
        shots,
        probe["modalities"]["scene"]["records"],
    )
    if not ranked:
        raise RuntimeError(
            "PySceneDetect produced no proposal containing a scene sample"
        )
    candidate_top_k = int(
        probe["current_control"]["candidate_top_k_per_modality"]
    )
    fused = rank_shots_with_rrf_evidence(
        ranked,
        {
            modality: result["records"]
            for modality, result in probe["modalities"].items()
        },
        candidate_top_k=candidate_top_k,
        rank_constant=RRF_RANK_CONSTANT,
    )

    expected_start = float(task["expected_start"])
    expected_end = float(task["expected_end"])
    top = ranked[0]
    top_fused = fused[0]
    oracle = max(
        ranked,
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
    payload = {
        "schema_version": 2,
        "task_id": task_id,
        "method": {
            "paper": DIWAN_PAPER_URL,
            "component": "ShotDetect proposals without SimpleWatershed",
            "published_content_threshold": DIWAN_CONTENT_THRESHOLD,
            "pyscenedetect_version": "0.7",
            "rrf_candidate_top_k": candidate_top_k,
            "rrf_rank_constant": RRF_RANK_CONSTANT,
            "rrf_boundary_rule": "keep the selected shot interval unchanged",
            "adaptations": [
                "reuse VidXP one-fps SigLIP2 records instead of CLIP-ViT-B/32",
                "reuse globally sampled frames instead of sampling within each shot",
                "rank each shot by its maximum contained scene ordering score",
                (
                    "rank fixed shot candidates with VidXP RRF using the best "
                    "overlapping top-k evidence rank per non-scene modality"
                ),
            ],
            "excluded": [
                "SimpleWatershed and its QVHighlights-tuned similarity threshold",
                "video captioning matcher",
            ],
        },
        "control": probe["current_control"],
        "top_retrieved": metrics(top),
        "top_rrf_proposal": {
            **metrics(top_fused),
            "score": top_fused.score,
            "scene_rank": top_fused.scene_rank,
            "best_ranks": dict(top_fused.best_ranks),
        },
        "best_proposal_oracle": {**oracle_metrics, "retrieval_rank": oracle.rank},
        "recall": {
            f"tiou_{threshold}": oracle_metrics["temporal_iou"] >= threshold
            for threshold in (0.3, 0.5, 0.7)
        },
        "resource_use": {
            "detection_seconds": detection_seconds,
            "detected_proposals": len(shots),
            "scored_proposals": len(ranked),
            "scene_records_reused": len(probe["modalities"]["scene"]["records"]),
            "model_calls": 0,
            "stored_bytes": 0,
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
        "control": probe["current_control"]["top_moment_metrics"],
        "top_retrieved": payload["top_retrieved"],
        "top_rrf_proposal": payload["top_rrf_proposal"],
        "best_proposal_oracle": payload["best_proposal_oracle"],
        "recall": payload["recall"],
        "resource_use": payload["resource_use"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Diwan-style disjoint shot proposals on one saved probe."
    )
    parser.add_argument("task_id")
    arguments = parser.parse_args()
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
