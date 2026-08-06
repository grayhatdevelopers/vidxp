import json
import subprocess
import sys
import tarfile
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.version import Version

from vidxp.capabilities.registry import create_capability_registry
from vidxp.settings import VidXPSettings


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_wheel_contains_mcp_app_and_versioned_plugin_bundle(self):
        with TemporaryDirectory() as directory:
            subprocess.run(
                [
                    sys.executable,
                    "setup.py",
                    "bdist_wheel",
                    "--dist-dir",
                    directory,
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            wheels = tuple(Path(directory).glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as archive:
                members = set(archive.namelist())
                entry_points_name = next(
                    name
                    for name in members
                    if name.endswith(".dist-info/entry_points.txt")
                )
                entry_points = archive.read(entry_points_name).decode("utf-8")

        required = {
            "vidxp/assets/mcp_app/index.html",
            "vidxp/bundled_plugins/vidxp/.mcp.json",
            "vidxp/bundled_plugins/vidxp/.codex-plugin/plugin.json",
            "vidxp/bundled_plugins/vidxp/skills/vidxp-ingest-video/SKILL.md",
            "vidxp/bundled_plugins/vidxp/skills/vidxp-ingest-video/agents/openai.yaml",
            "vidxp/bundled_plugins/vidxp/skills/vidxp-find-video-evidence/SKILL.md",
            "vidxp/bundled_plugins/vidxp/skills/vidxp-find-video-evidence/agents/openai.yaml",
        }
        self.assertLessEqual(required, members)
        self.assertIn(
            "vidxp-codex-plugin = vidxp.codex_plugin_cli:main",
            entry_points,
        )

    def test_bundled_plugin_is_valid_and_skills_match_canonical_sources(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        plugin_root = ROOT / "src" / "vidxp" / "bundled_plugins" / "vidxp"
        manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        mcp = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "vidxp")
        self.assertEqual(
            Version(manifest["version"]),
            Version(project["project"]["version"]),
        )
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        self.assertEqual(mcp["mcpServers"]["vidxp"]["command"], "vidxp-mcp")

        canonical = ROOT / "skills"
        bundled = plugin_root / "skills"
        canonical_files = {
            path.relative_to(canonical)
            for path in canonical.rglob("*")
            if path.is_file()
        }
        bundled_files = {
            path.relative_to(bundled)
            for path in bundled.rglob("*")
            if path.is_file()
        }
        self.assertEqual(bundled_files, canonical_files)
        for relative in canonical_files:
            self.assertEqual(
                (bundled / relative).read_text(encoding="utf-8"),
                (canonical / relative).read_text(encoding="utf-8"),
                relative.as_posix(),
            )

    def test_sdist_contains_every_upload_page_build_input(self):
        with TemporaryDirectory() as directory:
            subprocess.run(
                [
                    sys.executable,
                    "setup.py",
                    "sdist",
                    "--dist-dir",
                    directory,
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            archives = tuple(Path(directory).glob("*.tar.gz"))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0], "r:gz") as archive:
                members = {
                    name.split("/", 1)[1]
                    for name in archive.getnames()
                    if "/" in name
                }

        required_sources = {
            "web/upload-page/package.json",
            "web/upload-page/package-lock.json",
            "web/upload-page/scripts/build.mjs",
            "web/upload-page/src/app.js",
            "web/upload-page/src/app.css",
            "web/upload-page/src/index.html",
            "web/upload-page/src/recovery.js",
        }
        self.assertLessEqual(required_sources, members)
        self.assertFalse(any("/node_modules/" in f"/{name}/" for name in members))

    def test_coolify_handoff_origin_matches_tusd_cors_policy(self):
        documentation = (
            ROOT / "docs" / "deployment" / "coolify.md"
        ).read_text(encoding="utf-8")
        example = {}
        for line in documentation.splitlines():
            if line.startswith("VIDXP_UPLOAD_") and "=" in line:
                key, value = line.split("=", 1)
                example[key] = value

        settings = VidXPSettings(
            upload_public_endpoint=example["VIDXP_UPLOAD_PUBLIC_ENDPOINT"],
            upload_internal_endpoint="http://tusd:8080/uploads/",
            upload_cleanup_token="c" * 32,
            upload_handoff_public_url=(
                example["VIDXP_UPLOAD_HANDOFF_PUBLIC_URL"]
            ),
            upload_handoff_secret="h" * 32,
            upload_cors_origin_regex=(
                example["VIDXP_UPLOAD_CORS_ORIGIN_REGEX"]
            ),
        )
        handoff = urlsplit(settings.upload_handoff_public_url)
        self.assertEqual(handoff.hostname, "api.example.com")

        compose = (ROOT / "compose.coolify.yaml").read_text(encoding="utf-8")
        self.assertIn("VIDXP_UPLOAD_CORS_ORIGIN_REGEX:", compose)
        self.assertIn(
            "-cors-allow-origin=${VIDXP_UPLOAD_CORS_ORIGIN_REGEX:",
            compose,
        )
        self.assertIn(
            "VIDXP_HTTP_AUTH_MODE: ${VIDXP_HTTP_AUTH_MODE:-static}",
            compose,
        )
        self.assertIn("VIDXP_HTTP_OIDC_ISSUER:", compose)
        self.assertIn("VIDXP_MCP_PUBLIC_URL:", compose)

    def test_upload_page_assets_are_pinned_self_hosted_and_packaged(self):
        package = json.loads(
            (ROOT / "web" / "upload-page" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            package["dependencies"],
            {
                "@uppy/core": "5.2.0",
                "@uppy/dashboard": "5.1.1",
                "@uppy/golden-retriever": "5.2.1",
                "@uppy/tus": "5.1.1",
                "@uppy/xhr-upload": "5.2.0",
            },
        )
        self.assertEqual(package["devDependencies"], {"esbuild": "0.28.1"})

        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn(
            "assets/upload_page/*",
            project["tool"]["setuptools"]["package-data"]["vidxp"],
        )
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        self.assertIn("prune web/upload-page/node_modules", manifest)
        self.assertNotIn("recursive-include web/upload-page", manifest)
        assets = ROOT / "src" / "vidxp" / "assets" / "upload_page"
        expected = {
            "index.html",
            "upload-page.js",
            "upload-page.css",
            "THIRD_PARTY_NOTICES.txt",
        }
        self.assertEqual(
            {path.name for path in assets.iterdir() if path.is_file()},
            expected,
        )
        html = (assets / "index.html").read_text(encoding="utf-8")
        self.assertIn("./assets/upload-page.js", html)
        self.assertIn("./assets/upload-page.css", html)
        self.assertNotRegex(html, r"https?://")

        source = (ROOT / "web" / "upload-page" / "src" / "app.js").read_text(
            encoding="utf-8"
        )
        for contract in (
            "allowedMetaFields: ['intent_id']",
            "limit: 1",
            "parallelUploads: 1",
            "overridePatchMethod: false",
            "uploadDataDuringCreation: false",
            "withCredentials: false",
            "removeFingerprintOnSuccess: true",
            "VidXP-Handoff",
            "uppy.use(Dashboard",
            "showProgressDetails: true",
            "theme: 'dark'",
            "history.replaceState",
            "uppy.addPreProcessor(authorizeFiles)",
            "uppy.use(XHRUpload",
            "sessionStatus.transfer_backend === 'tus'",
            "maxTotalFileSize: sessionStatus.maximum_aggregate_bytes",
            "maxNumberOfFiles: sessionStatus.maximum_files",
            "client_file_key",
            "needsFileAuthorization(current, childByKey(key))",
            "indexedDB: { maxFileSize: 0, maxTotalSize: 0 }",
            "if (!shouldPollSession(sessionStatus)) return",
            "resumePollingAfterFileAuthorization(",
            "poll_after_seconds",
            "document.hidden",
            "visibilitychange",
        ):
            self.assertIn(contract, source)
        self.assertNotIn("if (current?.meta?.intent_id) return", source)
        self.assertIn(
            '"check:bundle": "npm run build && git diff --exit-code -- '
            '../../src/vidxp/assets/upload_page"',
            (ROOT / "web" / "upload-page" / "package.json").read_text(
                encoding="utf-8"
            ),
        )
        self.assertGreater(
            source.index("if (capability) clearFragment()"),
            source.index("await requestJson(apiUrl('./bootstrap')"),
        )
        self.assertNotIn("8 * 1024 * 1024", source)
        self.assertNotIn("./authenticate", source)
        self.assertNotIn("OIDC access token", source)
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("outerHTML", source)
        self.assertIn(
            "tus-js-client@4.3.1",
            (assets / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "@uppy/dashboard@5.1.1",
            (assets / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "@uppy/xhr-upload@5.2.0",
            (assets / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8"),
        )

    def test_artifact_download_landing_assets_are_safe_and_packaged(self):
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertIn(
            "assets/artifact_download/*",
            project["tool"]["setuptools"]["package-data"]["vidxp"],
        )
        self.assertIn(
            "recursive-include src/vidxp/assets/artifact_download *",
            (ROOT / "MANIFEST.in").read_text(encoding="utf-8"),
        )
        assets = ROOT / "src" / "vidxp" / "assets" / "artifact_download"
        self.assertEqual(
            {path.name for path in assets.iterdir() if path.is_file()},
            {
                "index.html",
                "artifact-download.css",
                "artifact-download.js",
                "vidxp-logo.png",
            },
        )
        html = (assets / "index.html").read_text(encoding="utf-8")
        stylesheet = (assets / "artifact-download.css").read_text(
            encoding="utf-8"
        )
        script = (assets / "artifact-download.js").read_text(encoding="utf-8")
        self.assertIn("./assets/artifact-download.js", html)
        self.assertIn("./assets/artifact-download.css", html)
        self.assertIn("./assets/vidxp-logo.png", html)
        self.assertNotRegex(html, r"https?://")
        self.assertNotIn("<script>", html)
        self.assertNotIn("style=", html)
        self.assertIn("@media (max-width: 34rem)", stylesheet)
        self.assertIn("window.history.replaceState", script)
        self.assertIn("credentials: 'same-origin'", script)
        self.assertIn("method: 'HEAD'", script)
        self.assertIn("downloadAgain.click()", script)
        self.assertIn("Download again", html)
        self.assertEqual(
            (assets / "vidxp-logo.png").read_bytes(),
            (
                ROOT / "desktop" / "src-tauri" / "icons" / "128x128.png"
            ).read_bytes(),
        )
        self.assertNotIn("local_path", script)

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
        self.assertIn('resolve(desktopRoot, "public")', sync_script)
        self.assertIn('resolve(publicDirectory, "icon.png")', sync_script)
        self.assertIn(
            'href="/icon.png"',
            (ROOT / "desktop" / "index.html").read_text(
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

    def test_local_profiles_include_unconditional_jwt_runtime_without_mcp(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        base = {
            Requirement(value).name.lower(): Requirement(value)
            for value in project["project"]["dependencies"]
        }
        jwt = base["pyjwt"]
        self.assertEqual(jwt.specifier, Requirement("pyjwt>=2.13,<3").specifier)
        self.assertEqual(jwt.extras, set())

        profiles = project["tool"]["setuptools"]["dynamic"][
            "optional-dependencies"
        ]
        for selected in (("local-worker",), ("local-worker", "frontend")):
            requirement_files = {
                path
                for profile in selected
                for path in profiles[profile]["file"]
            }
            self.assertNotIn("src/vidxp/requirements/mcp.txt", requirement_files)
            self.assertNotIn("src/vidxp/requirements/server.txt", requirement_files)
            self.assertIn("pyjwt", base)

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
        self.assertEqual(
            set(manifest["surfaces"]),
            {"worker", "browser", "mcp", "server"},
        )
        self.assertTrue(manifest["surfaces"]["worker"]["default"])
        self.assertTrue(manifest["surfaces"]["browser"]["default"])
        self.assertFalse(manifest["surfaces"]["mcp"]["default"])
        self.assertFalse(manifest["surfaces"]["server"]["default"])
        for surface in manifest["surfaces"].values():
            self.assertIn(surface["extra"], dynamic_extras)
        for capability in manifest["capabilities"].values():
            self.assertIn(capability["extra"], dynamic_extras)
        self.assertEqual(
            manifest["media_runtime"]["strategy"],
            "system",
        )

    def test_combined_release_version_contract(self):
        expected_extra_files = {
            "uv.lock",
            "src/vidxp/bundled_plugins/vidxp/.codex-plugin/plugin.json",
            "desktop/src-tauri/Cargo.toml",
            "desktop/src-tauri/Cargo.lock",
            "desktop/package.json",
            "desktop/package-lock.json",
            "desktop/runtime-manifest.json",
            "desktop/src-tauri/tauri.conf.json",
        }
        for filename in (
            "release-please-config.json",
            "release-please-config.stable.json",
        ):
            config = json.loads(
                (ROOT / filename).read_text(encoding="utf-8")
            )
            self.assertEqual(set(config["packages"]), {"."}, filename)
            root_package = config["packages"]["."]
            self.assertFalse(root_package["include-component-in-tag"])
            self.assertNotIn("component", root_package)
            self.assertEqual(
                {
                    entry["path"]
                    for entry in root_package["extra-files"]
                    if entry["path"] != ".release-please-manifest.json"
                },
                expected_extra_files,
                filename,
            )
            generic_files = {
                extra["path"]
                for extra in root_package["extra-files"]
                if extra["type"] == "generic"
            }
            self.assertEqual(
                generic_files,
                {
                    "uv.lock",
                    "desktop/src-tauri/Cargo.toml",
                },
                filename,
            )
            toml_files = {
                extra["path"]: extra["jsonpath"]
                for extra in root_package["extra-files"]
                if extra["type"] == "toml"
            }
            self.assertEqual(
                toml_files,
                {
                    "desktop/src-tauri/Cargo.lock": (
                        '$.package[?(@.name.value=="vidxp-desktop")].version'
                    )
                },
                filename,
            )

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
            {(".release-please-manifest.json", "$['.']")},
        )
        for filename in (
            ".release-please-manifest.json",
            ".release-please-manifest.stable.json",
        ):
            manifest_versions = json.loads(
                (ROOT / filename).read_text(encoding="utf-8")
            )
            self.assertEqual(set(manifest_versions), {"."}, filename)

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
        project = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        version = project["project"]["version"]
        self.assertFalse((ROOT / "desktop" / "VERSION").exists())
        self.assertEqual(version, manifest["desktop_version"])
        self.assertEqual(version, manifest["package_version"])
        self.assertEqual(version, package["version"])
        self.assertEqual(version, cargo["package"]["version"])
        self.assertEqual(manifest["dependency_index"], "https://pypi.org/simple")
        version_marker = (
            f'version = "{version}" # x-release-please-version'
        )
        self.assertIn(
            version_marker,
            (
                ROOT / "desktop" / "src-tauri" / "Cargo.toml"
            ).read_text(encoding="utf-8"),
            "Cargo.toml",
        )
        cargo_lock = tomllib.loads(
            (
                ROOT / "desktop" / "src-tauri" / "Cargo.lock"
            ).read_text(encoding="utf-8")
        )
        desktop_lock = next(
            package
            for package in cargo_lock["package"]
            if package["name"] == "vidxp-desktop"
        )
        self.assertEqual(desktop_lock["version"], version)
        self.assertIn(
            version_marker,
            (ROOT / "uv.lock").read_text(encoding="utf-8"),
            "uv.lock",
        )
        self.assertNotIn(
            f"vidxp=={version}",
            (
                ROOT / "desktop" / "src-tauri" / "src" / "lib.rs"
            ).read_text(encoding="utf-8"),
        )

    def test_windows_release_binary_uses_the_gui_subsystem(self):
        main = (
            ROOT / "desktop" / "src-tauri" / "src" / "main.rs"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]',
            main,
        )

    def test_desktop_bundle_includes_generated_third_party_notices(self):
        tauri = json.loads(
            (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(
            "../THIRD_PARTY_NOTICES.txt",
            tauri["bundle"]["resources"],
        )

        notices = (
            ROOT / "desktop" / "THIRD_PARTY_NOTICES.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("RUST DEPENDENCIES", notices)
        self.assertIn("FRONTEND DEPENDENCIES", notices)
        self.assertGreater(len(notices), 100_000)

        package = json.loads(
            (ROOT / "desktop" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            package["scripts"]["notices:check"],
            "node scripts/generate-notices.mjs",
        )


if __name__ == "__main__":
    unittest.main()
