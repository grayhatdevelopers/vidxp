import unittest
from pathlib import Path
import tomllib

from packaging.requirements import Requirement

from vidxp.capabilities.registry import create_capability_registry


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_cpu_lock_uses_explicit_pytorch_index_without_cuda_packages(self):
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("https://download.pytorch.org/whl/cpu", lock)
        self.assertNotIn('name = "nvidia-', lock)
        self.assertIn('explicit = true', project)
        self.assertIn("sys_platform == 'linux'", project)
        self.assertIn("sys_platform == 'win32'", project)

    def test_publishable_metadata_contains_no_direct_url_requirements(self):
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        requirements = list(project["dependencies"])
        requirements.extend(
            line.strip()
            for path in (ROOT / "src" / "vidxp").rglob("requirements.txt")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        for value in requirements:
            self.assertIsNone(Requirement(value).url, value)

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
        chroma_contracts = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "src" / "vidxp").rglob("*.txt")
            if any(
                line.strip().startswith("chromadb")
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        }
        self.assertEqual(
            chroma_contracts,
            {
                "src/vidxp/requirements/storage.txt",
                "src/vidxp/requirements/server-storage.txt",
            },
        )
        self.assertIn(
            "chromadb-client",
            (
                ROOT / "src" / "vidxp" / "requirements" / "server-storage.txt"
            ).read_text(encoding="utf-8"),
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

    def test_install_profiles_keep_server_free_of_ml_dependencies(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("local-worker = { file = [", pyproject)
        self.assertIn("mcp = { file = [", pyproject)
        self.assertIn("server = { file = [", pyproject)
        self.assertIn(
            '"src/vidxp/requirements/mcp.txt"',
            pyproject,
        )
        server = (
            ROOT / "src" / "vidxp" / "requirements" / "server.txt"
        ).read_text(encoding="utf-8")
        for distribution in (
            "chromadb",
            "faster-whisper",
            "opencv-python-headless",
            "sentence-transformers",
            "torch",
            "transformers",
        ):
            self.assertNotIn(distribution, server)
        for requirement in (
            "asgi-correlation-id>=5.0.1,<6",
            "fastapi>=0.140.13,<0.141",
            "pyjwt[crypto]>=2.13,<3",
            "python-multipart>=0.0.32,<0.1",
            "uvicorn[standard]>=0.51,<0.52",
        ):
            self.assertIn(requirement, server)
        self.assertNotIn("fastapi-mcp", server)
        self.assertNotIn("\nmcp", server)
        self.assertNotIn("httpx2", server)
        mcp_requirements = (
            ROOT / "src" / "vidxp" / "requirements" / "mcp.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("mcp>=2.0,<3", mcp_requirements)
        self.assertNotIn("fastmcp", mcp_requirements)
        self.assertNotIn("fastapi-mcp", mcp_requirements)
        slm_requirements = (
            ROOT / "src" / "vidxp" / "requirements" / "slm.txt"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "pydantic-ai-slim[openai]>=2.13,<3",
            slm_requirements,
        )
        self.assertNotIn("pydantic-ai", server)
        test_requirements = (
            ROOT / "src" / "vidxp" / "requirements" / "test.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("httpx>=0.28.1,<0.29", test_requirements)
        self.assertNotIn("httpx2", test_requirements)

    def test_optional_ollama_profile_never_pulls_a_model_implicitly(self):
        compose = (ROOT / "compose.coolify.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "ollama/ollama:0.32.5@sha256:"
            "4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131",
            compose,
        )
        self.assertIn('profiles: ["slm"]', compose)
        self.assertIn(
            "VIDXP_SLM_BASE_URL: ${VIDXP_SLM_BASE_URL:-}",
            compose,
        )
        self.assertIn(
            "VIDXP_SLM_MODEL: ${VIDXP_SLM_MODEL:-}",
            compose,
        )
        self.assertNotIn("ollama pull", compose.lower())


if __name__ == "__main__":
    unittest.main()
