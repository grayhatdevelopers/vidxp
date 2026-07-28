from __future__ import annotations

from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Sequence

from filelock import FileLock, Timeout

from vidxp.capabilities.registry import (
    CapabilityRegistry,
    create_capability_registry,
)
from vidxp.core.contracts import (
    INDEX_SCHEMA_VERSION,
    CancellationToken,
    IndexCancelledError,
    IndexConfig,
    IndexSchemaError,
    VideoSource,
)
from vidxp.core.manifest import (
    ManifestStore,
    combined_checksum,
    source_checksum,
    source_checksums,
)
from vidxp.core.storage import IndexStorage
from vidxp.index_state import (
    IndexingInProgressError,
    write_index_status,
)
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


ProgressCallback = Callable[[dict[str, Any]], None]
_INDEXING_LOCK = Lock()


def require_dependencies(
    names: tuple[str, ...],
    *,
    source: VideoSource,
    registry: CapabilityRegistry,
) -> None:
    """Single testable dependency gate for an injected registry."""

    registry.require_dependencies(names, source=source)


class _RunLock:
    def __init__(self, run_directory: Path):
        self.path = run_directory / ".indexing.lock"
        self.lock = FileLock(self.path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.lock.acquire(timeout=0)
        except Timeout as exc:
            raise IndexingInProgressError(
                f"Indexing is already active for {self.path.parent}."
            ) from exc
        return self

    def __exit__(self, *_):
        self.lock.release()


def _run_lock_held(run_directory: Path) -> bool:
    if not run_directory.is_dir():
        return False
    lock = FileLock(run_directory / ".indexing.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return True
    else:
        lock.release()
        return False


def indexing_in_progress(config: IndexConfig | None = None) -> bool:
    if _INDEXING_LOCK.locked():
        return True
    active_config = config or IndexConfig.local()
    return _run_lock_held(active_config.run_directory)


def local_config_from_status(
    status: dict[str, Any],
    *,
    storage_directory: str | Path | None = None,
) -> IndexConfig:
    summary = status.get("summary") or {}
    if summary.get("index_schema_version") != INDEX_SCHEMA_VERSION:
        raise IndexSchemaError(
            "The saved index predates the benchmark-ready schema. "
            "Re-index the video before searching."
        )
    stored = dict(summary.get("configuration") or {})
    stored = {
        key: value
        for key, value in stored.items()
        if key in IndexConfig.__dataclass_fields__
    }
    stored.update(
        {
            "dataset": str(summary["dataset"]),
            "split": str(summary["split"]),
            "run_id": str(summary["run_id"]),
            "video_id": str(summary["video_id"]),
        }
    )
    if storage_directory is not None:
        stored["storage_directory"] = str(storage_directory)
    else:
        stored.setdefault("storage_directory", "chroma_data")
    if "enabled_modalities" in stored:
        stored["enabled_modalities"] = tuple(stored["enabled_modalities"])
    if "collection_names" in stored:
        stored["collection_names"] = dict(stored["collection_names"])
    return IndexConfig(**stored)


def _resolve_sources(
    sources: Sequence[VideoSource],
    config: IndexConfig,
) -> list[tuple[str, VideoSource, str, dict[str, str]]]:
    resolved = []
    used_ids = set()
    for source in sources:
        checksums = source_checksums(source)
        checksum = combined_checksum(checksums)
        video_id = source.video_id or config.video_id or checksum
        if video_id in used_ids:
            raise ValueError(f"Duplicate video_id in run: {video_id}")
        used_ids.add(video_id)
        resolved.append((video_id, source, checksum, checksums))
    return sorted(resolved, key=lambda item: item[0])


def _report(
    callback: ProgressCallback | None,
    event: dict[str, Any],
) -> None:
    if callback is not None:
        callback(event)


def _run_capability_group(
    names: tuple[str, ...],
    source: VideoSource,
    config: IndexConfig,
    storage: IndexStorage,
    manifest: ManifestStore,
    cancellation: CancellationToken,
    progress_callback: ProgressCallback | None,
    registry: CapabilityRegistry,
    runtime: ModelRuntime,
) -> dict[str, Any]:
    definitions = tuple(registry.get(name) for name in names)
    indexers = tuple(registry.executor(name).indexer for name in names)
    indexer = indexers[0]
    index_stage = definitions[0].index_stage
    if indexer is None or index_stage is None:
        raise ValueError(
            f"Capability {names[0]!r} does not support indexing."
        )
    if any(candidate is not indexer for candidate in indexers):
        raise RuntimeError("Grouped capabilities must share one indexer.")
    if any(
        definition.index_stage != index_stage
        for definition in definitions
    ):
        raise RuntimeError("Grouped capabilities must share one index stage.")

    started = perf_counter()
    active_substage: str | None = None
    substage_started = started

    def stage_progress(event: dict[str, Any]) -> None:
        nonlocal active_substage, substage_started
        event_stage = str(event["stage"])
        if active_substage is None:
            active_substage = event_stage
            substage_started = perf_counter()
        elif event_stage != active_substage:
            manifest.record_stage(
                str(config.video_id),
                active_substage,
                perf_counter() - substage_started,
                {},
            )
            active_substage = event_stage
            substage_started = perf_counter()
        _report(
            progress_callback,
            {**event, "video_id": config.video_id},
        )

    try:
        result = indexer(
            source,
            config=config,
            storage=storage,
            cancellation=cancellation,
            progress=stage_progress,
            modalities=names,
            registry=registry,
            runtime=runtime,
        )
    except BaseException:
        if active_substage is not None and active_substage != index_stage:
            manifest.record_stage(
                str(config.video_id),
                active_substage,
                perf_counter() - substage_started,
                {"state": "incomplete"},
            )
        manifest.record_stage(
            str(config.video_id),
            index_stage,
            perf_counter() - started,
            {"state": "incomplete", "capabilities": list(names)},
        )
        raise
    if active_substage is not None and active_substage != index_stage:
        manifest.record_stage(
            str(config.video_id),
            active_substage,
            perf_counter() - substage_started,
            {},
        )
    for stage_name, duration in result.timings.items():
        if stage_name.endswith("_total"):
            continue
        manifest.record_stage(
            str(config.video_id),
            stage_name,
            float(duration),
            {},
        )
    manifest.record_stage(
        str(config.video_id),
        index_stage,
        float(
            result.timings.get(
                f"{index_stage.removesuffix('_indexing')}_total",
                result.timings.get(
                    "visual_total",
                    perf_counter() - started,
                ),
            )
        ),
        result.summary,
    )
    return dict(result.summary)


def _index_groups(
    names: tuple[str, ...],
    registry: CapabilityRegistry,
) -> tuple[tuple[str, ...], ...]:
    groups: list[list[str]] = []
    execution_groups: list[str] = []
    for name in names:
        definition = registry.get(name)
        if registry.executor(name).indexer is None:
            raise ValueError(
                f"Capability {name!r} does not support indexing."
            )
        execution_group = definition.execution_group
        if execution_group is None:
            raise RuntimeError(
                f"Capability {name!r} has no execution group."
            )
        try:
            group_index = execution_groups.index(execution_group)
        except ValueError:
            execution_groups.append(execution_group)
            groups.append([name])
        else:
            groups[group_index].append(name)
    return tuple(tuple(group) for group in groups)


def _run_enabled_modalities(
    source: VideoSource,
    config: IndexConfig,
    storage: IndexStorage,
    manifest: ManifestStore,
    cancellation: CancellationToken,
    progress_callback: ProgressCallback | None,
    set_stage: Callable[[str], None],
    registry: CapabilityRegistry,
    runtime: ModelRuntime,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for names in _index_groups(config.enabled_modalities, registry):
        cancellation.raise_if_cancelled()
        set_stage(str(registry.get(names[0]).index_stage))
        summary.update(
            _run_capability_group(
                names,
                source,
                config,
                storage,
                manifest,
                cancellation,
                progress_callback,
                registry,
                runtime,
            )
        )
    return summary


def _process_video(
    video_id: str,
    source: VideoSource,
    checksum: str,
    config: IndexConfig,
    storage: IndexStorage,
    manifest: ManifestStore,
    cancellation: CancellationToken,
    progress_callback: ProgressCallback | None,
    *,
    fail_fast: bool,
    registry: CapabilityRegistry,
    runtime: ModelRuntime,
) -> None:
    video_config = config.for_video(video_id)
    manifest.start_video(video_id)
    summary: dict[str, Any] = {
        "video_id": video_id,
        "modalities": list(config.enabled_modalities),
    }
    stage = "preparing_dependencies"

    def set_stage(value: str) -> None:
        nonlocal stage
        stage = value

    try:
        cancellation.raise_if_cancelled()
        require_dependencies(
            config.enabled_modalities,
            source=source,
            registry=registry,
        )
        stage = "preparing_storage"
        for modality in config.enabled_modalities:
            storage.delete_video(modality, video_id)

        summary.update(
            _run_enabled_modalities(
                source,
                video_config,
                storage,
                manifest,
                cancellation,
                progress_callback,
                set_stage,
                registry,
                runtime,
            )
        )
        manifest.complete_video(
            video_id,
            checksum=checksum,
            summary=summary,
        )
        _report(
            progress_callback,
            {
                "state": "video_complete",
                "stage": "complete",
                "message": f"Completed video {video_id}.",
                "video_id": video_id,
                "summary": summary,
            },
        )
    except (IndexCancelledError, KeyboardInterrupt):
        manifest.interrupt_video(video_id, stage)
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        manifest.fail_video(video_id, stage, error)
        _report(
            progress_callback,
            {
                "state": "failed",
                "stage": stage,
                "message": f"Indexing failed for video {video_id}.",
                "video_id": video_id,
                "error": error,
            },
        )
        if fail_fast:
            raise


def _run_index_unlocked(
    sources: Sequence[VideoSource],
    config: IndexConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
    resume: bool = True,
    reset: bool = False,
    fail_fast: bool = True,
    storage: IndexStorage | None = None,
    manifest_store: ManifestStore | None = None,
    registry: CapabilityRegistry | None = None,
    runtime: ModelRuntime | None = None,
) -> dict[str, Any]:
    if not sources:
        raise ValueError("At least one video or transcript source is required.")
    cancellation = cancellation or CancellationToken()
    registry = registry or create_capability_registry()
    runtime = runtime or ModelRuntime(
        VidXPSettings(runtime_backend=config.device)
    )
    resolved = _resolve_sources(sources, config)
    owns_storage = storage is None
    store = storage or IndexStorage(config)
    try:
        manifest = manifest_store or ManifestStore(
            config,
            registry=registry,
            runtime=runtime,
        )
        if reset:
            store.clear()
        manifest.initialize(resolved, reset=reset)

        for video_id, source, checksum, _ in resolved:
            if resume and manifest.completed(
                video_id,
                checksum=checksum,
                config_fingerprint=config.fingerprint(),
            ):
                _report(
                    progress_callback,
                    {
                        "state": "skipped",
                        "stage": "checkpoint",
                        "message": f"Skipping completed video {video_id}.",
                        "video_id": video_id,
                    },
                )
                continue

            _process_video(
                video_id,
                source,
                checksum,
                config,
                store,
                manifest,
                cancellation,
                progress_callback,
                fail_fast=fail_fast,
                registry=registry,
                runtime=runtime,
            )

        return manifest.complete_run(index_size_bytes=store.size_bytes())
    finally:
        if owns_storage:
            store.close()


def run_index(
    sources: Sequence[VideoSource],
    config: IndexConfig,
    *,
    progress_callback: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
    resume: bool = True,
    reset: bool = False,
    fail_fast: bool = True,
    storage: IndexStorage | None = None,
    manifest_store: ManifestStore | None = None,
    registry: CapabilityRegistry | None = None,
    runtime: ModelRuntime | None = None,
) -> dict[str, Any]:
    with _RunLock(config.run_directory):
        return _run_index_unlocked(
            sources,
            config,
            progress_callback=progress_callback,
            cancellation=cancellation,
            resume=resume,
            reset=reset,
            fail_fast=fail_fast,
            storage=storage,
            manifest_store=manifest_store,
            registry=registry,
            runtime=runtime,
        )


def _video_status(path: Path, source_name: str, checksum: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "source_name": source_name,
        "size": stat.st_size,
        "sha256": checksum,
    }


def index_video(
    path: str,
    progress_callback: ProgressCallback | None = None,
    source_name: str | None = None,
    *,
    config: IndexConfig | None = None,
    cancellation: CancellationToken | None = None,
    registry: CapabilityRegistry | None = None,
    runtime: ModelRuntime | None = None,
) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_file():
        raise FileNotFoundError(f"Video not found: {input_path}")
    if not _INDEXING_LOCK.acquire(blocking=False):
        raise IndexingInProgressError("Another video is already being indexed.")

    source = VideoSource(
        path=input_path,
        source_name=source_name or input_path.name,
    )
    checksum = source_checksum(source)
    source = VideoSource(
        path=input_path,
        source_name=source.source_name,
        checksum=checksum,
    )
    active_config = config or IndexConfig.local(video_id=checksum)
    video_id = active_config.video_id or checksum
    video = _video_status(
        input_path,
        source.source_name or input_path.name,
        checksum,
    )
    latest_event: dict[str, Any] = {
        "state": "indexing",
        "stage": "initializing",
    }

    def report(event: dict[str, Any]) -> None:
        latest_event.update(event)
        write_index_status(
            state=event["state"],
            stage=event["stage"],
            message=event["message"],
            video=video,
            current=event.get("current"),
            total=event.get("total"),
            summary=event.get("summary"),
            error=event.get("error"),
            index_directory=active_config.index_directory,
        )
        if progress_callback is not None:
            progress_callback(event)

    report(
        {
            "state": "indexing",
            "stage": "initializing",
            "message": (
                "Preparing the selected indexing modalities. "
                "Missing model weights will download before their first use."
            ),
        }
    )
    try:
        manifest = run_index(
            [source],
            active_config,
            progress_callback=report,
            cancellation=cancellation,
            registry=registry,
            runtime=runtime,
            resume=False,
            reset=True,
            fail_fast=True,
        )
        summary = dict(manifest["videos"][video_id]["summary"])
        summary.update(
            {
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "dataset": active_config.dataset,
                "split": active_config.split,
                "run_id": active_config.run_id,
                "video_id": video_id,
                "configuration": active_config.to_dict(),
            }
        )
        report(
            {
                "state": "ready",
                "stage": "complete",
                "message": "Video indexing completed successfully.",
                "summary": summary,
            }
        )
        return summary
    except (IndexCancelledError, KeyboardInterrupt) as exc:
        report(
            {
                "state": "interrupted",
                "stage": latest_event["stage"],
                "message": "Video indexing was cancelled.",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        raise
    except Exception as exc:
        if latest_event.get("state") != "failed":
            report(
                {
                    "state": "failed",
                    "stage": latest_event["stage"],
                    "message": "Video indexing failed.",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        raise
    finally:
        _INDEXING_LOCK.release()
