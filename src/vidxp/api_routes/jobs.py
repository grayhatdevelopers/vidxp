from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from vidxp.api_routes.dependencies import (
    accepted,
    context,
    HttpIdempotencyKey,
    read_principal,
    scoped_job_id,
    write_principal,
)
from vidxp.application_models import (
    CreateActorOverlayCommand,
    CreateIndexCommand,
    CreateSnippetCommand,
    Job,
    JobPage,
    JobResult,
    ListJobsCommand,
    Principal,
    PrepareModelsCommand,
    QueryVideoCommand,
    SearchCommand,
)
from vidxp.composition import HttpApplicationContext
from vidxp.core.identifiers import JobId


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/index",
    response_model=Job,
    status_code=202,
    operation_id="startIndexing",
    summary="Start indexing",
    description=(
        "Add or replace one registered media item in the active multi-video "
        "index snapshot."
    ),
    dependencies=[Depends(write_principal)],
)
def submit_index(
    command: CreateIndexCommand,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> Job:
    service.application.require_models(command.modalities)
    return accepted(
        response,
        service.jobs.submit_index(
            command,
            job_id=scoped_job_id(
                service,
                actor,
                operation="index",
                idempotency_key=idempotency_key,
            ),
        ),
    )


@router.post(
    "/search",
    response_model=Job,
    status_code=202,
    operation_id="searchMoments",
    summary="Search indexed moments",
    description=(
        "Set media_id to search one video, or omit it to rank moments across "
        "every media item in the active index snapshot."
    ),
    dependencies=[Depends(read_principal)],
)
def submit_search(
    command: SearchCommand,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(read_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> Job:
    return accepted(
        response,
        service.jobs.submit_search(
            command,
            job_id=scoped_job_id(
                service,
                actor,
                operation="search",
                idempotency_key=idempotency_key,
            ),
        ),
    )


@router.post(
    "/query",
    response_model=Job,
    status_code=202,
    operation_id="queryVideo",
    summary="Ask a grounded question about indexed media",
    description=(
        "Set media_id to ground the answer in one video, or omit it to use "
        "evidence across every media item in the active index snapshot."
    ),
    dependencies=[Depends(read_principal)],
)
def submit_query(
    command: QueryVideoCommand,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(read_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> Job:
    return accepted(
        response,
        service.jobs.submit_query(
            command,
            job_id=scoped_job_id(
                service,
                actor,
                operation="query",
                idempotency_key=idempotency_key,
            ),
        ),
    )


@router.post(
    "/snippet",
    response_model=Job,
    status_code=202,
    operation_id="createSnippet",
    summary="Create a downloadable video clip",
    description=(
        "Create a durable clip-rendering job from a media ID and time range "
        "returned by search or query. Poll the job, then download the "
        "resulting artifact through GET /api/v1/artifacts/{artifact_id}/content."
    ),
    dependencies=[Depends(write_principal)],
)
def submit_snippet(
    command: CreateSnippetCommand,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> Job:
    return accepted(
        response,
        service.jobs.submit_snippet(
            command,
            job_id=scoped_job_id(
                service,
                actor,
                operation="snippet",
                idempotency_key=idempotency_key,
            ),
        ),
    )


@router.post(
    "/actor-overlay",
    response_model=Job,
    status_code=202,
    operation_id="createActorOverlay",
    summary="Create an actor overlay",
    dependencies=[Depends(write_principal)],
)
def submit_actor_overlay(
    command: CreateActorOverlayCommand,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> Job:
    return accepted(
        response,
        service.jobs.submit_actor_overlay(
            command,
            job_id=scoped_job_id(
                service,
                actor,
                operation="actor-overlay",
                idempotency_key=idempotency_key,
            ),
        ),
    )


@router.post(
    "/model-preparation",
    response_model=Job,
    status_code=202,
    operation_id="prepareModels",
    summary="Prepare models",
    dependencies=[Depends(write_principal)],
)
def submit_model_preparation(
    command: PrepareModelsCommand,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> Job:
    return accepted(
        response,
        service.jobs.submit_prepare_models(
            command,
            job_id=scoped_job_id(
                service,
                actor,
                operation="model-preparation",
                idempotency_key=idempotency_key,
            ),
        ),
    )


@router.get(
    "",
    response_model=JobPage,
    operation_id="listJobs",
    summary="List jobs",
    dependencies=[Depends(read_principal)],
)
def list_jobs(
    service: Annotated[HttpApplicationContext, Depends(context)],
    page_size: Annotated[int, Query(gt=0, le=100)] = 50,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=512),
    ] = None,
) -> JobPage:
    return service.jobs.list(
        ListJobsCommand(page_size=page_size, cursor=cursor)
    )


@router.get(
    "/{job_id}",
    response_model=Job,
    operation_id="getJob",
    summary="Get a job",
    dependencies=[Depends(read_principal)],
)
def get_job(
    job_id: JobId,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Job:
    return service.jobs.get(job_id)


@router.get(
    "/{job_id}/result",
    response_model=JobResult,
    operation_id="getJobResult",
    summary="Get a job result",
    dependencies=[Depends(read_principal)],
)
def get_job_result(
    job_id: JobId,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> JobResult:
    return service.jobs.result(job_id)


@router.post(
    "/{job_id}/cancellation",
    response_model=Job,
    operation_id="cancelJob",
    summary="Cancel a job",
    dependencies=[Depends(write_principal)],
)
def cancel_job(
    job_id: JobId,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Job:
    return service.jobs.cancel(job_id)


@router.post(
    "/{job_id}/retries",
    response_model=Job,
    status_code=202,
    operation_id="retryJob",
    summary="Retry a job",
    dependencies=[Depends(write_principal)],
)
def retry_job(
    job_id: JobId,
    response: Response,
    service: Annotated[HttpApplicationContext, Depends(context)],
    actor: Annotated[Principal, Depends(write_principal)],
    idempotency_key: HttpIdempotencyKey,
) -> Job:
    return accepted(
        response,
        service.jobs.retry(
            job_id,
            retry_id=scoped_job_id(
                service,
                actor,
                operation=f"retry:{job_id}",
                idempotency_key=idempotency_key,
            ),
        ),
    )
