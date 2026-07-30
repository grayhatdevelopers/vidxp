from fastapi import APIRouter

from vidxp.api_models import ErrorEnvelope
from vidxp.api_routes import artifacts, index, jobs, media, platform


ERROR_RESPONSES = {
    status: {"model": ErrorEnvelope}
    for status in (400, 401, 403, 404, 409, 413, 422, 429, 500, 503)
}


def create_api_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1",
        responses=ERROR_RESPONSES,
    )
    router.include_router(platform.router)
    router.include_router(media.router)
    router.include_router(index.router)
    router.include_router(jobs.router)
    router.include_router(artifacts.router)
    return router
