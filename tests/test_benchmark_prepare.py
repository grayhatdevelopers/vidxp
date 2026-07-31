import hashlib
import json
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.benchmarks.prepare import (
    DIDEMO_KNOWN_REPLACEMENT_VIDEO,
    DIDEMO_KNOWN_REPLACEMENT_URL,
    PreparationPlan,
    PreparationResource,
    _didemo_aws_url,
    _extract_hirest_asr,
    execute_preparation,
    plan_didemo,
    plan_hirest,
)
from vidxp.composition import LocalApplicationContext
from vidxp.repositories import RepositoryConfig, RepositoryRegistry


def _annotation(video: str, annotation_id: int = 1) -> dict:
    return {
        "annotation_id": annotation_id,
        "description": "a visible action",
        "num_segments": 6,
        "times": [[0, 0]],
        "video": video,
    }


def _resolve_sizes(resources):
    return tuple(
        replace(resource, size_bytes=resource.size_bytes or 123)
        for resource in resources
    )


class BenchmarkPreparationTests(unittest.TestCase):
    def test_didemo_aws_url_uses_official_yfcc_mapping(self):
        url = _didemo_aws_url(
            "owner_4253489686_source.m4v",
            {"4253489686": "abcdef012345"},
        )

        self.assertEqual(
            url,
            "https://multimedia-commons.s3-us-west-2.amazonaws.com/"
            "data/videos/mp4/abc/def/abcdef012345.mp4",
        )

    def test_didemo_plan_is_subset_aware_and_copy_paste_ready(self):
        first = "owner_4253489686_source.m4v"
        second = "owner_7071386095_source.mpg"
        annotations = json.dumps(
            [_annotation(first), _annotation(second, 2)]
        ).encode()

        def fetched(url, _checksum, _name):
            if url.endswith("test_data.json"):
                return annotations
            if url.endswith("yfcc100m_hash.txt"):
                return (
                    b"4253489686\tabcdef012345\n"
                    b"7071386095\t123456abcdef\n"
                )
            return b"evaluator"

        with (
            TemporaryDirectory() as directory,
            patch(
                "vidxp.benchmarks.prepare.inspect_media_runtime",
                return_value=Mock(ready=True, errors=()),
            ),
            patch(
                "vidxp.benchmarks.prepare._fetch_verified",
                side_effect=fetched,
            ),
            patch(
                "vidxp.benchmarks.prepare._with_remote_sizes",
                side_effect=_resolve_sizes,
            ),
            patch(
                "vidxp.benchmarks.prepare._valid_media",
                return_value=False,
            ),
            patch(
                "vidxp.benchmarks.prepare._valid_artifact",
                return_value=False,
            ),
        ):
            plan = plan_didemo(
                root=directory,
                split="test",
                annotation_indices=[1],
                ffprobe="ffprobe",
                ffmpeg="ffmpeg",
            )

        self.assertEqual(plan.selected_count, 1)
        self.assertEqual(plan.selected_video_names, (second,))
        media = [
            resource
            for resource in plan.resources
            if resource.kind == "media"
        ]
        self.assertEqual(len(media), 1)
        self.assertIn("123/456/123456abcdef.mp4", media[0].url)
        self.assertIn("--annotation-indices 1", plan.command)
        self.assertIn("--media-directory", plan.command)

    def test_known_didemo_replacement_is_explicit_and_manifested(self):
        annotations = json.dumps(
            [_annotation(DIDEMO_KNOWN_REPLACEMENT_VIDEO)]
        ).encode()

        def fetched(url, _checksum, _name):
            if url.endswith("test_data.json"):
                return annotations
            if url.endswith("yfcc100m_hash.txt"):
                return b"13482799053\tdeb3d8c8aba7077b378d16b236b0a5\n"
            return b"evaluator"

        with (
            TemporaryDirectory() as directory,
            patch(
                "vidxp.benchmarks.prepare.inspect_media_runtime",
                return_value=Mock(ready=True, errors=()),
            ),
            patch(
                "vidxp.benchmarks.prepare._fetch_verified",
                side_effect=fetched,
            ),
            patch(
                "vidxp.benchmarks.prepare._with_remote_sizes",
                side_effect=_resolve_sizes,
            ),
            patch(
                "vidxp.benchmarks.prepare._valid_media",
                return_value=False,
            ),
            patch(
                "vidxp.benchmarks.prepare._valid_artifact",
                return_value=False,
            ),
        ):
            plan = plan_didemo(
                root=directory,
                split="test",
                annotation_indices=[0],
                ffprobe="ffprobe",
                ffmpeg="ffmpeg",
            )

        replacement = next(
            resource
            for resource in plan.resources
            if resource.replacement_for is not None
        )
        self.assertEqual(replacement.url, DIDEMO_KNOWN_REPLACEMENT_URL)
        self.assertEqual(
            replacement.replacement_for,
            DIDEMO_KNOWN_REPLACEMENT_VIDEO,
        )
        self.assertIsNotNone(plan.media_overrides_path)
        self.assertIn("--media-overrides", plan.command)

    def test_hirest_plan_prepares_released_asr_without_video_downloads(self):
        ground_truth = {
            "Make tea": {
                "tea.mp4": {
                    "clip": True,
                    "bounds": [1, 2],
                    "v_duration": 3,
                },
                "other.mp4": {
                    "clip": True,
                    "bounds": [0, 1],
                    "v_duration": 2,
                },
            }
        }

        def fetched(url, _checksum, _name):
            if "all_data_val.json" in url:
                return json.dumps(ground_truth).encode()
            if url.endswith("categories.json"):
                return b"{}"
            return b"evaluator"

        with (
            TemporaryDirectory() as directory,
            patch(
                "vidxp.benchmarks.prepare._fetch_verified",
                side_effect=fetched,
            ),
            patch(
                "vidxp.benchmarks.prepare._with_remote_sizes",
                side_effect=_resolve_sizes,
            ),
            patch(
                "vidxp.benchmarks.prepare._valid_artifact",
                return_value=False,
            ),
        ):
            plan = plan_hirest(
                root=directory,
                split="validation",
                pairs=[("Make tea", "tea.mp4")],
            )

        self.assertEqual(plan.selected_count, 1)
        self.assertEqual(plan.selected_video_names, ("tea.mp4",))
        self.assertFalse(
            any(resource.kind == "media" for resource in plan.resources)
        )
        self.assertGreater(plan.additional_bytes, 17_000_000)
        self.assertIn("--asr-archive", plan.command)
        self.assertIn("--asr-directory", plan.command)
        self.assertIn("--pairs", plan.command)

    def test_hirest_extraction_keeps_only_selected_transcripts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "ASR.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ASR/first.srt", "first")
                bundle.writestr("ASR/second.srt", "second")
            destination = root / "asr"

            extracted = _extract_hirest_asr(
                archive,
                destination,
                ["first.mp4"],
            )

            self.assertEqual(extracted, 1)
            self.assertEqual(
                (destination / "first.srt").read_text(),
                "first",
            )
            self.assertFalse((destination / "second.srt").exists())

    def test_execution_writes_verified_artifacts_and_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"official"
            destination = root / "annotations.json"
            plan = PreparationPlan(
                benchmark="didemo",
                split="test",
                root=root,
                resources=(
                    PreparationResource(
                        name="annotations",
                        url="https://example.invalid/annotations",
                        destination=destination,
                        size_bytes=len(content),
                        kind="artifact",
                        content=content,
                        expected_sha256=hashlib.sha256(
                            content
                        ).hexdigest(),
                    ),
                ),
                selected_count=1,
                selected_video_names=("video.mp4",),
                command="vidxp benchmark didemo ...",
                manifest_path=root / "preparation-manifest.json",
            )

            result = execute_preparation(plan)

            self.assertEqual(result["status"], "ready")
            self.assertEqual(destination.read_bytes(), content)
            self.assertTrue(plan.manifest_path.is_file())

    def test_complete_partial_download_is_verified_without_network_retry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"resume"
            destination = root / "ASR.zip"
            partial = destination.with_name(destination.name + ".part")
            partial.write_bytes(content)
            plan = PreparationPlan(
                benchmark="didemo",
                split="test",
                root=root,
                resources=(
                    PreparationResource(
                        name="archive",
                        url="https://example.invalid/ASR.zip",
                        destination=destination,
                        size_bytes=len(content),
                        kind="artifact",
                        expected_sha256=hashlib.sha256(
                            content
                        ).hexdigest(),
                    ),
                ),
                selected_count=1,
                selected_video_names=(),
                command="vidxp benchmark didemo ...",
                manifest_path=root / "preparation-manifest.json",
            )

            with patch(
                "vidxp.benchmarks.prepare.urllib.request.urlopen"
            ) as urlopen:
                result = execute_preparation(plan)

            urlopen.assert_not_called()
            self.assertEqual(result["status"], "ready")
            self.assertEqual(destination.read_bytes(), content)


class BenchmarkPreparationCliTests(unittest.TestCase):
    def test_prepare_command_prints_generated_run_command(self):
        runner = CliRunner()
        service = Mock()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "repositories.json"
            plan = PreparationPlan(
                benchmark="didemo",
                split="test",
                root=root / "prepared",
                resources=(),
                selected_count=1,
                selected_video_names=("video.mp4",),
                command="vidxp benchmark didemo --run-id ready",
                manifest_path=root / "prepared" / "manifest.json",
            )
            with (
                patch.object(
                    cli,
                    "create_local_application",
                    return_value=LocalApplicationContext(
                        application=service,
                        jobs=Mock(),
                        repositories=RepositoryRegistry(config),
                        repository=RepositoryConfig(
                            "default",
                            root / "repository",
                            device="cpu",
                            configured=False,
                        ),
                    ),
                ),
                patch(
                    "vidxp.benchmarks.cli.plan_didemo",
                    return_value=plan,
                ),
                patch(
                    "vidxp.benchmarks.cli.execute_preparation",
                    return_value={"status": "ready"},
                ),
            ):
                response = runner.invoke(
                    cli.app,
                    [
                        "--config",
                        str(config),
                        "benchmark",
                        "prepare",
                        "didemo",
                        "--annotation-indices",
                        "0",
                        "--yes",
                    ],
                )

        self.assertEqual(response.exit_code, 0, response.output)
        self.assertIn("Benchmark inputs are ready.", response.output)
        self.assertIn(plan.command, response.output)


if __name__ == "__main__":
    unittest.main()
