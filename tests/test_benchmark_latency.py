from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vidxp.benchmarks.common import benchmark_media_id
from vidxp.benchmarks.latency import (
    RealCorpusSpec,
    SyntheticCorpusSpec,
    _load_corpus_overrides,
    _validate_baseline_compatibility,
    aggregate_latency_runs,
    build_clip_command,
    build_latency_sources,
    build_real_corpus_sources,
    compare_baseline,
    discover_real_corpus,
    resolve_corpus_directory,
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
        self.assertEqual(record["kind"], "synthetic")
        self.assertEqual(record["videos"], 2)
        self.assertEqual(record["duration_seconds"], 8.0)
        self.assertEqual(record["audio_mode"], "none")

    def test_flite_mode_recorded(self):
        spec = SyntheticCorpusSpec(
            videos=1, duration_seconds=5.0, fps=30,
            width=640, height=480, audio_mode="flite", seed=7,
        )
        self.assertEqual(spec.public_record()["audio_mode"], "flite")


class RealCorpusResolutionTests(unittest.TestCase):
    def test_none_selects_synthetic(self):
        directory, name = resolve_corpus_directory(None, data_dir=Path("/x"))
        self.assertIsNone(directory)
        self.assertIsNone(name)

    def test_directory_path_is_used_as_is(self):
        path = Path("/tmp/media")
        directory, name = resolve_corpus_directory(path)
        self.assertEqual(directory, path)
        self.assertIsNone(name)

    def test_named_corpus_resolves_to_prepared_media(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            directory, name = resolve_corpus_directory(
                "didemo",
                data_dir=data_dir,
            )
        self.assertEqual(name, "didemo")
        self.assertEqual(
            directory,
            data_dir / "benchmarks" / "didemo" / "media",
        )

    def test_unknown_corpus_rejected(self):
        with self.assertRaises(ValueError):
            resolve_corpus_directory("kinetics", data_dir=Path("/x"))

    def test_named_corpus_requires_data_dir(self):
        with self.assertRaises(ValueError):
            resolve_corpus_directory("didemo")


class RealCorpusSpecTests(unittest.TestCase):
    def test_public_record_marks_real(self):
        spec = RealCorpusSpec(
            name="didemo",
            source="/tmp/media",
            video_count=3,
            total_bytes=1500,
            min_duration_seconds=4.0,
            max_duration_seconds=9.0,
            containers=(".mp4", ".webm"),
            media_overrides=True,
        )
        record = spec.public_record()
        self.assertEqual(record["kind"], "real")
        self.assertEqual(record["name"], "didemo")
        self.assertEqual(record["video_count"], 3)
        self.assertEqual(record["total_bytes"], 1500)
        self.assertEqual(record["containers"], [".mp4", ".webm"])
        self.assertTrue(record["media_overrides"])


class RealCorpusSourcesTests(unittest.TestCase):
    def test_sources_transcribe_and_derive_ids(self):
        clips = [
            {
                "video_name": "a.mp4",
                "path": Path("/media/a.mp4"),
                "duration_seconds": 5.0,
                "size_bytes": 100,
            },
            {
                "video_name": "b.webm",
                "path": Path("/media/b.webm"),
                "duration_seconds": 6.0,
                "size_bytes": 200,
            },
        ]
        sources = build_real_corpus_sources(clips)
        self.assertEqual(len(sources), 2)
        self.assertIsNone(sources[0].transcript)
        self.assertEqual(sources[0].source_name, "a.mp4")
        self.assertEqual(
            sources[0].video_id,
            benchmark_media_id("latency", "a.mp4"),
        )
        self.assertEqual(sources[1].source_name, "b.webm")


class CorpusOverrideTests(unittest.TestCase):
    def test_no_override_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            overrides = _load_corpus_overrides(Path(temporary))
        self.assertEqual(overrides, {})

    def test_resolves_relative_replacements_beside_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "media-overrides.json").write_text(
                '{"12090392@N02_13482799053_87ef417396.mov": '
                '"replacements/common-starlings.webm"}',
                encoding="utf-8",
            )
            overrides = _load_corpus_overrides(root)
            expected = (root / "replacements" / "common-starlings.webm").resolve()
            self.assertEqual(
                overrides["12090392@N02_13482799053_87ef417396.mov"],
                expected,
            )

    def test_rejects_malformed_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "media-overrides.json").write_text(
                "not json", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                _load_corpus_overrides(root)


class RealCorpusDiscoveryTests(unittest.TestCase):
    def test_missing_media_directory_rejected(self):
        with self.assertRaises(ValueError):
            discover_real_corpus(
                Path("/missing/media"),
                corpus_root=Path("/missing"),
                ffprobe="ffprobe",
            )

    def test_empty_media_directory_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "media"
            media.mkdir()
            with self.assertRaises(ValueError):
                discover_real_corpus(
                    media,
                    corpus_root=root,
                    ffprobe="ffprobe",
                )


class BaselineCompatibilityTests(unittest.TestCase):
    @staticmethod
    def _synthetic_corpus(seed):
        return {
            "kind": "synthetic",
            "videos": 2,
            "duration_seconds": 8.0,
            "fps": 24,
            "width": 320,
            "height": 180,
            "audio_mode": "none",
            "seed": seed,
        }

    @staticmethod
    def _real_corpus(**overrides):
        corpus = {
            "kind": "real",
            "name": "didemo",
            "source": "/tmp/media",
            "video_count": 2,
            "total_bytes": 1000,
            "min_duration_seconds": 5.0,
            "max_duration_seconds": 8.0,
            "containers": [".mp4"],
            "media_overrides": False,
        }
        corpus.update(overrides)
        return corpus

    @staticmethod
    def _report(corpus):
        return {
            "corpus": corpus,
            "input_mode": "transcript",
            "device": "cpu",
            "modalities": ["scene"],
        }

    def test_matching_real_corpus_passes(self):
        report = self._report(self._real_corpus())
        baseline = self._report(self._real_corpus())
        _validate_baseline_compatibility(report, baseline)

    def test_real_versus_synthetic_mismatch_rejected(self):
        report = self._report(self._real_corpus())
        baseline = self._report(self._synthetic_corpus(2026))
        with self.assertRaises(ValueError):
            _validate_baseline_compatibility(report, baseline)

    def test_real_corpus_scale_change_rejected(self):
        report = self._report(self._real_corpus(video_count=2))
        baseline = self._report(self._real_corpus(video_count=3))
        with self.assertRaises(ValueError):
            _validate_baseline_compatibility(report, baseline)

    def test_synthetic_seed_change_rejected(self):
        report = self._report(self._synthetic_corpus(2026))
        baseline = self._report(self._synthetic_corpus(7))
        with self.assertRaises(ValueError):
            _validate_baseline_compatibility(report, baseline)
