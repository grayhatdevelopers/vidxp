from __future__ import annotations

import ast
import json
import math
import os
import re
import sys
import tempfile
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
from vidxp.capabilities.dialogue.config import dialogue_config
from vidxp.capabilities.dialogue.operations import search_dialogue
from vidxp.capabilities.schemas import SearchHit
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.manifest import ManifestStore, sha256_file, write_json_atomic
from vidxp.core.runner import run_index
from vidxp.core.storage import IndexStorage
from vidxp.infrastructure.local_index import LOCAL_INDEX_RUNTIME_CHECKS
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


HIREST_REVISION = "deffc169b4e8d51c1589d5512ad05da61e81bcee"
HIREST_REPOSITORY = "https://github.com/j-min/HiREST"
HIREST_ASR_REVISION = "54e2f8da7a4384fec8a137011399f5e104069032"
HIREST_ASR_URL = (
    "https://huggingface.co/j-min/HiREST-baseline/resolve/"
    f"{HIREST_ASR_REVISION}/ASR.zip"
)
HIREST_ASR_SHA256 = (
    "0b452d38e30064dc7273a58b7b73ec33e307ff83d30048a472777f56e3a29fbc"
)
HIREST_TEST_SHA256 = (
    "00219050c022ff2fc89c210ca4db605de6aa13c5c6014e4c678345ade3448a62"
)
HIREST_VALIDATION_SHA256 = (
    "70d32c5fcdffe66cbf3c732dd274f03378da2082f50c9cec7e67705f529ecb4d"
)
HIREST_CATEGORIES_SHA256 = (
    "157623d50f7b8482f55fa1c4efc500539784c0399fb2dd60bb687b4006d85ca1"
)
HIREST_EVALUATOR_SHA256 = (
    "871b48dc5ce42fbe1a4b672fe4df88a88ce568d57759dfc971e5aacc5f88f119"
)
HIREST_EVALUATOR_CRLF_SHA256 = (
    "c4b8ba9b572ae4088e90ddc3eec2b2cc4f5b4c1a0153ff6e0843817da89a5ca0"
)
HIREST_DEFAULT_WINDOW_FRACTION = 0.8


def load_ground_truth(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("HiREST ground truth must be a non-empty JSON object.")
    return payload


def moment_pairs(
    ground_truth: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[tuple[str, str]]:
    return [
        (prompt, video)
        for prompt, videos in ground_truth.items()
        for video, annotation in videos.items()
        if annotation.get("clip") is True
    ]


def select_ground_truth(
    ground_truth: Mapping[str, Mapping[str, Mapping[str, Any]]],
    pairs: Sequence[tuple[str, str]] | None,
) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    available = moment_pairs(ground_truth)
    selected = available if pairs is None else list(pairs)
    if not selected:
        raise ValueError("HiREST subset must contain at least one moment pair.")
    if len(selected) != len(set(selected)):
        raise ValueError("HiREST subset contains duplicate prompt/video pairs.")
    available_set = set(available)
    invalid = [pair for pair in selected if pair not in available_set]
    if invalid:
        raise ValueError(
            "HiREST subset contains a non-moment pair: "
            + repr(invalid[0])
        )
    selected_set = set(selected)
    subset: dict[str, dict[str, Any]] = {}
    for prompt, videos in ground_truth.items():
        retained = {
            video: dict(annotation)
            for video, annotation in videos.items()
            if (prompt, video) in selected_set
        }
        if retained:
            subset[prompt] = retained
    ordered_pairs = [
        pair for pair in available
        if pair in selected_set
    ]
    return subset, ordered_pairs


def parse_srt(path: str | Path) -> list[dict[str, Any]]:
    import srt

    subtitles = list(
        srt.parse(Path(path).read_text(encoding="utf-8-sig"))
    )
    segments = []
    for subtitle in subtitles:
        text = " ".join(subtitle.content.split())
        start = subtitle.start.total_seconds()
        end = subtitle.end.total_seconds()
        if text and start >= 0 and end > start:
            segments.append({"text": text, "start": start, "end": end})
    if not segments:
        raise ValueError(f"HiREST ASR contains no valid segments: {path}")
    return segments


def validate_predictions(
    predictions: Mapping[str, Mapping[str, Mapping[str, Any]]],
    ground_truth: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    if set(predictions) != set(ground_truth):
        raise ValueError("HiREST prediction prompt keys do not match the subset.")
    for prompt, videos in ground_truth.items():
        predicted_videos = predictions[prompt]
        if set(predicted_videos) != set(videos):
            raise ValueError(
                f"HiREST video keys do not match for prompt {prompt!r}."
            )
        for video, prediction in predicted_videos.items():
            if set(prediction) != {"bounds"}:
                raise ValueError(
                    f"HiREST prediction {prompt!r}/{video!r} must contain "
                    "only bounds."
                )
            bounds = prediction["bounds"]
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise ValueError("HiREST bounds must be a two-item JSON list.")
            start, end = (float(value) for value in bounds)
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end <= start
            ):
                raise ValueError(
                    f"HiREST prediction {prompt!r}/{video!r} has invalid bounds."
                )


def rank_interval(
    hits: Sequence[SearchHit],
    *,
    duration: float,
    window_fraction: float,
) -> tuple[float, float]:
    if duration <= 0 or not math.isfinite(duration):
        raise ValueError("HiREST video duration must be finite and positive.")
    if not 0 < window_fraction < 1:
        raise ValueError(
            "HiREST temporal window fraction must be between zero and one."
        )
    if not hits:
        raise ValueError("HiREST temporal ranking requires dialogue hits.")

    second_count = max(1, math.ceil(duration))
    hit_scores = [float(hit.score) for hit in hits]
    score_range = max(hit_scores) - min(hit_scores)
    floor_score = min(hit_scores) - max(score_range, 1e-6)
    timeline = [floor_score] * second_count
    for hit in hits:
        start = max(0, math.floor(float(hit.start)))
        end = min(second_count, math.ceil(float(hit.end)))
        for second in range(start, end):
            timeline[second] = max(timeline[second], float(hit.score))

    width = max(1, min(second_count, round(duration * window_fraction)))
    best_start = max(
        range(second_count - width + 1),
        key=lambda start: (
            mean(timeline[start:start + width]),
            -start,
        ),
    )
    return float(best_start), min(duration, float(best_start + width))


def _metrics_from_output(output: str) -> dict[str, Any]:
    mappings = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            normalized = re.sub(
                r"np\.float(?:16|32|64)\(([-+0-9.eE]+)\)",
                r"\1",
                line,
            )
            try:
                value = ast.literal_eval(normalized)
            except (SyntaxError, ValueError):
                continue
            if isinstance(value, dict):
                mappings.append(value)
    if not mappings:
        raise RuntimeError("HiREST evaluator did not emit a metrics mapping.")
    metrics = mappings[-1]
    for value in metrics.values():
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            raise ValueError("HiREST evaluator returned non-finite metrics.")
    return metrics


def _verified_artifacts(
    *,
    ground_truth_path: str | Path,
    categories_path: str | Path,
    evaluator_path: str | Path,
    asr_archive_path: str | Path,
    split: Literal["validation", "test"],
) -> list[dict[str, Any]]:
    expected_categories = (
        Path(evaluator_path).resolve().parent
        / "data"
        / "evaluation"
        / "categories.json"
    )
    if Path(categories_path).resolve() != expected_categories.resolve():
        raise ValueError(
            "HiREST evaluate.py loads categories from "
            f"{expected_categories}; --categories must identify that file."
        )
    revision_root = f"{HIREST_REPOSITORY}/blob/{HIREST_REVISION}"
    split_file = (
        "all_data_val.json"
        if split == "validation"
        else "all_data_test.json"
    )
    split_sha256 = (
        HIREST_VALIDATION_SHA256
        if split == "validation"
        else HIREST_TEST_SHA256
    )
    return [
        verify_artifact(
            ground_truth_path,
            name=f"HiREST {split} split",
            expected_sha256=split_sha256,
            source=f"{revision_root}/data/splits/{split_file}",
            revision=HIREST_REVISION,
        ),
        verify_artifact(
            categories_path,
            name="HiREST evaluator categories",
            expected_sha256=HIREST_CATEGORIES_SHA256,
            source=f"{revision_root}/data/evaluation/categories.json",
            revision=HIREST_REVISION,
        ),
        verify_artifact(
            evaluator_path,
            name="HiREST evaluator",
            expected_sha256=(
                HIREST_EVALUATOR_SHA256,
                HIREST_EVALUATOR_CRLF_SHA256,
            ),
            source=f"{revision_root}/evaluate.py",
            revision=HIREST_REVISION,
        ),
        verify_artifact(
            asr_archive_path,
            name="HiREST released ASR archive",
            expected_sha256=HIREST_ASR_SHA256,
            source=HIREST_ASR_URL,
            revision=HIREST_ASR_REVISION,
        ),
    ]


def _transcript_sources(
    ordered_pairs: Sequence[tuple[str, str]],
    *,
    asr_directory: str | Path,
) -> list[VideoSource]:
    sources = []
    for video in sorted({video for _, video in ordered_pairs}):
        asr_path = Path(asr_directory) / f"{Path(video).stem}.srt"
        if not asr_path.is_file():
            raise FileNotFoundError(
                f"HiREST released ASR not found: {asr_path}"
            )
        sources.append(
            VideoSource(
                video_id=benchmark_media_id("hirest", video),
                source_name=asr_path.name,
                transcript=parse_srt(asr_path),
                checksum=sha256_file(asr_path),
                metadata={
                    "released_asr": True,
                    "asr_path": str(asr_path.resolve()),
                },
            )
        )
    return sources


def _generate_predictions(
    ordered_pairs: Sequence[tuple[str, str]],
    *,
    ground_truth: Mapping[str, Mapping[str, Mapping[str, Any]]],
    config: IndexConfig,
    manifest: Mapping[str, Any],
    temporal_window_fraction: float,
    runtime: ModelRuntime,
    storage: IndexStorage,
) -> dict[str, dict[str, dict[str, list[float]]]]:
    dialogue_counts = {
        video_id: int(video["summary"]["dialogue_phrases"])
        for video_id, video in manifest["videos"].items()
    }
    predictions: dict[str, dict[str, dict[str, list[float]]]] = {}
    for prompt, video in ordered_pairs:
        media_id = benchmark_media_id("hirest", video)
        hits = search_dialogue(
            prompt,
            config=config,
            top_k=dialogue_counts[media_id],
            video_id=media_id,
            query_id=f"{prompt}\0{video}",
            runtime=runtime,
            storage=storage,
        ).hits
        if not hits:
            raise RuntimeError(
                f"HiREST search returned no interval for {prompt!r}/{video!r}."
            )
        duration = float(ground_truth[prompt][video]["v_duration"])
        start, end = rank_interval(
            hits,
            duration=duration,
            window_fraction=temporal_window_fraction,
        )
        if end <= start:
            raise ValueError(
                f"HiREST interval falls outside video duration for "
                f"{prompt!r}/{video!r}."
            )
        predictions.setdefault(prompt, {})[video] = {
            "bounds": [start, end]
        }
    return predictions


def _evaluate_predictions(
    *,
    evaluator_path: str | Path,
    ground_truth_path: Path,
    predictions_path: Path,
    log_path: Path,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(
        prefix="vidxp-hirest-evaluator-"
    ) as shim_directory:
        Path(shim_directory, "language_evaluation.py").write_text(
            "# Unused by HiREST moment retrieval.\n",
            encoding="utf-8",
        )
        pythonpath = shim_directory
        if os.environ.get("PYTHONPATH"):
            pythonpath += os.pathsep + str(os.environ["PYTHONPATH"])
        completed = run_logged_evaluator(
            [
                sys.executable,
                str(Path(evaluator_path).resolve()),
                "--task",
                "moment_retrieval",
                "--gt_data",
                str(ground_truth_path.resolve()),
                "--pred_data",
                str(predictions_path.resolve()),
            ],
            cwd=Path(evaluator_path).resolve().parent,
            log_path=log_path,
            environment={
                "PYTHONPATH": pythonpath,
                "PYTHONUTF8": "1",
            },
            note=(
                "The official evaluator is unchanged. An empty "
                "language_evaluation import shim is supplied because that "
                "captioning-only dependency is imported at module load but "
                "is never used by moment retrieval."
            ),
        )
    return _metrics_from_output(completed.stdout)


def run_hirest(
    *,
    ground_truth_path: str | Path,
    categories_path: str | Path,
    evaluator_path: str | Path,
    asr_archive_path: str | Path,
    asr_directory: str | Path,
    run_id: str,
    output_root: str | Path = "benchmark_runs",
    pairs: Sequence[tuple[str, str]] | None = None,
    split: Literal["validation", "test"] = "test",
    temporal_window_fraction: float = HIREST_DEFAULT_WINDOW_FRACTION,
    device: str = "cpu",
    reset: bool = False,
) -> dict[str, Any]:
    if split not in {"validation", "test"}:
        raise ValueError("HiREST split must be 'validation' or 'test'.")
    if not 0 < temporal_window_fraction < 1:
        raise ValueError(
            "HiREST temporal window fraction must be between zero and one."
        )
    artifacts = _verified_artifacts(
        ground_truth_path=ground_truth_path,
        categories_path=categories_path,
        evaluator_path=evaluator_path,
        asr_archive_path=asr_archive_path,
        split=split,
    )
    ground_truth, ordered_pairs = select_ground_truth(
        load_ground_truth(ground_truth_path),
        pairs,
    )
    config = IndexConfig(
        dataset="hirest",
        split=split,
        run_id=run_id,
        enabled_modalities=("dialogue",),
        device=device,
        output_root=output_root,
        generation_id=benchmark_generation_id("hirest", split, run_id),
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
            f"full_moment_{split}"
            if pairs is None
            else f"{split}_subset"
        ),
        "pair_count": len(ordered_pairs),
        "prompt_count": len(ground_truth),
        "video_count": len({video for _, video in ordered_pairs}),
    }
    try:
        with IndexStorage(config) as storage:
            manifest = run_index(
                _transcript_sources(
                    ordered_pairs,
                    asr_directory=asr_directory,
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
                benchmark="hirest",
                subset=subset,
                artifacts=artifacts,
                state="predicting",
            )
            predictions = _generate_predictions(
                ordered_pairs,
                ground_truth=ground_truth,
                config=config,
                manifest=manifest,
                temporal_window_fraction=temporal_window_fraction,
                runtime=runtime,
                storage=storage,
            )
        validate_predictions(predictions, ground_truth)
        predictions_path = run_directory / "predictions.json"
        ground_truth_subset_path = (
            run_directory / "ground_truth.subset.json"
        )
        write_json_atomic(predictions_path, predictions)
        write_json_atomic(ground_truth_subset_path, ground_truth)

        result_details = {
            "prediction_count": len(ordered_pairs),
            "prediction_format_validated": True,
            "input_mode": "released_timestamped_asr",
            "dialogue_words_per_phrase": (
                dialogue_config(config).words_per_phrase
            ),
            "segment_word_timestamps": (
                "linear_interpolation_within_srt_cue"
            ),
            "temporal_ranking": (
                "duration_relative_window_mean_second_score"
            ),
            "temporal_window_fraction": temporal_window_fraction,
            "transcription_provider": "supplied-transcript",
            "video_decode_used": False,
            "media_id_mapping": (
                "deterministic_uuid4_from_benchmark_and_official_video_id"
            ),
        }
        if split == "test":
            summary = {
                "scored": False,
                "prediction_count": len(ordered_pairs),
                "prompt_count": len(ground_truth),
                "video_count": len(
                    {video for _, video in ordered_pairs}
                ),
                "reason": (
                    "The pinned HiREST test split contains placeholder "
                    "moment bounds. Official local moment evaluation uses "
                    "the validation split."
                ),
            }
            write_json_atomic(
                run_directory / "submission.summary.json",
                summary,
            )
            (run_directory / "evaluator.log").write_text(
                "not run: the pinned HiREST test split contains "
                "placeholder moment bounds; predictions are retained as "
                "an unscored submission artifact.\n",
                encoding="utf-8",
            )
            record_adapter_manifest(
                run_directory,
                benchmark="hirest",
                subset=subset,
                artifacts=artifacts,
                state="complete",
                details={
                    **result_details,
                    "result_classification": (
                        "official_full_moment_test_predictions_unscored"
                        if pairs is None
                        else "test_prediction_smoke_unscored"
                    ),
                },
            )
            return summary

        metrics = _evaluate_predictions(
            evaluator_path=evaluator_path,
            ground_truth_path=ground_truth_subset_path,
            predictions_path=predictions_path,
            log_path=run_directory / "evaluator.log",
        )
        write_json_atomic(run_directory / "metrics.json", metrics)
        record_adapter_manifest(
            run_directory,
            benchmark="hirest",
            subset=subset,
            artifacts=artifacts,
            state="complete",
            details={
                **result_details,
                "result_classification": (
                    "validation_result_not_paper_score"
                    if pairs is None
                    else "validation_smoke_test_not_paper_score"
                ),
            },
        )
        return metrics
    except BaseException as error:
        append_failure(run_directory, stage="hirest_adapter", error=error)
        record_adapter_manifest(
            run_directory,
            benchmark="hirest",
            subset=subset,
            artifacts=artifacts,
            state="failed",
        )
        raise
