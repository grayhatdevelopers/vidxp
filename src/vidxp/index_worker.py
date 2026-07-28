from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from threading import Lock

from vidxp.application import VidXPApplication
from vidxp.application_models import CreateIndexCommand
from vidxp.composition import create_application
from vidxp.core.contracts import CancellationToken
from vidxp.index_state import IndexingInProgressError
from vidxp.repositories import resolve_repository
from vidxp.settings import VidXPSettings

_process: BaseProcess | None = None
_cancel_event = None
_start_lock = Lock()


def _configured_service() -> VidXPApplication:
    _, repository = resolve_repository()
    return create_application(
        VidXPSettings(
            repository_root=repository.index_directory,
            runtime_backend=repository.device or "auto",
        )
    )


def _run_indexing(
    path: str,
    source_name: str,
    cancel_event,
    repository_root: str,
    runtime_backend: str,
    modalities: tuple[str, ...],
) -> None:
    create_application(
        VidXPSettings(
            repository_root=repository_root,
            runtime_backend=runtime_backend,
        )
    ).create_index(
        CreateIndexCommand(
            path=path,
            modalities=modalities,
            source_name=source_name,
        ),
        cancellation=CancellationToken(cancel_event),
    )


def indexing_in_progress(service: VidXPApplication | None = None) -> bool:
    active_service = service or _configured_service()
    return (
        _process is not None and _process.is_alive()
    ) or active_service.indexing_in_progress()


def start_indexing(
    path: str,
    source_name: str,
    service: VidXPApplication | None = None,
    *,
    modalities: tuple[str, ...],
) -> None:
    global _cancel_event, _process

    active_service = service or _configured_service()
    with _start_lock:
        if indexing_in_progress(active_service):
            raise IndexingInProgressError("Another video is already being indexed.")

        context = get_context("spawn")
        _cancel_event = context.Event()
        _process = context.Process(
            target=_run_indexing,
            args=(
                path,
                source_name,
                _cancel_event,
                str(active_service.layout.root),
                active_service.runtime.backends.requested,
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
