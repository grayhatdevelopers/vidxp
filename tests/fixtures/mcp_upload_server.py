from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from vidxp.application_models import MediaUploadSessionStatus, Principal
from vidxp.authorization import AuthorizationPolicy
from vidxp.composition import ControlPlaneContext
from vidxp.control_plane import ControlPlaneApplication
from vidxp.core.uploads import UploadSessionState
from vidxp.job_service import JobService
from vidxp.mcp import create_mcp_server
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService, UploadSessionLink


UPLOAD_SESSION_ID = "423456781234423481234567890abcde"


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
    status = MediaUploadSessionStatus(
        session_id=UPLOAD_SESSION_ID,
        session_state=UploadSessionState.open,
        aggregate_state="empty",
        expires_at=now + timedelta(hours=24),
        maximum_files=10,
        maximum_file_bytes=50 * 1024 * 1024 * 1024,
        maximum_aggregate_bytes=100 * 1024 * 1024 * 1024,
        file_count=0,
        total_bytes=0,
        reserved_file_count=0,
        reserved_bytes=0,
        uploaded_file_count=0,
        uploaded_bytes=0,
        ready_file_count=0,
        failed_file_count=0,
        status="No files selected yet.",
        next_action="Open the upload session and select one or more videos.",
    )
    uploads = Mock(spec=RemoteUploadService)
    uploads.create_upload_session.return_value = UploadSessionLink(
        status=status,
        capability="fixture-capability",
    )
    uploads.get_status.return_value = status
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
