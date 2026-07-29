import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from unittest.mock import Mock, patch

from filelock import Timeout
from vidxp import __version__
from vidxp.application_models import (
    ApplicationError,
    JobState,
    PrepareModelsCommand,
)
from vidxp.infrastructure.dbos_jobs import DBOSJobBackend
from vidxp.infrastructure.local_worker import LocalWorkerSupervisor
from vidxp.job_service import JobService
from vidxp.settings import LocalExecutionSettings, VidXPSettings
from vidxp.workflow_worker import _configure_logging, run_worker
from vidxp.workflow_runtime import (
    LOCAL_WORKER_BOOTSTRAP_ENV,
    LocalWorkerBootstrap,
    LocalWorkerReady,
    LocalWorkerStopRequest,
    local_worker_bootstrap,
    local_executor_id,
    server_executor_id,
    workflow_application_version,
    workflow_database_url,
)
from vidxp.workflow_worker import main as workflow_worker_main


class LocalWorkerSupervisorTests(unittest.TestCase):
    def test_workflow_version_identifies_the_release_and_implementation(self):
        value = workflow_application_version()

        self.assertRegex(
            value,
            rf"^{re.escape(__version__)}\+[0-9a-f]{{16}}$",
        )

    def test_server_executor_identity_keeps_the_single_worker_ordinal(self):
        version = workflow_application_version()

        self.assertEqual(server_executor_id(role="cpu"), f"{version}-cpu-0")
        self.assertEqual(server_executor_id(role="gpu"), f"{version}-gpu-0")
        with self.assertRaisesRegex(ValueError, "role"):
            server_executor_id(role="other")

    def test_worker_is_spawned_detached_without_job_state(self):
        with TemporaryDirectory() as directory:
            settings = VidXPSettings(
                repository_root=Path(directory),
                runtime_backend="cpu",
                http_auth_mode="static",
                http_static_bearer_token="s" * 32,
            )
            supervisor = LocalWorkerSupervisor(settings)
            captured_bootstrap = None

            def spawn(command, **options):
                nonlocal captured_bootstrap
                bootstrap_path = Path(
                    options["env"][LOCAL_WORKER_BOOTSTRAP_ENV]
                )
                captured_bootstrap = LocalWorkerBootstrap.model_validate_json(
                    bootstrap_path.read_text(encoding="utf-8")
                )
                process = Mock()
                process.poll.return_value = None
                return process

            with (
                patch(
                    "vidxp.infrastructure.local_worker.subprocess.Popen",
                    side_effect=spawn,
                ) as popen,
                patch.object(supervisor, "_wait_for_worker") as wait,
                patch.dict(
                    os.environ,
                    {
                        "VIDXP_HTTP_AUTH_MODE": "static",
                        "VIDXP_HTTP_STATIC_BEARER_TOKEN": "e" * 32,
                        "VIDXP_DATABASE_URL": (
                            "postgresql://user:password@db/vidxp"
                        ),
                        "DBOS__CLOUD": "true",
                        "DBOS_SYSTEM_DATABASE_URL": (
                            "postgresql://other:secret@db/other"
                        ),
                    },
                ),
            ):
                supervisor.ensure_running()

        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(
            wait.call_args.kwargs["timeout_seconds"],
            supervisor._startup_timeout_seconds,
        )
        self.assertGreaterEqual(supervisor._startup_timeout_seconds, 10)
        self.assertEqual(command[:3], [sys.executable, "-m", "vidxp.workflow_worker"])
        self.assertIn("--lock-file", command)
        lock_file = command[command.index("--lock-file") + 1]
        self.assertIn(workflow_application_version(), lock_file)
        self.assertNotIn("--database-url", command)
        self.assertNotIn("password", " ".join(command))
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertIs(options["stdout"], subprocess.DEVNULL)
        self.assertIs(options["stderr"], subprocess.DEVNULL)
        self.assertFalse(
            any(
                key.upper().startswith(("VIDXP_", "DBOS_"))
                for key in options["env"]
                if key != LOCAL_WORKER_BOOTSTRAP_ENV
            )
        )
        self.assertIsNotNone(captured_bootstrap)
        serialized = captured_bootstrap.settings.model_dump_json()
        self.assertNotIn("s" * 32, serialized)
        self.assertNotIn("e" * 32, serialized)
        self.assertNotIn("http_static_bearer_token", serialized)
        worker_settings = captured_bootstrap.settings.application_settings()
        self.assertEqual(
            worker_settings.repository_root,
            settings.repository_root,
        )
        self.assertEqual(worker_settings.http_auth_mode, "none")
        wait.assert_called_once()
        if sys.platform == "win32":
            self.assertEqual(
                options["creationflags"],
                (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                ),
            )
        else:
            self.assertTrue(options["start_new_session"])

    def test_application_settings_ignore_inherited_vidxp_environment(self):
        local = LocalExecutionSettings.from_settings(
            VidXPSettings(runtime_backend="cpu")
        )
        with patch.dict(
            os.environ,
            {
                "VIDXP_HTTP_AUTH_MODE": "static",
                "VIDXP_HTTP_STATIC_BEARER_TOKEN": "e" * 32,
                "VIDXP_DATABASE_URL": (
                    "postgresql://user:password@db/vidxp"
                ),
            },
        ):
            settings = local.application_settings()

        self.assertEqual(settings.http_auth_mode, "none")
        self.assertIsNone(settings.http_static_bearer_token)
        self.assertNotIn("database_url", type(settings).model_fields)

    def test_startup_wait_finishes_when_worker_owns_lock(self):
        with TemporaryDirectory() as directory:
            ready_path = Path(directory) / "worker.ready"
            ready_path.write_text("123", encoding="utf-8")
            process = Mock()
            process.poll.return_value = None
            worker_lock = Mock()
            worker_lock.acquire.side_effect = Timeout("worker.lock")
            fingerprint = "a" * 64
            ready_path.write_text(
                LocalWorkerReady(
                    pid=123,
                    application_version=workflow_application_version(),
                    fingerprint=fingerprint,
                ).model_dump_json(),
                encoding="utf-8",
            )

            LocalWorkerSupervisor._wait_for_worker(
                process,
                worker_lock,
                ready_path,
                fingerprint=fingerprint,
            )

        worker_lock.acquire.assert_called_once_with(timeout=0)

    def test_failed_startup_is_backed_off(self):
        with TemporaryDirectory() as directory:
            supervisor = LocalWorkerSupervisor(
                VidXPSettings(repository_root=Path(directory))
            )
            with patch.object(
                supervisor,
                "_ensure_running",
                side_effect=RuntimeError("failed"),
            ) as start:
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    supervisor.ensure_running()
                with self.assertRaisesRegex(RuntimeError, "backoff"):
                    supervisor.ensure_running()

        start.assert_called_once_with()

    def test_stale_worker_configuration_is_rejected(self):
        with TemporaryDirectory() as directory:
            ready_path = Path(directory) / "worker.ready"
            ready_path.write_text(
                LocalWorkerReady(
                    pid=123,
                    application_version=workflow_application_version(),
                    fingerprint="a" * 64,
                ).model_dump_json(),
                encoding="utf-8",
            )
            worker_lock = Mock()
            worker_lock.acquire.side_effect = Timeout("worker.lock")

            with self.assertRaisesRegex(
                RuntimeError,
                "different execution settings",
            ):
                LocalWorkerSupervisor._wait_for_existing_worker(
                    worker_lock,
                    ready_path,
                    fingerprint="b" * 64,
                    timeout_seconds=0.1,
                )

    def test_startup_timeout_terminates_exact_spawned_process(self):
        with TemporaryDirectory() as directory:
            supervisor = LocalWorkerSupervisor(
                VidXPSettings(repository_root=Path(directory))
            )
            process = Mock()
            process.poll.return_value = None
            with (
                patch(
                    "vidxp.infrastructure.local_worker.subprocess.Popen",
                    return_value=process,
                ),
                patch.object(
                    supervisor,
                    "_wait_for_worker",
                    side_effect=RuntimeError("startup timed out"),
                ),
                self.assertRaisesRegex(RuntimeError, "startup timed out"),
            ):
                supervisor.ensure_running()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)

    def test_stale_versioned_bootstrap_is_removed_before_start(self):
        with TemporaryDirectory() as directory:
            supervisor = LocalWorkerSupervisor(
                VidXPSettings(repository_root=Path(directory))
            )
            supervisor.layout.ensure_local_directories()
            stale = (
                supervisor.layout.local_workflows
                / (
                    f".worker-{workflow_application_version()}-"
                    "bootstrap-stale.json"
                )
            )
            stale.write_text("database-secret", encoding="utf-8")

            supervisor._remove_stale_bootstraps(
                workflow_application_version()
            )

            self.assertFalse(stale.exists())

    def test_worker_recomputes_bootstrap_identity(self):
        with TemporaryDirectory() as directory:
            settings = VidXPSettings(
                repository_root=Path(directory),
                runtime_backend="cpu",
            )
            supervisor = LocalWorkerSupervisor(settings)
            supervisor.layout.ensure_local_directories()
            bootstrap = local_worker_bootstrap(settings)
            tampered = bootstrap.model_copy(
                update={
                    "settings": bootstrap.settings.model_copy(
                        update={"runtime_backend": "cuda"}
                    )
                }
            )
            path = supervisor._write_bootstrap(tampered)
            with (
                patch.dict(
                    os.environ,
                    {LOCAL_WORKER_BOOTSTRAP_ENV: str(path)},
                ),
                patch("vidxp.workflow_worker.run_worker") as run,
                self.assertRaisesRegex(ValueError, "identity is invalid"),
            ):
                workflow_worker_main(
                    ["--executor-id", "test", "--role", "all"]
                )

        run.assert_not_called()
        self.assertFalse(path.exists())

    def test_stop_terminates_only_ready_lock_owner(self):
        with TemporaryDirectory() as directory:
            settings = VidXPSettings(repository_root=Path(directory))
            supervisor = LocalWorkerSupervisor(settings)
            supervisor.layout.ensure_local_directories()
            ready_path = (
                supervisor.layout.local_workflows
                / f"worker-{workflow_application_version()}.ready"
            )
            ready_path.write_text(
                LocalWorkerReady(
                    pid=123,
                    application_version=workflow_application_version(),
                    fingerprint="a" * 64,
                ).model_dump_json(),
                encoding="utf-8",
            )
            worker_lock = Mock()
            worker_lock.acquire.side_effect = [
                Timeout("worker.lock"),
                None,
            ]
            with (
                patch(
                    "vidxp.infrastructure.local_worker.FileLock",
                    return_value=worker_lock,
                ),
                patch(
                    "vidxp.infrastructure.local_worker.write_json_atomic"
                ) as write_stop,
            ):
                stopped = supervisor.stop()

        self.assertTrue(stopped)
        write_stop.assert_called_once()
        self.assertEqual(write_stop.call_args.args[1]["pid"], 123)
        self.assertEqual(
            write_stop.call_args.args[1]["application_version"],
            workflow_application_version(),
        )
        self.assertFalse(ready_path.exists())

    def test_worker_destroys_dbos_after_stop_request(self):
        stop_event = Event()
        stop_event.set()

        with (
            patch("vidxp.workflow_worker.DBOS") as dbos,
            patch("vidxp.workflow_worker.VidXPWorkerWorkflows"),
            patch("vidxp.workflow_worker.create_application"),
        ):
            run_worker(
                settings=VidXPSettings(runtime_backend="cpu"),
                database_url="sqlite:///test.db",
                executor_id="test",
                role="cpu",
                stop_event=stop_event,
            )

        dbos.destroy.assert_called_once_with(
            workflow_completion_timeout_sec=30
        )

    def test_worker_uses_live_rotating_log_handler(self):
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "worker.log"
            with (
                patch(
                    "vidxp.workflow_worker.RotatingFileHandler"
                ) as handler,
                patch("vidxp.workflow_worker.logging.basicConfig") as config,
            ):
                _configure_logging(log_path)

        handler.assert_called_once_with(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        config.assert_called_once()

    def test_fresh_database_runs_a_job_in_a_separate_worker_process(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            settings = VidXPSettings(
                repository_root=Path(directory),
                runtime_backend="cpu",
                allow_model_downloads=False,
                workflow_poll_interval_seconds=0.01,
                http_auth_mode="static",
                http_static_bearer_token="s" * 32,
            )
            settings.layout.ensure_local_directories()
            database_url = workflow_database_url(settings)
            ready_path = settings.layout.local_workflows / "test.ready"
            lock_path = settings.layout.local_workflows / "test.lock"
            stop_path = settings.layout.local_workflows / "test.stop"
            supervisor = LocalWorkerSupervisor(settings)
            bootstrap = local_worker_bootstrap(settings)
            bootstrap_path = supervisor._write_bootstrap(bootstrap)
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.upper().startswith("VIDXP_")
            }
            environment[LOCAL_WORKER_BOOTSTRAP_ENV] = str(bootstrap_path)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "vidxp.workflow_worker",
                    "--executor-id",
                    local_executor_id(settings),
                    "--role",
                    "all",
                    "--lock-file",
                    str(lock_path),
                    "--ready-file",
                    str(ready_path),
                    "--stop-file",
                    str(stop_path),
                    "--log-file",
                    str(settings.layout.local_workflows / "test.log"),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
            backend = None
            try:
                for _ in range(200):
                    if ready_path.is_file():
                        break
                    if process.poll() is not None:
                        self.fail("The separate worker exited during startup.")
                    sleep(0.05)
                else:
                    self.fail("The separate worker did not become ready.")

                backend = DBOSJobBackend(
                    system_database_url=database_url,
                    application_version=workflow_application_version(),
                )
                jobs = JobService(settings=settings, backend=backend)
                submitted = jobs.submit_prepare_models(
                    PrepareModelsCommand(modalities=())
                )
                with self.assertRaises(ApplicationError):
                    jobs.wait(submitted.job_id)
                completed = jobs.get(submitted.job_id)

                self.assertEqual(completed.state, JobState.failed)
                self.assertEqual(completed.error.code, "invalid_request")
            finally:
                if backend is not None:
                    backend.close()
                stop_path.write_text(
                    LocalWorkerStopRequest(
                        pid=process.pid,
                        application_version=workflow_application_version(),
                    ).model_dump_json(),
                    encoding="utf-8",
                )
                try:
                    process.wait(timeout=35)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
