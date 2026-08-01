import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from vidxp.api import create_app
from vidxp.application_models import ApplicationError, Artifact
from vidxp.artifact_delivery import (
    ArtifactDownloadCapabilities,
    artifact_binding,
    verified_local_path,
)
from vidxp.authentication import create_authenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.composition import HttpApplicationContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.core.artifacts import ArtifactKind, ArtifactState
from vidxp.job_service import JobService
from vidxp.ports import LocalFileResource
from vidxp.settings import VidXPSettings


ARTIFACT_ID = "323456781234423481234567890abcde"
OTHER_ARTIFACT_ID = "423456781234423481234567890abcde"
MEDIA_ID = "123456781234423481234567890abcde"
PUBLIC_URL = "https://download.example/artifact-download"
SECRET = "d" * 32


def test_missing_local_artifact_uses_stable_delivery_error(tmp_path: Path) -> None:
    with pytest.raises(ApplicationError) as caught:
        verified_local_path(tmp_path / "missing.mp4")

    assert caught.value.detail.code == "local_path_unavailable"


def _artifact(content: bytes, mime_type: str, *, artifact_id: str = ARTIFACT_ID):
    return Artifact(
        artifact_id=artifact_id,
        media_id=MEDIA_ID,
        kind=ArtifactKind.snippet,
        profile="compatible_mp4" if mime_type == "video/mp4" else "source_mkv",
        mime_type=mime_type,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        state=ArtifactState.ready,
        created_at=datetime.now(timezone.utc),
    )


def test_png_evidence_frame_has_a_download_and_resource_binding() -> None:
    artifact = _artifact(b"png", "image/png").model_copy(
        update={"kind": ArtifactKind.evidence_frame, "profile": "png"}
    )
    binding = artifact_binding(artifact)
    assert binding.extension == "png"
    assert binding.filename == f"evidence_frame-{ARTIFACT_ID}.png"


def _context(root: Path, artifact: Artifact, content_path: Path):
    settings = VidXPSettings(
        repository_root=root,
        runtime_backend="cpu",
        http_auth_mode="static",
        http_static_bearer_token="a" * 32,
        http_trusted_hosts=("download.example", "testserver"),
        mcp_allowed_hosts=("download.example", "testserver"),
        artifact_download_public_url=PUBLIC_URL,
        artifact_download_secret=SECRET,
    )
    application = Mock(spec=ControlPlaneApplication)

    def get_artifact(artifact_id: str):
        if artifact_id == artifact.artifact_id:
            return artifact
        return artifact.model_copy(update={"artifact_id": artifact_id})

    application.get_artifact.side_effect = get_artifact
    extension = "mp4" if artifact.mime_type == "video/mp4" else "mkv"
    application.open_artifact_content.return_value = LocalFileResource(
        path=content_path,
        filename=f"snippet-{artifact.artifact_id}.{extension}",
        mime_type=artifact.mime_type,
        byte_size=artifact.byte_size,
        etag=artifact.sha256,
    )
    jobs = Mock(spec=JobService)
    jobs.start.return_value = None
    readiness = Mock()
    readiness.ready.return_value = True
    return HttpApplicationContext(
        application=application,
        jobs=jobs,
        readiness=readiness,
        authenticator=create_authenticator(settings),
        authorization=AuthorizationPolicy(),
        settings=settings,
    )


def _bootstrap(client: TestClient, artifact: Artifact, capability: str):
    return client.post(
        f"/artifact-download/{artifact.artifact_id}/bootstrap",
        headers={
            "Origin": "https://download.example",
            "Sec-Fetch-Site": "same-origin",
        },
        json={"capability": capability},
    )


@pytest.mark.parametrize(
    ("mime_type", "extension"),
    (("video/mp4", "mp4"), ("video/x-matroska", "mkv")),
)
def test_capability_download_supports_full_head_ranges_and_resume(
    mime_type: str,
    extension: str,
):
    content = b"0123456789abcdef"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        content_path = root / f"clip.{extension}"
        content_path.write_bytes(content)
        artifact = _artifact(content, mime_type)
        context = _context(root, artifact, content_path)
        issued = ArtifactDownloadCapabilities(context.settings).issue(artifact)
        parsed = urlsplit(issued.url)
        capability = parse_qs(parsed.fragment)["capability"][0]
        with TestClient(
            create_app(context=context),
            base_url="https://download.example",
        ) as client:
            landing = client.get(f"/artifact-download/{ARTIFACT_ID}")
            stylesheet = client.get(
                "/artifact-download/assets/artifact-download.css"
            )
            logo = client.get("/artifact-download/assets/vidxp-logo.png")
            bootstrap = _bootstrap(client, artifact, capability)
            head = client.head(f"/artifact-download/{ARTIFACT_ID}/content")
            full = client.get(f"/artifact-download/{ARTIFACT_ID}/content")
            cached = client.get(
                f"/artifact-download/{ARTIFACT_ID}/content",
                headers={"If-None-Match": f'"{artifact.sha256}"'},
            )
            first = client.get(
                f"/artifact-download/{ARTIFACT_ID}/content",
                headers={"Range": "bytes=2-5"},
            )
            second = client.get(
                f"/artifact-download/{ARTIFACT_ID}/content",
                headers={"Range": "bytes=6-"},
            )
            unsatisfied = client.get(
                f"/artifact-download/{ARTIFACT_ID}/content",
                headers={"Range": "bytes=99-100"},
            )

    assert parsed.scheme == "https"
    assert parsed.netloc == "download.example"
    assert parsed.query == ""
    assert capability not in landing.text
    assert landing.status_code == 200
    csp = landing.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "img-src 'self'" in csp
    assert "'unsafe-inline'" not in csp
    assert landing.headers["referrer-policy"] == "no-referrer"
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"
    assert bootstrap.status_code == 200
    bootstrap_payload = bootstrap.json()
    assert set(bootstrap_payload) == {
        "content_url",
        "filename",
        "mime_type",
        "byte_size",
        "expires_at",
    }
    assert capability not in bootstrap.text
    assert bootstrap_payload["content_url"].endswith("/content")
    assert bootstrap_payload["filename"] == f"snippet-{ARTIFACT_ID}.{extension}"
    assert bootstrap_payload["mime_type"] == mime_type
    assert bootstrap_payload["byte_size"] == len(content)
    assert "secure" in bootstrap.headers["set-cookie"].lower()
    assert "httponly" in bootstrap.headers["set-cookie"].lower()
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(content))
    assert head.headers["content-type"] == mime_type
    assert head.headers["accept-ranges"] == "bytes"
    assert head.headers["etag"] == f'"{artifact.sha256}"'
    assert full.status_code == 200
    assert full.content == content
    assert f"snippet-{ARTIFACT_ID}.{extension}" in full.headers[
        "content-disposition"
    ]
    assert cached.status_code == 304
    assert cached.content == b""
    assert first.status_code == 206
    assert first.content == content[2:6]
    assert first.headers["content-range"] == f"bytes 2-5/{len(content)}"
    assert second.status_code == 206
    assert second.content == content[6:]
    assert unsatisfied.status_code == 416
    assert unsatisfied.headers["content-range"] == f"bytes */{len(content)}"


def test_capability_download_rejects_missing_auth_tampering_and_wrong_artifact():
    content = b"protected-artifact"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "clip.mp4"
        path.write_bytes(content)
        artifact = _artifact(content, "video/mp4")
        context = _context(root, artifact, path)
        issued = ArtifactDownloadCapabilities(context.settings).issue(artifact)
        capability = parse_qs(urlsplit(issued.url).fragment)["capability"][0]
        with TestClient(
            create_app(context=context),
            base_url="https://download.example",
        ) as client:
            missing = client.get(f"/artifact-download/{ARTIFACT_ID}/content")
            header, payload, signature = capability.split(".")
            replacement = "A" if signature[0] != "A" else "B"
            tampered_capability = ".".join(
                (header, payload, replacement + signature[1:])
            )
            tampered = _bootstrap(client, artifact, tampered_capability)
            wrong = client.post(
                f"/artifact-download/{OTHER_ARTIFACT_ID}/bootstrap",
                headers={
                    "Origin": "https://download.example",
                    "Sec-Fetch-Site": "same-origin",
                },
                json={"capability": capability},
            )
            traversal = client.get("/artifact-download/../../etc/passwd")

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "artifact_download_capability_invalid"
    assert tampered.status_code == 401
    assert tampered.json()["error"]["code"] == "artifact_download_capability_invalid"
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "artifact_download_binding_mismatch"
    assert traversal.status_code in {401, 404}
    context.application.open_artifact_content.assert_not_called()


def test_capability_download_rejects_expired_and_unsupported_artifacts():
    content = b"expired-artifact"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "clip.mp4"
        path.write_bytes(content)
        artifact = _artifact(content, "video/mp4")
        context = _context(root, artifact, path)
        old_now = datetime.now(timezone.utc) - timedelta(hours=1)
        with patch("vidxp.artifact_delivery.utc_now", return_value=old_now):
            issued = ArtifactDownloadCapabilities(context.settings).issue(artifact)
        capability = parse_qs(urlsplit(issued.url).fragment)["capability"][0]
        with TestClient(
            create_app(context=context),
            base_url="https://download.example",
        ) as client:
            expired = _bootstrap(client, artifact, capability)
            unsupported_artifact = artifact.model_copy(
                update={"mime_type": "video/webm"}
            )
            unsupported_context = replace(
                context,
                application=Mock(spec=ControlPlaneApplication),
            )
            unsupported_context.application.get_artifact.return_value = (
                unsupported_artifact
            )
            with TestClient(
                create_app(context=unsupported_context),
                base_url="https://download.example",
            ) as unsupported_client:
                unsupported = unsupported_client.post(
                    f"/artifact-download/{ARTIFACT_ID}/bootstrap",
                    headers={
                        "Origin": "https://download.example",
                        "Sec-Fetch-Site": "same-origin",
                    },
                    json={"capability": capability},
                )

    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "artifact_download_capability_expired"
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "artifact_type_unsupported"


def test_public_download_requires_no_api_bearer_and_rejects_cross_origin_exchange():
    content = b"no-login-required"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "clip.mp4"
        path.write_bytes(content)
        artifact = _artifact(content, "video/mp4")
        context = _context(root, artifact, path)
        issued = ArtifactDownloadCapabilities(context.settings).issue(artifact)
        capability = parse_qs(urlsplit(issued.url).fragment)["capability"][0]
        with TestClient(
            create_app(context=context),
            base_url="https://download.example",
        ) as client:
            accepted = _bootstrap(client, artifact, capability)
            case_normalized = client.post(
                f"/artifact-download/{ARTIFACT_ID}/bootstrap",
                headers={
                    "Origin": "https://DOWNLOAD.EXAMPLE",
                    "Sec-Fetch-Site": "same-origin",
                },
                json={"capability": capability},
            )
            forbidden = client.post(
                f"/artifact-download/{ARTIFACT_ID}/bootstrap",
                headers={
                    "Origin": "https://attacker.example",
                    "Sec-Fetch-Site": "cross-site",
                },
                json={"capability": capability},
            )

    assert accepted.status_code == 200
    assert case_normalized.status_code == 200
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "artifact_download_origin_forbidden"
