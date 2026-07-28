import unittest
from pathlib import Path

from vidxp.capabilities.registry import create_capability_registry


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_capability_extras_read_capability_owned_requirements(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        storage = "src/vidxp/requirements/storage.txt"
        all_block = pyproject.split("all = { file = [", 1)[1].split(
            "] }",
            1,
        )[0]

        for capability in create_capability_registry().definitions.values():
            extra_block = pyproject.split(
                f"{capability.extra} = {{ file = [",
                1,
            )[1].split("] }", 1)[0]
            requirements = (
                ROOT
                / "src"
                / "vidxp"
                / "capabilities"
                / capability.name
                / "requirements.txt"
            )
            relative = requirements.relative_to(ROOT).as_posix()
            self.assertTrue(requirements.is_file())
            self.assertIn(f'"{storage}"', extra_block)
            self.assertIn(f'"{relative}"', extra_block)
            self.assertIn(f'"{relative}"', all_block)

        self.assertIn(f'storage = {{ file = ["{storage}"] }}', pyproject)
        self.assertEqual(
            sum(
                line.strip().startswith("chromadb")
                for path in (ROOT / "src" / "vidxp").rglob("*.txt")
                for line in path.read_text(encoding="utf-8").splitlines()
            ),
            1,
        )
        self.assertNotIn("benchmarks/requirements.txt", all_block)
        self.assertNotIn("requirements/frontend.txt", all_block)

    def test_base_dependencies_exclude_capability_runtimes(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        base_dependencies = pyproject.split("dependencies = [", 1)[1].split(
            "]",
            1,
        )[0]

        for distribution in (
            "chromadb",
            "faster-whisper",
            "numpy",
            "opencv-python-headless",
            "sentence-transformers",
            "torch",
            "transformers",
            "pooch",
            "streamlit",
            "srt",
        ):
            self.assertNotIn(distribution, base_dependencies)


if __name__ == "__main__":
    unittest.main()
