import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from vidxp.core.contracts import (
    IndexCancelledError,
    IndexConfig,
    VideoSource,
)
from vidxp.core.manifest import COMPLETION_FILE, ManifestStore
from vidxp.capabilities.contracts import CapabilityIndexResult
from vidxp.capabilities.speech.specs import FASTER_WHISPER_MODEL
from vidxp.core.runner import (
    _RunLock,
    index_video as _index_video,
    indexing_in_progress,
    run_index as _run_index,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.index_state import IndexingInProgressError
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


EXECUTION_STATE = {
    "git": {"commit": "abc123", "dirty": False},
    "implementation_sha256": "implementation",
    "package_version": "0.1.0",
    "python": "test",
    "platform": "test",
    "dependencies": {"chromadb": "1.0"},
}


def _dependencies(config):
    return {
        "registry": create_capability_registry(),
        "runtime": ModelRuntime(
            VidXPSettings(
                repository_root=config.run_directory,
                runtime_backend=config.device,
            )
        ),
    }


def run_index(sources, config, **options):
    dependencies = _dependencies(config)
    options.setdefault("registry", dependencies["registry"])
    options.setdefault("runtime", dependencies["runtime"])
    options.setdefault("storage", FakeStorage())
    options.setdefault(
        "manifest_store",
        ManifestStore(
            config,
            registry=options["registry"],
            runtime=options["runtime"],
        ),
    )
    return _run_index(sources, config, **options)


def index_video(path, *args, config, **options):
    dependencies = _dependencies(config)
    options.setdefault("registry", dependencies["registry"])
    options.setdefault("runtime", dependencies["runtime"])
    options.setdefault("storage", FakeStorage())
    options.setdefault(
        "manifest_store",
        ManifestStore(
            config,
            registry=options["registry"],
            runtime=options["runtime"],
        ),
    )
    return _index_video(path, *args, config=config, **options)


def visual_result(summary, timings=None):
    normalized = dict(summary)
    scene_frames = int(normalized.get("scene_frames", 0))
    actor_frames = int(normalized.get("actor_frames", 0))
    sampled_frames = max(scene_frames, actor_frames)
    normalized.setdefault("sampled_frames", sampled_frames)
    normalized.setdefault("processed_frames", sampled_frames)
    normalized.setdefault("frame_operations", scene_frames + actor_frames)
    normalized.setdefault("source_frames_advanced", sampled_frames)
    return CapabilityIndexResult(
        summary=normalized,
        timings=timings or {},
    )


class FakeStorage:
    def __init__(self, size=321):
        self.cleared = []
        self.deleted = []
        self.size = size

    def clear(self, modalities=None):
        self.cleared.append(
            None if modalities is None else tuple(modalities)
        )

    def delete_video(self, modality, video_id):
        self.deleted.append((modality, video_id))

    def delete_records(
        self,
        modality,
        *,
        video_id,
        filters=None,
    ):
        self.deleted.append(
            (modality, video_id, dict(filters or {}))
        )

    def size_bytes(self):
        return self.size


class RunnerTests(unittest.TestCase):
    def _config(self, root, modalities=("scene",)):
        return IndexConfig(
            dataset="sample",
            split="test",
            run_id="run-1",
            enabled_modalities=modalities,
            output_root=root,
        )

    def test_two_videos_complete_one_isolated_resumable_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            config = self._config(directory)
            sources = [
                VideoSource(video_id="video-1", path=first),
                VideoSource(video_id="video-2", path=second),
            ]
            storage = FakeStorage()
            calls = []

            def scene_indexer(source, *, config, **_):
                calls.append(config.video_id)
                return visual_result(
                    {
                        "scene_frames": 2,
                        "decoded_frames": 4,
                        "duration": 1.0,
                        "fps": 4.0,
                    }
                )

            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    side_effect=scene_indexer,
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                manifest = run_index(sources, config, storage=storage)
                resumed = run_index(sources, config, storage=storage)

            self.assertEqual(calls, ["video-1", "video-2"])
            self.assertEqual(
                manifest["completed_videos"],
                ["video-1", "video-2"],
            )
            self.assertEqual(resumed["state"], "complete")
            self.assertEqual(manifest["git"]["commit"], "abc123")
            self.assertEqual(
                manifest["store_size_bytes_at_commit"],
                321,
            )
            self.assertEqual(manifest["processed_frames"], 4)
            self.assertTrue((config.run_directory / COMPLETION_FILE).is_file())
            self.assertEqual(
                storage.deleted,
                [("scene", "video-1"), ("scene", "video-2")],
            )

    def test_generation_cleanup_is_scoped_to_each_video(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            config = IndexConfig(
                dataset="sample",
                split="test",
                run_id="run-1",
                enabled_modalities=("scene",),
                output_root=root,
                generation_id="generation-1",
            )
            storage = FakeStorage()

            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    return_value=visual_result(
                        {
                            "scene_frames": 1,
                            "decoded_frames": 1,
                            "duration": 1.0,
                            "fps": 1.0,
                        }
                    ),
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                run_index(
                    [
                        VideoSource(video_id="video-1", path=first),
                        VideoSource(video_id="video-2", path=second),
                    ],
                    config,
                    storage=storage,
                )

            self.assertEqual(
                storage.deleted,
                [
                    (
                        "scene",
                        "video-1",
                        {"generation_id": "generation-1"},
                    ),
                    (
                        "scene",
                        "video-2",
                        {"generation_id": "generation-1"},
                    ),
                ],
            )

    def test_scene_and_actor_are_dispatched_as_one_visual_pipeline(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            config = self._config(directory, ("scene", "actor"))
            source = VideoSource(video_id="video-1", path=path)
            visual = Mock(
                return_value=visual_result(
                    {
                        "source_frames_advanced": 10,
                        "sampled_frames": 5,
                        "processed_frames": 5,
                        "frame_operations": 10,
                        "scene_frames": 5,
                        "actor_frames": 5,
                        "actor_detections": 2,
                        "actor_clusters": 1,
                    },
                    {
                        "frame_stream": 1.0,
                        "scene": 2.0,
                        "actor": 3.0,
                        "visual_total": 6.0,
                    },
                )
            )
            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    visual,
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                manifest = run_index(
                    [source],
                    config,
                    storage=FakeStorage(),
                )

            visual.assert_called_once()
            self.assertEqual(
                visual.call_args.kwargs["modalities"],
                ("scene", "actor"),
            )
            summary = manifest["videos"]["video-1"]["summary"]
            self.assertEqual(summary["source_frames_advanced"], 10)
            self.assertEqual(summary["sampled_frames"], 5)
            self.assertEqual(summary["frame_operations"], 10)

    def test_interrupted_run_skips_completed_video_on_resume(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            config = self._config(directory)
            sources = [
                VideoSource(video_id="video-1", path=first),
                VideoSource(video_id="video-2", path=second),
            ]
            storage = FakeStorage()
            first_attempt = []

            def cancelling_indexer(source, *, config, **_):
                first_attempt.append(config.video_id)
                if config.video_id == "video-2":
                    raise IndexCancelledError("stop")
                return visual_result({"scene_frames": 1})

            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    side_effect=cancelling_indexer,
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
                self.assertRaises(IndexCancelledError),
            ):
                run_index(sources, config, storage=storage)

            self.assertFalse((config.run_directory / COMPLETION_FILE).exists())
            resumed_calls = []

            def successful_indexer(source, *, config, **_):
                resumed_calls.append(config.video_id)
                return visual_result({"scene_frames": 1})

            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    side_effect=successful_indexer,
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                manifest = run_index(sources, config, storage=storage)

            self.assertEqual(first_attempt, ["video-1", "video-2"])
            self.assertEqual(resumed_calls, ["video-2"])
            self.assertEqual(manifest["state"], "complete")

    def test_transcript_only_run_does_not_request_transcription_dependencies(self):
        with TemporaryDirectory() as directory:
            config = self._config(directory, ("speech",))
            source = VideoSource(
                video_id="video-1",
                transcript=(
                    {"text": "hello", "start": 0.0, "end": 1.0},
                ),
            )
            dependency_check = Mock()

            with (
                patch(
                    "vidxp.core.runner.require_dependencies",
                    dependency_check,
                ),
                patch(
                    "vidxp.capabilities.speech.operations.index_speech",
                    return_value={"dialogue_phrases": 1},
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                run_index([source], config, storage=FakeStorage())

        dependency_check.assert_called_once()
        self.assertEqual(
            dependency_check.call_args.args,
            (("speech",),),
        )
        self.assertIs(dependency_check.call_args.kwargs["source"], source)

    def test_manifest_adds_transcription_model_when_run_later_needs_it(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "second.mp4"
            path.write_bytes(b"video")
            config = self._config(directory, ("speech",))
            supplied = VideoSource(
                video_id="video-1",
                transcript=(
                    {"text": "hello", "start": 0.0, "end": 1.0},
                ),
            )
            video = VideoSource(video_id="video-2", path=path)
            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.speech.operations.index_speech",
                    return_value={"dialogue_phrases": 1},
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                first = run_index(
                    [supplied],
                    config,
                    storage=FakeStorage(),
                )
                second = run_index(
                    [supplied, video],
                    config,
                    storage=FakeStorage(),
                )

            self.assertNotIn("transcription", first["models"])
        self.assertEqual(
            second["models"]["transcription"]["model"],
            FASTER_WHISPER_MODEL.model_id,
        )

    def test_changed_input_is_not_silently_accepted_by_checkpoint(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"first")
            config = self._config(directory)
            source = VideoSource(video_id="video-1", path=path)
            indexer = Mock(
                return_value=visual_result({"scene_frames": 1})
            )
            common = (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    indexer,
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            )
            with common[0], common[1], common[2]:
                run_index([source], config, storage=FakeStorage())

            path.write_bytes(b"changed")
            with (
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
                self.assertRaisesRegex(ValueError, "different input bytes"),
            ):
                run_index([source], config, storage=FakeStorage())

    def test_changed_supplied_transcript_invalidates_same_video_input(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"same-video")
            config = self._config(directory, ("speech",))
            first = VideoSource(
                video_id="video-1",
                path=path,
                transcript=(
                    {"text": "first", "start": 0.0, "end": 1.0},
                ),
            )
            second = VideoSource(
                video_id="video-1",
                path=path,
                transcript=(
                    {"text": "changed", "start": 0.0, "end": 1.0},
                ),
            )
            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.speech.operations.index_speech",
                    return_value={"dialogue_phrases": 1},
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                run_index([first], config, storage=FakeStorage())
                with self.assertRaisesRegex(
                    ValueError,
                    "different input bytes",
                ):
                    run_index([second], config, storage=FakeStorage())

    def test_reset_clears_every_collection_not_only_enabled_modalities(self):
        with TemporaryDirectory() as directory:
            source = VideoSource(
                video_id="video-1",
                transcript=(
                    {"text": "hello", "start": 0.0, "end": 1.0},
                ),
            )
            config = self._config(directory, ("speech",))
            storage = FakeStorage()
            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.speech.operations.index_speech",
                    return_value={"dialogue_phrases": 1},
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                run_index(
                    [source],
                    config,
                    storage=storage,
                    reset=True,
                )

            self.assertEqual(storage.cleared, [None])

    def test_failed_forced_rebuild_does_not_leave_stale_checkpoint(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            source = VideoSource(video_id="video-1", path=path)
            config = self._config(directory)
            calls = []

            def indexer(*_, **__):
                calls.append(len(calls) + 1)
                if len(calls) == 2:
                    raise RuntimeError("forced failure")
                return visual_result({"scene_frames": 1})

            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    side_effect=indexer,
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                run_index([source], config, storage=FakeStorage())
                with self.assertRaisesRegex(RuntimeError, "forced failure"):
                    run_index(
                        [source],
                        config,
                        storage=FakeStorage(),
                        resume=False,
                    )
                manifest = run_index(
                    [source],
                    config,
                    storage=FakeStorage(),
                )

            self.assertEqual(calls, [1, 2, 3])
            self.assertEqual(manifest["state"], "complete")

    def test_resume_rejects_execution_environment_drift(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            source = VideoSource(video_id="video-1", path=path)
            config = self._config(directory)
            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    return_value=visual_result({"scene_frames": 1}),
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                run_index([source], config, storage=FakeStorage())

            changed = {
                **EXECUTION_STATE,
                "implementation_sha256": "different",
            }
            with (
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=changed,
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "implementation or dependency environment changed",
                ),
            ):
                run_index([source], config, storage=FakeStorage())

    def test_generated_run_files_do_not_invalidate_execution_fingerprint(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            source = VideoSource(video_id="video-1", path=path)
            config = self._config(directory)
            dirty_state = {
                **EXECUTION_STATE,
                "git": {"commit": "abc123", "dirty": True},
            }
            indexer = Mock(
                return_value=visual_result({"scene_frames": 1})
            )
            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    indexer,
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    side_effect=[EXECUTION_STATE, dirty_state],
                ),
            ):
                run_index([source], config, storage=FakeStorage())
                run_index([source], config, storage=FakeStorage())

            indexer.assert_called_once()

    def test_run_lock_rejects_a_second_process_owner(self):
        with TemporaryDirectory() as directory:
            run_directory = Path(directory) / "run"
            with _RunLock(run_directory):
                with self.assertRaises(IndexingInProgressError):
                    with _RunLock(run_directory):
                        pass

    def test_run_lock_is_visible_outside_the_worker_process(self):
        with TemporaryDirectory() as directory:
            config = IndexConfig.local(storage_directory=directory)
            with _RunLock(config.run_directory):
                self.assertTrue(indexing_in_progress(config))
            self.assertFalse(indexing_in_progress(config))

    def test_local_index_hash_is_reused_by_run_index(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            checksum = "a" * 64
            config = IndexConfig.local(
                storage_directory=str(Path(directory) / "index"),
            )
            manifest = {
                "videos": {
                    checksum: {
                        "summary": {"scene_frames": 1},
                    }
                }
            }
            with (
                patch(
                    "vidxp.core.runner.source_checksum",
                    return_value=checksum,
                ) as hash_source,
                patch(
                    "vidxp.core.runner.run_index",
                    return_value=manifest,
                ) as run,
            ):
                index_video(str(path), config=config)

            hash_source.assert_called_once()
            indexed_source = run.call_args.args[0][0]
            self.assertEqual(indexed_source.checksum, checksum)

    def test_manifest_and_timing_files_are_valid_json(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            config = self._config(directory)
            with (
                patch("vidxp.core.runner.require_dependencies"),
                patch(
                    "vidxp.capabilities.visual.index_visuals",
                    return_value=visual_result({"scene_frames": 1}),
                ),
                patch(
                    "vidxp.core.manifest.execution_state",
                    return_value=EXECUTION_STATE,
                ),
            ):
                run_index(
                    [VideoSource(video_id="video-1", path=path)],
                    config,
                    storage=FakeStorage(),
                )

            manifest = json.loads(
                (config.run_directory / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            timing_lines = (
                config.run_directory / "timings.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(manifest["configuration"]["frame_stride"], 1)
            self.assertTrue(all(json.loads(line) for line in timing_lines))


if __name__ == "__main__":
    unittest.main()
