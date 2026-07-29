from __future__ import annotations

from pathlib import Path

from vidxp.application_models import (
    CreateUploadIntentCommand,
    Principal,
)
from vidxp.authentication import StaticBearerAuthenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.infrastructure.tusd_contracts import TusdHookRequest
from vidxp.settings import VidXPSettings
from vidxp.tusd_hooks import TusdHookService
from vidxp.upload_service import RemoteUploadService


class _Jobs:
    def enqueue_media_import_in_transaction(
        self,
        upload_id: str,
        *,
        connection,
        job_id: str,
    ) -> str:
        del upload_id, connection
        return job_id


def _hooks(tmp_path: Path):
    token = "t" * 32
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'server.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    settings = VidXPSettings(
        repository_root=tmp_path,
        upload_public_endpoint="http://localhost:8080/uploads/",
        upload_internal_endpoint="http://localhost:8080/uploads/",
        upload_cleanup_token="c" * 32,
    )
    uploads = RemoteUploadService(
        settings=settings,
        catalog=catalog,
        media=object(),
        jobs=_Jobs(),
    )
    intent = uploads.create_intent(
        CreateUploadIntentCommand(
            original_filename="sample.mp4",
            byte_size=20,
            declared_mime_type="video/mp4",
        ),
        principal=Principal(
            subject="static",
            scopes=frozenset({"*"}),
        ),
        request_key="a" * 64,
    )
    return (
        TusdHookService(
            uploads=uploads,
            authenticator=StaticBearerAuthenticator(token),
            authorization=AuthorizationPolicy(),
        ),
        catalog,
        intent,
        token,
    )


def _pre_create(intent_id: str, token: str) -> TusdHookRequest:
    return TusdHookRequest.model_validate(
        {
            "Type": "pre-create",
            "Event": {
                "Upload": {
                    "ID": "",
                    "Size": 20,
                    "Offset": 0,
                    "MetaData": {"intent_id": intent_id},
                },
                "HTTPRequest": {
                    "Method": "POST",
                    "URI": "/uploads/",
                    "Header": {"Authorization": [f"Bearer {token}"]},
                },
            },
        }
    )


def test_pre_create_authenticates_and_assigns_stored_id(
    tmp_path: Path,
) -> None:
    hooks, catalog, intent, token = _hooks(tmp_path)
    response = hooks.handle(_pre_create(intent.intent_id, token))

    assert not response.reject_upload
    assert response.change_file_info is not None
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None
    assert response.change_file_info.upload_id == stored.upload_id
    catalog.close()


def test_pre_create_rejects_unsupported_tus_modes(tmp_path: Path) -> None:
    hooks, catalog, intent, token = _hooks(tmp_path)
    request = _pre_create(intent.intent_id, token).model_copy(
        update={
            "event": _pre_create(intent.intent_id, token).event.model_copy(
                update={
                    "upload": _pre_create(
                        intent.intent_id,
                        token,
                    ).event.upload.model_copy(
                        update={"size_is_deferred": True}
                    )
                }
            )
        }
    )

    response = hooks.handle(request)

    assert response.reject_upload
    assert response.http_response is not None
    assert response.http_response.status_code == 400
    catalog.close()


def test_pre_create_retry_reuses_id_then_rejects_new_materialized_upload(
    tmp_path: Path,
) -> None:
    hooks, catalog, intent, token = _hooks(tmp_path)
    first = hooks.handle(_pre_create(intent.intent_id, token))
    replay = hooks.handle(_pre_create(intent.intent_id, token))
    assert first.change_file_info == replay.change_file_info
    assert first.change_file_info is not None

    upload_id = first.change_file_info.upload_id
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None
    quarantine = tmp_path / "upload-quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    (quarantine / f"{upload_id}.info").write_text("{}", encoding="utf-8")

    duplicate = hooks.handle(_pre_create(intent.intent_id, token))
    assert duplicate.reject_upload
    assert duplicate.http_response is not None
    assert duplicate.http_response.status_code == 409
    catalog.close()
