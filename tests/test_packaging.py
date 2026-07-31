import json
import unittest
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.version import Version

from vidxp.capabilities.registry import create_capability_registry


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_canonical_icon_is_packaged_and_desktop_derivatives_are_wired(self):
        icon = ROOT / "docs" / "images" / "logo.png"
        self.assertTrue(icon.is_file())
        self.assertTrue(icon.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

        self.assertIn(
            "include docs/images/logo.png",
            (ROOT / "MANIFEST.in").read_text(encoding="utf-8"),
        )
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            project["project"]["urls"]["Homepage"],
            "https://github.com/grayhatdevelopers/vidxp",
        )
        self.assertIn(
            "./docs/images/logo.png",
            (ROOT / "README.md").read_text(encoding="utf-8"),
        )
        build_hook = (ROOT / "setup.py").read_text(encoding="utf-8")
        self.assertIn('"docs" / "images" / "logo.png"', build_hook)
        self.assertIn('"vidxp" / "assets" / "icon.png"', build_hook)

        package = json.loads(
            (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            package["scripts"]["sync:branding"],
            "node scripts/sync-branding.mjs",
        )
        self.assertEqual(
            package["scripts"]["predesktop:dev"],
            "npm run sync:branding",
        )
        self.assertEqual(
            package["scripts"]["predesktop:build"],
            "npm run icons",
        )
        self.assertEqual(
            package["scripts"]["icons"],
            (
                "npm run sync:branding && "
                "tauri icon ../docs/images/logo.png "
                "--output src-tauri/icons"
            ),
        )
        sync_script = (
            ROOT / "desktop" / "scripts" / "sync-branding.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("../docs/images/logo.png", sync_script)
        self.assertIn("web/icon.png", sync_script)
        self.assertIn(
            'href="icon.png"',
            (ROOT / "desktop" / "web" / "index.html").read_text(
                encoding="utf-8"
            ),
        )

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

    def test_desktop_manifest_matches_published_package_contract(self):
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            project["project"]["scripts"]["vidxp"],
            "vidxp.entrypoint:main",
        )
        manifest = json.loads(
            (ROOT / "desktop" / "runtime-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        package = json.loads(
            (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
        )
        tauri = json.loads(
            (
                ROOT
                / "desktop"
                / "src-tauri"
                / "tauri.conf.json"
            ).read_text(encoding="utf-8")
        )
        cargo = tomllib.loads(
            (
                ROOT / "desktop" / "src-tauri" / "Cargo.toml"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(manifest["package_name"], project["project"]["name"])
        self.assertEqual(
            Version(manifest["package_version"]),
            Version(project["project"]["version"]),
        )
        self.assertEqual(package["version"], manifest["desktop_version"])
        self.assertEqual(tauri["version"], manifest["desktop_version"])
        self.assertFalse(tauri["app"]["windows"][0]["visible"])
        self.assertEqual(
            tauri["bundle"]["windows"]["nsis"]["installerHooks"],
            "nsis-hooks.nsh",
        )
        nsis_hooks = (
            ROOT / "desktop" / "src-tauri" / "nsis-hooks.nsh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            r"$LOCALAPPDATA\Programs\${PRODUCTNAME}",
            nsis_hooks,
        )
        self.assertIn(
            "tray-icon",
            cargo["dependencies"]["tauri"]["features"],
        )
        self.assertEqual(
            Version(manifest["desktop_version"]),
            Version(manifest["package_version"]),
        )
        self.assertEqual(manifest["python_version"], "3.14.6")
        self.assertEqual(manifest["uv_version"], "0.12.0")
        sidecars = json.loads(
            (ROOT / "desktop" / "sidecars.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sidecars["uv_version"], manifest["uv_version"])
        self.assertEqual(
            set(sidecars["targets"]),
            {
                "x86_64-pc-windows-msvc",
                "aarch64-apple-darwin",
                "x86_64-unknown-linux-gnu",
            },
        )
        for target in sidecars["targets"].values():
            self.assertRegex(target["sha256"], r"^[a-f0-9]{64}$")
        dynamic_extras = project["tool"]["setuptools"]["dynamic"][
            "optional-dependencies"
        ]
        self.assertEqual(set(manifest["surfaces"]), {"browser"})
        self.assertTrue(manifest["surfaces"]["browser"]["default"])
        for surface in manifest["surfaces"].values():
            self.assertIn(surface["extra"], dynamic_extras)
        for capability in manifest["capabilities"].values():
            self.assertIn(capability["extra"], dynamic_extras)
        self.assertEqual(
            manifest["media_runtime"]["strategy"],
            "system",
        )

    def test_release_please_preserves_desktop_manifests_and_links_versions(self):
        for filename in (
            "release-please-config.json",
            "release-please-config.stable.json",
        ):
            config = json.loads(
                (ROOT / filename).read_text(encoding="utf-8")
            )
            linked_versions = [
                plugin
                for plugin in config["plugins"]
                if plugin["type"] == "linked-versions"
            ]
            self.assertEqual(len(linked_versions), 1, filename)
            self.assertEqual(
                set(linked_versions[0]["components"]),
                {"vidxp", "desktop"},
                filename,
            )

            desktop = config["packages"]["desktop"]
            self.assertEqual(desktop["version-file"], "VERSION", filename)
            generic_files = {
                extra["path"]
                for extra in desktop["extra-files"]
                if extra["type"] == "generic"
            }
            self.assertIn("src-tauri/Cargo.toml", generic_files, filename)
            self.assertIn("src-tauri/Cargo.lock", generic_files, filename)

        stable = json.loads(
            (ROOT / "release-please-config.stable.json").read_text(
                encoding="utf-8"
            )
        )
        beta_manifest_updates = {
            (entry["path"], entry.get("jsonpath"))
            for package in stable["packages"].values()
            for entry in package["extra-files"]
            if entry["path"] == ".release-please-manifest.json"
        }
        self.assertEqual(
            beta_manifest_updates,
            {
                (".release-please-manifest.json", "$['.']"),
                (".release-please-manifest.json", "$.desktop"),
            },
        )

        manifest = json.loads(
            (ROOT / "desktop" / "runtime-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        package = json.loads(
            (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
        )
        cargo = tomllib.loads(
            (
                ROOT / "desktop" / "src-tauri" / "Cargo.toml"
            ).read_text(encoding="utf-8")
        )
        version_file = (
            ROOT / "desktop" / "VERSION"
        ).read_text(encoding="utf-8").strip()
        self.assertEqual(version_file, manifest["desktop_version"])
        self.assertEqual(version_file, package["version"])
        self.assertEqual(version_file, cargo["package"]["version"])
        self.assertIn(
            f'version = "{version_file}" # x-release-please-version',
            (
                ROOT / "desktop" / "src-tauri" / "Cargo.toml"
            ).read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            f"vidxp=={version_file}",
            (
                ROOT / "desktop" / "src-tauri" / "src" / "lib.rs"
            ).read_text(encoding="utf-8"),
        )

        build_command = "bash utils/build_package.sh"
        for workflow in (
            ".github/workflows/ci.yml",
            ".github/workflows/release-to-test-pypi.yml",
            ".github/workflows/release-to-pypi.yml",
        ):
            self.assertIn(
                build_command,
                (ROOT / workflow).read_text(encoding="utf-8"),
                workflow,
            )

        release_workflow = (
            ROOT / ".github" / "workflows" / "release-please.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(release_workflow.count('--ref "$TAG"'), 2)
        self.assertEqual(
            release_workflow.count('--repo "$GITHUB_REPOSITORY"'),
            2,
        )
        self.assertNotIn(
            '--ref "${{ github.ref_name }}"',
            release_workflow,
        )

        publisher_repo_flags = {
            "release-to-test-pypi.yml": 2,
            "release-to-pypi.yml": 3,
            "publish-desktop.yml": 4,
        }
        for workflow, expected in publisher_repo_flags.items():
            contents = (
                ROOT / ".github" / "workflows" / workflow
            ).read_text(encoding="utf-8")
            self.assertEqual(
                contents.count('--repo "$GITHUB_REPOSITORY"'),
                expected,
                workflow,
            )

        desktop_publish = (
            ROOT / ".github" / "workflows" / "publish-desktop.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('"VERSION":', desktop_publish)
        self.assertIn('"runtime manifest package":', desktop_publish)
        self.assertEqual(desktop_publish.count("--latest"), 1)
        core_publish = (
            ROOT / ".github" / "workflows" / "release-to-pypi.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("--latest", core_publish)

        for workflow in ("ci.yml", "desktop.yml", "security.yml"):
            contents = (
                ROOT / ".github" / "workflows" / workflow
            ).read_text(encoding="utf-8")
            self.assertIn("      - release", contents, workflow)
            self.assertIn("github.base_ref != 'release'", contents, workflow)
            self.assertIn("github.head_ref != 'main'", contents, workflow)
        desktop_ci = (
            ROOT / ".github" / "workflows" / "desktop.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "!startsWith(github.head_ref, 'release-please--branches--')",
            desktop_ci,
        )

        promotion = (
            ROOT / ".github" / "workflows" / "promotion-pr.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('"$status" == "diverged"', promotion)
        synchronization = (
            ROOT / ".github" / "workflows" / "sync-channels.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'git push origin "$publication_sha:refs/heads/main"',
            synchronization,
        )
        self.assertNotIn(
            'git push origin "origin/release:refs/heads/main"',
            synchronization,
        )
        self.assertIn(
            '--title "chore(release): synchronize stable baseline"',
            synchronization,
        )

    def test_windows_release_binary_uses_the_gui_subsystem(self):
        main = (
            ROOT / "desktop" / "src-tauri" / "src" / "main.rs"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]',
            main,
        )


if __name__ == "__main__":
    unittest.main()
