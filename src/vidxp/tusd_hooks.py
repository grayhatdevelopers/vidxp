from __future__ import annotations

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
)
from vidxp.authentication import Authenticator
from vidxp.authorization import AuthorizationPolicy, RepositoryPermission
from vidxp.infrastructure.tusd_contracts import (
    TusdChangeFileInfo,
    TusdHookRequest,
    TusdHookResponse,
    TusdHTTPResponse,
)
from vidxp.upload_service import RemoteUploadService


def _reject(
    *,
    status: int,
    code: str,
    message: str,
    stop: bool = False,
) -> TusdHookResponse:
    return TusdHookResponse(
        reject_upload=not stop,
        reject_termination=stop,
        http_response=TusdHTTPResponse(
            status_code=status,
            body=message,
            headers={"X-VidXP-Error": code},
        ),
    )


def _status(error: ApplicationError) -> int:
    return {
        ErrorCategory.authentication: 401,
        ErrorCategory.authorization: 403,
        ErrorCategory.not_found: 404,
        ErrorCategory.conflict: 409,
        ErrorCategory.resource_limit: 429,
        ErrorCategory.validation: 400,
        ErrorCategory.unavailable: 503,
    }.get(error.detail.category, 500)


def _authorization(request) -> tuple[str, str] | None:
    values = request.header("authorization")
    if len(values) != 1:
        return None
    scheme, separator, token = values[0].partition(" ")
    if separator != " " or not scheme or not token:
        return None
    return scheme.lower(), token


class TusdHookService:
    """Translate tusd hook events into the upload application service."""

    def __init__(
        self,
        *,
        uploads: RemoteUploadService,
        authenticator: Authenticator,
        authorization: AuthorizationPolicy,
    ) -> None:
        self.uploads = uploads
        self.authenticator = authenticator
        self.authorization = authorization

    def handle(self, hook: TusdHookRequest) -> TusdHookResponse:
        try:
            if hook.hook_type == "pre-create":
                return self._pre_create(hook)
            if hook.hook_type == "post-finish":
                self._post_finish(hook)
                return TusdHookResponse()
            if hook.hook_type == "pre-terminate":
                self._pre_terminate(hook)
                return TusdHookResponse()
            self.uploads.record_terminated(hook.event.upload.upload_id)
            return TusdHookResponse()
        except ApplicationError as exc:
            if hook.hook_type in {"post-finish", "post-terminate"}:
                raise
            return _reject(
                status=_status(exc),
                code=exc.detail.code,
                message=exc.detail.message,
                stop=hook.hook_type == "pre-terminate",
            )

    def _pre_create(self, hook: TusdHookRequest) -> TusdHookResponse:
        upload = hook.event.upload
        if (
            upload.size_is_deferred
            or upload.is_partial
            or upload.is_final
            or upload.partial_uploads
            or set(upload.metadata) != {"intent_id"}
        ):
            raise ApplicationError(
                "upload_protocol_unsupported",
                ErrorCategory.validation,
                "Deferred-length and concatenated uploads are not supported.",
            )
        authorization = _authorization(hook.event.request)
        if authorization is not None and authorization[0] == "vidxp-handoff":
            record = self.uploads.accept_session_creation(
                upload.metadata["intent_id"],
                grant=authorization[1],
                byte_size=upload.size,
            )
        else:
            bearer = (
                authorization[1]
                if authorization is not None and authorization[0] == "bearer"
                else None
            )
            principal = self.authorization.require(
                self.authenticator.authenticate(bearer),
                RepositoryPermission.write,
            )
            record = self.uploads.accept_creation(
                upload.metadata["intent_id"],
                principal=principal,
                byte_size=upload.size,
            )
        assert record.upload_id is not None
        return TusdHookResponse(
            change_file_info=TusdChangeFileInfo(upload_id=record.upload_id)
        )

    def _post_finish(self, hook: TusdHookRequest) -> None:
        upload = hook.event.upload
        intent_id = upload.metadata.get("intent_id")
        if intent_id is None:
            raise ApplicationError(
                "upload_completion_invalid",
                ErrorCategory.validation,
                "The completed upload is missing its intent.",
            )
        self.uploads.complete_tus_transfer(
            intent_id=intent_id,
            upload_id=upload.upload_id,
            byte_size=upload.size,
            offset=upload.offset,
        )

    def _pre_terminate(self, hook: TusdHookRequest) -> None:
        values = hook.event.request.header("x-vidxp-cleanup-token")
        token = values[0] if len(values) == 1 else None
        self.uploads.authorize_termination(
            hook.event.upload.upload_id,
            cleanup_token=token,
        )
