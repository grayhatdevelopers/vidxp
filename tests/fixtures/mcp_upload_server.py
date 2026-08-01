from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from vidxp.application_models import MediaUploadStatus, Principal
from vidxp.authorization import AuthorizationPolicy
from vidxp.composition import ControlPlaneContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.core.uploads import UploadState
from vidxp.job_service import JobService
from vidxp.mcp import create_mcp_server
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService, UploadHandoff


MEDIA_ID = "123456781234423481234567890abcde"


def main() -> None:
    settings = VidXPSettings(
        runtime_backend="cpu",
        http_auth_mode="oidc",
        http_oidc_issuer="https://identity.example",
        http_oidc_audience="vidxp-api",
        http_oidc_jwks_url="https://identity.example/jwks",
        http_required_scopes=("vidxp.write",),
        mcp_public_url="https://vidxp.example/mcp",
        upload_public_endpoint="https://uploads.example/uploads/",
        upload_internal_endpoint="http://tusd:8080/uploads/",
        upload_cleanup_token="c" * 32,
        upload_handoff_public_url="https://vidxp.example/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_cors_origin_regex=r"^(https://vidxp\.example)$",
    )
    now = datetime.now(timezone.utc)
    status = MediaUploadStatus(
        intent_id=MEDIA_ID,
        state=UploadState.pending,
        original_filename="sample.mp4",
        byte_size=5 * 1024 * 1024,
        declared_mime_type="video/mp4",
        maximum_bytes=50 * 1024 * 1024 * 1024,
        expires_at=now + timedelta(hours=24),
        status="Waiting for the expected video to be selected.",
        next_action="Open the upload page.",
    )
    uploads = Mock(spec=RemoteUploadService)
    uploads.create_handoff.return_value = UploadHandoff(
        status=status,
        capability="fixture-capability",
        expires_at=now + timedelta(minutes=15),
    )
    uploads.get_status.return_value = status.model_copy(
        update={
            "state": UploadState.processing,
            "status": "The upload completed and import started.",
            "next_action": "Poll the import job.",
        }
    )
    context = ControlPlaneContext(
        application=Mock(spec=ControlPlaneApplication),
        jobs=Mock(spec=JobService),
        authorization=AuthorizationPolicy(),
        settings=settings,
        uploads=uploads,
    )
    server = create_mcp_server(
        context,
        default_principal=Principal(
            subject="stdio-test",
            client_id="stdio-client",
            scopes=frozenset({"*"}),
        ),
    )
    server.run("stdio")


if __name__ == "__main__":
    main()
