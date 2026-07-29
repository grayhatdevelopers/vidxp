import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vidxp.core.contracts import CancellationToken
from vidxp.core.video import (
    FrameSampling,
    FrameStreamStats,
    iter_frame_batches,
    render_actor_video,
)


class FakeCapture:
    def __init__(self, frames, *, fps=10.0):
        self.frames = list(frames)
        self.fps = fps
        self.position = 0
        self.read_calls = 0
        self.grab_calls = 0
        self.released = False

    def get(self, _):
        return self.fps

    def isOpened(self):
        return True

    def read(self):
        self.read_calls += 1
        if self.position >= len(self.frames):
            return False, None
        frame = self.frames[self.position]
        self.position += 1
        return True, frame

    def grab(self):
        self.grab_calls += 1
        if self.position >= len(self.frames):
            return False
        self.position += 1
        return True

    def release(self):
        self.released = True


class VideoFrameStreamTests(unittest.TestCase):
    def test_stride_advances_skipped_frames_without_materializing_them(self):
        capture = FakeCapture(["f0", "f1", "f2", "f3"])
        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_FPS=5,
            VideoCapture=lambda _: capture,
        )
        stats = FrameStreamStats()

        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            batches = list(
                iter_frame_batches(
                    "unused.mp4",
                    frame_stride=2,
                    batch_size=2,
                    cancellation=CancellationToken(),
                    stats=stats,
                )
            )

        self.assertEqual(
            [sample.frame_index for sample in batches[0]],
            [0, 2],
        )
        self.assertEqual(capture.grab_calls, 2)
        self.assertEqual(stats.frames_advanced, 4)
        self.assertEqual(stats.frames_materialized, 2)
        self.assertTrue(capture.released)

    def test_multiple_cadences_materialize_their_union_once(self):
        capture = FakeCapture([f"f{index}" for index in range(8)])
        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_FPS=5,
            VideoCapture=lambda _: capture,
        )

        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            batches = list(
                iter_frame_batches(
                    "unused.mp4",
                    frame_strides=(3, 4),
                    batch_size=8,
                    cancellation=CancellationToken(),
                )
            )

        self.assertEqual(
            [sample.frame_index for sample in batches[0]],
            [0, 3, 4, 6],
        )
        self.assertEqual(capture.read_calls, 5)
        self.assertEqual(capture.grab_calls, 4)

    def test_time_and_frame_cadences_share_one_materialized_union(self):
        capture = FakeCapture(
            [f"f{index}" for index in range(6)],
            fps=1.5,
        )
        fake_cv2 = types.SimpleNamespace(
            CAP_PROP_FPS=5,
            VideoCapture=lambda _: capture,
        )

        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            batches = list(
                iter_frame_batches(
                    "unused.mp4",
                    samplings=(
                        FrameSampling(source_fps=1.5, target_fps=1.0),
                        FrameSampling(frame_stride=2),
                    ),
                    batch_size=8,
                    cancellation=CancellationToken(),
                )
            )

        self.assertEqual(
            [sample.frame_index for sample in batches[0]],
            [0, 2, 3, 4, 5],
        )

    def test_actor_renderer_creates_output_directory_and_falls_back_codec(self):
        with TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.mp4"
            output_path = Path(directory) / "nested" / "actor.mp4"
            input_path.write_bytes(b"input")
            capture = FakeCapture([object()])
            codecs = []

            class FakeWriter:
                def __init__(self, path, codec):
                    self.path = Path(path)
                    self.opened = codec == "mp4v"

                def isOpened(self):
                    return self.opened

                def write(self, _):
                    self.path.write_bytes(b"video")

                def release(self):
                    pass

            def writer(path, codec, *_):
                codecs.append(codec)
                return FakeWriter(path, codec)

            fake_cv2 = types.SimpleNamespace(
                CAP_PROP_FPS=1,
                CAP_PROP_FRAME_WIDTH=2,
                CAP_PROP_FRAME_HEIGHT=3,
                CAP_PROP_FRAME_COUNT=5,
                FONT_HERSHEY_SIMPLEX=4,
                VideoCapture=lambda _: capture,
                VideoWriter=writer,
                VideoWriter_fourcc=lambda *codec: "".join(codec),
                rectangle=lambda *_args: None,
                putText=lambda *_args: None,
            )

            with patch.dict(sys.modules, {"cv2": fake_cv2}):
                render_actor_video(
                    input_path,
                    output_path,
                    "1",
                    [{"frame_index": 0, "bbox": (0, 1, 1, 0)}],
                )

            self.assertEqual(codecs, ["avc1", "mp4v"])
            self.assertEqual(output_path.read_bytes(), b"video")


if __name__ == "__main__":
    unittest.main()
