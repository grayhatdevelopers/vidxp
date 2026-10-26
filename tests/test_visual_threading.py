from __future__ import annotations

import os
import signal
import threading
import time
import unittest
from contextvars import ContextVar
from unittest.mock import Mock, patch

from vidxp.capabilities.visual import (
    _Participant,
    _consume_visual_stream,
)
from vidxp.core.contracts import CancellationToken, IndexConfig, VideoSource
from vidxp.core.video import FrameSample, FrameSampling


def _mock_participant(
    name: str,
    *,
    batch_size: int = 4,
    frame_stride: int = 1,
) -> _Participant:
    processor = Mock()
    processor.batch_size.return_value = batch_size
    processor.sampling.return_value = FrameSampling(frame_stride=frame_stride)
    processor.state = object()
    return _Participant(
        name=name,
        processor=processor,
        state=processor.state,
        sampling=FrameSampling(frame_stride=frame_stride),
    )


def _sample(frame_index: int, timestamp: float) -> FrameSample:
    return FrameSample(frame_index=frame_index, timestamp=timestamp, frame=object())


def _consume(participants, batches, *, progress=None, decoded=None):
    def fake_stream(path, *, stats=None, **kw):
        for batch in batches:
            if decoded is not None:
                decoded.append(batch)
            yield batch

    frame_count = sum(len(batch) for batch in batches)
    with patch(
        "vidxp.capabilities.visual.iter_frame_batches",
        side_effect=fake_stream,
    ):
        return _consume_visual_stream(
            VideoSource(video_id="v1", path="unused.mp4"),
            participants=participants,
            expected=frame_count,
            info=Mock(
                fps=24,
                frame_count=frame_count,
                duration=frame_count / 24,
                width=2,
                height=2,
            ),
            config=IndexConfig(
                video_id="v1",
                enabled_modalities=tuple(p.name for p in participants),
            ),
            storage=Mock(),
            cancellation=CancellationToken(),
            progress=progress,
            timings={
                "frame_stream": 0.0,
                **{p.name: 0.0 for p in participants},
            },
        )


class ConsumeVisualStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._rgb_patch = patch(
            "vidxp.capabilities.visual._rgb_samples",
            side_effect=lambda samples: samples,
        )
        cls._rgb_patch.start()
        cls.addClassCleanup(cls._rgb_patch.stop)

    def test_single_participant_processes_all_samples(self):
        scene = _mock_participant("scene")
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene",))
        storage = Mock()
        cancellation = CancellationToken()
        timings = {"frame_stream": 0.0, "scene": 0.3}
        batches = [
            [_sample(0, 0.0), _sample(1, 0.04)],
        ]

        def fake_stream(path, *, stats=None, **kw):
            stats.frames_advanced = 2
            stats.frames_materialized = 2
            return iter(batches)

        with patch(
            "vidxp.capabilities.visual.iter_frame_batches",
            side_effect=fake_stream,
        ):
            result = _consume_visual_stream(
                source,
                participants=[scene],
                expected=2,
                info=Mock(fps=24, frame_count=2, duration=0.08, width=2, height=2),
                config=config,
                storage=storage,
                cancellation=cancellation,
                progress=None,
                timings=timings,
            )

        self.assertEqual(result.frames_advanced, 2)
        self.assertEqual(result.frames_materialized, 2)
        scene.processor.process.assert_called_once()
        call_args = scene.processor.process.call_args[0]
        self.assertEqual(len(call_args[0]), 2)

    def test_two_participants_each_receive_own_samples(self):
        scene = _mock_participant("scene", frame_stride=1)
        actor = _mock_participant("actor", frame_stride=2)
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene", "actor"))
        storage = Mock()
        cancellation = CancellationToken()
        timings = {"frame_stream": 0.0, "scene": 0.3, "actor": 0.2}
        batches = [
            [_sample(0, 0.0), _sample(1, 0.04), _sample(2, 0.08)],
            [_sample(3, 0.12)],
        ]

        def fake_stream(path, *, stats=None, **kw):
            stats.frames_advanced = 4
            stats.frames_materialized = 4
            return iter(batches)

        with patch(
            "vidxp.capabilities.visual.iter_frame_batches",
            side_effect=fake_stream,
        ):
            _consume_visual_stream(
                source,
                participants=[scene, actor],
                expected=4,
                info=Mock(fps=24, frame_count=4, duration=0.16, width=2, height=2),
                config=config,
                storage=storage,
                cancellation=cancellation,
                progress=None,
                timings=timings,
            )

        scene_sample_count = sum(
            len(call[0][0]) for call in scene.processor.process.call_args_list
        )
        actor_sample_count = sum(
            len(call[0][0]) for call in actor.processor.process.call_args_list
        )
        all_actor_samples = [
            s for call in actor.processor.process.call_args_list for s in call[0][0]
        ]
        self.assertEqual(scene_sample_count, 4)
        self.assertEqual(actor_sample_count, 2)
        for sample in all_actor_samples:
            self.assertEqual(sample.frame_index % 2, 0)

    def test_cancellation_between_batches_stops_early(self):
        scene = _mock_participant("scene")
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene",))
        storage = Mock()
        cancellation = CancellationToken()
        timings = {"frame_stream": 0.0, "scene": 0.3}
        proceed = threading.Event()

        def blocking_batch(path, *, stats=None, cancellation=None, **kw):
            yield [_sample(0, 0.0)]
            proceed.wait(timeout=5)
            cancellation.raise_if_cancelled()
            yield [_sample(1, 0.04)]

        with patch(
            "vidxp.capabilities.visual.iter_frame_batches",
            side_effect=blocking_batch,
        ):
            from vidxp.core.contracts import IndexCancelledError

            cancel_timer = threading.Timer(
                0.1, lambda: (cancellation.cancel(), proceed.set())
            )
            cancel_timer.start()
            with self.assertRaises(IndexCancelledError):
                _consume_visual_stream(
                    source,
                    participants=[scene],
                    expected=2,
                    info=Mock(fps=24, frame_count=2, duration=0.08, width=2, height=2),
                    config=config,
                    storage=storage,
                    cancellation=cancellation,
                    progress=None,
                    timings=timings,
                )
            cancel_timer.cancel()

    def test_error_in_decode_propagates(self):
        scene = _mock_participant("scene")
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene",))
        storage = Mock()
        cancellation = CancellationToken()
        timings = {"frame_stream": 0.0, "scene": 0.3}

        def broken_stream(path, *, stats=None, **kw):
            yield [_sample(0, 0.0)]
            raise RuntimeError("decode failure")

        with patch(
            "vidxp.capabilities.visual.iter_frame_batches",
            side_effect=broken_stream,
        ):
            with self.assertRaises(RuntimeError):
                _consume_visual_stream(
                    source,
                    participants=[scene],
                    expected=2,
                    info=Mock(fps=24, frame_count=2, duration=0.08, width=2, height=2),
                    config=config,
                    storage=storage,
                    cancellation=cancellation,
                    progress=None,
                    timings=timings,
                )

    def test_error_in_participant_propagates(self):
        scene = _mock_participant("scene")
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene",))
        storage = Mock()
        cancellation = CancellationToken()
        timings = {"frame_stream": 0.0, "scene": 0.3}

        def broken_process(samples, **kw):
            raise ValueError("participant error")

        scene.processor.process = broken_process

        batches = [[_sample(0, 0.0)]]

        def fake_stream(path, *, stats=None, **kw):
            return iter(batches)

        with patch(
            "vidxp.capabilities.visual.iter_frame_batches",
            side_effect=fake_stream,
        ):
            with self.assertRaises(ValueError):
                _consume_visual_stream(
                    source,
                    participants=[scene],
                    expected=1,
                    info=Mock(fps=24, frame_count=1, duration=0.04, width=2, height=2),
                    config=config,
                    storage=storage,
                    cancellation=cancellation,
                    progress=None,
                    timings=timings,
                )

    def test_error_in_participant_mid_stream_propagates_without_deadlock(self):
        scene = _mock_participant("scene")
        actor = _mock_participant("actor")
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene", "actor"))
        storage = Mock()
        cancellation = CancellationToken()
        timings = {"frame_stream": 0.0, "scene": 0.3, "actor": 0.2}

        def broken_process(samples, **kw):
            raise ValueError("actor failure mid-stream")

        actor.processor.process = broken_process

        batches = [
            [_sample(i, i / 24.0), _sample(i + 1, (i + 1) / 24.0)]
            for i in range(0, 20, 2)
        ]

        def fake_stream(path, *, stats=None, **kw):
            stats.frames_advanced = 20
            stats.frames_materialized = 20
            return iter(batches)

        outcome = {}

        def run():
            with patch(
                "vidxp.capabilities.visual.iter_frame_batches",
                side_effect=fake_stream,
            ):
                try:
                    _consume_visual_stream(
                        source,
                        participants=[scene, actor],
                        expected=20,
                        info=Mock(
                            fps=24, frame_count=20, duration=0.8, width=2, height=2
                        ),
                        config=config,
                        storage=storage,
                        cancellation=cancellation,
                        progress=None,
                        timings=timings,
                    )
                    outcome["error"] = None
                except ValueError as exc:
                    outcome["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(
            worker.is_alive(),
            "_consume_visual_stream deadlocked on a failed participant",
        )
        self.assertIsInstance(outcome.get("error"), ValueError)

    def test_worker_callbacks_inherit_owning_thread_context(self):
        workflow_id: ContextVar[str | None] = ContextVar(
            "vidxp_test_workflow_id", default=None
        )

        class ContextAwareCancellationEvent:
            def __init__(self) -> None:
                self._cancelled = False

            def is_set(self) -> bool:
                if workflow_id.get() is None:
                    raise RuntimeError(
                        "DBOS workflow context missing in cancellation"
                    )
                return self._cancelled

            def set(self) -> None:
                self._cancelled = True

        scene = _mock_participant("scene")
        actor = _mock_participant("actor")
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene", "actor"))
        storage = Mock()
        cancellation = CancellationToken(ContextAwareCancellationEvent())
        timings = {"frame_stream": 0.0, "scene": 0.3, "actor": 0.2}
        batches = [
            [_sample(0, 0.0), _sample(1, 0.04)],
            [_sample(2, 0.08)],
        ]
        progress_events = []

        def fake_stream(path, *, stats=None, **kw):
            stats.frames_advanced = 3
            stats.frames_materialized = 3
            return iter(batches)

        def publish(event):
            if workflow_id.get() is None:
                raise RuntimeError("DBOS workflow context missing in progress")
            progress_events.append(event)

        token = workflow_id.set("test-workflow")
        try:
            with patch(
                "vidxp.capabilities.visual.iter_frame_batches",
                side_effect=fake_stream,
            ):
                _consume_visual_stream(
                    source,
                    participants=[scene, actor],
                    expected=3,
                    info=Mock(
                        fps=24, frame_count=3, duration=0.12, width=2, height=2
                    ),
                    config=config,
                    storage=storage,
                    cancellation=cancellation,
                    progress=publish,
                    timings=timings,
                )
        finally:
            workflow_id.reset(token)

        self.assertTrue(progress_events)
        self.assertIsNone(workflow_id.get())

    def test_slow_participant_at_shutdown_does_not_deadlock(self):
        scene = _mock_participant("scene")
        actor = _mock_participant("actor")
        source = VideoSource(video_id="v1", path="unused.mp4")
        config = IndexConfig(video_id="v1", enabled_modalities=("scene", "actor"))
        storage = Mock()
        cancellation = CancellationToken()
        timings = {"frame_stream": 0.0, "scene": 0.3, "actor": 0.2}

        def slow_process(samples, **kw):
            time.sleep(0.01)

        scene.processor.process = slow_process

        batches = [[_sample(i, i / 24.0)] for i in range(24)]

        def fake_stream(path, *, stats=None, **kw):
            stats.frames_advanced = 24
            stats.frames_materialized = 24
            return iter(batches)

        outcome = {}

        def run():
            with patch(
                "vidxp.capabilities.visual.iter_frame_batches",
                side_effect=fake_stream,
            ):
                try:
                    _consume_visual_stream(
                        source,
                        participants=[scene, actor],
                        expected=24,
                        info=Mock(
                            fps=24, frame_count=24, duration=1.0, width=2, height=2
                        ),
                        config=config,
                        storage=storage,
                        cancellation=cancellation,
                        progress=None,
                        timings=timings,
                    )
                    outcome["error"] = None
                except Exception as exc:
                    outcome["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=5)
        self.assertFalse(
            worker.is_alive(),
            "_consume_visual_stream deadlocked at shutdown with a full queue",
        )
        self.assertIsNone(outcome.get("error"))

    def test_participants_process_at_the_same_time(self):
        scene = _mock_participant("scene")
        actor = _mock_participant("actor")
        both_processing = threading.Barrier(2, timeout=2)

        def meet(samples, **kw):
            both_processing.wait()

        scene.processor.process = meet
        actor.processor.process = meet

        _consume([scene, actor], [[_sample(0, 0.0)], [_sample(1, 1 / 24)]])

    def test_decode_waits_while_a_participant_queue_is_full(self):
        scene = _mock_participant("scene")
        started = threading.Event()
        release = threading.Event()

        def held(samples, **kw):
            started.set()
            release.wait(timeout=5)

        scene.processor.process = held
        batches = [[_sample(i, i / 24)] for i in range(20)]
        decoded = []
        worker = threading.Thread(
            target=_consume, args=([scene], batches), kwargs={"decoded": decoded}
        )
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=2))
            time.sleep(0.3)
            decoded_while_held = len(decoded)
        finally:
            release.set()
            worker.join(timeout=5)

        # One batch in process, four queued, one waiting to be queued.
        self.assertLessEqual(decoded_while_held, 6)
        self.assertEqual(len(decoded), 20)

    def test_progress_counts_frames_every_participant_finished(self):
        scene = _mock_participant("scene")
        actor = _mock_participant("actor")
        started = threading.Event()
        release = threading.Event()

        def held(samples, **kw):
            started.set()
            release.wait(timeout=5)

        actor.processor.process = held
        batches = [[_sample(i, i / 24)] for i in range(10)]
        events = []
        worker = threading.Thread(
            target=_consume,
            args=([scene, actor], batches),
            kwargs={"progress": events.append},
        )
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=2))
            time.sleep(0.3)
            reported_while_held = list(events)
        finally:
            release.set()
            worker.join(timeout=5)

        self.assertEqual(reported_while_held, [])
        currents = [event["current"] for event in events]
        self.assertEqual(currents, sorted(set(currents)))
        self.assertEqual(currents[-1], 10)
        self.assertTrue(all(event["total"] == 10 for event in events))

    def test_interrupt_in_owning_thread_stops_workers(self):
        scene = _mock_participant("scene")
        processed = []

        def interrupt_once(samples, **kw):
            processed.append(samples)
            if len(processed) == 1:
                os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.01)

        scene.processor.process = interrupt_once
        batches = [[_sample(i, i / 24)] for i in range(200)]

        with self.assertRaises(KeyboardInterrupt):
            _consume([scene], batches)

        workers = [
            t for t in threading.enumerate() if t.name.startswith("vidxp-visual-")
        ]
        self.assertEqual(workers, [])
        self.assertLess(len(processed), 200)
