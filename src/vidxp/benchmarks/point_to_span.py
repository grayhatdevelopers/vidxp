from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks


PAPER_URL = "https://arxiv.org/abs/2512.10363"
PEAK_PROMINENCE = 0.05
MINIMUM_PEAK_DISTANCE_SECONDS = 1.0
NMS_TIOU_THRESHOLD = 0.8


@dataclass(frozen=True)
class TemporalSimilarity:
    start: float
    end: float
    similarity: float


@dataclass(frozen=True)
class SpanCandidate:
    start: float
    end: float
    score: float
    peak_time: float
    peak_similarity: float
    expansion_threshold: float


@dataclass(frozen=True)
class AdaptiveSpanResult:
    candidates: tuple[SpanCandidate, ...]
    sample_rate_hz: float
    signal_standard_deviation: float
    adaptive_ratio: float
    smoothing_window_samples: int


def squared_l2_to_cosine(raw_distance: float) -> float:
    """Convert squared L2 distance between normalized vectors to cosine similarity."""

    distance = float(raw_distance)
    if not math.isfinite(distance) or not 0.0 <= distance <= 4.0:
        raise ValueError("normalized squared L2 distance must be between 0 and 4")
    return 1.0 - distance / 2.0


def _temporal_iou(first: SpanCandidate, second: SpanCandidate) -> float:
    intersection = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    union = max(first.end, second.end) - min(first.start, second.start)
    return intersection / union if union > 0.0 else 0.0


def _non_maximum_suppression(
    candidates: Sequence[SpanCandidate],
) -> tuple[SpanCandidate, ...]:
    selected: list[SpanCandidate] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-item.score, item.start, item.end),
    ):
        if all(
            _temporal_iou(candidate, retained) <= NMS_TIOU_THRESHOLD
            for retained in selected
        ):
            selected.append(candidate)
    return tuple(selected)


def adaptive_span_generator(
    sequence: Sequence[TemporalSimilarity],
) -> AdaptiveSpanResult:
    """Apply Point-to-Span's Adaptive Span Generator to one uniform score curve.

    This implements Section 3.1 only. The paper does not specify how to turn its
    floating-point smoothing width into samples or how to pad video boundaries;
    this comparison rounds to the nearest sample, uses a minimum width of one,
    and extends edge values during smoothing.
    """

    if len(sequence) < 3:
        raise ValueError("adaptive span generation requires at least three records")
    ordered = tuple(sorted(sequence, key=lambda item: (item.start, item.end)))
    if any(item.start < 0.0 or item.end <= item.start for item in ordered):
        raise ValueError("temporal similarities require valid positive intervals")
    starts = np.asarray([item.start for item in ordered], dtype=np.float64)
    steps = np.diff(starts)
    if np.any(steps <= 0.0):
        raise ValueError("temporal similarities require unique increasing starts")
    median_step = float(np.median(steps))
    if np.any(steps < median_step * 0.45) or np.any(steps > median_step * 1.55):
        raise ValueError(
            "adaptive span generation requires an approximately uniform timeline"
        )

    similarities = np.asarray(
        [item.similarity for item in ordered],
        dtype=np.float64,
    )
    if not np.all(np.isfinite(similarities)):
        raise ValueError("temporal similarities must be finite")
    sample_rate = 1.0 / median_step
    standard_deviation = float(np.std(similarities))
    adaptive_ratio = 0.5 + 0.5 / (1.0 + math.exp(-standard_deviation))
    smoothing_window = max(1, int(round(sample_rate * adaptive_ratio)))
    smoothed = uniform_filter1d(
        similarities,
        size=smoothing_window,
        mode="nearest",
    )
    minimum_distance = max(
        1,
        int(round(sample_rate * MINIMUM_PEAK_DISTANCE_SECONDS)),
    )
    peaks, _ = find_peaks(
        smoothed,
        distance=minimum_distance,
        prominence=PEAK_PROMINENCE,
    )

    candidates = []
    for peak in peaks:
        peak_similarity = float(smoothed[peak])
        threshold = peak_similarity * adaptive_ratio
        if peak_similarity <= threshold:
            continue
        start_index = end_index = int(peak)
        while start_index > 0 and smoothed[start_index - 1] > threshold:
            start_index -= 1
        while end_index + 1 < len(ordered) and smoothed[end_index + 1] > threshold:
            end_index += 1
        candidates.append(
            SpanCandidate(
                start=ordered[start_index].start,
                end=ordered[end_index].end,
                score=float(np.mean(smoothed[start_index : end_index + 1])),
                peak_time=ordered[int(peak)].start,
                peak_similarity=peak_similarity,
                expansion_threshold=threshold,
            )
        )

    return AdaptiveSpanResult(
        candidates=_non_maximum_suppression(candidates),
        sample_rate_hz=sample_rate,
        signal_standard_deviation=standard_deviation,
        adaptive_ratio=adaptive_ratio,
        smoothing_window_samples=smoothing_window,
    )
