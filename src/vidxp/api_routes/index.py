from typing import Annotated

from fastapi import APIRouter, Depends

from vidxp.api_routes.dependencies import context, read_principal
from vidxp.application_models import IndexStatus
from vidxp.composition import HttpApplicationContext


router = APIRouter(
    prefix="/index",
    tags=["index"],
    dependencies=[Depends(read_principal)],
)


@router.get(
    "/status",
    response_model=IndexStatus,
    operation_id="getIndexStatus",
    summary="Get index status",
)
def get_index_status(
    service: Annotated[HttpApplicationContext, Depends(context)],
) -> IndexStatus:
    return service.application.index_status()
