from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from dbos import DBOS, DBOSConfiguredInstance
from pydantic import TypeAdapter

from vidxp.application import VidXPApplication
from vidxp.application_models import (
    ActorOverlayJobRequest,
    ApplicationError,
    ErrorCategory,
    ErrorDetail,
    EvidenceBoardJobRequest,
    IndexJobRequest,
    JobKind,
    JobProgress,
    MediaImportJobRequest,
    SnippetJobRequest,
    PrepareModelsJobRequest,
    QueryJobRequest,
    SearchJobRequest,
)
from vidxp.core.contracts import CancellationToken
from vidxp.core.contracts import IndexCancelledError
from vidxp.execution import ExecutionContext
from vidxp.workflow_contracts import (
    ERROR_EVENT,
    PROGRESS_EVENT,
    WORKFLOW_CLASS_NAME,
    WORKFLOW_INSTANCE_NAME,
    WORKFLOW_NAMES,
    decode_workflow_request,
)


_ARTIFACT_REQUEST = TypeAdapter(ActorOverlayJobRequest | SnippetJobRequest)
LOGGER = logging.getLogger(__name__)


class _DBOSCancellationEvent:
    def __init__(self, poll_interval_seconds: float = 0.2) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self._checked_at = 0.0
        self._cancelled = False

    def is_set(self) -> bool:
        if self._cancelled:
            return True
        now = monotonic()
        if now - self._checked_at < self._poll_interval_seconds:
            return False
        self._checked_at = now
        status = DBOS.get_workflow_status(DBOS.workflow_id)
        self._cancelled = status is not None and status.status == "CANCELLED"
        return self._cancelled

    def set(self) -> None:
        if DBOS.workflow_id is not None:
            DBOS.cancel_workflow(DBOS.workflow_id)
        self._cancelled = True


def _publish_progress(event: dict[str, Any]) -> None:
    stage = str(event.get("stage") or "working")[:128]
    message = str(event.get("message") or stage.replace("_", " "))[:512]
    current = event.get("current")
    total = event.get("total")
    progress = JobProgress(
        stage=stage,
        message=message,
        current=current if isinstance(current, int) else None,
        total=total if isinstance(total, int) and total > 0 else None,
        updated_at=datetime.now(timezone.utc),
    )
    DBOS.set_event(PROGRESS_EVENT, progress.model_dump(mode="json"))


def _execution() -> ExecutionContext:
    cancellation = CancellationToken(_DBOSCancellationEvent())

    def publish(event: dict[str, Any]) -> None:
        cancellation.raise_if_cancelled()
        _publish_progress(event)

    return ExecutionContext(
        job_id=DBOS.workflow_id,
        progress=publish,
        cancellation=cancellation,
    )


def _publish_error(exc: Exception) -> None:
    if isinstance(exc, ApplicationError):
        error = exc.detail
    else:
        error = ErrorDetail(
            code="job_execution_failed",
            category=ErrorCategory.internal,
            message="The background job failed unexpectedly.",
        )
    DBOS.set_event(ERROR_EVENT, error.model_dump(mode="json"))


def _step_boundary(
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    try:
        return operation()
    except IndexCancelledError:
        raise
    except Exception as exc:
        if isinstance(exc, ApplicationError):
            LOGGER.warning(
                "VidXP workflow %s failed with %s.",
                DBOS.workflow_id,
                exc.detail.code,
            )
        else:
            LOGGER.exception(
                "VidXP workflow %s failed unexpectedly.",
                DBOS.workflow_id,
            )
        _publish_error(exc)
        raise RuntimeError("VidXP job execution failed.") from exc


@DBOS.dbos_class(class_name=WORKFLOW_CLASS_NAME)
class VidXPWorkerWorkflows(DBOSConfiguredInstance):
    """Official DBOS configured instance for one composed worker application."""

    def __init__(self, application: VidXPApplication) -> None:
        self.application = application
        super().__init__(WORKFLOW_INSTANCE_NAME)

    @DBOS.step(name="vidxp.run_index.v1")
    def run_index_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            request = IndexJobRequest.model_validate(payload)
            execution = _execution()
            _publish_progress(
                {
                    "stage": "starting",
                    "message": "Starting the indexing job.",
                }
            )
            result = self.application.create_index(
                request.command,
                execution=execution,
            )
            _publish_progress(
                {
                    "stage": "complete",
                    "message": "The index generation is ready.",
                    "current": 1,
                    "total": 1,
                }
            )
            return result.model_dump(mode="json")

        return _step_boundary(execute)

    @DBOS.step(name="vidxp.run_media_import.v1")
    def run_media_import_step(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            request = MediaImportJobRequest.model_validate(payload)
            _publish_progress(
                {
                    "stage": "importing",
                    "message": "Validating and importing the completed upload.",
                }
            )
            result = (
                self.application.import_media(request.command)
                if request.command is not None
                else self.application.import_completed_upload(request.upload_id or "")
            )
            _publish_progress(
                {
                    "stage": "complete",
                    "message": "The uploaded media is ready.",
                    "current": 1,
                    "total": 1,
                }
            )
            return result.model_dump(mode="json")

        return _step_boundary(execute)

    @DBOS.step(name="vidxp.run_artifact.v1")
    def run_artifact_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            request = _ARTIFACT_REQUEST.validate_python(payload)
            execution = _execution()
            if isinstance(request, SnippetJobRequest):
                result = self.application.create_snippet(
                    request.command,
                    execution=execution,
                )
            else:
                result = self.application.render_actor(
                    request.command,
                    snapshot=request.snapshot,
                    execution=execution,
                )
            return result.model_dump(mode="json")

        return _step_boundary(execute)

    @DBOS.step(name="vidxp.run_evidence_board.v1")
    def run_evidence_board_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            request = EvidenceBoardJobRequest.model_validate(payload)
            execution = _execution()
            _publish_progress(
                {
                    "stage": "planning_board",
                    "message": "Planning the evidence board pages.",
                }
            )
            result = self.application.create_evidence_board(
                request,
                execution=execution,
            )
            return result.model_dump(mode="json")

        return _step_boundary(execute)

    def _run_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            request = decode_workflow_request(payload)
            if not isinstance(request, SearchJobRequest):
                raise ValueError("A search workflow requires a search request.")
            execution = _execution()
            _publish_progress(
                {
                    "stage": "searching",
                    "message": "Searching the committed index.",
                }
            )
            execution.checkpoint()
            result = self.application.search(
                request.command,
                snapshot=request.snapshot,
                execution=execution,
            )
            execution.checkpoint()
            _publish_progress(
                {
                    "stage": "complete",
                    "message": "Search results are ready.",
                    "current": 1,
                    "total": 1,
                }
            )
            return result.model_dump(mode="json")

        return _step_boundary(execute)

    @DBOS.step(name="vidxp.run_search.v1")
    def run_legacy_search_step(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._run_search(payload)

    @DBOS.step(name="vidxp.run_search.v2")
    def run_search_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._run_search(payload)

    @DBOS.step(name="vidxp.run_query.v1")
    def run_query_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            request = QueryJobRequest.model_validate(payload)
            execution = _execution()
            _publish_progress(
                {
                    "stage": "querying",
                    "message": "Querying the committed index.",
                }
            )
            execution.checkpoint()
            result = self.application.query_video(
                request.command,
                snapshot=request.snapshot,
                execution=execution,
            )
            execution.checkpoint()
            _publish_progress(
                {
                    "stage": "complete",
                    "message": "The grounded query result is ready.",
                    "current": 1,
                    "total": 1,
                }
            )
            return result.model_dump(mode="json")

        return _step_boundary(execute)

    @DBOS.step(name="vidxp.prepare_models_step.v1")
    def prepare_models_step(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            request = PrepareModelsJobRequest.model_validate(payload)
            execution = _execution()
            _publish_progress(
                {
                    "stage": "preparing_models",
                    "message": "Preparing model artifacts.",
                }
            )
            result = self.application.prepare_models(
                request.command,
                execution=execution,
            )
            _publish_progress(
                {
                    "stage": "complete",
                    "message": "Model artifacts are ready.",
                    "current": 1,
                    "total": 1,
                }
            )
            return result.model_dump(mode="json")

        return _step_boundary(execute)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.index])
    def index_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.run_index_step(payload)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.media_import])
    def media_import_workflow(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.run_media_import_step(payload)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.snippet])
    def snippet_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.run_artifact_step(payload)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.search])
    def search_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.run_search_step(payload)

    @DBOS.workflow(name="vidxp.search.v1")
    def legacy_search_workflow(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.run_legacy_search_step(payload)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.query])
    def query_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.run_query_step(payload)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.actor_overlay])
    def actor_overlay_workflow(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.run_artifact_step(payload)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.evidence_board])
    def evidence_board_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.run_evidence_board_step(payload)

    @DBOS.workflow(name=WORKFLOW_NAMES[JobKind.prepare_models])
    def prepare_models_workflow(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.prepare_models_step(payload)
