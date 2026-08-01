from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from vidxp.api import create_app
from vidxp.application_models import CreateUploadIntentCommand, Principal
from vidxp.authentication import create_authenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.composition import HttpApplicationContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.job_service import JobService
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService


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
        upload_cors_origin_regex=r"^https://testserver$",
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
    handoff = uploads.create_handoff(
        CreateUploadIntentCommand(
            original_filename="sample.mp4",
            byte_size=5 * 1024 * 1024,
            declared_mime_type="video/mp4",
        ),
        principal=Principal(subject="agent", scopes=frozenset({"*"})),
        request_key="a" * 64,
    )
    return context, catalog, handoff


def _assert_security_headers(response) -> None:
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "connect-src 'self' https://uploads.example" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp


def test_upload_page_bootstrap_session_assets_and_security(tmp_path: Path) -> None:
    context, catalog, handoff = _fixture(tmp_path)
    path = f"/upload-handoff/{handoff.status.intent_id}"

    with TestClient(
        create_app(context=context),
        base_url="https://testserver",
    ) as client:
        page = client.get(path)
        _assert_security_headers(page)
        assert page.status_code == 200
        assert handoff.capability not in page.text
        assert "./assets/upload-page.js" in page.text
        assert "./assets/upload-page.css" in page.text

        script = client.get("/upload-handoff/assets/upload-page.js")
        stylesheet = client.get("/upload-handoff/assets/upload-page.css")
        assert script.status_code == stylesheet.status_code == 200
        assert script.headers["content-type"].startswith("text/javascript")
        assert stylesheet.headers["content-type"].startswith("text/css")

        bootstrap = client.post(
            f"{path}/bootstrap",
            headers={
                "Origin": "https://testserver",
                "Sec-Fetch-Site": "same-origin",
            },
            json={"capability": handoff.capability},
        )
        _assert_security_headers(bootstrap)
        assert bootstrap.status_code == 200
        payload = bootstrap.json()
        assert payload["status"]["original_filename"] == "sample.mp4"
        assert payload["status"]["byte_size"] == 5 * 1024 * 1024
        assert payload["status"]["maximum_bytes"] == 50 * 1024 * 1024 * 1024
        assert payload["creation_url"] == "https://uploads.example/uploads/"
        cookie = bootstrap.headers["set-cookie"]
        assert "__Secure-vidxp-upload=" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=strict" in cookie
        assert f"Path={path}" in cookie

        status = client.get(f"{path}/status")
        assert status.status_code == 200
        assert status.json()["status"]["state"] == "pending"

        grant = client.post(
            f"{path}/creation-grant",
            headers={
                "Origin": "https://testserver",
                "Sec-Fetch-Site": "same-origin",
            },
            json={},
        )
        assert grant.status_code == 200
        assert grant.json()["scheme"] == "VidXP-Handoff"
        assert len(grant.json()["grant"]) >= 64

        protected = client.get(f"/upload-handoff/{handoff.status.intent_id}evil")
        assert protected.status_code == 401
        normal_api = client.get("/api/v1/workspace")
        assert normal_api.status_code == 401

    stored = catalog.get_upload_handoff_by_intent(handoff.status.intent_id)
    assert stored is not None
    assert stored.session_digest is not None
    assert handoff.capability not in stored.model_dump_json()
    catalog.close()


def test_upload_page_rejects_tamper_and_cross_origin_with_headers(
    tmp_path: Path,
) -> None:
    context, catalog, handoff = _fixture(tmp_path)
    path = f"/upload-handoff/{handoff.status.intent_id}/bootstrap"
    header, payload, signature = handoff.capability.split(".")
    tampered = ".".join(
        (
            header,
            payload,
            ("A" if signature[0] != "A" else "B") + signature[1:],
        )
    )

    with TestClient(
        create_app(context=context),
        base_url="https://testserver",
    ) as client:
        wrong_origin = client.post(
            path,
            headers={
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            },
            json={"capability": handoff.capability},
        )
        _assert_security_headers(wrong_origin)
        assert wrong_origin.status_code == 403
        assert wrong_origin.json()["error"]["code"] == (
            "upload_handoff_origin_forbidden"
        )

        invalid = client.post(
            path,
            headers={
                "Origin": "https://testserver",
                "Sec-Fetch-Site": "same-origin",
            },
            json={"capability": tampered},
        )
        _assert_security_headers(invalid)
        assert invalid.status_code == 401
        assert invalid.json()["error"]["code"] == "upload_handoff_invalid"
        assert tampered not in invalid.text

    catalog.close()
