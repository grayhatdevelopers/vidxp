from __future__ import annotations

import itertools
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Literal, Mapping, Sequence

from vidxp.benchmarks.common import (
    append_failure,
    benchmark_generation_id,
    benchmark_media_id,
    ensure_adapter_outputs,
    record_adapter_manifest,
    run_logged_evaluator,
    verify_artifact,
)
from vidxp.capabilities.scene.operations import search_scene
from vidxp.capabilities.schemas import SearchHit
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.manifest import ManifestStore, write_json_atomic
from vidxp.core.runner import run_index
from vidxp.core.storage import IndexStorage
from vidxp.infrastructure.local_index import LOCAL_INDEX_RUNTIME_CHECKS
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


DIDEMO_REVISION = "b6a555c8134581305d0ed4716fbc192860e0b88c"
DIDEMO_REPOSITORY = "https://github.com/LisaAnne/LocalizingMoments"
DIDEMO_TEST_SHA256 = (
    "1891c04ec48b3d364c739594b2b6413806b74bd9027c092d896e7ebb930ff1cd"
)
DIDEMO_VALIDATION_SHA256 = (
    "b0364cc256553332feb19d46bcc4cd2b09774949fe6c0b25e7ed0ff3c6aefebb"
)
DIDEMO_EVALUATOR_SHA256 = (
    "9ec3e7a171272eb3551b0eaa7bbe9292131ad5cf34fd5c1e02c0fc4a11234df6"
)
DIDEMO_EVALUATOR_CRLF_SHA256 = (
    "4754bb320564e5d2e7c633e0b660e87feca7f00fa73269e50140e81ffb4ca762"
)
DIDEMO_MOMENTS = (
    tuple((index, index) for index in range(6))
    + tuple(itertools.combinations(range(6), 2))
)


def parse_annotations(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("DiDeMo annotations must be a non-empty JSON list.")
    required = {
        "annotation_id",
        "description",
        "num_segments",
        "times",
        "video",
    }
    for index, annotation in enumerate(payload):
        missing = sorted(required - set(annotation))
        if missing:
            raise ValueError(
                f"DiDeMo annotation {index} lacks: {', '.join(missing)}."
            )
        if annotation["num_segments"] not in {5, 6}:
            raise ValueError(
                f"DiDeMo annotation {index} has unsupported num_segments."
            )
        if not str(annotation["description"]).strip():
            raise ValueError(f"DiDeMo annotation {index} has an empty query.")
        for moment in annotation["times"]:
            if tuple(moment) not in DIDEMO_MOMENTS:
                raise ValueError(
                    f"DiDeMo annotation {index} has illegal moment {moment}."
                )
            if int(moment[1]) >= int(annotation["num_segments"]):
                raise ValueError(
                    f"DiDeMo annotation {index} exceeds num_segments."
                )
    return [dict(annotation) for annotation in payload]


def load_annotations(path: str | Path) -> list[dict[str, Any]]:
    return parse_annotations(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )


def select_annotations(
    annotations: Sequence[Mapping[str, Any]],
    indices: Sequence[int] | None,
) -> list[dict[str, Any]]:
    if indices is None:
        return [dict(item) for item in annotations]
    selected = []
    seen = set()
    for index in indices:
        if index in seen:
            raise ValueError(f"Duplicate DiDeMo subset index: {index}")
        if index < 0 or index >= len(annotations):
            raise IndexError(f"DiDeMo subset index out of range: {index}")
        seen.add(index)
        selected.append(dict(annotations[index]))
    if not selected:
        raise ValueError("DiDeMo subset must contain at least one annotation.")
    return selected


def resolve_media(
    media_directory: str | Path,
    video_name: str,
    overrides: Mapping[str, str | Path] | None = None,
) -> Path:
    if overrides and video_name in overrides:
        candidate = Path(overrides[video_name])
    else:
        candidate = Path(media_directory) / video_name
    if not candidate.is_file():
        raise FileNotFoundError(f"DiDeMo video not found: {candidate}")
    return candidate


def rank_moments(
    hits: Sequence[SearchHit],
    *,
    num_segments: int,
    chunk_pooling: Literal["max", "mean"] = "max",
) -> list[tuple[int, int]]:
    if num_segments not in {5, 6}:
        raise ValueError("DiDeMo num_segments must be five or six.")
    chunk_scores: dict[int, list[float]] = defaultdict(list)
    for hit in hits:
        timestamp = float(hit.metadata["timestamp"])
        if 0 <= timestamp < 30:
            chunk_scores[int(timestamp // 5)].append(float(hit.score))
    missing = [
        index
        for index in range(num_segments)
        if not chunk_scores.get(index)
    ]
    if missing:
        raise ValueError(
            "No sampled scene frame was available for DiDeMo chunk(s): "
            + ", ".join(str(index) for index in missing)
        )
    if chunk_pooling not in {"max", "mean"}:
        raise ValueError("DiDeMo chunk pooling must be 'max' or 'mean'.")
    pooled_scores = {
        index: (
            max(values)
            if chunk_pooling == "max"
            else mean(values)
        )
        for index, values in chunk_scores.items()
    }
    canonical_order = {
        moment: index for index, moment in enumerate(DIDEMO_MOMENTS)
    }
    valid = [
        moment for moment in DIDEMO_MOMENTS
        if moment[1] < num_segments
    ]
    invalid = [
        moment for moment in DIDEMO_MOMENTS
        if moment[1] >= num_segments
    ]
    valid.sort(
        key=lambda moment: (
            -mean(
                pooled_scores[index]
                for index in range(moment[0], moment[1] + 1)
            ),
            canonical_order[moment],
        )
    )
    return valid + invalid


def validate_predictions(
    predictions: Sequence[Sequence[Sequence[int]]],
    annotations: Sequence[Mapping[str, Any]],
) -> None:
    if len(predictions) != len(annotations):
        raise ValueError(
            "DiDeMo prediction count does not match annotation count."
        )
    expected = set(DIDEMO_MOMENTS)
    for index, (ranking, annotation) in enumerate(
        zip(predictions, annotations)
    ):
        moments = [tuple(int(value) for value in moment) for moment in ranking]
        if len(moments) != 21 or set(moments) != expected:
            raise ValueError(
                f"DiDeMo prediction {index} must rank all 21 moments once."
            )
        num_segments = int(annotation["num_segments"])
        invalid_started = False
        for moment in moments:
            invalid = moment[1] >= num_segments
            invalid_started = invalid_started or invalid
            if invalid_started and not invalid:
                raise ValueError(
                    f"DiDeMo prediction {index} ranks an unavailable "
                    "segment ahead of an available segment."
                )


def _metrics_from_output(output: str) -> dict[str, float]:
    prefix = "VIDXP_METRICS_JSON="
    matches = [
        line[len(prefix):]
        for line in output.splitlines()
        if line.startswith(prefix)
    ]
    if len(matches) != 1:
        raise RuntimeError("DiDeMo evaluator did not emit one metrics record.")
    metrics = json.loads(matches[0])
    if not all(math.isfinite(float(value)) for value in metrics.values()):
        raise ValueError("DiDeMo evaluator returned non-finite metrics.")
    return {key: float(value) for key, value in metrics.items()}


def _verified_artifacts(
    annotations_path: str | Path,
    evaluator_path: str | Path,
    *,
    split: Literal["validation", "test"],
) -> list[dict[str, Any]]:
    split_file = "val_data.json" if split == "validation" else "test_data.json"
    split_sha256 = (
        DIDEMO_VALIDATION_SHA256
        if split == "validation"
        else DIDEMO_TEST_SHA256
    )
    return [
        verify_artifact(
            annotations_path,
            name=f"DiDeMo {split} annotations",
            expected_sha256=split_sha256,
            source=(
                f"{DIDEMO_REPOSITORY}/blob/{DIDEMO_REVISION}"
                f"/data/{split_file}"
            ),
            revision=DIDEMO_REVISION,
        ),
        verify_artifact(
            evaluator_path,
            name="DiDeMo evaluator",
            expected_sha256=(
                DIDEMO_EVALUATOR_SHA256,
                DIDEMO_EVALUATOR_CRLF_SHA256,
            ),
            source=(
                f"{DIDEMO_REPOSITORY}/blob/{DIDEMO_REVISION}"
                "/utils/eval.py"
            ),
            revision=DIDEMO_REVISION,
        ),
    ]


def _video_sources(
    annotations: Sequence[Mapping[str, Any]],
    *,
    media_directory: str | Path,
    media_overrides: Mapping[str, str | Path] | None,
) -> list[VideoSource]:
    return [
        VideoSource(
            video_id=benchmark_media_id("didemo", video_name),
            path=resolve_media(
                media_directory,
                video_name,
                media_overrides,
            ),
            source_name=video_name,
        )
        for video_name in sorted({item["video"] for item in annotations})
    ]


def _generate_predictions(
    annotations: Sequence[Mapping[str, Any]],
    *,
    config: IndexConfig,
    manifest: Mapping[str, Any],
    chunk_pooling: Literal["max", "mean"],
    runtime: ModelRuntime,
    storage: IndexStorage,
) -> list[list[list[int]]]:
    scene_counts = {
        video_id: int(video["summary"]["scene_frames"])
        for video_id, video in manifest["videos"].items()
    }
    predictions = []
    for annotation in annotations:
        video_id = benchmark_media_id(
            "didemo",
            str(annotation["video"]),
        )
        result = search_scene(
            str(annotation["description"]),
            config=config,
            top_k=scene_counts[video_id],
            video_id=video_id,
            query_id=str(annotation["annotation_id"]),
            runtime=runtime,
            storage=storage,
        )
        predictions.append(
            [
                list(moment)
                for moment in rank_moments(
                    result.hits,
                    num_segments=int(annotation["num_segments"]),
                    chunk_pooling=chunk_pooling,
                )
            ]
        )
    return predictions


def _evaluate_predictions(
    *,
    evaluator_path: str | Path,
    annotations_path: Path,
    predictions_path: Path,
    log_path: Path,
) -> dict[str, float]:
    completed = run_logged_evaluator(
        [
            sys.executable,
            str(Path(__file__).with_name("didemo_eval.py")),
            "--evaluator",
            str(Path(evaluator_path).resolve()),
            "--annotations",
            str(annotations_path.resolve()),
            "--predictions",
            str(predictions_path.resolve()),
        ],
        cwd=Path(evaluator_path).resolve().parent,
        log_path=log_path,
        note=(
            "The pinned Python 2 evaluator source is loaded unchanged. "
            "Only its three print statements are converted to Python 3 "
            "syntax before execution; metric expressions are untouched."
        ),
    )
    return _metrics_from_output(completed.stdout)


def _result_classification(
    *,
    split: str,
    full_split: bool,
    has_media_overrides: bool,
) -> str:
    if split == "test" and full_split:
        return (
            "official_full_test_result_with_documented_media_substitution"
            if has_media_overrides
            else "official_full_test_result"
        )
    if split == "validation":
        return "validation_result_not_paper_score"
    return "smoke_test_not_paper_score"


def run_didemo(
    *,
    annotations_path: str | Path,
    evaluator_path: str | Path,
    media_directory: str | Path,
    run_id: str,
    output_root: str | Path = "benchmark_runs",
    annotation_indices: Sequence[int] | None = None,
    media_overrides: Mapping[str, str | Path] | None = None,
    scene_sample_fps: float = 1.0,
    device: str = "cpu",
    split: Literal["validation", "test"] = "test",
    chunk_pooling: Literal["max", "mean"] = "max",
    reset: bool = False,
) -> dict[str, Any]:
    if split not in {"validation", "test"}:
        raise ValueError("DiDeMo split must be 'validation' or 'test'.")
    if chunk_pooling not in {"max", "mean"}:
        raise ValueError("DiDeMo chunk pooling must be 'max' or 'mean'.")
    artifacts = _verified_artifacts(
        annotations_path,
        evaluator_path,
        split=split,
    )
    annotations = select_annotations(
        load_annotations(annotations_path),
        annotation_indices,
    )
    config = IndexConfig(
        dataset="didemo",
        split=split,
        run_id=run_id,
        enabled_modalities=("scene",),
        capability_options={
            "scene": {"sample_fps": scene_sample_fps},
        },
        device=device,
        output_root=output_root,
        generation_id=benchmark_generation_id("didemo", split, run_id),
    )
    run_directory = config.run_directory
    registry = create_capability_registry(
        platform_runtime_checks=LOCAL_INDEX_RUNTIME_CHECKS
    )
    runtime = ModelRuntime(
        VidXPSettings(
            repository_root=run_directory,
            runtime_backend=device,
        ),
        allowed_specs=registry.model_specs(),
    )
    ensure_adapter_outputs(run_directory)
    subset = {
        "label": (
            f"full_{split}"
            if annotation_indices is None
            else f"{split}_subset"
        ),
        "annotation_count": len(annotations),
        "annotation_indices": (
            None if annotation_indices is None else list(annotation_indices)
        ),
        "video_count": len({item["video"] for item in annotations}),
    }
    try:
        with IndexStorage(config) as storage:
            manifest = run_index(
                _video_sources(
                    annotations,
                    media_directory=media_directory,
                    media_overrides=media_overrides,
                ),
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
            ensure_adapter_outputs(run_directory)
            record_adapter_manifest(
                run_directory,
                benchmark="didemo",
                subset=subset,
                artifacts=artifacts,
                state="predicting",
            )
            predictions = _generate_predictions(
                annotations,
                config=config,
                manifest=manifest,
                chunk_pooling=chunk_pooling,
                runtime=runtime,
                storage=storage,
            )
        validate_predictions(predictions, annotations)
        predictions_path = run_directory / "predictions.json"
        ground_truth_path = run_directory / "ground_truth.subset.json"
        write_json_atomic(predictions_path, predictions)
        write_json_atomic(ground_truth_path, annotations)
        metrics = _evaluate_predictions(
            evaluator_path=evaluator_path,
            annotations_path=ground_truth_path,
            predictions_path=predictions_path,
            log_path=run_directory / "evaluator.log",
        )
        write_json_atomic(run_directory / "metrics.json", metrics)
        record_adapter_manifest(
            run_directory,
            benchmark="didemo",
            subset=subset,
            artifacts=artifacts,
            state="complete",
            details={
                "prediction_count": len(predictions),
                "prediction_format_validated": True,
                "chunk_pooling": chunk_pooling,
                "media_override_count": len(media_overrides or {}),
                "media_override_video_ids": sorted(media_overrides or {}),
                "media_id_mapping": (
                    "deterministic_uuid4_from_benchmark_and_official_video_id"
                ),
                "result_classification": _result_classification(
                    split=split,
                    full_split=annotation_indices is None,
                    has_media_overrides=bool(media_overrides),
                ),
            },
        )
        return metrics
    except BaseException as error:
        append_failure(run_directory, stage="didemo_adapter", error=error)
        record_adapter_manifest(
            run_directory,
            benchmark="didemo",
            subset=subset,
            artifacts=artifacts,
            state="failed",
        )
        raise
