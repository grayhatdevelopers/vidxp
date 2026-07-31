from typing import Annotated

from fastapi import APIRouter, Depends

from vidxp.api_routes.dependencies import context, read_principal
from vidxp.application_models import (
    CapabilityInfo,
    CapabilityList,
    RuntimeReadiness,
)
from vidxp.composition import HttpApplicationContext


router = APIRouter(
    tags=["platform"],
    dependencies=[Depends(read_principal)],
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
