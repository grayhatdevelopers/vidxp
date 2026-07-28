import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from vidxp import cli
from vidxp.composition import LocalApplicationContext
from vidxp.repositories import RepositoryConfig, RepositoryRegistry


class BenchmarkCliTests(unittest.TestCase):
    def test_benchmark_app_is_not_loaded_without_its_extra(self):
        with patch.object(cli, "requirements_available", return_value=False):
            self.assertIsNone(cli._load_benchmark_app())

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
            annotations.write_text("[]", encoding="utf-8")
            evaluator.write_text("", encoding="utf-8")
            media.mkdir()
            with (
                patch.object(
                    cli,
                    "create_local_application",
                    return_value=LocalApplicationContext(
                        application=service,
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
                    ],
                )

        self.assertEqual(response.exit_code, 0, response.output)
        self.assertEqual(json.loads(response.stdout)["rank_at_1"], 0.5)
        self.assertEqual(run.call_args.kwargs["device"], "cuda")


if __name__ == "__main__":
    unittest.main()
