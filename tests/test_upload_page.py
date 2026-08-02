from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from vidxp.api import create_app
from vidxp.application_models import Principal
from vidxp.authentication import create_authenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.composition import HttpApplicationContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.job_service import JobService
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService, TusUploadProbe


def _fixture(root: Path):
    settings = VidXPSettings(
        repository_root=root,
        runtime_backend="cpu",
        http_auth_mode="static",
        http_static_bearer_token="a" * 32,
        http_trusted_hosts=("testserver",),
        mcp_allowed_hosts=("testserver",),
        upload_public_endpoint="https://uploads.example/uploads/",
        upload_internal_endpoint="http://tusd:8080/uploads/",
        upload_cleanup_token="c" * 32,
        upload_handoff_public_url="https://testserver/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_cors_origin_regex=r"^(https://testserver)$",
        upload_max_bytes=20 * 1024 * 1024,
        upload_quota_bytes=100 * 1024 * 1024,
        upload_session_max_files=4,
        upload_session_max_bytes=60 * 1024 * 1024,
    )
    catalog = SQLCatalog(
        f"sqlite:///{(root / 'server.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    jobs = Mock(spec=JobService)
    uploads = RemoteUploadService(
        settings=settings,
        catalog=catalog,
        media=object(),
        jobs=jobs,
        tusd_upload_probe=lambda upload_id: (
            TusUploadProbe(upload_id=upload_id, length=20, offset=0)
            if (settings.quarantine_root / f"{upload_id}.info").exists()
            else None
        ),
    )
    application = Mock(spec=ControlPlaneApplication)
    readiness = Mock()
    readiness.ready.return_value = True
    context = HttpApplicationContext(
        application=application,
        jobs=jobs,
        authorization=AuthorizationPolicy(),
        settings=settings,
        catalog=catalog,
        uploads=uploads,
        readiness=readiness,
        authenticator=create_authenticator(settings),
    )
    session = uploads.create_upload_session(
        principal=Principal(
            subject="agent",
            client_id="mcp-client",
            scopes=frozenset({"*"}),
        ),
        request_key="a" * 64,
    )
    return context, catalog, uploads, session


def _assert_security_headers(response) -> None:
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "style-src-attr 'unsafe-inline'" in csp
    assert "connect-src 'self' https://uploads.example" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp


def _same_origin_headers() -> dict[str, str]:
    return {
        "Origin": "https://testserver",
        "Sec-Fetch-Site": "same-origin",
    }


def test_capability_bootstrap_multi_file_and_reload_recovery(tmp_path: Path) -> None:
    context, catalog, uploads, session = _fixture(tmp_path)
    path = f"/upload-handoff/{session.status.session_id}"

    with TestClient(create_app(context=context), base_url="https://testserver") as client:
        page = client.get(path)
        _assert_security_headers(page)
        assert page.status_code == 200
        assert session.capability not in page.text
        assert "Upload your videos" in page.text
        assert "OIDC access token" not in page.text

        script = client.get("/upload-handoff/assets/upload-page.js")
        stylesheet = client.get("/upload-handoff/assets/upload-page.css")
        assert script.status_code == stylesheet.status_code == 200
        assert b"parallelUploads:1" in script.content.replace(b" ", b"")
        assert b"./authenticate" not in script.content
        assert b"OIDC access token" not in script.content

        bootstrap = client.post(
            f"{path}/bootstrap",
            headers=_same_origin_headers(),
            json={"capability": session.capability},
        )
        _assert_security_headers(bootstrap)
        assert bootstrap.status_code == 200
        assert bootstrap.json()["status"]["file_count"] == 0
        cookie = bootstrap.headers["set-cookie"]
        assert "__Secure-vidxp-upload=" in cookie
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie

        first = client.post(
            f"{path}/files",
            headers=_same_origin_headers(),
            json={
                "client_file_key": "browser-file-a",
                "original_filename": "same.mp4",
                "byte_size": 10 * 1024 * 1024,
                "declared_mime_type": "video/mp4",
            },
        )
        second = client.post(
            f"{path}/files",
            headers=_same_origin_headers(),
            json={
                "client_file_key": "browser-file-b",
                "original_filename": "same.mp4",
                "byte_size": 9 * 1024 * 1024,
                "declared_mime_type": "video/mp4",
            },
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["status"]["intent_id"] != second.json()["status"]["intent_id"]
        assert first.json()["grant"]

        replay = client.post(
            f"{path}/files",
            headers=_same_origin_headers(),
            json={
                "client_file_key": "browser-file-a",
                "original_filename": "same.mp4",
                "byte_size": 10 * 1024 * 1024,
                "declared_mime_type": "video/mp4",
            },
        )
        assert replay.status_code == 200
        assert replay.json()["status"]["intent_id"] == first.json()["status"]["intent_id"]

        accepted = uploads.accept_session_creation(
            first.json()["status"]["intent_id"],
            grant=replay.json()["grant"],
            byte_size=10 * 1024 * 1024,
        )
        assert accepted.upload_id is not None
        context.settings.quarantine_root.mkdir(parents=True, exist_ok=True)
        (context.settings.quarantine_root / f"{accepted.upload_id}.info").write_text(
            "{}",
            encoding="utf-8",
        )

        refreshed = client.get(f"{path}/status")
        assert refreshed.status_code == 200
        assert refreshed.json()["status"]["file_count"] == 2
        assert refreshed.json()["resume_urls"]["browser-file-a"].endswith(
            accepted.upload_id
        )

    stored = catalog.get_upload_session(session.status.session_id)
    assert stored is not None
    assert session.capability not in stored.model_dump_json()
    catalog.close()


def test_page_rejects_tamper_cross_origin_and_conflicting_replay(tmp_path: Path) -> None:
    context, catalog, _, session = _fixture(tmp_path)
    path = f"/upload-handoff/{session.status.session_id}"
    header, payload, signature = session.capability.split(".")
    tampered = ".".join(
        (header, payload, ("A" if signature[0] != "A" else "B") + signature[1:])
    )

    with TestClient(create_app(context=context), base_url="https://testserver") as client:
        wrong_origin = client.post(
            f"{path}/bootstrap",
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
            json={"capability": session.capability},
        )
        assert wrong_origin.status_code == 403

        invalid = client.post(
            f"{path}/bootstrap",
            headers=_same_origin_headers(),
            json={"capability": tampered},
        )
        assert invalid.status_code == 401
        assert invalid.json()["error"]["code"] == "upload_session_capability_invalid"
        assert tampered not in invalid.text

        opened = client.post(
            f"{path}/bootstrap",
            headers=_same_origin_headers(),
            json={"capability": session.capability},
        )
        assert opened.status_code == 200
        created = client.post(
            f"{path}/files",
            headers=_same_origin_headers(),
            json={
                "client_file_key": "stable-key",
                "original_filename": "first.mp4",
                "byte_size": 1024,
                "declared_mime_type": "video/mp4",
            },
        )
        assert created.status_code == 200
        conflict = client.post(
            f"{path}/files",
            headers=_same_origin_headers(),
            json={
                "client_file_key": "stable-key",
                "original_filename": "other.mp4",
                "byte_size": 1024,
                "declared_mime_type": "video/mp4",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "upload_client_key_conflict"

        closed = client.post(
            f"{path}/close",
            headers=_same_origin_headers(),
            json={},
        )
        assert closed.status_code == 200
        assert closed.json()["session_state"] == "closed"

    catalog.close()
