import hashlib
import os
import sys
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from time import sleep
from unittest.mock import Mock, call, patch

from packaging.requirements import Requirement
from pydantic import ValidationError

from vidxp.capabilities.contracts import CapabilityDefinition
from vidxp.capabilities.dialogue import models as dialogue_models
from vidxp.capabilities.dialogue.specs import (
    FASTER_WHISPER_MODEL,
    QWEN3_EMBEDDING_MODEL,
)
from vidxp.capabilities.actor import models as actor_models
from vidxp.capabilities.actor.definition import model_manifest as actor_manifest
from vidxp.capabilities.actor.specs import SFACE_MODEL, YUNET_MODEL
from vidxp.capabilities.registry import create_capability_registry
from vidxp.application_models import DependencyKind
from vidxp.core.contracts import IndexConfig, VideoSource
from vidxp.dependencies import (
    active_requirements,
    inspect_requirement,
    packaged_requirements,
)
from vidxp.infrastructure.local_index import (
    LOCAL_INDEX_RUNTIME_CHECKS,
    SERVER_INDEX_RUNTIME_CHECKS,
)
from vidxp.model_contracts import (
    ModelArtifactDownloadError,
    ModelArtifactUnavailableError,
    ModelKey,
    model_artifact_cached,
    model_artifact_path,
)
from vidxp.runtime import ModelRuntime, resolve_backends
from vidxp.settings import VidXPSettings


class ModelTests(unittest.TestCase):
    def runtime(
        self,
        root: str,
        *,
        allowed_specs=None,
        allow_model_downloads: bool | None = None,
    ) -> ModelRuntime:
        settings = {
            "repository_root": root,
            "model_cache": Path(root) / "models",
            "runtime_backend": "cpu",
        }
        if allow_model_downloads is not None:
            settings["allow_model_downloads"] = allow_model_downloads
        return ModelRuntime(
            VidXPSettings(**settings),
            allowed_specs=(
                create_capability_registry().model_specs()
                if allowed_specs is None
                else tuple(allowed_specs)
            ),
        )

    def test_model_runtime_reuses_one_provider_instance(self):
        with TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            constructor = Mock(return_value=object())
            fake_module = types.SimpleNamespace(
                SentenceTransformer=constructor
            )
            snapshot = Path(directory) / "snapshot"
            runtime.resolve_model = Mock(return_value=snapshot)
            with patch.dict(
                sys.modules,
                {"sentence_transformers": fake_module},
            ):
                first = dialogue_models.get_embedder(runtime)
                second = dialogue_models.get_embedder(runtime)

        self.assertIs(first, second)
        constructor.assert_called_once_with(
            str(snapshot),
            device="cpu",
            cache_folder=str(Path(directory) / "models"),
            local_files_only=True,
        )
        self.assertEqual(
            runtime.describe()["compute_precision"]["dialogue.embedding"],
            "bfloat16",
        )

    def test_unrelated_model_loads_do_not_hold_the_global_cache_lock(self):
        runtime = self.runtime("unused")
        barrier = Barrier(2)

        def load(value):
            barrier.wait(timeout=2)
            return value

        keys = (
            ModelKey("scene", "one", "one", "1", "cpu"),
            ModelKey("dialogue", "two", "two", "1", "cpu"),
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(runtime.get_or_load, key, lambda v=value: load(v))
                for value, key in enumerate(keys)
            ]
        self.assertEqual([future.result() for future in futures], [0, 1])

    def test_actor_download_uses_pinned_media_object_not_lfs_pointer(self):
        with TemporaryDirectory() as directory:
            content = b"verified model"
            downloaded = Path(directory) / "model.onnx"
            downloaded.write_bytes(content)
            spec = replace(
                YUNET_MODEL,
                filename=downloaded.name,
                sha256=hashlib.sha256(content).hexdigest(),
            )
            runtime = self.runtime(
                directory,
                allowed_specs=(spec,),
                allow_model_downloads=True,
            )
            retrieve = Mock(return_value=str(downloaded))
            fake_pooch = types.SimpleNamespace(retrieve=retrieve)
            with patch.dict(sys.modules, {"pooch": fake_pooch}):
                result = runtime.resolve_artifact(spec, download=True)

        self.assertEqual(result, downloaded)
        url = retrieve.call_args.kwargs["url"]
        self.assertIn("media.githubusercontent.com/media/", url)
        self.assertIn(spec.revision, url)
        self.assertNotIn("raw.githubusercontent.com", url)
        self.assertEqual(
            retrieve.call_args.kwargs["known_hash"],
            f"sha256:{spec.sha256}",
        )
        self.assertEqual(
            runtime.describe()["resolved_models"]["actor.detector"]["model"],
            "yunet",
        )

    def test_actor_download_retries_without_claiming_partial_resume(self):
        with TemporaryDirectory() as directory:
            spec = replace(YUNET_MODEL, filename="missing.onnx")
            runtime = self.runtime(
                directory,
                allowed_specs=(spec,),
                allow_model_downloads=True,
            )
            retrieve = Mock(side_effect=ConnectionError("interrupted"))
            events = []
            with patch.dict(
                sys.modules,
                {"pooch": types.SimpleNamespace(retrieve=retrieve)},
            ), patch("vidxp.runtime.sleep"), self.assertRaises(
                ModelArtifactDownloadError
            ) as raised:
                runtime.resolve_artifact(
                    spec,
                    download=True,
                    progress=events.append,
                )

        self.assertEqual(retrieve.call_count, 3)
        self.assertFalse(raised.exception.resumable)
        self.assertTrue(raised.exception.retryable)
        self.assertTrue(
            any(
                event["stage"] == "downloading_model"
                and "restart from zero" in event["message"]
                for event in events
            )
        )

    def test_normal_model_resolution_never_downloads_implicitly(self):
        with TemporaryDirectory() as directory:
            spec = replace(YUNET_MODEL, filename="missing.onnx")
            runtime = self.runtime(
                directory,
                allowed_specs=(spec,),
                allow_model_downloads=True,
            )
            retrieve = Mock(side_effect=AssertionError("downloaded"))
            with (
                patch.dict(
                    sys.modules,
                    {"pooch": types.SimpleNamespace(retrieve=retrieve)},
                ),
                self.assertRaises(ModelArtifactUnavailableError),
            ):
                runtime.resolve_artifact(spec)

        retrieve.assert_not_called()

    def test_model_readiness_rejects_corrupt_pinned_cache_without_loading(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "models"
            path = model_artifact_path(cache, YUNET_MODEL)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"present")
            checks = create_capability_registry().model_checks(
                ("actor",),
                cache=cache,
            )

        self.assertEqual(
            [
                (check.name, check.download_size_bytes, check.ok)
                for check in checks
            ],
            [
                (
                    YUNET_MODEL.model_id,
                    YUNET_MODEL.download_size_bytes,
                    False,
                ),
                (
                    SFACE_MODEL.model_id,
                    SFACE_MODEL.download_size_bytes,
                    False,
                ),
            ],
        )

    def test_model_cache_requires_the_pinned_checksum(self):
        with TemporaryDirectory() as directory:
            content = b"verified model"
            spec = replace(
                YUNET_MODEL,
                filename="verified.onnx",
                sha256=hashlib.sha256(content).hexdigest(),
            )
            cache = Path(directory) / "models"
            path = model_artifact_path(cache, spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(content)

            self.assertTrue(model_artifact_cached(cache, spec))
            path.write_bytes(b"corrupt")
            self.assertFalse(model_artifact_cached(cache, spec))

    def test_explicit_snapshot_download_reports_bytes(self):
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot"
            weights = snapshot / FASTER_WHISPER_MODEL.weights_file
            weights.parent.mkdir(parents=True)
            content = b"verified weights"
            weights.write_bytes(content)
            spec = replace(
                FASTER_WHISPER_MODEL,
                weights_sha256=hashlib.sha256(content).hexdigest(),
            )
            events = []

            def download(**options):
                progress = options["tqdm_class"](
                    desc="Downloading bytes",
                    total=1024,
                    unit="B",
                )
                progress.update(512)
                sleep(0.6)
                progress.update(512)
                sleep(0.6)
                progress.close()
                return str(snapshot)

            with patch(
                "huggingface_hub.snapshot_download",
                side_effect=download,
            ) as snapshot_download, patch(
                "huggingface_hub.constants.HF_HUB_DISABLE_XET",
                False,
            ):
                resolved = ModelRuntime._download_snapshot(
                    spec,
                    cache=Path(directory),
                    progress=events.append,
                )
                self.assertTrue(
                    __import__("huggingface_hub").constants.HF_HUB_DISABLE_XET
                )

        self.assertEqual(resolved, snapshot)
        self.assertEqual(
            snapshot_download.call_args.kwargs["ignore_patterns"],
            ("*.h5", "*.msgpack", "*.npz", "*.ot"),
        )
        download_events = [
            event for event in events if event["stage"] == "downloading_model"
        ]
        self.assertTrue(download_events)
        self.assertTrue(
            all(
                event["total"] == spec.download_size_bytes
                for event in download_events
            )
        )
        self.assertTrue(
            any(
                event["stage"] == "downloading_model"
                and event["current"]
                == spec.download_size_bytes
                and event["total"]
                == spec.download_size_bytes
                for event in events
            )
        )

    def test_snapshot_download_retries_and_resumes_partial_cache(self):
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot"
            weights = snapshot / FASTER_WHISPER_MODEL.weights_file
            weights.parent.mkdir(parents=True)
            content = b"verified weights"
            weights.write_bytes(content)
            spec = replace(
                FASTER_WHISPER_MODEL,
                weights_sha256=hashlib.sha256(content).hexdigest(),
            )
            download = Mock(
                side_effect=(ConnectionError("interrupted"), str(snapshot))
            )
            events = []

            with patch(
                "huggingface_hub.snapshot_download",
                download,
            ), patch("vidxp.runtime.sleep") as retry_sleep:
                resolved = ModelRuntime._download_snapshot(
                    spec,
                    cache=Path(directory),
                    progress=events.append,
                )

        self.assertEqual(resolved, snapshot)
        self.assertEqual(download.call_count, 2)
        retry_sleep.assert_called_once_with(1)
        self.assertTrue(
            any(
                event["stage"] == "downloading_model"
                and "partial files will be resumed" in event["message"]
                for event in events
            )
        )

    def test_snapshot_download_retries_an_incomplete_returned_snapshot(self):
        with TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot"
            weights = snapshot / FASTER_WHISPER_MODEL.weights_file
            content = b"verified weights"
            spec = replace(
                FASTER_WHISPER_MODEL,
                weights_sha256=hashlib.sha256(content).hexdigest(),
            )
            calls = 0

            def download(**_options):
                nonlocal calls
                calls += 1
                if calls == 2:
                    weights.parent.mkdir(parents=True)
                    weights.write_bytes(content)
                return str(snapshot)

            events = []
            with patch(
                "huggingface_hub.snapshot_download",
                side_effect=download,
            ), patch("vidxp.runtime.sleep") as retry_sleep:
                resolved = ModelRuntime._download_snapshot(
                    spec,
                    cache=Path(directory),
                    progress=events.append,
                )

        self.assertEqual(resolved, snapshot)
        self.assertEqual(calls, 2)
        retry_sleep.assert_called_once_with(1)
        self.assertTrue(
            any(
                event["stage"] == "downloading_model"
                and "partial files will be resumed" in event["message"]
                for event in events
            )
        )

    def test_snapshot_download_reports_actionable_terminal_failure(self):
        with TemporaryDirectory() as directory:
            download = Mock(side_effect=ConnectionError("private detail"))

            with patch(
                "huggingface_hub.snapshot_download",
                download,
            ), patch("vidxp.runtime.sleep"), self.assertRaises(
                ModelArtifactDownloadError
            ) as raised:
                ModelRuntime._download_snapshot(
                    FASTER_WHISPER_MODEL,
                    cache=Path(directory),
                    progress=None,
                )

        self.assertEqual(download.call_count, 3)
        self.assertEqual(raised.exception.attempts, 3)
        self.assertEqual(raised.exception.reason, "ConnectionError")
        self.assertTrue(raised.exception.resumable)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("private detail", str(raised.exception))

    def test_snapshot_download_does_not_mask_programming_errors(self):
        with TemporaryDirectory() as directory:
            download = Mock(side_effect=TypeError("implementation bug"))

            with patch(
                "huggingface_hub.snapshot_download",
                download,
            ), self.assertRaisesRegex(TypeError, "implementation bug"):
                ModelRuntime._download_snapshot(
                    FASTER_WHISPER_MODEL,
                    cache=Path(directory),
                    progress=None,
                )

        download.assert_called_once()

    def test_snapshot_download_does_not_retry_terminal_http_errors(self):
        with TemporaryDirectory() as directory:
            response = Mock(status_code=404)
            failure = RuntimeError("not found")
            failure.response = response
            download = Mock(side_effect=failure)

            with patch(
                "huggingface_hub.snapshot_download",
                download,
            ), self.assertRaises(ModelArtifactDownloadError) as raised:
                ModelRuntime._download_snapshot(
                    FASTER_WHISPER_MODEL,
                    cache=Path(directory),
                    progress=None,
                )

        download.assert_called_once()
        self.assertEqual(raised.exception.reason, "HTTP 404 RuntimeError")
        self.assertFalse(raised.exception.retryable)

    def test_incomplete_cached_snapshot_is_resumed_during_prepare(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory) / "models"
            incomplete = Path(directory) / "incomplete"
            complete = Path(directory) / "complete"
            content = b"verified weights"
            weights = complete / FASTER_WHISPER_MODEL.weights_file
            weights.parent.mkdir(parents=True)
            weights.write_bytes(content)
            spec = replace(
                FASTER_WHISPER_MODEL,
                weights_sha256=hashlib.sha256(content).hexdigest(),
            )
            runtime = self.runtime(
                directory,
                allowed_specs=(spec,),
                allow_model_downloads=True,
            )

            with patch(
                "huggingface_hub.snapshot_download",
                return_value=str(incomplete),
            ), patch.object(
                ModelRuntime,
                "_download_snapshot",
                return_value=complete,
            ) as resume:
                resolved = runtime.resolve_model(spec, download=True)

        self.assertEqual(resolved, complete)
        resume.assert_called_once_with(spec, cache=cache, progress=None)

    def test_runtime_rejects_specs_not_declared_by_enabled_capabilities(self):
        with TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            untrusted = replace(YUNET_MODEL, model_id="untrusted-detector")

            with self.assertRaises(ModelArtifactUnavailableError):
                runtime.resolve_artifact(untrusted)

        self.assertEqual(runtime.describe()["resolved_models"], {})

    def test_actor_artifact_resolves_verified_offline_cache(self):
        with TemporaryDirectory() as directory:
            content = b"verified cached model"
            spec = replace(
                YUNET_MODEL,
                filename="cached.onnx",
                sha256=hashlib.sha256(content).hexdigest(),
            )
            cache = Path(directory) / "models"
            path = cache / spec.provider / spec.filename
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            runtime = ModelRuntime(
                VidXPSettings(
                    repository_root=directory,
                    model_cache=cache,
                    runtime_backend="cpu",
                    allow_model_downloads=False,
                ),
                allowed_specs=(spec,),
            )
            fake_pooch = types.SimpleNamespace(
                retrieve=Mock(side_effect=AssertionError("downloaded"))
            )
            with patch.dict(sys.modules, {"pooch": fake_pooch}):
                resolved = runtime.resolve_artifact(spec)

        self.assertEqual(resolved, path)
        self.assertTrue(
            runtime.describe()["resolved_models"]["actor.detector"]["cached"]
        )

    def test_bad_or_missing_offline_artifact_is_not_recorded(self):
        for content in (None, b"corrupt"):
            with self.subTest(content=content):
                with TemporaryDirectory() as directory:
                    cache = Path(directory) / "models"
                    spec = replace(
                        YUNET_MODEL,
                        filename="cached.onnx",
                        sha256=hashlib.sha256(b"expected").hexdigest(),
                    )
                    if content is not None:
                        path = cache / spec.provider / spec.filename
                        path.parent.mkdir(parents=True)
                        path.write_bytes(content)
                    runtime = ModelRuntime(
                        VidXPSettings(
                            repository_root=directory,
                            model_cache=cache,
                            runtime_backend="cpu",
                            allow_model_downloads=False,
                        ),
                        allowed_specs=(spec,),
                    )
                    with self.assertRaises(ModelArtifactUnavailableError):
                        runtime.resolve_artifact(spec)

                    self.assertNotIn(
                        "actor.detector",
                        runtime.describe()["resolved_models"],
                    )

    def test_actor_models_resolve_both_specs_through_runtime(self):
        runtime = Mock()
        runtime.device_for.return_value = "cpu"
        runtime.get_or_load.side_effect = lambda _key, loader: loader()
        runtime.resolve_artifact.side_effect = (
            Path("detector.onnx"),
            Path("recognizer.onnx"),
        )
        detector = Mock()
        recognizer = Mock()
        fake_cv2 = types.SimpleNamespace(
            FaceDetectorYN=types.SimpleNamespace(create=detector),
            FaceRecognizerSF=types.SimpleNamespace(create=recognizer),
        )

        with patch.dict(sys.modules, {"cv2": fake_cv2}):
            actor_models.get_actor_models(runtime)

        self.assertEqual(
            [call.args[0] for call in runtime.resolve_artifact.call_args_list],
            [YUNET_MODEL, SFACE_MODEL],
        )
        detector.assert_called_once()
        recognizer.assert_called_once()
        self.assertEqual(
            runtime.record_compute_precision.call_args_list,
            [
                call("actor.detector", "float32"),
                call("actor.recognizer", "float32"),
            ],
        )

    def test_actor_manifest_derives_provider_identity_from_specs(self):
        manifest = actor_manifest(IndexConfig.local(), ())
        actor = manifest["actor"]

        self.assertEqual(
            actor["models"],
            {
                "detector": YUNET_MODEL.identity(),
                "recognizer": SFACE_MODEL.identity(),
            },
        )
        for repeated in ("provider", "revision", "license", "precision"):
            self.assertNotIn(repeated, actor)

    def test_model_spec_metadata_is_canonical_and_unambiguous(self):
        self.assertEqual(YUNET_MODEL.license, "MIT")
        self.assertEqual(SFACE_MODEL.license, "Apache-2.0")
        self.assertEqual(QWEN3_EMBEDDING_MODEL.weights_precision, "bfloat16")
        self.assertEqual(
            FASTER_WHISPER_MODEL.model_id,
            "dropbox-dash/faster-whisper-large-v3-turbo",
        )
        self.assertEqual(
            FASTER_WHISPER_MODEL.weights_precision,
            "float16",
        )

    def test_scene_dependency_check_is_capability_scoped(self):
        registry = create_capability_registry()
        inspected = []

        def installed(name):
            inspected.append(name)
            return {
                "chromadb": "1.5.9",
                "psutil": "7.2.2",
                "numpy": "2.5.1",
                "opencv-python-headless": "5.0.0.93",
                "Pillow": "12.3.0",
                "huggingface-hub": "1.25.1",
                "torch": "2.13.0",
                "transformers": "5.14.1",
            }[name]

        with patch("vidxp.dependencies.version", side_effect=installed):
            checks = registry.dependency_checks(("scene",))

        self.assertTrue(
            all(
                check.ok
                for check in checks
                if check.kind == DependencyKind.distribution
            )
        )
        self.assertNotIn("faster-whisper", inspected)
        self.assertNotIn("sentence-transformers", inspected)
        self.assertNotIn("pooch", inspected)

    def test_platform_runtime_checks_are_storage_owned_and_not_duplicated(self):
        registry = create_capability_registry(
            platform_runtime_checks=LOCAL_INDEX_RUNTIME_CHECKS
        )

        checks = registry.dependency_checks(registry.names())
        storage_checks = [
            check
            for check in checks
            if check.kind == DependencyKind.runtime
            and check.capability == "storage"
        ]

        self.assertEqual(
            [check.name for check in storage_checks],
            [
                "Chroma storage import",
                "Host resource monitor import",
            ],
        )

    def test_server_storage_checks_match_the_remote_chroma_client(self):
        registry = create_capability_registry(
            platform_runtime_checks=SERVER_INDEX_RUNTIME_CHECKS,
            storage_requirements=active_requirements(
                packaged_requirements(
                    "vidxp",
                    "requirements/server-storage.txt",
                )
            ),
        )

        requirements = {
            requirement.name
            for requirement in registry.requirements_for(("scene",))
        }

        self.assertIn("chromadb-client", requirements)
        self.assertNotIn("chromadb", requirements)

    def test_transcript_only_excludes_transcription_provider(self):
        registry = create_capability_registry()
        source = VideoSource(
            transcript=({"text": "hello", "start": 0, "end": 1},)
        )

        distributions = {
            requirement.name
            for requirement in registry.requirements_for(
                ("dialogue",),
                source=source,
            )
        }

        self.assertIn("sentence-transformers", distributions)
        self.assertNotIn("faster-whisper", distributions)

    def test_runtime_distributions_come_from_registry(self):
        registry = create_capability_registry()
        with patch(
            "vidxp.capabilities.registry.installed_base_requirements",
            return_value=(),
        ):
            distributions = registry.runtime_distributions()

        self.assertIn("transformers", distributions)
        self.assertIn("faster-whisper", distributions)
        self.assertIn("opencv-python-headless", distributions)
        self.assertNotIn("clip-anytorch", distributions)
        self.assertNotIn("face-recognition", distributions)
        self.assertEqual(len(distributions), len(set(distributions)))

    def test_requirement_files_are_dependency_contract(self):
        self.assertNotIn("dependencies", CapabilityDefinition.model_fields)
        registry = create_capability_registry()
        self.assertIn(
            "faster-whisper import",
            {
                check.label
                for check in registry.runtime_checks_for(("dialogue",))
            },
        )

    def test_requirement_check_uses_distribution_metadata(self):
        requirement = Requirement("example>=2,<3")
        with patch("vidxp.dependencies.version", return_value="2.5"):
            self.assertTrue(inspect_requirement(requirement)["ok"])
        with patch("vidxp.dependencies.version", return_value="1.0"):
            self.assertFalse(inspect_requirement(requirement)["ok"])
        with patch(
            "vidxp.dependencies.version",
            side_effect=PackageNotFoundError("example"),
        ):
            self.assertFalse(inspect_requirement(requirement)["ok"])

    def test_server_runtime_and_external_allowlist_are_explicit(self):
        with self.assertRaises(ValidationError):
            VidXPSettings(mode="server", runtime_backend="mps")
        with self.assertRaises(ValidationError):
            VidXPSettings(capability_allowlist=("distribution-only",))
        settings = VidXPSettings(
            mode="server",
            runtime_backend="cuda:0",
            capability_allowlist=("acme-capabilities:ocr",),
        )
        self.assertEqual(settings.runtime_backend, "cuda:0")
        self.assertNotIn("database_url", VidXPSettings.model_fields)
        self.assertNotIn("chroma_server_url", VidXPSettings.model_fields)
        with self.assertRaises(ValidationError):
            VidXPSettings(slm_base_url="http://localhost:11434/v1")
        with self.assertRaises(ValidationError):
            VidXPSettings(
                slm_base_url="https://ollama.com/v1",
                slm_model="qwen3",
            )
        with self.assertRaises(ValidationError):
            VidXPSettings(
                slm_base_url="http://localhost:11434/v1",
                slm_model="qwen3-cloud",
            )
        slm = VidXPSettings(
            slm_base_url="http://localhost:11434/v1",
            slm_model="evaluated-model",
        )
        self.assertEqual(slm.slm_model, "evaluated-model")

    def test_only_optional_slm_environment_values_ignore_empty_strings(self):
        with patch.dict(
            os.environ,
            {
                "VIDXP_SLM_BASE_URL": "",
                "VIDXP_SLM_MODEL": "",
            },
            clear=True,
        ):
            settings = VidXPSettings(_env_file=None)

        self.assertIsNone(settings.slm_base_url)
        self.assertIsNone(settings.slm_model)

        with (
            patch.dict(
                os.environ,
                {
                    "VIDXP_HTTP_AUTH_MODE": "static",
                    "VIDXP_HTTP_STATIC_BEARER_TOKEN": "",
                },
                clear=True,
            ),
            self.assertRaises(ValidationError),
        ):
            VidXPSettings(_env_file=None)

    def test_auto_runtime_remains_cpu_until_acceleration_parity_is_enabled(self):
        with patch(
            "vidxp.runtime._torch_accelerators",
            return_value=(True, True),
        ):
            profile = resolve_backends("auto")

        self.assertEqual(profile.torch_device, "cpu")
        self.assertEqual(profile.transcription_device, "cpu")


if __name__ == "__main__":
    unittest.main()
