from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from vidxp.application_models import JobKind, JobQueue, JobRequest


APPLICATION_NAME = "vidxp"
WORKFLOW_CLASS_NAME = "VidXPWorkerWorkflows"
WORKFLOW_INSTANCE_NAME = "default"
PROGRESS_EVENT = "vidxp.progress"
ERROR_EVENT = "vidxp.error"

WORKFLOW_NAMES: dict[JobKind, str] = {
    JobKind.media_import: "vidxp.media_import.v1",
    JobKind.index: "vidxp.index.v1",
    JobKind.search: "vidxp.search.v2",
    JobKind.query: "vidxp.query.v1",
    JobKind.snippet: "vidxp.snippet.v1",
    JobKind.actor_overlay: "vidxp.actor_overlay.v1",
    JobKind.prepare_models: "vidxp.prepare_models.v1",
}
WORKFLOW_KINDS = {name: kind for kind, name in WORKFLOW_NAMES.items()}
WORKFLOW_KINDS["vidxp.search.v1"] = JobKind.search
_JOB_REQUEST_ADAPTER = TypeAdapter(JobRequest)


def decode_workflow_request(payload: Any) -> JobRequest:
    """Validate current jobs while upgrading the retired search-v1 shape."""
    if isinstance(payload, dict) and payload.get("kind") == JobKind.search:
        command = payload.get("command")
        if (
            isinstance(command, dict)
            and "modality" in command
            and "modalities" not in command
        ):
            modality = command["modality"]
            command = {
                key: value
                for key, value in command.items()
                if key != "modality"
            }
            command["modalities"] = (modality,)
            payload = {**payload, "command": command}
    return _JOB_REQUEST_ADAPTER.validate_python(payload)

QUEUE_NAMES: dict[JobQueue, str] = {
    JobQueue.cpu: "vidxp-cpu",
    JobQueue.gpu: "vidxp-gpu",
}
QUEUE_KINDS = {name: queue for queue, name in QUEUE_NAMES.items()}
