from __future__ import annotations

from typing import Any

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    ErrorDetail,
    JobState,
    MediaUploadSessionStatus,
    MediaUploadStatus,
)
from vidxp.core.media import utc_now
from vidxp.core.uploads import (
    UploadIntentRecord,
    UploadSessionFileRecord,
    UploadSessionRecord,
    UploadSessionState,
    UploadState,
    UploadTransferBackend,
)
from vidxp.infrastructure.sql_catalog import SQLCatalog


class UploadStatusProjector:
    """Pure read projection over durable upload and job state."""

    def __init__(self, *, catalog: SQLCatalog, jobs: Any | None) -> None:
        self.catalog = catalog
        self.jobs = jobs

    def _job(self, job_id: str | None):
        if self.jobs is None or job_id is None:
            return None
        try:
            return self.jobs.get(job_id)
        except (ApplicationError, KeyError):
            return None

    def file(
        self,
        link: UploadSessionFileRecord,
        intent: UploadIntentRecord,
        *,
        session: UploadSessionRecord,
    ) -> MediaUploadStatus:
        import_job = self._job(intent.job_id)
        index_job = self._job(intent.index_job_id)
        generation_id = None
        snapshot_id = None
        if (
            index_job is not None
            and index_job.state == JobState.succeeded
            and getattr(index_job, "result", None) is not None
        ):
            generation_id = index_job.result.result.generation_id
            snapshot_id = index_job.result.result.snapshot_id
        error = None
        if index_job is not None and getattr(index_job, "error", None) is not None:
            error = index_job.error
        elif import_job is not None and getattr(import_job, "error", None) is not None:
            error = import_job.error
        elif intent.failure_code is not None:
            error = ErrorDetail(
                code=intent.failure_code,
                category=ErrorCategory.validation,
                message=intent.failure_message or "Media ingestion failed.",
            )

        if (
            intent.state == UploadState.pending
            and intent.transfer_backend == UploadTransferBackend.local_path
        ):
            phase = "uploaded"
            status = "The local file is validated and queued for import."
            next_action = "Poll this ingestion status."
        elif (
            intent.state == UploadState.accepted
            and intent.transfer_backend == UploadTransferBackend.multipart
        ):
            phase = "uploaded"
            status = "The multipart body is durably accepted and queued for import."
            next_action = "Poll this ingestion status."
        elif intent.state in {UploadState.pending, UploadState.accepted}:
            phase = "transferring"
            status = "The file is authorized and transferring to VidXP."
            next_action = "Continue the transfer, then poll this ingestion status."
        elif intent.state == UploadState.processing:
            phase = "importing"
            status = "VidXP is validating and registering the video."
            next_action = "Poll this ingestion status; import_job_id is diagnostic."
        elif intent.state == UploadState.ready and not intent.index_after_import:
            phase = "registered"
            status = "The video is registered; automatic indexing was disabled."
            next_action = (
                "Use start_indexing only if the advanced opt-out was unintended."
            )
        elif intent.state == UploadState.indexed:
            phase = "indexed"
            status = "The video is registered, indexed, and searchable."
            next_action = "Use search_moments or query_video."
        elif (
            intent.state == UploadState.ready
            and intent.index_job_id is not None
            and intent.failure_code is not None
        ):
            phase = "index_failed"
            status = (
                "The video is registered, but automatic indexing failed and it "
                "is not searchable."
            )
            next_action = (
                "Inspect the structured index error, fix its cause, then use "
                "start_indexing with this media_id; do not upload the video again."
            )
        elif intent.state == UploadState.ready and intent.index_job_id is not None:
            phase = "indexing"
            status = "The registered video is being indexed."
            next_action = "Poll this ingestion status; index_job_id is diagnostic."
        elif intent.state == UploadState.ready:
            phase = "registered"
            status = "The video is registered and awaiting automatic indexing."
            next_action = "Poll this ingestion status."
        else:
            phase = "failed"
            status = "This file did not complete media ingestion."
            next_action = (
                "Inspect the structured error and retry only this file with a new key."
            )
        terminal = phase in {"indexed", "index_failed", "failed"} or (
            phase == "registered" and not intent.index_after_import
        )
        return MediaUploadStatus(
            intent_id=intent.intent_id,
            client_file_key=link.client_file_key,
            state=intent.state,
            original_filename=intent.original_filename,
            byte_size=intent.byte_size,
            declared_mime_type=intent.declared_mime_type,
            expires_at=intent.expires_at,
            phase=phase,
            transport=session.transfer_backend,
            resumable=session.transfer_backend == UploadTransferBackend.tus,
            job_id=intent.job_id,
            import_job_id=intent.job_id,
            index_job_id=intent.index_job_id,
            media_id=intent.media_id,
            generation_id=generation_id,
            snapshot_id=snapshot_id,
            searchable=phase == "indexed",
            index_after_import=intent.index_after_import,
            index_modalities=intent.index_modalities,
            error=error,
            terminal=terminal,
            poll_after_seconds=0 if terminal else 2,
            status=status,
            next_action=next_action,
        )

    def session(
        self,
        record: UploadSessionRecord,
        *,
        children: tuple[
            tuple[UploadSessionFileRecord, UploadIntentRecord], ...
        ]
        | None = None,
    ) -> MediaUploadSessionStatus:
        children = (
            self.catalog.list_upload_session_files(record.session_id)
            if children is None
            else children
        )
        items = tuple(self.file(link, intent, session=record) for link, intent in children)
        states = tuple(intent.state for _, intent in children)
        failed_states = {UploadState.failed, UploadState.expired}
        uploaded_states = {
            UploadState.processing,
            UploadState.ready,
            UploadState.indexed,
            UploadState.failed,
        }
        reserved_states = {
            UploadState.pending,
            UploadState.accepted,
            UploadState.processing,
            UploadState.failed,
        }
        phases = tuple(item.phase for item in items)
        if not states:
            aggregate_state = "empty"
        elif all(phase in {"registered", "indexed"} for phase in phases):
            aggregate_state = "ready"
        elif all(phase == "index_failed" for phase in phases):
            aggregate_state = "index_failed"
        elif all(phase == "failed" for phase in phases):
            aggregate_state = "failed"
        elif any(phase == "failed" for phase in phases):
            aggregate_state = "partial_failure"
        elif any(phase == "index_failed" for phase in phases):
            aggregate_state = "partial_index_failure"
        elif any(phase == "transferring" for phase in phases):
            aggregate_state = "uploading"
        else:
            aggregate_state = "processing"
        session_state = (
            UploadSessionState.expired
            if record.expires_at <= utc_now()
            else record.state
        )
        reserved = [intent for _, intent in children if intent.state in reserved_states]
        uploaded = [intent for _, intent in children if intent.state in uploaded_states]
        current_work_complete = bool(items) and all(item.terminal for item in items)
        terminal = current_work_complete or (
            session_state != UploadSessionState.open
            and all(item.terminal for item in items)
        )
        status_tool = (
            "get_media_ingestion"
            if record.purpose == "local-media-ingestion"
            else "get_media_upload"
        )
        if session_state == UploadSessionState.open and current_work_complete:
            status = (
                "Current ingestion work is complete; the upload session remains "
                "open for more files."
            )
            next_action = (
                "Stop polling. The user may add more files through the existing "
                "session; poll again after another file is accepted."
            )
        elif session_state == UploadSessionState.open:
            status = "The upload session is open for file selection."
            next_action = (
                "Give the capability link to the user, then poll "
                "get_media_upload for per-file results."
            )
        elif session_state == UploadSessionState.expired:
            status = "The upload session expired."
            next_action = (
                "Create a new upload session; existing processing or ready "
                "child results remain visible."
            )
        elif terminal:
            status = "The ingestion session reached a terminal state."
            next_action = "Use searchable media or inspect per-file failures."
        else:
            status = "The upload session is closed to new files."
            next_action = (
                f"Poll {status_tool} until every successful child is indexed "
                "and searchable."
            )
        return MediaUploadSessionStatus(
            session_id=record.session_id,
            session_state=session_state,
            aggregate_state=aggregate_state,
            transfer_backend=record.transfer_backend,
            resumable=record.transfer_backend == UploadTransferBackend.tus,
            index_after_import=record.index_after_import,
            index_modalities=record.index_modalities,
            expires_at=record.expires_at,
            maximum_files=record.maximum_files,
            maximum_file_bytes=record.maximum_file_bytes,
            maximum_aggregate_bytes=record.maximum_aggregate_bytes,
            file_count=len(children),
            total_bytes=sum(intent.byte_size for _, intent in children),
            reserved_file_count=len(reserved),
            reserved_bytes=sum(intent.byte_size for intent in reserved),
            uploaded_file_count=len(uploaded),
            uploaded_bytes=sum(intent.byte_size for intent in uploaded),
            ready_file_count=sum(
                item.phase in {"registered", "index_failed", "indexed"}
                for item in items
            ),
            searchable_file_count=sum(item.searchable for item in items),
            failed_file_count=sum(state in failed_states for state in states),
            index_failed_file_count=sum(
                item.phase == "index_failed" for item in items
            ),
            items=items,
            terminal=terminal,
            poll_after_seconds=0 if terminal else 2,
            status=status,
            next_action=next_action,
        )
