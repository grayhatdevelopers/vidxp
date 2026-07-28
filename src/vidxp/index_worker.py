from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from threading import Lock
from typing import Any, Mapping

from vidxp.application import VidXPApplication
from vidxp.application_models import CreateIndexCommand
from vidxp.composition import create_application
from vidxp.core.contracts import CancellationToken
from vidxp.index_state import IndexingInProgressError
from vidxp.settings import VidXPSettings

_process: BaseProcess | None = None
_cancel_event = None
_start_lock = Lock()


def _run_indexing(
    media_id: str,
    cancel_event,
    settings_payload: Mapping[str, Any],
    modalities: tuple[str, ...],
) -> None:
    create_application(
        VidXPSettings.model_validate(settings_payload)
    ).create_index(
        CreateIndexCommand(
            media_id=media_id,
            modalities=modalities,
        ),
        cancellation=CancellationToken(cancel_event),
    )


def indexing_in_progress(service: VidXPApplication) -> bool:
    return (
        _process is not None and _process.is_alive()
    ) or service.indexing_in_progress()


def start_indexing(
    media_id: str,
    service: VidXPApplication,
    *,
    modalities: tuple[str, ...],
) -> None:
    global _cancel_event, _process

    with _start_lock:
        if indexing_in_progress(service):
            raise IndexingInProgressError("Another video is already being indexed.")

        context = get_context("spawn")
        _cancel_event = context.Event()
        _process = context.Process(
            target=_run_indexing,
            args=(
                media_id,
                _cancel_event,
                service.settings.model_dump(mode="python"),
                modalities,
            ),
            name="vidxp-indexer",
            daemon=True,
        )
        _process.start()


def cancel_indexing() -> bool:
    if _process is None or not _process.is_alive() or _cancel_event is None:
        return False
    _cancel_event.set()
    return True
