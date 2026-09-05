"""Standalone benchmark for #83 clip_mode comparison.

Not part of the automated test suite. Run manually:

    python scripts/benchmark_clip_modes.py path/to/video.mp4

This is a stand-in for the harness in #76, which does not exist yet.
It only measures the action/VideoPrism capability in isolation.
"""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path

from unittest.mock import patch

from vidxp.capabilities.registry import create_capability_registry
from vidxp.capabilities.visual import index_visuals
from vidxp.core.contracts import CancellationToken, IndexConfig, VideoSource
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings

# This benchmark measures clip windowing / shot-detection overhead only,
# not VideoPrism model inference (which requires a ~1GB local download).
# The model layer is mocked, same as the unit tests do.


class _NullStorage:
    def upsert(self, _modality, records, **_kwargs):
        return len(records)

    def delete_records(self, *_args, **_kwargs):
        pass

    def delete_video(self, *_args, **_kwargs):
        pass


def run_once(video_path: str, clip_mode: str) -> dict:
    config = IndexConfig(
        video_id="benchmark-video",
        enabled_modalities=("action",),
        capability_options={"action": {"clip_mode": clip_mode}},
    )
    registry = create_capability_registry()
    runtime = ModelRuntime(
        VidXPSettings(repository_root="unused", runtime_backend="cpu")
    )
    source = VideoSource(path=video_path, video_id="benchmark-video")

    tracemalloc.start()
    started = time.perf_counter()
    with (
        patch(
            "vidxp.capabilities.action.indexing.get_videoprism_model",
            return_value=object(),
        ),
        patch(
            "vidxp.capabilities.action.indexing.encode_video_clips",
            side_effect=lambda clips, _provider: [[0.1] for _ in clips],
        ),
    ):
        result = index_visuals(
            source,
            config=config,
            storage=_NullStorage(),
            cancellation=CancellationToken(),
            registry=registry,
            runtime=runtime,
        )
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "clip_mode": clip_mode,
        "seconds": round(elapsed, 3),
        "peak_memory_mb": round(peak / (1024 * 1024), 2),
        "clips": result.summary.get("videoprism_clips"),
    }


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/benchmark_clip_modes.py <video_path>")
        raise SystemExit(1)
    video_path = sys.argv[1]
    if not Path(video_path).is_file():
        print(f"Not a file: {video_path}")
        raise SystemExit(1)

    print(f"Benchmarking {video_path}\n")
    for clip_mode in ("fixed", "scene"):
        stats = run_once(video_path, clip_mode)
        print(
            f"  clip_mode={stats['clip_mode']:<7} "
            f"time={stats['seconds']:>7}s  "
            f"peak_mem={stats['peak_memory_mb']:>7}MB  "
            f"clips={stats['clips']}"
        )


if __name__ == "__main__":
    main()
