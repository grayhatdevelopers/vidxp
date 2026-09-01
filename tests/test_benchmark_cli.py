import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from packaging.requirements import Requirement
import typer
from typer.testing import CliRunner

from vidxp import cli
from vidxp.benchmarks import cli as benchmark_cli
from vidxp.composition import LocalApplicationContext
from vidxp.repositories import RepositoryConfig, RepositoryRegistry


class BenchmarkCliTests(unittest.TestCase):
    def test_benchmark_group_is_available_without_srt_parser(self):
        source = Path(__file__).resolve().parents[1] / "src"
        code = """
import importlib.abc
import sys

class RejectSrt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "srt" or fullname.startswith("srt."):
            raise ModuleNotFoundError("blocked optional srt dependency")
        return None

sys.meta_path.insert(0, RejectSrt())
from vidxp import cli
assert cli.benchmark_app is not None
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(source)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_benchmark_uses_global_device_and_json_output(self):
        runner = CliRunner()
        service = Mock()
        service.index_directory = Path("chroma_data")
        service.device = "cuda"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "repositories.json"
            annotations = root / "annotations.json"
            evaluator = root / "evaluator.py"
            media = root / "media"
            replacement = root / "replacement.webm"
            overrides = root / "media-overrides.json"
            annotations.write_text("[]", encoding="utf-8")
            evaluator.write_text("", encoding="utf-8")
            media.mkdir()
            replacement.write_bytes(b"replacement")
            overrides.write_text(
                json.dumps({"broken.mp4": replacement.name}),
                encoding="utf-8",
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
                            Path("chroma_data"),
                            device="cuda",
                            configured=False,
                        ),
                    ),
                ),
                patch(
                    "vidxp.benchmarks.cli.run_didemo",
                    return_value={"rank_at_1": 0.5},
                ) as run,
            ):
                response = runner.invoke(
                    cli.app,
                    [
                        "--config",
                        str(config),
                        "--device",
                        "cuda",
                        "--format",
                        "json",
                        "benchmark",
                        "didemo",
                        "--annotations",
                        str(annotations),
                        "--evaluator",
                        str(evaluator),
                        "--media-directory",
                        str(media),
                        "--run-id",
                        "run-1",
                        "--media-overrides",
                        str(overrides),
                        "--scene-sample-fps",
                        "2",
                    ],
                )

        self.assertEqual(response.exit_code, 0, response.output)
        self.assertEqual(json.loads(response.stdout)["rank_at_1"], 0.5)
        self.assertEqual(run.call_args.kwargs["device"], "cuda")
        self.assertEqual(run.call_args.kwargs["scene_sample_fps"], 2.0)
        self.assertEqual(
            run.call_args.kwargs["media_overrides"],
            {"broken.mp4": replacement.resolve()},
        )

    def test_missing_adapter_dependencies_fail_with_exact_extra_hint(self):
        registry = Mock()
        registry.requirements_for.return_value = (
            Requirement("missing-runtime>=1"),
        )
        failure = {
            "name": "missing-runtime",
            "requirement": "missing-runtime>=1",
            "installed_version": None,
            "ok": False,
            "error": "distribution is not installed",
        }
        with (
            patch.object(
                benchmark_cli,
                "create_capability_registry",
                return_value=registry,
            ),
            patch.object(
                benchmark_cli,
                "inspect_requirement",
                return_value=failure,
            ),
            self.assertRaises(typer.BadParameter) as raised,
        ):
            benchmark_cli._require_benchmark_dependencies("scene")

        self.assertIn(
            'pip install "vidxp[scene]"',
            str(raised.exception),
        )

    def test_hirest_dependency_hint_includes_benchmark_parser(self):
        registry = Mock()
        registry.requirements_for.return_value = ()
        failure = {
            "name": "srt",
            "requirement": "srt<4,>=3.5",
            "installed_version": None,
            "ok": False,
            "error": "distribution is not installed",
        }
        with (
            patch.object(
                benchmark_cli,
                "create_capability_registry",
                return_value=registry,
            ),
            patch.object(
                benchmark_cli,
                "inspect_requirement",
                return_value=failure,
            ),
            self.assertRaises(typer.BadParameter) as raised,
        ):
            benchmark_cli._require_benchmark_dependencies(
                "speech",
                include_benchmark_extra=True,
            )

        self.assertIn(
            'pip install "vidxp[speech,benchmarks]"',
            str(raised.exception),
        )

    def test_invalid_hirest_pair_json_is_reported_as_input_error(self):
        with TemporaryDirectory() as directory:
            pairs = Path(directory) / "pairs.json"
            pairs.write_text("{", encoding="utf-8")

            with self.assertRaises(typer.BadParameter) as raised:
                benchmark_cli._pair_file(pairs)

        self.assertIn("valid readable JSON", str(raised.exception))
        self.assertEqual(raised.exception.param_hint, "--pairs")

    def test_invalid_didemo_media_override_is_reported_as_input_error(self):
        with TemporaryDirectory() as directory:
            overrides = Path(directory) / "media-overrides.json"
            overrides.write_text(
                json.dumps({"broken.mp4": "missing.webm"}),
                encoding="utf-8",
            )

            with self.assertRaises(typer.BadParameter) as raised:
                benchmark_cli._media_override_file(overrides)

        self.assertIn("was not found", str(raised.exception))
        self.assertEqual(raised.exception.param_hint, "--media-overrides")

    def test_hirest_rejects_closed_temporal_fraction_before_dependencies(self):
        runner = CliRunner()
        service = Mock()
        service.index_directory = Path("chroma_data")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "repositories.json"
            required_files = {
                name: root / name
                for name in (
                    "ground-truth.json",
                    "categories.json",
                    "evaluator.py",
                    "asr.zip",
                )
            }
            for path in required_files.values():
                path.write_text("", encoding="utf-8")
            asr_directory = root / "asr"
            asr_directory.mkdir()
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
                            Path("chroma_data"),
                            device="cpu",
                            configured=False,
                        ),
                    ),
                ),
                patch.object(
                    benchmark_cli,
                    "_require_benchmark_dependencies",
                ) as dependencies,
            ):
                response = runner.invoke(
                    cli.app,
                    [
                        "--config",
                        str(config),
                        "benchmark",
                        "hirest",
                        "--ground-truth",
                        str(required_files["ground-truth.json"]),
                        "--categories",
                        str(required_files["categories.json"]),
                        "--evaluator",
                        str(required_files["evaluator.py"]),
                        "--asr-archive",
                        str(required_files["asr.zip"]),
                        "--asr-directory",
                        str(asr_directory),
                        "--run-id",
                        "run-1",
                        "--temporal-window-fraction",
                        "1",
                    ],
                )

        self.assertEqual(response.exit_code, 2, response.output)
        self.assertIn("less than one", response.output)
        dependencies.assert_not_called()


if __name__ == "__main__":
    unittest.main()
