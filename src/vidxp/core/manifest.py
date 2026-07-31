from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

from vidxp.capabilities.registry import (
    CapabilityRegistry,
)
from vidxp.core.contracts import (
    INDEX_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    IndexConfig,
    VideoSource,
)
from vidxp.ports import ModelRuntimePort


MANIFEST_FILE = "manifest.json"
TIMINGS_FILE = "timings.jsonl"
FAILURES_FILE = "failures.jsonl"
COMPLETION_FILE = "run.complete.json"
CHECKPOINT_DIRECTORY = "checkpoints"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary = Path(destination.name)
            destination.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            destination.flush()
            os.fsync(destination.fileno())

        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

        sync_parent_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # The replacement is already visible. Some supported filesystems
        # reject directory fsync, which cannot roll that replacement back.
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as destination:
        destination.write(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_checksums(source: VideoSource) -> dict[str, str]:
    checksums = {}
    if source.path is not None:
        checksums["video"] = source.checksum or sha256_file(source.path)
    elif source.checksum:
        checksums["declared"] = source.checksum
    if source.transcript is not None:
        encoded = json.dumps(
            list(source.transcript),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        checksums["transcript"] = hashlib.sha256(encoded).hexdigest()
    return checksums


def source_checksum(source: VideoSource) -> str:
    return combined_checksum(source_checksums(source))


def combined_checksum(checksums: Mapping[str, str]) -> str:
    if len(checksums) == 1:
        return next(iter(checksums.values()))
    encoded = json.dumps(
        checksums,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_size(source: VideoSource) -> int | None:
    if source.path is None:
        return None
    return Path(source.path).stat().st_size


def git_state() -> dict[str, Any]:
    repository = next(
        (
            parent
            for parent in Path(__file__).resolve().parents
            if (parent / ".git").exists()
        ),
        None,
    )
    if repository is None:
        return {"commit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(repository), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def implementation_digest() -> str:
    package_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def dependency_versions(
    registry: CapabilityRegistry,
) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in registry.runtime_distributions():
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return versions


def execution_state(
    registry: CapabilityRegistry,
) -> dict[str, Any]:
    try:
        package_version = version("vidxp")
    except PackageNotFoundError:
        package_version = None
    return {
        "git": git_state(),
        "implementation_sha256": implementation_digest(),
        "package_version": package_version,
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": dependency_versions(registry),
    }


def execution_fingerprint(state: Mapping[str, Any]) -> str:
    fingerprint_state = dict(state)
    git = dict(fingerprint_state.get("git") or {})
    git.pop("dirty", None)
    fingerprint_state["git"] = git
    encoded = json.dumps(
        fingerprint_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ManifestStore:
    def __init__(
        self,
        config: IndexConfig,
        *,
        registry: CapabilityRegistry,
        runtime: ModelRuntimePort,
    ):
        self.config = config
        self.registry = registry
        self.runtime = runtime
        self.run_directory = config.run_directory
        self.manifest_path = self.run_directory / MANIFEST_FILE
        self.timings_path = self.run_directory / TIMINGS_FILE
        self.failures_path = self.run_directory / FAILURES_FILE
        self.completion_path = self.run_directory / COMPLETION_FILE
        self.checkpoint_directory = (
            self.run_directory / CHECKPOINT_DIRECTORY
        )

    def _checkpoint_path(self, video_id: str) -> Path:
        digest = hashlib.sha256(video_id.encode("utf-8")).hexdigest()
        return self.checkpoint_directory / f"{digest}.json"

    def _model_manifest(
        self,
        sources: list[
            tuple[str, VideoSource, str, Mapping[str, str]]
        ],
    ) -> dict[str, Any]:
        models: dict[str, Any] = {
            "device": self.config.device,
            "runtime": self.runtime.describe(),
        }
        source_values = tuple(source for _, source, _, _ in sources)
        for name in self.config.enabled_modalities:
            manifest = self.registry.executor(name).model_manifest
            if manifest is not None:
                models.update(manifest(self.config, source_values))
        return models

    def initialize(
        self,
        sources: list[tuple[str, VideoSource, str, Mapping[str, str]]],
        *,
        reset: bool = False,
    ) -> dict[str, Any]:
        self.run_directory.mkdir(parents=True, exist_ok=True)
        config_fingerprint = self.config.fingerprint()
        current_execution = execution_state(self.registry)
        current_execution_fingerprint = execution_fingerprint(
            current_execution
        )
        if self.manifest_path.is_file() and not reset:
            manifest = self.read()
            if manifest["config_fingerprint"] != config_fingerprint:
                raise ValueError(
                    "The existing run uses a different configuration. "
                    "Choose a new run_id or explicitly reset the run."
                )
            if (
                manifest.get("execution_fingerprint")
                != current_execution_fingerprint
            ):
                raise ValueError(
                    "The implementation or dependency environment changed "
                    "since this run started. Use a new run_id or reset the run."
                )
            manifest["models"].update(self._model_manifest(sources))
        else:
            manifest = {
                "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "dataset": self.config.dataset,
                "split": self.config.split,
                "run_id": self.config.run_id,
                "generation_id": self.config.generation_id,
                "state": "running",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "config_fingerprint": config_fingerprint,
                "execution_fingerprint": current_execution_fingerprint,
                "configuration": self.config.to_dict(),
                "models": self._model_manifest(sources),
                "git": current_execution["git"],
                "environment": current_execution,
                "inputs": {},
                "videos": {},
                "completed_videos": [],
                "failed_videos": [],
                "interrupted_videos": [],
                "processed_frames": 0,
                "record_counts": {},
                "store_size_bytes_at_commit": None,
            }
            if reset:
                for path in (
                    self.timings_path,
                    self.failures_path,
                    self.completion_path,
                ):
                    path.unlink(missing_ok=True)
                if self.checkpoint_directory.is_dir():
                    for checkpoint in self.checkpoint_directory.glob("*.json"):
                        checkpoint.unlink()

        for video_id, source, checksum, checksums in sources:
            existing = manifest["inputs"].get(video_id)
            if existing and existing["sha256"] != checksum:
                raise ValueError(
                    f"Video ID {video_id!r} now resolves to different input "
                    "bytes. Use a new video_id or reset the run."
                )
            manifest["inputs"][video_id] = {
                "sha256": checksum,
                "checksums": dict(checksums),
                "size": source_size(source),
                "source_name": (
                    source.source_name
                    or (Path(source.path).name if source.path is not None else None)
                ),
                "path": (
                    str(Path(source.path).resolve())
                    if source.path is not None
                    else None
                ),
                "metadata": dict(source.metadata),
            }
        manifest["state"] = "running"
        manifest["updated_at"] = utc_now()
        self.completion_path.unlink(missing_ok=True)
        self.write(manifest)
        return manifest

    def read(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def write(self, manifest: Mapping[str, Any]) -> None:
        write_json_atomic(self.manifest_path, manifest)

    def _refresh_runtime(self, manifest: dict[str, Any]) -> None:
        manifest["models"]["runtime"] = self.runtime.describe()

    def checkpoint(self, video_id: str) -> dict[str, Any] | None:
        path = self._checkpoint_path(video_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def completed(
        self,
        video_id: str,
        *,
        checksum: str,
        config_fingerprint: str,
    ) -> bool:
        checkpoint = self.checkpoint(video_id)
        if checkpoint is None:
            return False
        if checkpoint.get("sha256") != checksum:
            raise ValueError(
                f"Checkpoint checksum mismatch for video {video_id!r}."
            )
        if checkpoint.get("config_fingerprint") != config_fingerprint:
            raise ValueError(
                f"Checkpoint configuration mismatch for video {video_id!r}."
            )
        if checkpoint.get("state") != "complete":
            return False

        manifest = self.read()
        if video_id not in manifest["completed_videos"]:
            manifest["videos"][video_id] = {
                "state": "complete",
                "completed_at": checkpoint["completed_at"],
                "summary": checkpoint.get("summary", {}),
                "stages": manifest["videos"].get(video_id, {}).get(
                    "stages",
                    {},
                ),
            }
            manifest["completed_videos"].append(video_id)
            manifest["completed_videos"].sort()
            for key in ("failed_videos", "interrupted_videos"):
                manifest[key] = [
                    item for item in manifest[key] if item != video_id
                ]
            manifest["updated_at"] = utc_now()
            self.write(manifest)
        return True

    def start_video(self, video_id: str) -> None:
        self._checkpoint_path(video_id).unlink(missing_ok=True)
        manifest = self.read()
        manifest["state"] = "running"
        manifest["videos"][video_id] = {
            "state": "indexing",
            "started_at": utc_now(),
            "stages": {},
        }
        for key in (
            "completed_videos",
            "failed_videos",
            "interrupted_videos",
        ):
            manifest[key] = [item for item in manifest[key] if item != video_id]
        manifest["updated_at"] = utc_now()
        self.write(manifest)

    def record_stage(
        self,
        video_id: str,
        stage: str,
        seconds: float,
        stats: Mapping[str, Any],
    ) -> None:
        timing = {
            "video_id": video_id,
            "stage": stage,
            "seconds": seconds,
            "stats": dict(stats),
            "recorded_at": utc_now(),
        }
        _append_jsonl(self.timings_path, timing)
        manifest = self.read()
        manifest["videos"][video_id]["stages"][stage] = timing
        manifest["updated_at"] = utc_now()
        self.write(manifest)

    def complete_video(
        self,
        video_id: str,
        *,
        checksum: str,
        summary: Mapping[str, Any],
    ) -> None:
        checkpoint = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "state": "complete",
            "video_id": video_id,
            "sha256": checksum,
            "config_fingerprint": self.config.fingerprint(),
            "completed_at": utc_now(),
            "summary": dict(summary),
        }
        write_json_atomic(self._checkpoint_path(video_id), checkpoint)
        manifest = self.read()
        video = manifest["videos"][video_id]
        video["state"] = "complete"
        video["completed_at"] = checkpoint["completed_at"]
        video["summary"] = dict(summary)
        if video_id not in manifest["completed_videos"]:
            manifest["completed_videos"].append(video_id)
        manifest["completed_videos"].sort()
        self._refresh_runtime(manifest)
        manifest["updated_at"] = utc_now()
        self.write(manifest)

    def fail_video(self, video_id: str, stage: str, error: str) -> None:
        failure = {
            "video_id": video_id,
            "stage": stage,
            "error": error,
            "failed_at": utc_now(),
        }
        _append_jsonl(self.failures_path, failure)
        manifest = self.read()
        manifest["videos"][video_id].update(
            {"state": "failed", **failure}
        )
        if video_id not in manifest["failed_videos"]:
            manifest["failed_videos"].append(video_id)
        manifest["failed_videos"].sort()
        manifest["state"] = "failed"
        self._refresh_runtime(manifest)
        manifest["updated_at"] = utc_now()
        self.write(manifest)

    def interrupt_video(self, video_id: str, stage: str) -> None:
        manifest = self.read()
        manifest["videos"][video_id].update(
            {
                "state": "interrupted",
                "stage": stage,
                "interrupted_at": utc_now(),
            }
        )
        if video_id not in manifest["interrupted_videos"]:
            manifest["interrupted_videos"].append(video_id)
        manifest["interrupted_videos"].sort()
        manifest["state"] = "interrupted"
        self._refresh_runtime(manifest)
        manifest["updated_at"] = utc_now()
        self.write(manifest)

    def complete_run(
        self,
        *,
        store_size_bytes_at_commit: int | None,
    ) -> dict[str, Any]:
        manifest = self.read()
        self._refresh_runtime(manifest)
        expected = set(manifest["inputs"])
        completed = set(manifest["completed_videos"])
        if expected != completed or manifest["failed_videos"]:
            manifest["state"] = "completed_with_failures"
            manifest["store_size_bytes_at_commit"] = (
                store_size_bytes_at_commit
            )
            manifest["updated_at"] = utc_now()
            self.write(manifest)
            return manifest

        processed_frames = sum(
            int(video.get("summary", {}).get("processed_frames", 0))
            for video in manifest["videos"].values()
        )
        manifest["state"] = "complete"
        manifest["completed_at"] = utc_now()
        manifest["processed_frames"] = processed_frames
        manifest["store_size_bytes_at_commit"] = store_size_bytes_at_commit
        manifest["updated_at"] = utc_now()
        self.write(manifest)
        completion = {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "dataset": self.config.dataset,
            "run_id": self.config.run_id,
            "config_fingerprint": self.config.fingerprint(),
            "completed_at": manifest["completed_at"],
            "completed_videos": manifest["completed_videos"],
            "store_size_bytes_at_commit": store_size_bytes_at_commit,
        }
        write_json_atomic(self.completion_path, completion)
        return manifest

    def record_storage_counts(
        self,
        record_counts: Mapping[str, int],
    ) -> dict[str, Any]:
        manifest = self.read()
        if manifest.get("state") != "complete":
            raise RuntimeError(
                "Storage counts can only finalize a completed generation."
            )
        counts = {
            str(modality): int(count)
            for modality, count in record_counts.items()
        }
        if set(counts) != set(self.config.enabled_modalities):
            raise ValueError(
                "Storage counts must cover every enabled modality exactly."
            )
        if any(count < 0 for count in counts.values()):
            raise ValueError("Storage record counts must be nonnegative.")
        manifest["record_counts"] = counts
        manifest["updated_at"] = utc_now()
        self.write(manifest)
        return manifest
