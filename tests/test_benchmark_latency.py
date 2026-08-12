from __future__ import annotations

import unittest
from pathlib import Path

from vidxp.benchmarks.latency import (
    SyntheticCorpusSpec,
    aggregate_latency_runs,
    build_clip_command,
    build_latency_sources,
    compare_baseline,
    synthetic_transcript,
    validate_latency_options,
)


class LatencyValidationTests(unittest.TestCase):
    def test_validates_default_options(self):
        selected = validate_latency_options(
            modalities=("scene",),
            videos=1,
            duration_seconds=8.0,
            fps=24,
            width=320,
            height=180,
            repetitions=3,
            input_mode="transcript",
            audio_mode="none",
            baseline_tolerance=0.15,
        )
        self.assertEqual(selected, ("scene",))

    def test_rejects_empty_modalities(self):
        with self.assertRaises(ValueError):
            validate_latency_options(
                modalities=(),
                videos=1,
                duration_seconds=8.0,
                fps=24,
                width=320,
                height=180,
                repetitions=1,
                input_mode="transcript",
                audio_mode="none",
                baseline_tolerance=0.15,
            )

    def test_rejects_unsupported_modality(self):
        with self.assertRaises(ValueError):
            validate_latency_options(
                modalities=("scene", "ocr"),
                videos=1,
                duration_seconds=8.0,
                fps=24,
                width=320,
                height=180,
                repetitions=1,
                input_mode="transcript",
                audio_mode="none",
                baseline_tolerance=0.15,
            )

    def test_rejects_transcribe_without_flite(self):
        with self.assertRaises(ValueError):
            validate_latency_options(
                modalities=("scene", "dialogue"),
                videos=1,
                duration_seconds=8.0,
                fps=24,
                width=320,
                height=180,
                repetitions=1,
                input_mode="transcribe",
                audio_mode="sine",
                baseline_tolerance=0.15,
            )

    def test_accepts_transcribe_with_flite(self):
        selected = validate_latency_options(
            modalities=("dialogue",),
            videos=1,
            duration_seconds=8.0,
            fps=24,
            width=320,
            height=180,
            repetitions=1,
            input_mode="transcribe",
            audio_mode="flite",
            baseline_tolerance=0.15,
        )
        self.assertEqual(selected, ("dialogue",))

    def test_deduplicates_modalities(self):
        selected = validate_latency_options(
            modalities=("scene", "scene", "actor"),
            videos=1,
            duration_seconds=8.0,
            fps=24,
            width=320,
            height=180,
            repetitions=1,
            input_mode="transcript",
            audio_mode="none",
            baseline_tolerance=0.15,
        )
        self.assertEqual(selected, ("scene", "actor"))


class SyntheticTranscriptTests(unittest.TestCase):
    def test_returns_one_segment_with_words(self):
        transcript = synthetic_transcript(duration_seconds=10.0, seed=42)
        self.assertEqual(len(transcript), 1)
        segment = transcript[0]
        self.assertGreater(len(segment["text"]), 0)
        self.assertEqual(segment["start"], 0.0)
        self.assertGreater(segment["end"], 0.0)
        self.assertGreater(len(segment["words"]), 0)
        for word in segment["words"]:
            self.assertIn("word", word)
            self.assertIsInstance(word["start"], float)
            self.assertIsInstance(word["end"], float)

    def test_deterministic_across_calls(self):
        first = synthetic_transcript(duration_seconds=5.0, seed=99)
        second = synthetic_transcript(duration_seconds=5.0, seed=99)
        self.assertEqual(first, second)

    def test_different_seeds_differ(self):
        first = synthetic_transcript(duration_seconds=5.0, seed=99)
        second = synthetic_transcript(duration_seconds=5.0, seed=100)
        self.assertNotEqual(first, second)


class BuildClipCommandTests(unittest.TestCase):
    def test_no_audio_default(self):
        spec = SyntheticCorpusSpec(
            videos=1, duration_seconds=8.0, fps=24,
            width=320, height=180, audio_mode="none", seed=42,
        )
        command = build_clip_command(spec=spec, ffmpeg="ffmpeg", destination=Path("out.mp4"))
        self.assertIn("testsrc2=size=320x180:rate=24", command)
        self.assertIn("-an", command)
        self.assertNotIn("-c:a", command)

    def test_sine_audio_adds_aac(self):
        spec = SyntheticCorpusSpec(
            videos=1, duration_seconds=8.0, fps=24,
            width=320, height=180, audio_mode="sine", seed=42,
        )
        command = build_clip_command(spec=spec, ffmpeg="ffmpeg", destination=Path("out.mp4"))
        self.assertIn("sine=frequency=440:sample_rate=16000", command)
        self.assertIn("-c:a", command)
        self.assertNotIn("-an", command)

    def test_flite_audio_contains_filter_ref(self):
        spec = SyntheticCorpusSpec(
            videos=1, duration_seconds=8.0, fps=24,
            width=320, height=180, audio_mode="flite", seed=42,
        )
        command = build_clip_command(spec=spec, ffmpeg="ffmpeg", destination=Path("out.mp4"))
        flite_args = [arg for arg in command if "flite=text=" in arg]
        self.assertEqual(len(flite_args), 1)

    def test_duration_is_formatted(self):
        spec = SyntheticCorpusSpec(
            videos=1, duration_seconds=3.5, fps=30,
            width=640, height=480, audio_mode="none", seed=0,
        )
        command = build_clip_command(spec=spec, ffmpeg="ffmpeg", destination=Path("clip.mp4"))
        idx = command.index("-t")
        self.assertEqual(command[idx + 1], "3.5")
        self.assertIn("testsrc2=size=640x480:rate=30", command)


class BuildSourcesTests(unittest.TestCase):
    def test_transcript_attached_in_input_mode(self):
        spec = SyntheticCorpusSpec(
            videos=2, duration_seconds=4.0, fps=24,
            width=320, height=180, audio_mode="none", seed=42,
        )
        clips = [Path(f"{i}.mp4") for i in range(2)]
        sources = build_latency_sources(clips=clips, spec=spec, input_mode="transcript")
        self.assertEqual(len(sources), 2)
        for index, source in enumerate(sources):
            self.assertIsNotNone(source.transcript)
            self.assertIsNotNone(source.path)
            self.assertIsNotNone(source.video_id)
            self.assertEqual(source.source_name, f"clip-{index:03d}.mp4")

    def test_no_transcript_in_transcribe_mode(self):
        spec = SyntheticCorpusSpec(
            videos=1, duration_seconds=4.0, fps=24,
            width=320, height=180, audio_mode="flite", seed=42,
        )
        sources = build_latency_sources(clips=[Path("0.mp4")], spec=spec, input_mode="transcribe")
        for source in sources:
            self.assertIsNone(source.transcript)


class AggregateMetricsTests(unittest.TestCase):
    def _sample_manifest(self, scene_seconds, scene_frames, actor_seconds, actor_frames):
        return {
            "processed_frames": scene_frames,
            "record_counts": {"scene": scene_frames, "actor": actor_frames},
            "git": {"commit": "abc", "dirty": False},
            "environment": {"platform": "test"},
            "config_fingerprint": "fp1",
            "completed_at": "2026-01-01T00:00:00",
            "videos": {
                "vid-1": {
                    "state": "complete",
                    "summary": {
                        "scene_frames": scene_frames,
                        "actor_frames": actor_frames,
                        "source_frames_advanced": scene_frames + 100,
                    },
                    "stages": {
                        "scene": {"seconds": scene_seconds, "state": ""},
                        "actor": {"seconds": actor_seconds, "state": ""},
                        "frame_stream": {"seconds": 0.5, "state": ""},
                    },
                }
            },
        }

    def test_aggregates_single_manifest(self):
        result = aggregate_latency_runs(
            [self._sample_manifest(2.0, 8, 1.5, 3)],
            wall_seconds=[3.5],
            peak_rss_samples=[100000],
        )
        self.assertEqual(result["processed_frames"], 8)
        self.assertEqual(result["record_counts"], {"actor": 3, "scene": 8})
        self.assertIn("scene", result["stages"])
        self.assertAlmostEqual(result["stages"]["scene"]["mean_seconds"], 2.0)
        self.assertAlmostEqual(result["stages"]["scene"]["rate_per_second"], 4.0)
        self.assertAlmostEqual(result["stages"]["actor"]["mean_seconds"], 1.5)
        self.assertAlmostEqual(result["summary"]["wall_seconds"]["mean_seconds"], 3.5)

    def test_aggregates_multiple_manifests(self):
        m1 = self._sample_manifest(2.0, 8, 1.5, 3)
        m2 = self._sample_manifest(2.5, 10, 2.0, 4)
        result = aggregate_latency_runs(
            [m1, m2],
            wall_seconds=[3.5, 4.5],
            peak_rss_samples=[100000, 120000],
        )
        self.assertEqual(result["processed_frames"], 18)
        self.assertAlmostEqual(result["stages"]["scene"]["mean_seconds"], 2.25)
        self.assertAlmostEqual(result["stages"]["scene"]["min_seconds"], 2.0)
        self.assertAlmostEqual(result["stages"]["scene"]["max_seconds"], 2.5)
        self.assertEqual(len(result["per_video"]), 2)
        self.assertAlmostEqual(
            result["summary"]["wall_seconds"]["mean_seconds"],
            4.0,
        )

    def test_skips_failed_videos(self):
        manifest = {
            "processed_frames": 0,
            "record_counts": {},
            "git": {},
            "environment": {},
            "config_fingerprint": "fp",
            "completed_at": "",
            "videos": {
                "vid-1": {
                    "state": "failed",
                    "summary": {},
                    "stages": {},
                }
            },
        }
        result = aggregate_latency_runs(
            [manifest],
            wall_seconds=[1.0],
            peak_rss_samples=[None],
        )
        self.assertEqual(result["processed_frames"], 0)
        self.assertEqual(result["stages"], {})


class CompareBaselineTests(unittest.TestCase):
    def test_no_baseline_stages_returns_empty(self):
        report = {"stages": {"scene": {"mean_seconds": 2.0, "runs": 1}}}
        baseline = {"stages": {}}
        result = compare_baseline(report, baseline, tolerance=0.1)
        self.assertEqual(result["stages"], {})
        self.assertEqual(result["regressions"], [])
        self.assertEqual(result["verdict"], "pass")

    def test_regression_detected(self):
        report = {"stages": {"scene": {"mean_seconds": 3.0, "runs": 1}}}
        baseline = {"stages": {"scene": {"mean_seconds": 2.0, "runs": 1}}}
        result = compare_baseline(report, baseline, tolerance=0.1)
        self.assertIn("scene", result["stages"])
        self.assertAlmostEqual(
            result["stages"]["scene"]["delta_ratio"], 0.5
        )
        self.assertTrue(result["stages"]["scene"]["regressed"])
        self.assertEqual(result["regressions"], ["scene"])
        self.assertEqual(result["verdict"], "fail")

    def test_improvement_not_regression(self):
        report = {"stages": {"scene": {"mean_seconds": 1.5, "runs": 1}}}
        baseline = {"stages": {"scene": {"mean_seconds": 2.0, "runs": 1}}}
        result = compare_baseline(report, baseline, tolerance=0.1)
        self.assertFalse(result["stages"]["scene"]["regressed"])
        self.assertEqual(result["regressions"], [])
        self.assertEqual(result["verdict"], "pass")


class CorpusSpecTests(unittest.TestCase):
    def test_public_record_roundtrip(self):
        spec = SyntheticCorpusSpec(
            videos=2, duration_seconds=8.0, fps=24,
            width=320, height=180, audio_mode="none", seed=42,
        )
        record = spec.public_record()
        self.assertEqual(record["videos"], 2)
        self.assertEqual(record["duration_seconds"], 8.0)
        self.assertEqual(record["audio_mode"], "none")

    def test_flite_mode_recorded(self):
        spec = SyntheticCorpusSpec(
            videos=1, duration_seconds=5.0, fps=30,
            width=640, height=480, audio_mode="flite", seed=7,
        )
        self.assertEqual(spec.public_record()["audio_mode"], "flite")
