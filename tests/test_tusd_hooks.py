from __future__ import annotations

from pathlib import Path

from vidxp.application_models import (
    CreateUploadIntentCommand,
    CreateUploadFileCommand,
    Principal,
)
from vidxp.authentication import StaticBearerAuthenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.infrastructure.tusd_contracts import TusdHookRequest
from vidxp.settings import VidXPSettings
from vidxp.tusd_hooks import TusdHookService
from vidxp.upload_service import RemoteUploadService, TusUploadProbe


class _Jobs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def enqueue_media_import_in_transaction(
        self,
        upload_id: str,
        *,
        connection,
        job_id: str,
    ) -> str:
        del connection
        self.calls.append((upload_id, job_id))
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
        upload_handoff_public_url="https://upload.example/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_cors_origin_regex=r"^(https://upload\.example)$",
    )
    uploads = RemoteUploadService(
        settings=settings,
        catalog=catalog,
        media=object(),
        jobs=_Jobs(),
        tusd_upload_probe=lambda upload_id: (
            TusUploadProbe(upload_id=upload_id, length=20, offset=0)
            if (settings.quarantine_root / f"{upload_id}.info").exists()
            else None
        ),
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


def _pre_create(
    intent_id: str,
    token: str,
    *,
    size: int = 20,
    scheme: str = "Bearer",
) -> TusdHookRequest:
    return TusdHookRequest.model_validate(
        {
            "Type": "pre-create",
            "Event": {
                "Upload": {
                    "ID": "",
                    "Size": size,
                    "Offset": 0,
                    "MetaData": {"intent_id": intent_id},
                },
                "HTTPRequest": {
                    "Method": "POST",
                    "URI": "/uploads/",
                    "Header": {"Authorization": [f"{scheme} {token}"]},
                },
            },
        }
    )


def _post_finish(intent_id: str, upload_id: str, *, size: int = 20) -> TusdHookRequest:
    return TusdHookRequest.model_validate(
        {
            "Type": "post-finish",
            "Event": {
                "Upload": {
                    "ID": upload_id,
                    "Size": size,
                    "Offset": size,
                    "MetaData": {"intent_id": intent_id},
                },
                "HTTPRequest": {
                    "Method": "PATCH",
                    "URI": f"/uploads/{upload_id}",
                    "Header": {},
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


def test_post_finish_atomically_links_deterministic_import_job(
    tmp_path: Path,
) -> None:
    hooks, catalog, intent, token = _hooks(tmp_path)
    created = hooks.handle(_pre_create(intent.intent_id, token))
    assert created.change_file_info is not None
    upload_id = created.change_file_info.upload_id

    finished = hooks.handle(_post_finish(intent.intent_id, upload_id))

    assert not finished.reject_upload
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None
    assert stored.state.value == "processing"
    assert stored.upload_id == upload_id
    assert stored.job_id == upload_id
    assert hooks.uploads.jobs.calls == [(upload_id, upload_id)]

    replay = hooks.handle(_post_finish(intent.intent_id, upload_id))
    assert not replay.reject_upload
    assert hooks.uploads.jobs.calls == [(upload_id, upload_id)]
    catalog.close()


def test_pre_create_accepts_session_file_grant_for_five_mib(
    tmp_path: Path,
) -> None:
    hooks, catalog, _, _ = _hooks(tmp_path)
    size = 5 * 1024 * 1024
    session = hooks.uploads.create_upload_session(
        principal=Principal(subject="agent", scopes=frozenset({"*"})),
        request_key="b" * 64,
    )
    browser = hooks.uploads.exchange_upload_session(
        session.status.session_id,
        capability=session.capability,
    )
    authorization = hooks.uploads.authorize_session_file(
        session.status.session_id,
        CreateUploadFileCommand(
            client_file_key="five-mib-file",
            original_filename="five-mib.mp4",
            byte_size=size,
            declared_mime_type="video/mp4",
        ),
        session_token=browser.session_token,
    )
    assert authorization.grant is not None

    response = hooks.handle(
        _pre_create(
            authorization.status.intent_id,
            authorization.grant,
            size=size,
            scheme="VidXP-Handoff",
        )
    )

    assert not response.reject_upload
    stored = catalog.get_upload_intent(authorization.status.intent_id)
    assert stored is not None and stored.state.value == "accepted"
    assert response.change_file_info is not None
    assert authorization.grant not in stored.model_dump_json()

    replay = hooks.handle(
        _pre_create(
            authorization.status.intent_id,
            authorization.grant,
            size=size,
            scheme="VidXP-Handoff",
        )
    )
    assert replay.reject_upload
    assert replay.http_response is not None
    assert replay.http_response.status_code == 409
    assert replay.http_response.headers["X-VidXP-Error"] == (
        "upload_creation_grant_replayed"
    )
    catalog.close()


def test_pre_create_never_falls_back_unknown_authorization_scheme(
    tmp_path: Path,
) -> None:
    hooks, catalog, intent, token = _hooks(tmp_path)

    response = hooks.handle(_pre_create(intent.intent_id, token, scheme="Unknown"))

    assert response.reject_upload
    assert response.http_response is not None
    assert response.http_response.status_code == 401
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
