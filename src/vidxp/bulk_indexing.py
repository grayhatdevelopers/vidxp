from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Sequence

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    ListMediaCommand,
    MediaAsset,
    MediaState,
)
from vidxp.core.snapshots import IndexSnapshot

if TYPE_CHECKING:
    from vidxp.application import VidXPApplication
    from vidxp.control_plane import ControlPlaneApplication
    from vidxp.job_service import JobService


@dataclass(frozen=True)
class BulkIndexItemResult:
    media_id: str
    filename: str
    status: str
    job_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class BulkIndexSummary:
    total: int
    indexed: int
    skipped: int
    failed: int
    queued: int = 0
    results: tuple[BulkIndexItemResult, ...] = ()


def _resolve_all_media(
    application: VidXPApplication | ControlPlaneApplication,
) -> list[MediaAsset]:
    media_list: list[MediaAsset] = []
    cursor: str | None = None
    while True:
        page = application.list_media(
            ListMediaCommand(
                page_size=100,
                cursor=cursor,
                state=MediaState.ready,
            )
        )
        media_list.extend(page.items)
        if not page.next_cursor or not page.items:
            break
        cursor = page.next_cursor
    return media_list


def _is_already_indexed(
    snapshot: IndexSnapshot | None,
    media_id: str,
    requested_modalities: Sequence[str] | None = None,
) -> bool:
    if snapshot is None:
        return False
    generation = snapshot.generations.get(media_id)
    if generation is None:
        return False
    if requested_modalities is not None:
        return set(requested_modalities).issubset(set(generation.modalities))
    return True


def run_bulk_index(
    application: VidXPApplication | ControlPlaneApplication,
    jobs: JobService,
    media_ids: Sequence[str] | None = None,
    *,
    all_eligible: bool = False,
    skip_indexed: bool = True,
    detach: bool = False,
    modalities: Sequence[str] | None = None,
    frame_stride: int = 1,
    scene_sample_fps: float | None = None,
    capability_options: dict[str, dict] | None = None,
    on_item_start: Callable[[str, str], None] | None = None,
    on_item_progress: Callable[[str, Any], None] | None = None,
    on_item_complete: Callable[[BulkIndexItemResult], None] | None = None,
) -> BulkIndexSummary:
    items: list[tuple[str, str]] = []
    if all_eligible:
        all_media = _resolve_all_media(application)
        items = [(asset.media_id, asset.original_filename) for asset in all_media]
    elif media_ids:
        for mid in media_ids:
            asset = application.get_media(mid)
            items.append((asset.media_id, asset.original_filename))

    read_snapshot = getattr(application, "_read_active_snapshot", None)
    snapshot: IndexSnapshot | None = (
        read_snapshot() if callable(read_snapshot) else None
    )

    if modalities is not None:
        cmd_modalities = tuple(modalities)
    elif hasattr(application, "select_index_modalities"):
        cmd_modalities = application.select_index_modalities(None)
    elif hasattr(application, "list_capabilities"):
        cmd_modalities = tuple(
            c.name
            for c in application.list_capabilities()
            if getattr(c, "supports_indexing", True)
        )
    else:
        cmd_modalities = ()

    results: list[BulkIndexItemResult] = []

    for media_id, filename in items:
        if skip_indexed and _is_already_indexed(snapshot, media_id, modalities):
            item_result = BulkIndexItemResult(
                media_id=media_id,
                filename=filename,
                status="skipped",
            )
            results.append(item_result)
            if on_item_complete is not None:
                on_item_complete(item_result)
            continue

        if on_item_start is not None:
            on_item_start(media_id, filename)

        command = CreateIndexCommand(
            media_id=media_id,
            modalities=cmd_modalities,
            frame_stride=frame_stride,
            scene_sample_fps=scene_sample_fps,
            capability_options=capability_options or {},
        )

        try:
            job = jobs.submit_index(command)
            if detach:
                item_result = BulkIndexItemResult(
                    media_id=media_id,
                    filename=filename,
                    status="queued",
                    job_id=job.job_id,
                )
                results.append(item_result)
                if on_item_complete is not None:
                    on_item_complete(item_result)
            else:
                def _progress(current: Any) -> None:
                    if on_item_progress is not None:
                        on_item_progress(media_id, current)

                job = jobs.wait(job.job_id, progress=_progress)
                item_result = BulkIndexItemResult(
                    media_id=media_id,
                    filename=filename,
                    status="indexed",
                    job_id=job.job_id,
                )
                results.append(item_result)
                if on_item_complete is not None:
                    on_item_complete(item_result)
        except ApplicationError as exc:
            item_result = BulkIndexItemResult(
                media_id=media_id,
                filename=filename,
                status="failed",
                error_code=exc.code,
                error_message=str(exc),
            )
            results.append(item_result)
            if on_item_complete is not None:
                on_item_complete(item_result)
        except Exception as exc:
            item_result = BulkIndexItemResult(
                media_id=media_id,
                filename=filename,
                status="failed",
                error_code="unexpected_error",
                error_message=str(exc),
            )
            results.append(item_result)
            if on_item_complete is not None:
                on_item_complete(item_result)

    return BulkIndexSummary(
        total=len(results),
        indexed=sum(1 for r in results if r.status == "indexed"),
        skipped=sum(1 for r in results if r.status == "skipped"),
        failed=sum(1 for r in results if r.status == "failed"),
        queued=sum(1 for r in results if r.status == "queued"),
        results=tuple(results),
    )
