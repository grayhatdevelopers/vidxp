"""
Benchmark: sequential vs concurrent capability-group indexing.

Run from the repo root with the venv active:

    python benchmark_concurrency.py

This does NOT touch real models — it patches the two capability entry
points (visual.index_visuals, speech.operations.index_speech) with a
fixed artificial delay to simulate model-inference latency, the same
way tests/test_runner.py does. That isolates the orchestration
overhead/speedup from actual model performance, which is the thing
this PR changes.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vidxp.capabilities.contracts import CapabilityIndexResult
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.manifest import ManifestStore
from vidxp.core.runner import run_index
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings

# Simulated per-group latencies (seconds). Numbers are illustrative
# stand-ins for real model call durations (e.g. a SigLIP2 batch vs a
# faster-whisper transcription pass) — swap these for numbers pulled
# from a real profiling run before quoting them in a PR description.
SCENE_LATENCY = 0.40
SPEECH_LATENCY = 0.55

REPEATS = 5


class FakeStorage:
    def clear(self, modalities=None):
        pass

    def delete_video(self, modality, video_id):
        pass

    def delete_records(self, modality, *, video_id, filters=None):
        pass

    def size_bytes(self):
        return 0


def _visual_result(summary):
    normalized = dict(summary)
    normalized.setdefault("sampled_frames", 1)
    normalized.setdefault("processed_frames", 1)
    normalized.setdefault("frame_operations", 1)
    normalized.setdefault("source_frames_advanced", 1)
    return CapabilityIndexResult(summary=normalized, timings={})


def slow_visual(*_args, **_kwargs):
    time.sleep(SCENE_LATENCY)
    return _visual_result({"scene_frames": 1})


def slow_speech(*_args, **_kwargs):
    time.sleep(SPEECH_LATENCY)
    return {"dialogue_phrases": 1}



def _run_once(config, source):
    registry = create_capability_registry()
    runtime = ModelRuntime(
        VidXPSettings(
            repository_root=config.run_directory,
            runtime_backend=config.device,
        )
    )
    manifest_store = ManifestStore(config, registry=registry, runtime=runtime)

    with (
        patch("vidxp.core.runner.require_dependencies"),
        patch("vidxp.capabilities.visual.index_visuals", side_effect=slow_visual),
        patch(
            "vidxp.capabilities.speech.operations.index_speech",
            side_effect=slow_speech,
        ),
        patch(
            "vidxp.core.manifest.execution_state",
            return_value={
                "git": {"commit": "bench", "dirty": False},
                "implementation_sha256": "bench",
                "package_version": "0.0.0",
                "python": "bench",
                "platform": "bench",
                "dependencies": {},
            },
        ),
    ):
        start = time.perf_counter()
        run_index(
            [source],
            config,
            storage=FakeStorage(),
            registry=registry,
            runtime=runtime,
            manifest_store=manifest_store,
            reset=True,
        )
        return time.perf_counter() - start


def main() -> None:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "video.mp4"
        path.write_bytes(b"video")
        source = VideoSource(video_id="bench-video", path=path)
        config = IndexConfig(
            dataset="bench",
            split="test",
            run_id="bench-run",
            enabled_modalities=("scene", "speech"),
            output_root=directory,
        )

        durations = [_run_once(config, source) for _ in range(REPEATS)]

    theoretical_sequential = SCENE_LATENCY + SPEECH_LATENCY
    theoretical_concurrent = max(SCENE_LATENCY, SPEECH_LATENCY)

    print(f"Simulated per-group latency: scene={SCENE_LATENCY}s, speech={SPEECH_LATENCY}s")
    print(f"Theoretical sequential total: {theoretical_sequential:.3f}s")
    print(f"Theoretical concurrent total (Amdahl ceiling = slowest group): {theoretical_concurrent:.3f}s")
    print(f"Theoretical max speedup: {theoretical_sequential / theoretical_concurrent:.2f}x")
    print()
    print(f"Measured wall-clock over {REPEATS} runs (current runner, concurrent groups):")
    for i, d in enumerate(durations, 1):
        print(f"  run {i}: {d:.3f}s")
    print(f"  mean: {statistics.mean(durations):.3f}s")
    print(f"  stdev: {statistics.pstdev(durations):.3f}s" if len(durations) > 1 else "")
    print()
    measured_speedup = theoretical_sequential / statistics.mean(durations)
    print(f"Measured speedup vs sequential baseline: {measured_speedup:.2f}x")
    print(
        "Note: measured speedup should approach but not exceed the theoretical "
        "ceiling above — overhead (thread startup, lock contention, manifest "
        "writes) accounts for the gap."
    )


if __name__ == "__main__":
    main()
