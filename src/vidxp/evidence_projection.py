from __future__ import annotations

from collections.abc import Callable

from vidxp.application_models import (
    Artifact,
    EvidenceArtifact,
    Job,
    JobKind,
    JobPage,
    JobResult,
    JobState,
)


ArtifactUri = Callable[[Artifact], str]
EvidenceArtifactProjection = Callable[[EvidenceArtifact], EvidenceArtifact]


def project_job_result_artifacts(
    result: JobResult,
    *,
    resource_uri: ArtifactUri | None = None,
    project_artifact: EvidenceArtifactProjection | None = None,
) -> JobResult:
    """Traverse and project every evidence artifact in a typed job result."""

    if result.kind not in {JobKind.search, JobKind.query}:
        return result
    delivery = result.result.evidence_delivery
    if delivery is None:
        return result
    if project_artifact is None:
        if resource_uri is None:
            raise ValueError("An evidence artifact projection is required.")

        def project_artifact(evidence: EvidenceArtifact) -> EvidenceArtifact:
            return evidence.model_copy(
                update={"resource_uri": resource_uri(evidence.artifact)}
            )
    projected_items = []
    for item in delivery.items:
        keyframe = item.keyframe
        clip = item.clip
        if keyframe is not None:
            keyframe = keyframe.model_copy(
                update={"artifact": project_artifact(keyframe.artifact)}
            )
        if clip is not None:
            clip = project_artifact(clip)
        projected_items.append(
            item.model_copy(update={"keyframe": keyframe, "clip": clip})
        )
    return result.model_copy(
        update={
            "result": result.result.model_copy(
                update={
                    "evidence_delivery": delivery.model_copy(
                        update={"items": tuple(projected_items)}
                    )
                }
            )
        }
    )


def project_job_artifacts(
    job: Job,
    *,
    resource_uri: ArtifactUri | None = None,
    project_artifact: EvidenceArtifactProjection | None = None,
) -> Job:
    if job.state != JobState.succeeded or job.result is None:
        return job
    return job.model_copy(
        update={
            "result": project_job_result_artifacts(
                job.result,
                resource_uri=resource_uri,
                project_artifact=project_artifact,
            )
        }
    )


def project_job_page_artifacts(page: JobPage, *, resource_uri: ArtifactUri) -> JobPage:
    return page.model_copy(
        update={
            "items": tuple(
                project_job_artifacts(job, resource_uri=resource_uri)
                for job in page.items
            )
        }
    )
