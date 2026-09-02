from __future__ import annotations

import argparse
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any

from vidxp.application_models import ListMediaCommand, MediaState
from vidxp.capabilities.scene.operations import search_scene
from vidxp.capabilities.scene.specs import SIGLIP2_MODEL
from vidxp.composition import create_local_application


BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
TASKS_PATH = BENCHMARK_ROOT / "tasks" / "longvale-part9-pilot.json"


def _load_environment() -> None:
    path = BENCHMARK_ROOT / ".env"
    if not path.is_file():
        raise RuntimeError("run benchmark setup before exporting a score curve")
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
    return data_directory.parent / "localization" / f"{task_id}.scene-curve.json"


def export_curve(task_id: str, output: Path | None = None) -> dict[str, Any]:
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

    started = time.perf_counter()
    with application.index_backend.open_store(config) as storage:
        record_count = storage.count_records("scene", video_id=media_id)
        if record_count == 0:
            raise RuntimeError(f"no scene records are indexed for {filename}")
        with application.runtime.scheduler.inference():
            result = search_scene(
                task["query"],
                config=config,
                runtime=application.runtime,
                top_k=record_count,
                video_id=media_id,
                storage=storage,
            )
    elapsed_seconds = time.perf_counter() - started
    samples = [
        {
            "start_seconds": hit.start,
            "end_seconds": hit.end,
            "cosine_similarity": 1.0 - hit.raw_distance,
            "retrieval_rank": hit.rank,
            "source_id": hit.source_id,
        }
        for hit in sorted(result.hits, key=lambda item: (item.start, item.end))
    ]
    payload = {
        "schema_version": 1,
        "task_id": task_id,
        "video_id": task["video_id"],
        "media_id": media_id,
        "query": task["query"],
        "expected_start": task["expected_start"],
        "expected_end": task["expected_end"],
        "encoder": SIGLIP2_MODEL.identity(),
        "score_definition": "1 - Chroma cosine distance",
        "snapshot_id": config.snapshot_id,
        "sample_count": len(samples),
        "elapsed_seconds": elapsed_seconds,
        "model_calls": {"text_embedding": 1},
        "samples": samples,
    }
    destination = _output_path(task_id, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(destination),
        "samples": len(samples),
        "elapsed_seconds": elapsed_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export one dense VidXP scene-similarity curve."
    )
    parser.add_argument("task_id")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(export_curve(arguments.task_id, arguments.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
