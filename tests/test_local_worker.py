import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from unittest.mock import Mock, patch

from filelock import Timeout
from vidxp.application_models import (
    ApplicationError,
    JobState,
    PrepareModelsCommand,
)
from vidxp.infrastructure.dbos_jobs import DBOSJobBackend
from vidxp.infrastructure.local_worker import LocalWorkerSupervisor
from vidxp.job_service import JobService
from vidxp.settings import VidXPSettings
from vidxp.workflow_runtime import (
    LOCAL_WORKER_SETTINGS_ENV,
    local_executor_id,
    workflow_application_version,
    workflow_database_url,
)


class LocalWorkerSupervisorTests(unittest.TestCase):
    def test_worker_is_spawned_detached_without_job_state(self):
        with TemporaryDirectory() as directory:
            settings = VidXPSettings(
                repository_root=Path(directory),
                runtime_backend="cpu",
            )
            supervisor = LocalWorkerSupervisor(settings)
            with (
                patch(
                    "vidxp.infrastructure.local_worker.subprocess.Popen"
                ) as popen,
                patch.object(supervisor, "_wait_for_worker") as wait,
            ):
                supervisor.ensure_running()

        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(command[:3], [sys.executable, "-m", "vidxp.workflow_worker"])
        self.assertIn("--lock-file", command)
        lock_file = command[command.index("--lock-file") + 1]
        self.assertIn(workflow_application_version(), lock_file)
        self.assertNotIn("--settings-json", command)
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertIs(options["stdout"], subprocess.DEVNULL)
        worker_settings = VidXPSettings.model_validate_json(
            options["env"][LOCAL_WORKER_SETTINGS_ENV]
        )
        self.assertEqual(
            worker_settings.repository_root,
            settings.repository_root,
        )
        wait.assert_called_once()
        if sys.platform == "win32":
            self.assertTrue(
                options["creationflags"] & subprocess.DETACHED_PROCESS
            )
        else:
            self.assertTrue(options["start_new_session"])

    def test_startup_wait_finishes_when_worker_owns_lock(self):
        with TemporaryDirectory() as directory:
            ready_path = Path(directory) / "worker.ready"
            ready_path.write_text("123", encoding="utf-8")
            process = Mock()
            process.poll.return_value = None
            worker_lock = Mock()
            worker_lock.acquire.side_effect = Timeout("worker.lock")

            LocalWorkerSupervisor._wait_for_worker(
                process,
                worker_lock,
                ready_path,
            )

        worker_lock.acquire.assert_called_once_with(timeout=0)

    def test_fresh_database_runs_a_job_in_a_separate_worker_process(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            settings = VidXPSettings(
                repository_root=Path(directory),
                runtime_backend="cpu",
                allow_model_downloads=False,
                workflow_poll_interval_seconds=0.01,
            )
            settings.layout.ensure_local_directories()
            database_url = workflow_database_url(settings)
            ready_path = settings.layout.local_workflows / "test.ready"
            lock_path = settings.layout.local_workflows / "test.lock"
            environment = os.environ.copy()
            environment[LOCAL_WORKER_SETTINGS_ENV] = (
                settings.model_dump_json()
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "vidxp.workflow_worker",
                    "--database-url",
                    database_url,
                    "--executor-id",
                    local_executor_id(settings),
                    "--role",
                    "all",
                    "--lock-file",
                    str(lock_path),
                    "--ready-file",
                    str(ready_path),
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
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
