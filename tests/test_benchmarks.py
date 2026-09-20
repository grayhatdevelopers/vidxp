import hashlib
import json
import unittest
from uuid import UUID
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vidxp.benchmarks.common import (
    benchmark_generation_id,
    benchmark_media_id,
    verify_artifact,
)
from vidxp.benchmarks.didemo import (
    DIDEMO_MOMENTS,
    _result_classification as didemo_result_classification,
    rank_moments,
    select_annotations,
    validate_predictions as validate_didemo_predictions,
)
from vidxp.benchmarks.hirest import (
    _evaluate_predictions,
    _metrics_from_output,
    moment_pairs,
    parse_srt,
    rank_interval,
    run_hirest,
    select_ground_truth,
    validate_predictions as validate_hirest_predictions,
)
from vidxp.capabilities.schemas import SearchHit


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"


def scene_hit(chunk, score):
    return SearchHit(
        rank=chunk + 1,
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        generation_id=GENERATION_ID,
        start=chunk * 5.0,
        end=chunk * 5.0 + 1.0,
        score=score,
        raw_distance=-score,
        modality="scene",
        source_id=f"scene-{chunk}",
        metadata={"timestamp": chunk * 5.0},
    )


def timed_hit(start, end, score, rank=1):
    return SearchHit(
        rank=rank,
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        generation_id=GENERATION_ID,
        start=start,
        end=end,
        score=score,
        raw_distance=-score,
        modality="speech",
        source_id=f"hit-{rank}",
        metadata={},
    )


class BenchmarkCommonTests(unittest.TestCase):
    def test_generation_identity_is_stable_and_run_scoped(self):
        first = benchmark_generation_id("hirest", "validation", "run-1")

        self.assertEqual(
            first,
            benchmark_generation_id("hirest", "validation", "run-1"),
        )
        self.assertEqual(len(first), 32)
        self.assertEqual(UUID(hex=first).version, 4)
        self.assertNotEqual(
            first,
            benchmark_generation_id("hirest", "validation", "run-2"),
        )
        self.assertNotEqual(
            first,
            benchmark_generation_id("didemo", "validation", "run-1"),
        )

    def test_official_video_key_maps_to_stable_media_id(self):
        first = benchmark_media_id("hirest", "video.mp4")

        self.assertEqual(first, benchmark_media_id("hirest", "video.mp4"))
        self.assertEqual(UUID(hex=first).version, 4)
        self.assertNotEqual(
            first,
            benchmark_media_id("didemo", "video.mp4"),
        )

    def test_artifact_checksum_is_verified(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text("official", encoding="utf-8")
            checksum = hashlib.sha256(b"official").hexdigest()

            artifact = verify_artifact(
                path,
                name="test artifact",
                expected_sha256=checksum,
                source="https://example.invalid/artifact",
                revision="abc123",
            )

            self.assertEqual(artifact["sha256"], checksum)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verify_artifact(
                    path,
                    name="test artifact",
                    expected_sha256="0" * 64,
                    source="https://example.invalid/artifact",
                    revision="abc123",
                )

    def test_artifact_accepts_declared_line_ending_variants(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "evaluator.py"
            path.write_bytes(b"print('official')\r\n")
            raw_sha = hashlib.sha256(b"print('official')\n").hexdigest()
            checkout_sha = hashlib.sha256(path.read_bytes()).hexdigest()

            artifact = verify_artifact(
                path,
                name="test evaluator",
                expected_sha256=(raw_sha, checkout_sha),
                source="https://example.invalid/evaluator.py",
                revision="abc123",
            )

        self.assertEqual(artifact["sha256"], checkout_sha)


class DiDeMoAdapterTests(unittest.TestCase):
    def test_media_override_is_disclosed_in_result_classification(self):
        self.assertEqual(
            didemo_result_classification(
                split="test",
                full_split=True,
                has_media_overrides=True,
            ),
            (
                "official_full_test_result_with_documented_"
                "media_substitution"
            ),
        )

    def test_official_candidate_order_is_preserved_for_ties(self):
        ranking = rank_moments(
            [scene_hit(chunk, 1.0) for chunk in range(6)],
            num_segments=6,
        )

        self.assertEqual(tuple(ranking), DIDEMO_MOMENTS)

    def test_five_segment_video_ranks_unavailable_moments_last(self):
        ranking = rank_moments(
            [scene_hit(chunk, float(chunk)) for chunk in range(5)],
            num_segments=5,
        )

        self.assertEqual(len(ranking), 21)
        self.assertTrue(all(moment[1] < 5 for moment in ranking[:15]))
        self.assertTrue(all(moment[1] == 5 for moment in ranking[15:]))

    def test_max_pooling_preserves_a_brief_relevant_frame(self):
        hits = [
            scene_hit(chunk, 0.0)
            for chunk in range(6)
        ]
        hits.extend(
            [
                timed_hit(15.5, 16.5, 0.8, rank=7),
                timed_hit(20.5, 21.5, 1.0, rank=8),
                timed_hit(21.5, 22.5, -1.0, rank=9),
            ]
        )
        hits[-3].metadata["timestamp"] = 15.5
        hits[-2].metadata["timestamp"] = 20.5
        hits[-1].metadata["timestamp"] = 21.5

        self.assertEqual(
            rank_moments(
                hits,
                num_segments=6,
                chunk_pooling="max",
            )[0],
            (4, 4),
        )
        self.assertEqual(
            rank_moments(
                hits,
                num_segments=6,
                chunk_pooling="mean",
            )[0],
            (3, 3),
        )

    def test_prediction_validation_rejects_missing_candidates(self):
        annotation = {"num_segments": 6}
        with self.assertRaisesRegex(ValueError, "all 21"):
            validate_didemo_predictions(
                [[list(moment) for moment in DIDEMO_MOMENTS[:-1]]],
                [annotation],
            )

    def test_subset_indices_remain_in_declared_order(self):
        annotations = [
            {"annotation_id": 1},
            {"annotation_id": 2},
            {"annotation_id": 3},
        ]
        selected = select_annotations(annotations, [2, 0])

        self.assertEqual(
            [item["annotation_id"] for item in selected],
            [3, 1],
        )


class HiRESTAdapterTests(unittest.TestCase):
    def test_held_out_test_writes_unscored_submission(self):
        ground_truth = {
            "prompt": {
                "video.mp4": {
                    "clip": True,
                    "bounds": [0, 1],
                    "v_duration": 10.0,
                }
            }
        }
        predictions = {
            "prompt": {
                "video.mp4": {
                    "bounds": [1.0, 9.0],
                }
            }
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            asr_directory = root / "asr"
            asr_directory.mkdir()
            (asr_directory / "video.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\ntext\n\n",
                encoding="utf-8",
            )
            with (
                patch(
                    "vidxp.benchmarks.hirest._verified_artifacts",
                    return_value=[],
                ),
                patch(
                    "vidxp.benchmarks.hirest.load_ground_truth",
                    return_value=ground_truth,
                ),
                patch(
                    "vidxp.benchmarks.hirest.run_index",
                    return_value={},
                ) as run_index,
                patch(
                    "vidxp.benchmarks.hirest._generate_predictions",
                    return_value=predictions,
                ) as generate_predictions,
                patch(
                    "vidxp.benchmarks.hirest._evaluate_predictions"
                ) as evaluate_predictions,
            ):
                result = run_hirest(
                    ground_truth_path=root / "test.json",
                    categories_path=root / "categories.json",
                    evaluator_path=root / "evaluate.py",
                    asr_archive_path=root / "ASR.zip",
                    asr_directory=asr_directory,
                    run_id="test-submission",
                    output_root=root,
                    split="test",
                )

            run_directory = root / "hirest" / "test-submission"
            self.assertFalse(result["scored"])
            self.assertEqual(result["prediction_count"], 1)
            self.assertTrue(
                (run_directory / "submission.summary.json").is_file()
            )
            self.assertFalse((run_directory / "metrics.json").exists())
            evaluate_predictions.assert_not_called()
            index_config = run_index.call_args.args[1]
            prediction_config = (
                generate_predictions.call_args.kwargs["config"]
            )
            self.assertEqual(
                index_config.generation_id,
                benchmark_generation_id(
                    "hirest",
                    "test",
                    "test-submission",
                ),
            )
            self.assertEqual(
                prediction_config.generation_id,
                index_config.generation_id,
            )

    @patch("vidxp.benchmarks.hirest.run_logged_evaluator")
    def test_official_evaluator_is_forced_to_utf8(
        self,
        run_evaluator,
    ):
        run_evaluator.return_value.stdout = (
            "{'total_videos': 1, 'R@0.5': 100.0, 'R@0.7': 100.0}\n"
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            evaluator = root / "evaluate.py"
            evaluator.write_text("# official evaluator\n", encoding="utf-8")

            _evaluate_predictions(
                evaluator_path=evaluator,
                ground_truth_path=root / "ground-truth.json",
                predictions_path=root / "predictions.json",
                log_path=root / "evaluator.log",
            )

        environment = run_evaluator.call_args.kwargs["environment"]
        self.assertEqual(environment["PYTHONUTF8"], "1")

    def test_official_numpy_scalar_output_is_normalized(self):
        metrics = _metrics_from_output(
            "{'total_videos': 1, 'R@0.5': np.float64(50.0), "
            "'R@0.7': np.float64(0.0)}\n"
        )

        self.assertEqual(
            metrics,
            {"total_videos": 1, "R@0.5": 50.0, "R@0.7": 0.0},
        )

    def test_srt_parser_preserves_released_timestamps(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.srt"
            path.write_text(
                "1\n00:00:01,000 --> 00:00:02,500\n"
                "First line\ncontinues.\n\n",
                encoding="utf-8",
            )

            segments = parse_srt(path)

            self.assertEqual(
                segments,
                [
                    {
                        "text": "First line continues.",
                        "start": 1.0,
                        "end": 2.5,
                    }
                ],
            )

    def test_only_clip_true_pairs_enter_moment_evaluation(self):
        ground_truth = {
            "query": {
                "clip.mp4": {"clip": True},
                "whole.mp4": {"clip": False},
            }
        }

        self.assertEqual(moment_pairs(ground_truth), [("query", "clip.mp4")])
        subset, pairs = select_ground_truth(
            ground_truth,
            [("query", "clip.mp4")],
        )
        self.assertEqual(pairs, [("query", "clip.mp4")])
        self.assertEqual(set(subset["query"]), {"clip.mp4"})

    def test_official_prediction_shape_is_strict(self):
        ground_truth = {
            "query": {"video.mp4": {"clip": True}}
        }
        valid = {
            "query": {"video.mp4": {"bounds": [1.0, 2.0]}}
        }

        validate_hirest_predictions(valid, ground_truth)
        invalid = json.loads(json.dumps(valid))
        invalid["query"]["video.mp4"]["score"] = 1.0
        with self.assertRaisesRegex(ValueError, "only bounds"):
            validate_hirest_predictions(invalid, ground_truth)

    def test_temporal_ranking_uses_all_dialogue_hits(self):
        hits = [
            timed_hit(0.0, 1.0, 0.9, rank=1),
            timed_hit(1.0, 2.0, 0.8, rank=2),
            timed_hit(5.0, 6.0, 1.0, rank=3),
        ]

        self.assertEqual(
            rank_interval(hits, duration=6.0, window_fraction=1 / 3),
            (0.0, 2.0),
        )
        with self.assertRaisesRegex(ValueError, "between zero and one"):
            rank_interval(hits, duration=6.0, window_fraction=1.0)


if __name__ == "__main__":
    unittest.main()
