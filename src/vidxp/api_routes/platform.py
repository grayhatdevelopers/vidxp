from typing import Annotated

from fastapi import APIRouter, Depends, Query

from vidxp.api_routes.dependencies import context, read_principal
from vidxp.application_models import (
    CapabilityInfo,
    CapabilityList,
    ListMediaCommand,
    RuntimeReadiness,
    WorkspaceOverview,
)
from vidxp.composition import HttpApplicationContext
from vidxp.core.media import MediaState


router = APIRouter(
    tags=["platform"],
    dependencies=[Depends(read_principal)],
)


@router.get(
    "/workspace",
    response_model=WorkspaceOverview,
    operation_id="getWorkspace",
    summary="Inspect media and usable capability roles",
)
def workspace(
    service: Annotated[HttpApplicationContext, Depends(context)],
    page_size: Annotated[int, Query(gt=0, le=100)] = 50,
    cursor: Annotated[
        str | None,
        Query(min_length=1, max_length=512),
    ] = None,
    filename: Annotated[
        str | None,
        Query(min_length=1),
    ] = None,
    state: Annotated[
        MediaState | None,
        Query(),
    ] = None,
) -> WorkspaceOverview:
    return service.application.workspace(
        ListMediaCommand(
            page_size=page_size,
            cursor=cursor,
            filename=filename,
            state=state,
        )
    )


@router.get(
    "/runtime/readiness",
    response_model=RuntimeReadiness,
    operation_id="getRuntimeReadiness",
    summary="Inspect runtime readiness",
)
def runtime_readiness(
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> RuntimeReadiness:
    return service.readiness.details()


@router.get(
    "/capabilities",
    response_model=CapabilityList,
    operation_id="listCapabilities",
    summary="List capabilities",
)
def list_capabilities(
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> CapabilityList:
    return CapabilityList(items=service.application.list_capabilities())


@router.get(
    "/capabilities/{name}",
    response_model=CapabilityInfo,
    operation_id="getCapability",
    summary="Get capability metadata",
)
def get_capability(
    name: str,
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> CapabilityInfo:
    return service.application.get_capability(name)
