from __future__ import annotations

import json
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Literal, Mapping, Sequence

from vidxp.benchmarks.common import (
    append_failure,
    benchmark_generation_id,
    benchmark_media_id,
    ensure_adapter_outputs,
    record_adapter_manifest,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.manifest import ManifestStore, write_json_atomic
from vidxp.core.runner import run_index
from vidxp.core.storage import IndexStorage
from vidxp.infrastructure.local_index import LOCAL_INDEX_RUNTIME_CHECKS
from vidxp.media_runtime import inspect_media_runtime
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


LATENCY_BENCHMARK = "latency"
LATENCY_SPLIT = "synthetic"
LATENCY_SCHEMA_VERSION = 1
DEFAULT_CORPUS_SEED = 2026
SUPPORTED_MODALITIES = ("scene", "actor", "dialogue")

_STAGE_RATES: Mapping[str, str] = {
    "scene": "scene_frames",
    "actor": "actor_frames",
    "frame_stream": "source_frames_advanced",
    "dialogue_indexing": "dialogue_phrases",
}

_VOCABULARY = (
    "the quick brown fox jumps over the lazy dog honest sunshine light "
    "morning river ocean mountain garden flower silver golden copper "
    "bright shadow shadow candle lantern window door table chair book "
    "letter number station market kitchen garden bakery camera video "
    "music voice speech word phrase moment memory journey story world "
    "quiet calm gentle peaceful vivid warm cool bright dark soft loud"
).split()


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def rss_unit() -> Literal["bytes", "KiB"]:
    return "bytes" if _sys_platform() == "darwin" else "KiB"


def _sys_platform() -> str:
    import sys

    return sys.platform


@dataclass(frozen=True)
class SyntheticCorpusSpec:
    videos: int
    duration_seconds: float
    fps: int
    width: int
    height: int
    audio_mode: Literal["none", "sine", "flite"]
    seed: int

    def public_record(self) -> dict[str, Any]:
        return {
            "videos": self.videos,
            "duration_seconds": self.duration_seconds,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "audio_mode": self.audio_mode,
            "seed": self.seed,
        }


def validate_latency_options(
    *,
    modalities: Sequence[str],
    videos: int,
    duration_seconds: float,
    fps: int,
    width: int,
    height: int,
    repetitions: int,
    input_mode: str,
    audio_mode: str,
    baseline_tolerance: float,
) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(modalities))
    if not selected:
        raise ValueError("At least one latency modality must be selected.")
    unsupported = sorted(set(selected) - set(SUPPORTED_MODALITIES))
    if unsupported:
        raise ValueError(
            "Latency modalities must be a subset of "
            + ", ".join(SUPPORTED_MODALITIES)
            + "; unsupported: "
            + ", ".join(unsupported)
        )
    if videos <= 0:
        raise ValueError("videos must be greater than zero.")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be greater than zero.")
    if fps <= 0:
        raise ValueError("fps must be greater than zero.")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be greater than zero.")
    if repetitions <= 0:
        raise ValueError("repetitions must be greater than zero.")
    if input_mode not in {"transcript", "transcribe"}:
        raise ValueError("input_mode must be 'transcript' or 'transcribe'.")
    if audio_mode not in {"none", "sine", "flite"}:
        raise ValueError("audio_mode must be 'none', 'sine', or 'flite'.")
    if "dialogue" in selected and input_mode == "transcribe":
        if audio_mode != "flite":
            raise ValueError(
                "Real transcription requires a speech audio source; "
                "use --audio-mode flite with --input-mode transcribe."
            )
    if not 0 <= baseline_tolerance <= 5:
        raise ValueError("baseline_tolerance must be between zero and five.")
    return selected


def synthetic_transcript(
    *,
    duration_seconds: float,
    seed: int,
) -> list[dict[str, Any]]:
    generator = random.Random(seed)
    strides = max(1, int(duration_seconds / 0.4))
    words = [generator.choice(_VOCABULARY) for _ in range(strides)]
    span = duration_seconds / len(words)
    word_events = [
        {
            "word": word,
            "start": round(index * span, 4),
            "end": round((index + 1) * span, 4),
        }
        for index, word in enumerate(words)
    ]
    return [
        {
            "text": " ".join(words),
            "start": 0.0,
            "end": duration_seconds,
            "words": word_events,
        }
    ]


def _flite_text(seed: int) -> str:
    generator = random.Random(seed)
    words = [generator.choice(_VOCABULARY) for _ in range(24)]
    return " ".join(words)


def build_clip_command(
    *,
    spec: SyntheticCorpusSpec,
    ffmpeg: str,
    destination: Path,
) -> list[str]:
    compact = spec.width != 0 and spec.height != 0
    if not compact:
        raise ValueError("The synthetic corpus requires positive dimensions.")
    command = [
        ffmpeg,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={spec.width}x{spec.height}:rate={spec.fps}",
    ]
    if spec.audio_mode == "sine":
        command += [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
        ]
    elif spec.audio_mode == "flite":
        command += [
            "-f",
            "lavfi",
            "-i",
            f"flite=text='{_flite_text(spec.seed)}',sample_rate=16000",
        ]
    command += [
        "-t",
        f"{spec.duration_seconds:g}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if spec.audio_mode == "none":
        command.append("-an")
    else:
        command += ["-c:a", "aac"]
    command.append(str(destination))
    return command


def _probe_duration(ffprobe: str, path: Path) -> float:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"ffprobe could not read a generated clip: {path}"
        )
    try:
        duration = float(json.loads(completed.stdout)["format"]["duration"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"ffprobe returned an invalid duration for {path}."
        ) from exc
    if duration <= 0:
        raise ValueError(f"ffprobe reported a non-positive duration for {path}.")
    return duration


def generate_synthetic_corpus(
    *,
    spec: SyntheticCorpusSpec,
    directory: str | Path,
    ffprobe: str,
    ffmpeg: str,
    audio_mode: str | None = None,
) -> list[Path]:
    runtime_status = inspect_media_runtime(
        ffprobe=ffprobe,
        ffmpeg=ffmpeg,
    )
    if not runtime_status.ready:
        raise ValueError(
            "The latency benchmark requires FFmpeg and ffprobe to generate "
            "the synthetic corpus. Run `vidxp init`, then retry."
        )
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    clips = []
    for index in range(spec.videos):
        path = destination / f"clip-{index:03d}.mp4"
        command = build_clip_command(
            spec=spec,
            ffmpeg=ffmpeg,
            destination=path,
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            if spec.audio_mode == "flite" and stderr:
                raise ValueError(
                    "FFmpeg could not apply the flite speech filter "
                    f"(libflite likely unavailable): {stderr}"
                )
            raise ValueError(f"FFmpeg could not generate {path}: {stderr}")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"FFmpeg did not produce {path}.")
        _probe_duration(ffprobe, path)
        clips.append(path)
    return clips


def _per_video_stages(
    manifest: Mapping[str, Any],
    video_id: str,
) -> dict[str, float]:
    video = manifest["videos"].get(video_id)
    if video is None or video.get("state") == "failed":
        return {}
    return {
        str(stage): float(entry["seconds"])
        for stage, entry in (video.get("stages") or {}).items()
        if entry.get("state") != "incomplete"
        and float(entry.get("seconds", 0.0)) > 0
    }


def _summary_ratio(
    manifest: Mapping[str, Any],
    video_id: str,
    *,
    metric: str,
    stage: str,
) -> float | None:
    video = manifest["videos"].get(video_id) or {}
    summary = video.get("summary") or {}
    seconds = _per_video_stages(manifest, video_id).get(stage)
    count = summary.get(metric)
    if seconds is None or count is None or seconds <= 0 or count <= 0:
        return None
    return float(count) / seconds


def aggregate_latency_runs(
    manifests: Sequence[Mapping[str, Any]],
    *,
    wall_seconds: Sequence[float],
    peak_rss_samples: Sequence[int | None],
) -> dict[str, Any]:
    if len(manifests) != len(wall_seconds):
        raise ValueError(
            "Every latency repetition requires a wall-clock sample."
        )
    stage_samples: dict[str, list[float]] = {}
    rate_samples: dict[str, list[float]] = {}
    per_video: list[dict[str, Any]] = []
    processed_frames = 0
    record_counts: dict[str, int] = {}
    for repetition, manifest in enumerate(manifests):
        processed_frames += int(manifest.get("processed_frames", 0))
        for modality, count in (manifest.get("record_counts") or {}).items():
            record_counts[modality] = record_counts.get(modality, 0) + int(count)
        for video_id in sorted(manifest.get("videos", {})):
            stages = _per_video_stages(manifest, video_id)
            if not stages:
                continue
            for stage, seconds in stages.items():
                stage_samples.setdefault(stage, []).append(seconds)
            rate_stages = {
                stage: _summary_ratio(
                    manifest,
                    video_id,
                    metric=_STAGE_RATES[stage],
                    stage=stage,
                )
                for stage in _STAGE_RATES
                if stage in stages
            }
            for stage, rate in rate_stages.items():
                if rate is None:
                    continue
                rate_samples.setdefault(stage, []).append(rate)
            video = manifest["videos"].get(video_id, {})
            per_video.append(
                {
                    "repetition": repetition,
                    "video_id": video_id,
                    "wall_seconds": (
                        wall_seconds[repetition]
                    ),
                    "stages": dict(sorted(stages.items())),
                    "summary": video.get("summary", {}),
                }
            )
    stages: dict[str, dict[str, Any]] = {}
    for stage, samples in stage_samples.items():
        values = sorted(samples)
        summary: dict[str, Any] = {
            "runs": len(values),
            "mean_seconds": mean(values),
            "min_seconds": values[0],
            "max_seconds": values[-1],
        }
        rates = rate_samples.get(stage)
        if rates:
            summary["rate_per_second"] = mean(rates)
        stages[stage] = summary
    approximate_rss = [
        sample for sample in peak_rss_samples if sample is not None
    ]
    summary = {
        "wall_seconds": {
            "runs": len(wall_seconds),
            "mean_seconds": mean(wall_seconds),
            "min_seconds": min(wall_seconds),
            "max_seconds": max(wall_seconds),
        },
        "peak_rss": {
            "unit": rss_unit(),
            "samples": len(approximate_rss),
            "value": int(max(approximate_rss)) if approximate_rss else None,
        },
    }
    return {
        "per_video": per_video,
        "stages": dict(sorted(stages.items())),
        "summary": summary,
        "processed_frames": processed_frames,
        "record_counts": dict(sorted(record_counts.items())),
    }


def _validate_baseline_compatibility(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> None:
    baseline_corpus = baseline.get("corpus") or {}
    report_corpus = report.get("corpus") or {}
    corpus_keys = ("videos", "duration_seconds", "fps", "width", "height", "audio_mode", "seed")
    for key in corpus_keys:
        report_val = report_corpus.get(key)
        baseline_val = baseline_corpus.get(key)
        if report_val is not None and baseline_val is not None and report_val != baseline_val:
            raise ValueError(
                f"Baseline corpus mismatch: {key!r} is {baseline_val!r}, "
                f"current is {report_val!r}."
            )
    for key in ("input_mode", "device"):
        report_val = report.get(key)
        baseline_val = baseline.get(key)
        if report_val is not None and baseline_val is not None and report_val != baseline_val:
            raise ValueError(
                f"Baseline {key!r} mismatch: {baseline_val!r}, current is {report_val!r}."
            )
    report_mods = sorted(report.get("modalities") or [])
    baseline_mods = sorted(baseline.get("modalities") or [])
    if report_mods and baseline_mods and report_mods != baseline_mods:
        raise ValueError(
            f"Baseline modalities mismatch: {baseline_mods}, current is {report_mods}."
        )


def compare_baseline(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    tolerance: float,
) -> dict[str, Any]:
    if tolerance < 0:
        raise ValueError("Baseline tolerance must be nonnegative.")
    comparisons: dict[str, dict[str, Any]] = {}
    regressions: list[str] = []
    for stage, previous in (baseline.get("stages") or {}).items():
        current = (report.get("stages") or {}).get(stage)
        if current is None or not previous.get("runs"):
            continue
        old_mean = float(previous["mean_seconds"])
        new_mean = float(current["mean_seconds"])
        if old_mean <= 0:
            continue
        delta = new_mean / old_mean - 1.0
        comparisons[stage] = {
            "old_mean_seconds": old_mean,
            "new_mean_seconds": new_mean,
            "delta_ratio": delta,
            "regressed": delta > tolerance,
        }
        if delta > tolerance:
            regressions.append(stage)
    return {
        "schema_version": LATENCY_SCHEMA_VERSION,
        "tolerance": tolerance,
        "stages": dict(sorted(comparisons.items())),
        "regressions": regressions,
        "verdict": "fail" if regressions else "pass",
    }


def build_latency_sources(
    *,
    clips: Sequence[Path],
    spec: SyntheticCorpusSpec,
    input_mode: Literal["transcript", "transcribe"],
) -> list[VideoSource]:
    sources = []
    for index, path in enumerate(clips):
        transcript = (
            synthetic_transcript(
                duration_seconds=spec.duration_seconds,
                seed=spec.seed + index,
            )
            if input_mode == "transcript"
            else None
        )
        sources.append(
            VideoSource(
                video_id=benchmark_media_id(
                    LATENCY_BENCHMARK,
                    f"clip-{index:03d}",
                ),
                path=path,
                source_name=f"clip-{index:03d}.mp4",
                transcript=transcript,
            )
        )
    return sources


def run_latency(
    *,
    run_id: str,
    output_root: str | Path = "benchmark_runs",
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    modalities: Sequence[str] = ("scene",),
    videos: int = 1,
    duration_seconds: float = 8.0,
    fps: int = 24,
    width: int = 320,
    height: int = 180,
    repetitions: int = 1,
    input_mode: Literal["transcript", "transcribe"] = "transcript",
    audio_mode: Literal["none", "sine", "flite"] = "none",
    device: str = "cpu",
    reset: bool = False,
    baseline_path: str | Path | None = None,
    baseline_tolerance: float = 0.15,
) -> dict[str, Any]:
    selected = validate_latency_options(
        modalities=modalities,
        videos=videos,
        duration_seconds=duration_seconds,
        fps=fps,
        width=width,
        height=height,
        repetitions=repetitions,
        input_mode=input_mode,
        audio_mode=audio_mode,
        baseline_tolerance=baseline_tolerance,
    )
    spec = SyntheticCorpusSpec(
        videos=videos,
        duration_seconds=duration_seconds,
        fps=fps,
        width=width,
        height=height,
        audio_mode=audio_mode,
        seed=DEFAULT_CORPUS_SEED,
    )
    config = IndexConfig(
        dataset=LATENCY_BENCHMARK,
        split=LATENCY_SPLIT,
        run_id=run_id,
        enabled_modalities=selected,
        device=device,
        output_root=output_root,
        generation_id=benchmark_generation_id(
            LATENCY_BENCHMARK,
            LATENCY_SPLIT,
            run_id,
        ),
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
    manifests: list[dict[str, Any]] = []
    wall_samples: list[float] = []
    rss_samples: list[int | None] = []
    try:
        clips = generate_synthetic_corpus(
            spec=spec,
            directory=run_directory / "corpus",
            ffprobe=ffprobe,
            ffmpeg=ffmpeg,
        )
        sources = build_latency_sources(
            clips=clips,
            spec=spec,
            input_mode=input_mode,
        )
        for _ in range(repetitions):
            started = perf_counter()
            with IndexStorage(config) as storage:
                manifest = run_index(
                    sources,
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
                store_size_bytes = storage.size_bytes()
            wall_samples.append(perf_counter() - started)
            rss_samples.append(_peak_rss_bytes())
            manifests.append(
                {
                    **manifest,
                    "store_size_bytes_at_commit": store_size_bytes,
                }
            )
        aggregated = aggregate_latency_runs(
            manifests,
            wall_seconds=wall_samples,
            peak_rss_samples=rss_samples,
        )
        report = {
            "schema_version": LATENCY_SCHEMA_VERSION,
            "benchmark": LATENCY_BENCHMARK,
            "run_id": run_id,
            "created_at": manifests[-1].get("completed_at"),
            "corpus": spec.public_record(),
            "input_mode": input_mode,
            "modalities": list(selected),
            "device": device,
            "repetitions": repetitions,
            "git": manifests[-1].get("git"),
            "environment": manifests[-1].get("environment"),
            "config_fingerprint": manifests[-1].get("config_fingerprint"),
            "record_counts": aggregated["record_counts"],
            "processed_frames": aggregated["processed_frames"],
            "summary": aggregated["summary"],
            "stages": aggregated["stages"],
            "per_video": aggregated["per_video"],
            "baseline": None,
        }
        if baseline_path is not None:
            try:
                baseline = json.loads(
                    Path(baseline_path).read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Baseline report is not readable JSON: {baseline_path}"
                ) from exc
            _validate_baseline_compatibility(report, baseline)
            report["baseline"] = compare_baseline(
                report,
                baseline,
                tolerance=baseline_tolerance,
            )
        write_json_atomic(run_directory / "report.json", report)
        record_adapter_manifest(
            run_directory,
            benchmark=LATENCY_BENCHMARK,
            subset={
                "label": f"latency_{run_id}",
                "modalities": list(selected),
                "video_count": videos,
                "duration_seconds": duration_seconds,
                "repetitions": repetitions,
            },
            artifacts=[],
            state="complete",
            details={
                "device": device,
                "input_mode": input_mode,
                "audio_mode": audio_mode,
                "corpus": spec.public_record(),
                "result_classification": "performance_benchmark_not_quality_score",
            },
        )
        return report
    except BaseException as error:
        append_failure(run_directory, stage="latency_adapter", error=error)
        record_adapter_manifest(
            run_directory,
            benchmark=LATENCY_BENCHMARK,
            subset={
                "label": f"latency_{run_id}",
                "modalities": list(selected),
            },
            artifacts=[],
            state="failed",
        )
        raise