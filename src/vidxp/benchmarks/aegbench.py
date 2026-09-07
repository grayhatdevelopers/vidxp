from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Protocol, Sequence

import numpy as np

from vidxp.benchmarks.common import (
    append_failure,
    benchmark_generation_id,
    ensure_adapter_outputs,
    record_adapter_manifest,
)
from vidxp.benchmarks.indexed_modality import input_artifact
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capabilities.sound.indexing import (
    DENSE_INTERVAL_SECONDS,
    iter_audio_windows,
)
from vidxp.capabilities.sound.models import get_finelap_model
from vidxp.capabilities.sound.specs import FINELAP_MODEL_SPECS
from vidxp.core.contracts import CancellationToken, IndexConfig
from vidxp.core.manifest import ManifestStore, sha256_file, write_json_atomic
from vidxp.infrastructure.local_index import LOCAL_INDEX_RUNTIME_CHECKS
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


AEGBENCH_SOURCE = "https://huggingface.co/datasets/zihan-audio/AEGBench"
AEGBENCH_REVISION = "49a1d919b6df6717c4a34ef9c01e75aa4b3fc8a5"
PE_A_FRAME_SOURCE = "https://huggingface.co/facebook/pe-a-frame-small"


def _peak_rss_bytes() -> int:
    try:
        import resource
    except ModuleNotFoundError:
        import psutil

        memory = psutil.Process().memory_info()
        return int(getattr(memory, "peak_wset", memory.rss))
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


@dataclass(frozen=True)
class AudioEventQuery:
    query_id: str
    category: str
    intervals: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class AEGBenchItem:
    item_id: str
    audio_path: Path
    duration: float
    queries: tuple[AudioEventQuery, ...]
    excluded_categories: tuple[str, ...]


class AudioEventScorer(Protocol):
    model_id: str
    revision: str
    threshold: float

    def scores(
        self,
        audio_path: Path,
        categories: Sequence[str],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...


def _audio_path(
    item: dict[str, Any],
    *,
    manifest: Path,
    audio_directory: Path | None,
) -> Path:
    raw = item.get("audio_rel") or item.get("audio_path")
    if not raw:
        raise ValueError(f"AEGBench item {item.get('id')!r} has no audio path.")
    candidate = Path(str(raw))
    if not candidate.is_absolute():
        candidate = (audio_directory or manifest.parent) / candidate
    if not candidate.is_file():
        raise FileNotFoundError(f"AEGBench audio not found: {candidate}")
    return candidate.resolve()


def load_aegbench(
    path: str | Path,
    *,
    audio_directory: str | Path | None = None,
) -> tuple[list[AEGBenchItem], dict[str, Any]]:
    manifest = Path(path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("AEGBench metadata must contain a non-empty item list.")
    root = Path(audio_directory).resolve() if audio_directory else None
    items = []
    for item_index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise ValueError(f"AEGBench item {item_index} must be an object.")
        item_id = str(raw_item.get("id") or raw_item.get("benchmark_id") or "").strip()
        duration = float(raw_item.get("duration", 0))
        categories = raw_item.get("categories")
        clips = raw_item.get("clips")
        if not item_id or duration <= 0 or not isinstance(categories, list):
            raise ValueError(f"AEGBench item {item_index} has invalid identity or duration.")
        if not isinstance(clips, list):
            raise ValueError(f"AEGBench item {item_index} has no clip annotations.")
        queries = []
        excluded = []
        for category_value in categories:
            category = str(category_value).strip()
            intervals = tuple(
                (float(clip["start"]), float(clip["end"]))
                for clip in clips
                if isinstance(clip, dict)
                and str(clip.get("category", "")).strip() == category
                and float(clip.get("end", 0)) > float(clip.get("start", 0)) >= 0
            )
            if not intervals:
                excluded.append(category)
                continue
            queries.append(
                AudioEventQuery(
                    query_id=f"{item_id}:{category}",
                    category=category,
                    intervals=intervals,
                )
            )
        if not queries:
            raise ValueError(f"AEGBench item {item_id!r} has no scoreable categories.")
        items.append(
            AEGBenchItem(
                item_id=item_id,
                audio_path=_audio_path(
                    raw_item,
                    manifest=manifest,
                    audio_directory=root,
                ),
                duration=duration,
                queries=tuple(queries),
                excluded_categories=tuple(excluded),
            )
        )
    metadata = {
        "dataset_revision": (
            str(payload.get("revision", AEGBENCH_REVISION))
            if isinstance(payload, dict)
            else AEGBENCH_REVISION
        ),
        "selection": payload.get("selection") if isinstance(payload, dict) else None,
        "source_item_count": len(raw_items),
    }
    return items, metadata


def _interval_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    intersection = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return intersection / union if union else 0.0


def _binary_auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    positives = int(labels.sum())
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    _values, inverse, counts = np.unique(
        scores,
        return_inverse=True,
        return_counts=True,
    )
    for group in np.flatnonzero(counts > 1):
        members = inverse == group
        ranks[members] = ranks[members].mean()
    rank_sum = ranks[labels].sum()
    return float(
        (rank_sum - positives * (positives + 1) / 2)
        / (positives * negatives)
    )


def _binary_average_precision(
    labels: np.ndarray,
    scores: np.ndarray,
) -> float | None:
    positives = int(labels.sum())
    if not positives:
        return None
    order = np.argsort(-scores, kind="stable")
    ranked_labels = labels[order]
    ranked_scores = scores[order]
    threshold_ends = np.concatenate(
        (np.flatnonzero(np.diff(ranked_scores)), [len(ranked_scores) - 1])
    )
    true_positives = np.cumsum(ranked_labels)[threshold_ends]
    precision = true_positives / (threshold_ends + 1)
    recall = true_positives / positives
    return float(np.sum(np.diff(np.concatenate(([0.0], recall))) * precision))


def spans_from_scores(
    scores: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    *,
    threshold: float,
) -> list[tuple[float, float]]:
    spans = []
    active_start: float | None = None
    active_end = 0.0
    for score, start, end in zip(scores, starts, ends):
        if score >= threshold:
            if active_start is None or start > active_end + 1e-6:
                if active_start is not None:
                    spans.append((active_start, active_end))
                active_start = float(start)
            active_end = float(end)
        elif active_start is not None:
            spans.append((active_start, active_end))
            active_start = None
    if active_start is not None:
        spans.append((active_start, active_end))
    return spans


def score_audio_event(
    scores: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    *,
    intervals: Sequence[tuple[float, float]],
    duration: float,
    threshold: float,
) -> dict[str, Any]:
    labels = np.asarray(
        [
            any(max(start, left) < min(end, right) for left, right in intervals)
            for start, end in zip(starts, ends)
        ],
        dtype=bool,
    )
    top_index = int(np.argmax(scores))
    center = (float(starts[top_index]) + float(ends[top_index])) / 2
    chunk_start = max(0.0, min(center - 5.0, max(0.0, duration - 10.0)))
    chunk = (chunk_start, min(duration, chunk_start + 10.0))
    predicted = spans_from_scores(
        scores,
        starts,
        ends,
        threshold=threshold,
    )
    best_ious = [
        max((_interval_iou(target, span) for span in predicted), default=0.0)
        for target in intervals
    ]
    return {
        "frame_auc": _binary_auc(labels, scores),
        "frame_average_precision": _binary_average_precision(labels, scores),
        "top_point_in_event": bool(labels[top_index]),
        "top_evidence_chunk_hits_event": any(
            _interval_iou(chunk, target) > 0 for target in intervals
        ),
        "top_evidence_chunk": list(chunk),
        "predicted_spans": [list(span) for span in predicted],
        "mean_iou": mean(best_ious),
        "recall_iou_0_3": mean(value >= 0.3 for value in best_ious),
        "recall_iou_0_5": mean(value >= 0.5 for value in best_ious),
        "recall_iou_0_7": mean(value >= 0.7 for value in best_ious),
    }


class _FineLAPScorer:
    model_id = "AndreasXi/FineLAP"
    revision = "b419aa22947d29907a5567f21b81bf3b39a40449"
    threshold = 0.5

    def __init__(self, runtime: ModelRuntime):
        self.provider = get_finelap_model(runtime)

    def scores(
        self,
        audio_path: Path,
        categories: Sequence[str],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        import torch

        with torch.inference_mode():
            text = self.provider.model.get_global_text_embeds(
                list(categories),
                device=self.provider.device,
            ).cpu()
        score_parts = []
        starts = []
        ends = []
        for window in iter_audio_windows(
            audio_path,
            window_seconds=10.0,
            cancellation=CancellationToken(),
        ):
            _global, dense_batch = self.provider.encode_audio([window.pcm])
            dense = dense_batch[0]
            raw = text @ dense.T
            calibrated = torch.sigmoid(
                raw / self.provider.model.temp_local
                + self.provider.model.b_local
            )
            valid = min(
                dense.shape[0],
                int(np.ceil((window.end - window.start) / DENSE_INTERVAL_SECONDS)),
            )
            score_parts.append(calibrated[:, :valid].detach().numpy())
            for activation_index in range(valid):
                start = window.start + activation_index * DENSE_INTERVAL_SECONDS
                starts.append(start)
                ends.append(min(window.end, start + DENSE_INTERVAL_SECONDS))
        return (
            np.concatenate(score_parts, axis=1),
            np.asarray(starts),
            np.asarray(ends),
        )


def _load_audio_48k(path: Path) -> np.ndarray:
    import av

    blocks = []
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=48_000)
        for frame in container.decode(container.streams.audio[0]):
            converted = resampler.resample(frame)
            values = converted if isinstance(converted, list) else [converted]
            blocks.extend(
                item.to_ndarray().reshape(-1)
                for item in values
                if item is not None
            )
        converted = resampler.resample(None)
        values = converted if isinstance(converted, list) else [converted]
        blocks.extend(
            item.to_ndarray().reshape(-1) for item in values if item is not None
        )
    return np.concatenate(blocks).astype(np.float32)


class _PEAFrameScorer:
    model_id = "facebook/pe-a-frame-small"
    threshold = 0.3

    def __init__(self, model_directory: Path, device: str):
        import torch
        from transformers import PeAudioFrameLevelModel, PeAudioProcessor

        self.revision = model_directory.resolve().name
        self.device = device
        self.processor = PeAudioProcessor.from_pretrained(
            model_directory,
            local_files_only=True,
        )
        self.model = PeAudioFrameLevelModel.from_pretrained(
            model_directory,
            local_files_only=True,
        ).to(device).eval()
        self.torch = torch

    def scores(
        self,
        audio_path: Path,
        categories: Sequence[str],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        audio_inputs = self.processor.feature_extractor(
            _load_audio_48k(audio_path),
            sampling_rate=48_000,
            return_tensors="pt",
        )
        text_inputs = self.processor.tokenizer(
            list(categories),
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        inputs = {
            name: value.to(self.device)
            for name, value in {**audio_inputs, **text_inputs}.items()
        }
        with self.torch.inference_mode():
            scores = self.model(**inputs).logits_audio_text[0].sigmoid()
        frame_count = scores.shape[1]
        starts = np.arange(frame_count, dtype=float) * 0.04
        return scores.cpu().numpy(), starts, starts + 0.04


def run_aegbench_sound(
    *,
    manifest_path: str | Path,
    run_id: str,
    provider: str,
    audio_directory: str | Path | None = None,
    pe_model_directory: str | Path | None = None,
    output_root: str | Path = "benchmark_runs",
    device: str = "cpu",
) -> dict[str, Any]:
    items, dataset_metadata = load_aegbench(
        manifest_path,
        audio_directory=audio_directory,
    )
    config = IndexConfig(
        dataset="aegbench",
        split="test",
        run_id=run_id,
        enabled_modalities=("sound",),
        device=device,
        output_root=output_root,
        generation_id=benchmark_generation_id("aegbench", "test", run_id),
    )
    run_directory = config.run_directory
    if run_directory.exists():
        raise FileExistsError(
            f"Benchmark run already exists: {run_directory}. "
            "Choose a new --run-id."
        )
    ensure_adapter_outputs(run_directory)
    registry = create_capability_registry(
        platform_runtime_checks=LOCAL_INDEX_RUNTIME_CHECKS
    )
    runtime = ModelRuntime(
        VidXPSettings(
            repository_root=run_directory,
            runtime_backend=device,
        ),
        allowed_specs=(*registry.model_specs(), *FINELAP_MODEL_SPECS),
    )
    manifest_store = ManifestStore(config, registry=registry, runtime=runtime)
    manifest_store.initialize([])
    resolved_device = runtime.device_for("sound")
    try:
        load_started = perf_counter()
        if provider == "finelap":
            scorer: AudioEventScorer = _FineLAPScorer(runtime)
        elif provider == "pe-a-frame":
            if pe_model_directory is None:
                raise ValueError(
                    "--pe-model-directory is required for PE-A-Frame."
                )
            scorer = _PEAFrameScorer(
                Path(pe_model_directory),
                resolved_device,
            )
        else:
            raise ValueError(f"Unsupported AEGBench provider: {provider}")
        load_seconds = perf_counter() - load_started
        inference_started = perf_counter()
        predictions = []
        excluded = []
        audio_seconds = 0.0
        for item in items:
            categories = [query.category for query in item.queries]
            scores, starts, ends = scorer.scores(item.audio_path, categories)
            valid = starts < item.duration
            starts = starts[valid]
            ends = np.minimum(ends[valid], item.duration)
            for query, query_scores in zip(item.queries, scores):
                metrics = score_audio_event(
                    query_scores[valid],
                    starts,
                    ends,
                    intervals=query.intervals,
                    duration=item.duration,
                    threshold=scorer.threshold,
                )
                predictions.append(
                    {
                        "query_id": query.query_id,
                        "item_id": item.item_id,
                        "category": query.category,
                        "ground_truth": [list(interval) for interval in query.intervals],
                        **metrics,
                    }
                )
            excluded.extend(
                {
                    "item_id": item.item_id,
                    "category": category,
                    "reason": "category_has_no_annotated_interval",
                }
                for category in item.excluded_categories
            )
            audio_seconds += item.duration
        inference_seconds = perf_counter() - inference_started
        metric_names = (
            "frame_auc",
            "frame_average_precision",
            "top_point_in_event",
            "top_evidence_chunk_hits_event",
            "mean_iou",
            "recall_iou_0_3",
            "recall_iou_0_5",
            "recall_iou_0_7",
        )
        metrics: dict[str, Any] = {
            "query_count": len(predictions),
            "excluded_query_count": len(excluded),
            "audio_count": len(items),
            "audio_seconds": audio_seconds,
            "load_seconds": load_seconds,
            "inference_seconds": inference_seconds,
            "realtime_factor": inference_seconds / audio_seconds,
            "peak_rss_bytes": _peak_rss_bytes(),
        }
        for name in metric_names:
            values = [
                float(row[name])
                for row in predictions
                if row[name] is not None
            ]
            metrics[name] = mean(values)
        write_json_atomic(run_directory / "predictions.json", predictions)
        write_json_atomic(run_directory / "excluded.json", excluded)
        write_json_atomic(run_directory / "metrics.json", metrics)
        write_json_atomic(
            run_directory / "ground_truth.subset.json",
            [
                {
                    "query_id": row["query_id"],
                    "item_id": row["item_id"],
                    "category": row["category"],
                    "ground_truth": row["ground_truth"],
                }
                for row in predictions
            ],
        )
        (run_directory / "timings.jsonl").write_text(
            json.dumps(
                {
                    "stage": "model_load",
                    "elapsed_seconds": load_seconds,
                },
                sort_keys=True,
            )
            + "\n"
            + json.dumps(
                {
                    "stage": "inference",
                    "elapsed_seconds": inference_seconds,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (run_directory / "evaluator.log").write_text(
            "Frame ranking is threshold-free. Interval metrics use the provider's "
            f"published default threshold ({scorer.threshold}).\n",
            encoding="utf-8",
        )
        artifacts = [
            input_artifact(
                manifest_path,
                name="AEGBench manifest",
                source=AEGBENCH_SOURCE,
            )
        ]
        if provider == "pe-a-frame":
            weights = Path(str(pe_model_directory)) / "model.safetensors"
            artifacts.append(
                {
                    "name": "PE-A-Frame weights",
                    "path": str(weights.resolve()),
                    "source": PE_A_FRAME_SOURCE,
                    "revision": scorer.revision,
                    "sha256": sha256_file(weights),
                    "size_bytes": weights.stat().st_size,
                }
            )
        manifest_store.complete_run(store_size_bytes_at_commit=None)
        record_adapter_manifest(
            run_directory,
            benchmark="aegbench",
            subset={
                "audio_count": len(items),
                "query_count": len(predictions),
                "selection": dataset_metadata["selection"],
            },
            artifacts=artifacts,
            state="complete",
            details={
                "provider": scorer.model_id,
                "provider_revision": scorer.revision,
                "resolved_device": resolved_device,
                "dataset_revision": dataset_metadata["dataset_revision"],
                "threshold": scorer.threshold,
                "result_classification": "candidate_selection_subset",
                "frame_ranking_metrics_are_threshold_free": True,
                "interval_metrics_use_provider_default_threshold": True,
                "evidence_chunk_seconds": 10,
            },
        )
        return metrics
    except BaseException as error:
        append_failure(run_directory, stage="aegbench_adapter", error=error)
        raise
