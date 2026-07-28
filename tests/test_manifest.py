import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.core.manifest import (
    ManifestStore,
    source_checksums,
    write_json_atomic,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


class ManifestIdentityTests(unittest.TestCase):
    def test_atomic_write_retries_transient_windows_reader_lock(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with (
                patch.object(
                    Path,
                    "replace",
                    side_effect=[
                        PermissionError("reader lock"),
                        PermissionError("reader lock"),
                        None,
                    ],
                ) as replace,
                patch("vidxp.core.manifest.time.sleep") as sleep,
            ):
                write_json_atomic(path, {"state": "running"})

            self.assertEqual(replace.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_declared_video_checksum_does_not_suppress_transcript_hash(self):
        transcript = ({"text": "hello", "start": 0.0, "end": 1.0},)
        checksums = source_checksums(
            VideoSource(
                path="not-read-when-checksum-is-declared.mp4",
                transcript=transcript,
                checksum="a" * 64,
            )
        )

        self.assertEqual(checksums["video"], "a" * 64)
        self.assertIn("transcript", checksums)

    def test_checkpoint_filenames_do_not_embed_dataset_video_ids(self):
        with TemporaryDirectory() as directory:
            config = IndexConfig(
                dataset="sample",
                split="test",
                run_id="run-1",
                output_root=directory,
                enabled_modalities=("scene",),
            )
            store = ManifestStore(
                config,
                registry=create_capability_registry(),
                runtime=ModelRuntime(
                    VidXPSettings(
                        repository_root=config.run_directory,
                        runtime_backend="cpu",
                    )
                ),
            )
            video_id = "folder/name:video"
            expected = hashlib.sha256(video_id.encode("utf-8")).hexdigest()

            self.assertEqual(
                store._checkpoint_path(video_id),
                Path(directory)
                / "sample"
                / "run-1"
                / "checkpoints"
                / f"{expected}.json",
            )

    def test_terminal_manifests_refresh_resolved_runtime_identity(self):
        for terminal in ("fail_video", "interrupt_video"):
            with self.subTest(terminal=terminal):
                with TemporaryDirectory() as directory:
                    config = IndexConfig(
                        dataset="sample",
                        split="test",
                        run_id="run-1",
                        output_root=directory,
                        enabled_modalities=("scene",),
                    )
                    runtime = Mock()
                    runtime.describe.return_value = {
                        "resolved_models": {},
                    }
                    store = ManifestStore(
                        config,
                        registry=create_capability_registry(),
                        runtime=runtime,
                    )
                    source = VideoSource(
                        video_id="video-1",
                        transcript=(
                            {"text": "hello", "start": 0.0, "end": 1.0},
                        ),
                    )
                    with patch(
                        "vidxp.core.manifest.execution_state",
                        return_value={
                            "git": {"commit": None, "dirty": None},
                            "implementation_sha256": "test",
                            "package_version": "test",
                            "python": "test",
                            "platform": "test",
                            "dependencies": {},
                        },
                    ):
                        store.initialize(
                            [
                                (
                                    "video-1",
                                    source,
                                    "a" * 64,
                                    {"declared": "a" * 64},
                                )
                            ]
                        )
                    store.start_video("video-1")
                    runtime.describe.return_value = {
                        "resolved_models": {"scene": {"cached": True}},
                        "compute_precision": {"scene": "float32"},
                    }

                    if terminal == "fail_video":
                        store.fail_video("video-1", "scene", "failed")
                    else:
                        store.interrupt_video("video-1", "scene")

                    self.assertEqual(
                        store.read()["models"]["runtime"]["resolved_models"],
                        {"scene": {"cached": True}},
                    )
                    self.assertEqual(
                        store.read()["models"]["runtime"][
                            "compute_precision"
                        ],
                        {"scene": "float32"},
                    )


if __name__ == "__main__":
    unittest.main()
