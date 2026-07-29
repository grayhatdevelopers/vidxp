from __future__ import annotations

from vidxp.application_models import JobKind, JobQueue


APPLICATION_NAME = "vidxp"
WORKFLOW_CLASS_NAME = "VidXPWorkerWorkflows"
WORKFLOW_INSTANCE_NAME = "default"
PROGRESS_EVENT = "vidxp.progress"
ERROR_EVENT = "vidxp.error"

WORKFLOW_NAMES: dict[JobKind, str] = {
    JobKind.index: "vidxp.index.v1",
    JobKind.search: "vidxp.search.v1",
    JobKind.snippet: "vidxp.snippet.v1",
    JobKind.actor_overlay: "vidxp.actor_overlay.v1",
    JobKind.prepare_models: "vidxp.prepare_models.v1",
}
WORKFLOW_KINDS = {name: kind for kind, name in WORKFLOW_NAMES.items()}

QUEUE_NAMES: dict[JobQueue, str] = {
    JobQueue.cpu: "vidxp-cpu",
    JobQueue.gpu: "vidxp-gpu",
}
QUEUE_KINDS = {name: queue for queue, name in QUEUE_NAMES.items()}
