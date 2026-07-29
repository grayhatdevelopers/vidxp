from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from vidxp.api_routes.dependencies import (
    context,
    file_response,
    read_principal,
)
from vidxp.application_models import Artifact
from vidxp.composition import HttpApplicationContext
from vidxp.core.identifiers import ArtifactId


router = APIRouter(
    prefix="/artifacts",
    tags=["artifacts"],
    dependencies=[Depends(read_principal)],
)


@router.get(
    "/{artifact_id}",
    response_model=Artifact,
    operation_id="getArtifact",
    summary="Get artifact metadata",
)
def get_artifact(
    artifact_id: ArtifactId,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Artifact:
    return service.application.get_artifact(artifact_id)


def _content(
    artifact_id: ArtifactId,
    request: Request,
    service: HttpApplicationContext,
) -> Response:
    return file_response(
        request,
        service.application.open_artifact_content(artifact_id),
        disposition="attachment",
    )


@router.get(
    "/{artifact_id}/content",
    response_model=None,
    operation_id="getArtifactContent",
    summary="Download a generated clip or artifact",
    description=(
        "Stream the generated artifact with attachment, byte-range, and ETag "
        "support. Use the artifact_id returned by a completed snippet or "
        "actor-overlay job."
    ),
)
def get_artifact_content(
    artifact_id: ArtifactId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    return _content(artifact_id, request, service)


@router.head(
    "/{artifact_id}/content",
    include_in_schema=False,
)
def head_artifact_content(
    artifact_id: ArtifactId,
    request: Request,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> Response:
    return _content(artifact_id, request, service)
