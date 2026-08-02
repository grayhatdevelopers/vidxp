from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi import HTTPException

from vidxp.composition import UploadHookContext, create_upload_hook_context
from vidxp.infrastructure.tusd_contracts import (
    TusdHookRequest,
    TusdHookResponse,
)
from vidxp.settings import VidXPSettings
from vidxp.tusd_hooks import TusdHookService



def create_hook_app(
    settings: VidXPSettings | None = None,
    *,
    context: UploadHookContext | None = None,
) -> FastAPI:
    active_context = context or create_upload_hook_context(settings)
    owns_context = context is None
    hooks = TusdHookService(
        uploads=active_context.uploads,
        authenticator=active_context.authenticator,
        authorization=active_context.authorization,
    )
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_context.start()
        try:
            yield
        finally:
            if owns_context:
                active_context.close()
            else:
                active_context.stop()

    app = FastAPI(
        title="VidXP tusd hooks",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            active_context.catalog.health()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="The upload catalog is unavailable.",
            ) from exc
        return {"status": "ok"}

    @app.post("/hooks")
    def handle(hook: TusdHookRequest) -> dict:
        response: TusdHookResponse = hooks.handle(hook)
        return response.wire()

    return app
