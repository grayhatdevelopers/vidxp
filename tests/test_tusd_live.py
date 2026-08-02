from __future__ import annotations

import base64
import os
import socket
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin

import httpx
import pytest
import uvicorn
from sqlalchemy import update

from vidxp.application_models import (
    CreateUploadFileCommand,
    JobState,
    Principal,
)
from vidxp.authentication import StaticBearerAuthenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.composition import UploadHookContext
from vidxp.core.uploads import UploadState
from vidxp.hook_app import create_hook_app
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.infrastructure.sql_tables import upload_intents
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService


class _Jobs:
    def start(self) -> None:
        pass

    def enqueue_media_import_in_transaction(
        self,
        upload_id: str,
        *,
        connection,
        job_id: str,
    ) -> str:
        del upload_id, connection
        return job_id

    def get(self, job_id: str):
        return SimpleNamespace(
            job_id=job_id,
            state=JobState.queued,
            result=None,
            error=None,
        )

    def close(self) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_http(url: str, *, process: subprocess.Popen[str] | None = None) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"tusd exited during startup ({process.returncode}):\n"
                f"{stdout}\n{stderr}"
            )
        try:
            if httpx.get(url, timeout=0.5).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {url}")


def test_live_tusd_split_topology_resumes_ten_mib(tmp_path: Path) -> None:
    executable_value = os.environ.get("VIDXP_TUSD_EXECUTABLE")
    if not executable_value:
        pytest.skip("set VIDXP_TUSD_EXECUTABLE to run the live tusd integration")
    executable = Path(executable_value)
    if not executable.is_file():
        pytest.fail(f"VIDXP_TUSD_EXECUTABLE is not a file: {executable}")

    hook_port = _free_port()
    tusd_port = _free_port()
    size = 10 * 1024 * 1024
    hook_settings = VidXPSettings(
        repository_root=tmp_path,
        upload_public_endpoint=f"http://127.0.0.1:{tusd_port}/uploads/",
        upload_internal_endpoint=f"http://127.0.0.1:{tusd_port}/uploads/",
        upload_cleanup_token="c" * 32,
        upload_handoff_public_url="https://upload.example/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_cors_origin_regex=r"^(https://upload\.example)$",
        upload_max_bytes=2 * size,
        upload_quota_bytes=2 * size,
        upload_recovery_interval_seconds=3600,
    )
    api_settings = hook_settings.model_copy(
        update={"upload_quarantine_root": tmp_path / "api-has-no-quarantine-volume"}
    )
    database_url = f"sqlite:///{(tmp_path / 'server.sqlite3').resolve().as_posix()}"
    hook_catalog = SQLCatalog(
        database_url,
        initialize=True,
    )
    api_catalog = SQLCatalog(database_url)
    jobs = _Jobs()
    hook_uploads = RemoteUploadService(
        settings=hook_settings,
        catalog=hook_catalog,
        media=object(),
        jobs=jobs,
    )
    api_uploads = RemoteUploadService(
        settings=api_settings,
        catalog=api_catalog,
        media=None,
        jobs=jobs,
    )
    authenticator = StaticBearerAuthenticator("t" * 32)
    authorization = AuthorizationPolicy()
    hook_context = UploadHookContext(
        jobs=jobs,  # type: ignore[arg-type]
        authenticator=authenticator,
        authorization=authorization,
        settings=hook_settings,
        catalog=hook_catalog,
        uploads=hook_uploads,
    )
    hook_server = uvicorn.Server(
        uvicorn.Config(
            create_hook_app(context=hook_context),
            host="127.0.0.1",
            port=hook_port,
            log_level="error",
        )
    )
    hook_thread = threading.Thread(target=hook_server.run, daemon=True)
    hook_thread.start()
    _wait_for_http(f"http://127.0.0.1:{hook_port}/health")
    assert hook_uploads.coordinator._thread is None

    hook_settings.quarantine_root.mkdir(parents=True, exist_ok=True)
    tusd = subprocess.Popen(
        [
            str(executable),
            "-host=127.0.0.1",
            f"-port={tusd_port}",
            f"-upload-dir={hook_settings.quarantine_root}",
            "-base-path=/uploads/",
            f"-max-size={2 * size}",
            "-disable-download",
            "-disable-concatenation",
            f"-hooks-http=http://127.0.0.1:{hook_port}/hooks",
            "-hooks-enabled-events=pre-create",
            "-hooks-http-timeout=5s",
            "-verbose=false",
            "-show-startup-logs=false",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_http(
            f"http://127.0.0.1:{tusd_port}/metrics",
            process=tusd,
        )
        upload_session = api_uploads.create_upload_session(
            principal=Principal(subject="agent", scopes=frozenset({"*"})),
            request_key="a" * 64,
        )
        browser = api_uploads.exchange_upload_session(
            upload_session.status.session_id,
            capability=upload_session.capability,
        )
        authorization = api_uploads.authorize_session_file(
            upload_session.status.session_id,
            CreateUploadFileCommand(
                client_file_key="ten-mib-file",
                original_filename="ten-mib.mp4",
                byte_size=size,
                declared_mime_type="video/mp4",
            ),
            session_token=browser.session_token,
        )
        assert authorization.grant is not None
        metadata = base64.b64encode(authorization.status.intent_id.encode()).decode()

        with httpx.Client(timeout=15) as client:
            created = client.post(
                f"http://127.0.0.1:{tusd_port}/uploads/",
                headers={
                    "Tus-Resumable": "1.0.0",
                    "Upload-Length": str(size),
                    "Upload-Metadata": f"intent_id {metadata}",
                    "Authorization": f"VidXP-Handoff {authorization.grant}",
                },
            )
            assert created.status_code == 201, created.text
            upload_url = urljoin(str(created.url), created.headers["Location"])

            first_chunk = 1024 * 1024
            first_patch = client.patch(
                upload_url,
                headers={
                    "Tus-Resumable": "1.0.0",
                    "Upload-Offset": "0",
                    "Content-Type": "application/offset+octet-stream",
                },
                content=b"a" * first_chunk,
            )
            assert first_patch.status_code == 204, first_patch.text

            paused = client.head(
                upload_url,
                headers={"Tus-Resumable": "1.0.0"},
            )
            assert paused.status_code == 200
            assert paused.headers["Upload-Offset"] == str(first_chunk)

            assert not api_settings.quarantine_root.exists()
            browser_session = api_uploads.browser_session(
                upload_session.status.session_id,
                session_token=browser.session_token,
            )
            assert browser_session.resume_urls["ten-mib-file"] == upload_url

            accepted_record = api_catalog.get_upload_intent(
                authorization.status.intent_id
            )
            assert accepted_record is not None
            with api_catalog.engine.begin() as connection:
                connection.execute(
                    update(upload_intents)
                    .where(upload_intents.c.intent_id == authorization.status.intent_id)
                    .values(
                        expires_at=(
                            accepted_record.created_at + timedelta(microseconds=1)
                        ).isoformat()
                    )
                )
            incomplete = api_uploads.reconcile()
            incomplete_record = api_catalog.get_upload_intent(
                authorization.status.intent_id
            )
            assert incomplete["expired"] == 0
            assert incomplete_record is not None
            assert incomplete_record.state == UploadState.accepted

            unavailable_settings = api_settings.model_copy(
                update={
                    "upload_internal_endpoint": (
                        f"http://127.0.0.1:{_free_port()}/uploads/"
                    )
                }
            )
            unavailable_uploads = RemoteUploadService(
                settings=unavailable_settings,
                catalog=api_catalog,
                media=None,
                jobs=jobs,
            )
            unavailable = unavailable_uploads.reconcile()
            unavailable_record = api_catalog.get_upload_intent(
                authorization.status.intent_id
            )
            assert unavailable["errors"] == 1
            assert unavailable["expired"] == 0
            assert unavailable_record is not None
            assert unavailable_record.state == UploadState.accepted

            resumed = client.patch(
                upload_url,
                headers={
                    "Tus-Resumable": "1.0.0",
                    "Upload-Offset": str(first_chunk),
                    "Content-Type": "application/offset+octet-stream",
                },
                content=b"b" * (size - first_chunk),
            )
            assert resumed.status_code == 204, resumed.text

            completed = client.head(
                upload_url,
                headers={"Tus-Resumable": "1.0.0"},
            )
            assert completed.status_code == 200
            assert completed.headers["Upload-Offset"] == str(size)

            before_recovery = api_catalog.get_upload_intent(
                authorization.status.intent_id
            )
            assert before_recovery is not None
            assert before_recovery.state == UploadState.accepted
            recovered = api_uploads.reconcile()
            assert recovered["recovered"] == 1

            missing_authorization = api_uploads.authorize_session_file(
                upload_session.status.session_id,
                CreateUploadFileCommand(
                    client_file_key="missing-file",
                    original_filename="missing.mp4",
                    byte_size=1024,
                    declared_mime_type="video/mp4",
                ),
                session_token=browser.session_token,
            )
            assert missing_authorization.grant is not None
            accepted_missing = api_uploads.accept_session_creation(
                missing_authorization.status.intent_id,
                grant=missing_authorization.grant,
                byte_size=1024,
            )
            assert accepted_missing.upload_id is not None
            accepted_missing_record = api_catalog.get_upload_intent(
                missing_authorization.status.intent_id
            )
            assert accepted_missing_record is not None
            with api_catalog.engine.begin() as connection:
                connection.execute(
                    update(upload_intents)
                    .where(
                        upload_intents.c.intent_id
                        == missing_authorization.status.intent_id
                    )
                    .values(
                        expires_at=(
                            accepted_missing_record.created_at
                            + timedelta(microseconds=1)
                        ).isoformat()
                    )
                )
            missing = api_uploads.reconcile()
            missing_record = api_catalog.get_upload_intent(
                missing_authorization.status.intent_id
            )
            assert missing["expired"] == 1
            assert missing_record is not None
            assert missing_record.state == UploadState.expired
            assert missing_record.upload_id is None

        record = api_catalog.get_upload_intent(authorization.status.intent_id)

        assert record is not None
        assert record.state == UploadState.processing
        assert record.job_id == record.upload_id
        assert record.upload_id is not None
        assert (hook_settings.quarantine_root / record.upload_id).stat().st_size == size
        info = (hook_settings.quarantine_root / f"{record.upload_id}.info").read_text(
            encoding="utf-8"
        )
        assert authorization.grant not in info
        assert browser.session_token not in info
        assert "Authorization" not in info
    finally:
        tusd.terminate()
        try:
            tusd.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            tusd.kill()
            tusd.communicate(timeout=5)
        hook_server.should_exit = True
        hook_thread.join(timeout=5)
        api_catalog.close()
        hook_catalog.close()
