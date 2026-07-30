from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
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

LOGGER = logging.getLogger(__name__)


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
    recovery_task: asyncio.Task[None] | None = None

    async def recover() -> None:
        interval = active_context.settings.upload_recovery_interval_seconds
        while True:
            try:
                await asyncio.to_thread(active_context.uploads.reconcile)
            except Exception:
                LOGGER.exception("The resumable-upload recovery sweep failed.")
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal recovery_task
        recovery_task = asyncio.create_task(recover())
        try:
            yield
        finally:
            recovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await recovery_task
            recovery_task = None
            if owns_context:
                active_context.close()

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
            if recovery_task is None or recovery_task.done():
                raise RuntimeError("The upload recovery task is not running.")
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
