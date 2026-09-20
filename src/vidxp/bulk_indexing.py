from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Sequence

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    ListMediaCommand,
    MediaAsset,
    PlanBulkIndexCommand,
    BulkIndexPlan,
    BulkIndexTargetState,
    MediaState,
)

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


def run_bulk_index(
    application: VidXPApplication | ControlPlaneApplication,
    jobs: JobService,
    media_ids: Sequence[str] | None = None,
    *,
    all_eligible: bool = False,
    skip_indexed: bool = True,
    detach: bool = False,
    plan: BulkIndexPlan | None = None,
    modalities: Sequence[str] | None = None,
    frame_stride: int = 1,
    scene_sample_fps: float | None = None,
    capability_options: dict[str, dict] | None = None,
    on_item_start: Callable[[str, str], None] | None = None,
    on_item_progress: Callable[[str, Any], None] | None = None,
    on_item_complete: Callable[[BulkIndexItemResult], None] | None = None,
) -> BulkIndexSummary:
    if plan is None:
        if bool(media_ids) == all_eligible:
            raise ValueError("Provide media IDs or all_eligible, not both.")
        selected = application.select_index_modalities(
            tuple(modalities) if modalities is not None else None
        )
        plan = application.plan_bulk_index(
            PlanBulkIndexCommand(
                media_ids=tuple(media_ids or ()),
                modalities=selected,
                reindex=not skip_indexed,
                frame_stride=frame_stride,
                scene_sample_fps=scene_sample_fps,
                capability_options=capability_options or {},
            )
        )

    results: list[BulkIndexItemResult] = []

    for target in plan.targets:
        media_id, filename = target.media_id, target.original_filename
        if target.state == BulkIndexTargetState.skipped:
            item_result = BulkIndexItemResult(
                media_id=media_id,
                filename=filename,
                status="skipped",
                error_message=target.reason.value if target.reason else None,
            )
            results.append(item_result)
            if on_item_complete is not None:
                on_item_complete(item_result)
            continue

        if on_item_start is not None:
            on_item_start(media_id, filename)

        command = CreateIndexCommand(
            media_id=media_id, **plan.options.model_dump(mode="python")
        )
        job_id = None

        try:
            job = jobs.submit_index(command)
            job_id = job.job_id
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
                job_id=job_id,
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
                job_id=job_id,
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
