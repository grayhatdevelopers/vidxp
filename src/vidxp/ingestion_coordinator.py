from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.engine import Connection

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    ErrorCategory,
    ErrorDetail,
    ImportMediaCommand,
    JobState,
)
from vidxp.core.uploads import UploadIntentRecord, UploadState, UploadTransferBackend
from vidxp.infrastructure.sql_catalog import SQLCatalog


LOGGER = logging.getLogger(__name__)


def derived_ingestion_job_id(intent_id: str, operation: str) -> str:
    payload = bytearray(
        hashlib.sha256(
            f"vidxp-ingestion-v1\0{intent_id}\0{operation}".encode()
        ).digest()[:16]
    )
    payload[6] = (payload[6] & 0x0F) | 0x40
    payload[8] = (payload[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(payload)).hex


class IngestionCoordinator:
    """Sole owner of import-to-index transitions and deterministic recovery."""

    def __init__(
        self,
        *,
        catalog: SQLCatalog,
        jobs: Any | None,
        interval_seconds: float,
        shutdown_timeout_seconds: float | None = None,
    ) -> None:
        self.catalog = catalog
        self.jobs = jobs
        self.interval_seconds = interval_seconds
        self.shutdown_timeout_seconds = (
            max(5.0, interval_seconds + 1.0)
            if shutdown_timeout_seconds is None
            else shutdown_timeout_seconds
        )
        self._wake = Event()
        self._stop = Event()
        self._lifecycle_lock = Lock()
        self._thread: Thread | None = None
        self._sweep: Callable[[], Any] | None = None

    def start(self, sweep: Callable[[], Any]) -> None:
        with self._lifecycle_lock:
            if self._thread is not None:
                if self._thread.is_alive():
                    return
                self._thread = None
            self._sweep = sweep
            self._stop.clear()
            self._wake.set()
            self._thread = Thread(
                target=self._run,
                name="vidxp-ingestion-coordinator",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            self._wake.set()
        thread.join(timeout=self.shutdown_timeout_seconds)
        with self._lifecycle_lock:
            if thread.is_alive():
                raise RuntimeError(
                    "The media-ingestion coordinator did not stop before the "
                    "shutdown timeout."
                )
            if self._thread is thread:
                self._thread = None
                self._sweep = None

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                if self._sweep is not None:
                    self._sweep()
            except Exception:
                LOGGER.exception("The media-ingestion coordinator sweep failed.")
            self._wake.wait(self.interval_seconds)

    def run_once(self) -> dict[str, int]:
        advanced = 0
        errors = 0
        if self.jobs is None:
            return {"advanced": advanced, "errors": errors}
        for record in self.catalog.active_ingestions():
            try:
                updated = self.advance(record)
                advanced += int(updated != record)
            except Exception:
                errors += 1
                LOGGER.exception(
                    "Media ingestion advancement failed for intent %s.",
                    record.intent_id,
                )
        return {"advanced": advanced, "errors": errors}

    def advance(self, intent: UploadIntentRecord) -> UploadIntentRecord:
        if self.jobs is None:
            return intent
        if (
            intent.state == UploadState.pending
            and intent.transfer_backend == UploadTransferBackend.local_path
        ) or (
            intent.state == UploadState.accepted
            and intent.transfer_backend == UploadTransferBackend.multipart
        ):
            # Link and submit once per sweep. If submission is interrupted after
            # the durable link, the next sweep recovers the same deterministic
            # job ID instead of issuing a second attempt immediately.
            return self._start_import(intent)
        if intent.state == UploadState.processing and intent.job_id is not None:
            try:
                job = self.jobs.get(intent.job_id)
            except ApplicationError as exc:
                if exc.detail.code != "resource_not_found":
                    if self.is_retryable(exc.detail):
                        return intent
                    return self._fail_import(intent, exc.detail)
                try:
                    job = self._submit_import_job(intent)
                except ApplicationError as submit_exc:
                    if self.is_retryable(submit_exc.detail):
                        return intent
                    return self._fail_import(intent, submit_exc.detail)
            if job.state == JobState.succeeded and job.result is not None:
                media_id = job.result.result.media_id

                def register(connection: Connection) -> bool:
                    return self.catalog.update_upload(
                        intent.intent_id,
                        state=UploadState.ready,
                        connection=connection,
                        media_id=media_id,
                        expected_states={UploadState.processing},
                        expected_job_id=intent.job_id,
                    )

                self.catalog.with_upload_transaction(register)
                intent = self.catalog.get_upload_intent(intent.intent_id) or intent
            elif job.state in {
                JobState.failed,
                JobState.cancelled,
                JobState.recovery_exhausted,
            }:
                detail = job.error or ErrorDetail(
                    code="media_import_failed",
                    category=ErrorCategory.internal,
                    message="The durable media import failed.",
                )
                intent = self._fail_import(intent, detail)
        if (
            intent.state == UploadState.ready
            and intent.index_after_import
            and intent.index_job_id is None
        ):
            return self._start_index(intent)
        if (
            intent.state == UploadState.ready
            and intent.index_job_id is not None
            and intent.failure_code is None
        ):
            try:
                job = self.jobs.get(intent.index_job_id)
            except ApplicationError as exc:
                if exc.detail.code == "resource_not_found":
                    try:
                        job = self._submit_index_job(intent)
                    except ApplicationError as submit_exc:
                        if self.is_retryable(submit_exc.detail):
                            return intent
                        return self._fail_index(intent, submit_exc.detail)
                elif self.is_retryable(exc.detail):
                    return intent
                else:
                    return self._fail_index(intent, exc.detail)
            if job.state == JobState.succeeded:
                self.catalog.with_upload_transaction(
                    lambda connection: self.catalog.update_upload(
                        intent.intent_id,
                        state=UploadState.indexed,
                        connection=connection,
                        expected_states={UploadState.ready},
                        expected_index_job_id=intent.index_job_id,
                    )
                )
                intent = self.catalog.get_upload_intent(intent.intent_id) or intent
            elif job.state in {
                JobState.failed,
                JobState.cancelled,
                JobState.recovery_exhausted,
            }:
                detail = job.error or self._index_terminal_detail(job.state)
                intent = self._fail_index(intent, detail)
        return intent

    def complete_tus_transfer(
        self,
        *,
        intent_id: str,
        upload_id: str,
        byte_size: int,
        offset: int,
    ) -> str:
        if self.jobs is None:
            raise RuntimeError("Upload job submission is not configured.")

        def complete(connection: Connection) -> str:
            record = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            if record is None or record.upload_id != upload_id:
                raise ApplicationError(
                    "upload_completion_invalid",
                    ErrorCategory.validation,
                    "The completed upload does not match its intent.",
                )
            if record.transfer_backend != UploadTransferBackend.tus:
                raise ApplicationError(
                    "upload_completion_invalid",
                    ErrorCategory.conflict,
                    "Only a tus transfer can be completed through the tus hook.",
                )
            if byte_size != record.byte_size or offset != record.byte_size:
                raise ApplicationError(
                    "upload_incomplete",
                    ErrorCategory.validation,
                    "The upload is not complete.",
                )
            if record.job_id is not None:
                return record.job_id
            if record.state != UploadState.accepted:
                raise ApplicationError(
                    "upload_completion_invalid",
                    ErrorCategory.conflict,
                    "The upload is not ready for completion.",
                )
            job_id = self.jobs.enqueue_media_import_in_transaction(
                upload_id,
                connection=connection,
                job_id=upload_id,
            )
            self.catalog.update_upload(
                record.intent_id,
                state=UploadState.processing,
                connection=connection,
                job_id=job_id,
            )
            return job_id

        result = self.catalog.with_upload_transaction(complete)
        self.wake()
        return result

    def _start_import(self, intent: UploadIntentRecord) -> UploadIntentRecord:
        if self.jobs is None:
            return intent
        expected_state = (
            UploadState.pending
            if intent.transfer_backend == UploadTransferBackend.local_path
            else UploadState.accepted
        )
        job_id = derived_ingestion_job_id(intent.intent_id, "import")

        def link(connection: Connection) -> bool:
            return self.catalog.update_upload(
                intent.intent_id,
                state=UploadState.processing,
                connection=connection,
                upload_id=intent.upload_id or intent.intent_id,
                job_id=job_id,
                expected_states={expected_state},
                expected_job_id=None,
            )

        linked_by_this_attempt = self.catalog.with_upload_transaction(link)
        linked = self.catalog.get_upload_intent(intent.intent_id) or intent
        if not linked_by_this_attempt:
            return linked
        try:
            self._submit_import_job(linked)
        except ApplicationError as exc:
            if not self.is_retryable(exc.detail):
                return self._fail_import(linked, exc.detail)
        return linked

    def _start_index(self, intent: UploadIntentRecord) -> UploadIntentRecord:
        if self.jobs is None:
            return intent
        job_id = derived_ingestion_job_id(intent.intent_id, "index")
        command = CreateIndexCommand(
            media_id=intent.media_id or "",
            modalities=intent.index_modalities,
        )

        def link(connection: Connection) -> bool:
            return self.catalog.update_upload(
                intent.intent_id,
                state=UploadState.ready,
                connection=connection,
                index_job_id=job_id,
                index_command=command.model_dump(mode="json"),
                expected_states={UploadState.ready},
                expected_index_job_id=None,
            )

        linked_by_this_attempt = self.catalog.with_upload_transaction(link)
        linked = self.catalog.get_upload_intent(intent.intent_id) or intent
        if not linked_by_this_attempt:
            return linked
        try:
            self._submit_index_job(linked)
        except ApplicationError as exc:
            if not self.is_retryable(exc.detail):
                return self._fail_index(linked, exc.detail)
        return linked

    def _submit_import_job(self, intent: UploadIntentRecord):
        if self.jobs is None or intent.job_id is None:
            raise RuntimeError("Media import job submission is not configured.")
        if intent.transfer_backend == UploadTransferBackend.local_path:
            if intent.source_path is None:
                raise RuntimeError("The local ingestion source is unavailable.")
            return self.jobs.submit_local_media_import(
                ImportMediaCommand(
                    path=Path(intent.source_path),
                    original_filename=intent.original_filename,
                    declared_mime_type=intent.declared_mime_type,
                ),
                job_id=intent.job_id,
            )
        if intent.upload_id is None:
            raise RuntimeError("The completed upload identifier is unavailable.")
        return self.jobs.submit_completed_media_import(
            intent.upload_id,
            job_id=intent.job_id,
        )

    def _submit_index_job(self, intent: UploadIntentRecord):
        if self.jobs is None or intent.index_job_id is None:
            raise RuntimeError("Index job submission is not configured.")
        return self.jobs.submit_index(
            CreateIndexCommand.model_validate(intent.index_command)
            if intent.index_command is not None
            else CreateIndexCommand(
                media_id=intent.media_id or "",
                modalities=intent.index_modalities,
            ),
            job_id=intent.index_job_id,
        )

    @staticmethod
    def is_retryable(detail: ErrorDetail) -> bool:
        return (
            detail.retryable
            or detail.category == ErrorCategory.unavailable
            or detail.code == "resource_not_found"
        )

    @staticmethod
    def _index_terminal_detail(state: JobState) -> ErrorDetail:
        if state == JobState.cancelled:
            return ErrorDetail(
                code="media_index_cancelled",
                category=ErrorCategory.conflict,
                message="Automatic indexing was cancelled.",
            )
        if state == JobState.recovery_exhausted:
            return ErrorDetail(
                code="media_index_recovery_exhausted",
                category=ErrorCategory.internal,
                message="Automatic indexing exhausted durable recovery.",
            )
        return ErrorDetail(
            code="media_index_failed",
            category=ErrorCategory.internal,
            message="Automatic indexing failed.",
        )

    def _fail_import(
        self,
        intent: UploadIntentRecord,
        detail: ErrorDetail,
    ) -> UploadIntentRecord:
        self.catalog.with_upload_transaction(
            lambda connection: self.catalog.update_upload(
                intent.intent_id,
                state=UploadState.failed,
                connection=connection,
                failure_code=detail.code,
                failure_message=detail.message,
                expected_states={UploadState.processing},
                expected_job_id=intent.job_id,
            )
        )
        return self.catalog.get_upload_intent(intent.intent_id) or intent

    def _fail_index(
        self,
        intent: UploadIntentRecord,
        detail: ErrorDetail,
    ) -> UploadIntentRecord:
        self.catalog.with_upload_transaction(
            lambda connection: self.catalog.update_upload(
                intent.intent_id,
                state=UploadState.ready,
                connection=connection,
                failure_code=detail.code,
                failure_message=detail.message,
                expected_states={UploadState.ready},
                expected_index_job_id=intent.index_job_id,
            )
        )
        return self.catalog.get_upload_intent(intent.intent_id) or intent
