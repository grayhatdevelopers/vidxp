from __future__ import annotations

import subprocess
from pathlib import Path
from time import monotonic, sleep

from pydantic import TypeAdapter

from vidxp.core.artifacts import (
    ArtifactRenderError,
    ArtifactRendererUnavailableError,
    StagedArtifact,
    StoredArtifact,
)
from vidxp.core.contracts import CancellationToken
from vidxp.core.identifiers import ArtifactId
from vidxp.core.indexing_common import ProgressCallback
from vidxp.core.video import render_actor_video
from vidxp.core.manifest import sha256_file
from vidxp.infrastructure.local_files import (
    prepare_managed_destination,
)
from vidxp.infrastructure.local_objects import LocalObjectStore


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.staging = root / ".staging"
        self._managed = LocalObjectStore(root)

    def stage(self, artifact_id: str, *, suffix: str) -> StagedArtifact:
        validated_id = TypeAdapter(ArtifactId).validate_python(artifact_id)
        if (
            not suffix.startswith(".")
            or not suffix[1:].isalnum()
            or len(suffix) > 10
        ):
            raise ValueError("Artifact suffix is invalid.")
        path = prepare_managed_destination(
            self.root,
            f".staging/{validated_id}.tmp{suffix.lower()}",
        )
        path.unlink(missing_ok=True)
        return StagedArtifact(artifact_id=artifact_id, path=path)

    def publish(self, staged: StagedArtifact) -> StoredArtifact:
        suffix = staged.path.suffix.lower()
        storage_key = (
            f"objects/{staged.artifact_id[:2]}/"
            f"{staged.artifact_id}{suffix}"
        )
        path, checksum, byte_size = self._managed.publish(
            staged.path,
            storage_key,
            expected_sha256=None,
            replace_corrupt=False,
        )
        return StoredArtifact(
            sha256=checksum,
            byte_size=byte_size,
            storage_key=storage_key,
            local_path=path,
        )

    def recover(
        self,
        artifact_id: str,
        *,
        suffix: str,
    ) -> StoredArtifact | None:
        validated_id = TypeAdapter(ArtifactId).validate_python(artifact_id)
        storage_key = (
            f"objects/{validated_id[:2]}/"
            f"{validated_id}{suffix.lower()}"
        )
        try:
            path = self._managed.resolve(storage_key)
        except FileNotFoundError:
            return None
        return StoredArtifact(
            sha256=sha256_file(path),
            byte_size=path.stat().st_size,
            storage_key=storage_key,
            local_path=path,
        )

    def discard(self, staged: StagedArtifact) -> None:
        staged.path.unlink(missing_ok=True)

    def delete(self, storage_key: str) -> None:
        self._managed.delete(storage_key)

    def verify(
        self,
        storage_key: str,
        *,
        sha256: str,
        byte_size: int,
    ) -> Path:
        return self._managed.verify(
            storage_key,
            sha256=sha256,
            byte_size=byte_size,
        )

    def resolve(self, storage_key: str) -> Path:
        self.objects.mkdir(parents=True, exist_ok=True)
        return self._managed.resolve(storage_key)


class LocalActorRenderer:
    def render(
        self,
        source: Path,
        destination: Path,
        cluster_id: str,
        detections: list[dict],
        *,
        cancellation: CancellationToken,
        progress: ProgressCallback | None,
    ) -> None:
        render_actor_video(
            source,
            destination,
            cluster_id,
            detections,
            cancellation=cancellation,
            progress=progress,
        )


class FFmpegSnippetRenderer:
    def __init__(
        self,
        executable: str = "ffmpeg",
        *,
        timeout_seconds: float = 600,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def render(
        self,
        source: Path,
        destination: Path,
        *,
        start_seconds: float,
        end_seconds: float,
        compatible_mp4: bool,
        cancellation: CancellationToken,
        progress: ProgressCallback | None,
    ) -> None:
        duration = end_seconds - start_seconds
        encoding = (
            ["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"]
            if compatible_mp4
            else ["-c", "copy"]
        )
        try:
            process = subprocess.Popen(
                [
                    self.executable,
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-ss",
                    str(start_seconds),
                    "-i",
                    str(source),
                    "-t",
                    str(duration),
                    *encoding,
                    str(destination),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ArtifactRendererUnavailableError(
                "The configured ffmpeg executable is unavailable."
            ) from exc

        started_at = monotonic()
        last_progress_at = started_at
        try:
            while process.poll() is None:
                cancellation.raise_if_cancelled()
                now = monotonic()
                if now - started_at > self.timeout_seconds:
                    raise ArtifactRenderError(
                        "The requested snippet render timed out."
                    )
                if progress is not None and now - last_progress_at >= 1:
                    progress(
                        {
                            "state": "rendering",
                            "stage": "rendering",
                            "message": "Rendering the requested video snippet.",
                        }
                    )
                    last_progress_at = now
                sleep(0.1)

            if process.returncode != 0:
                raise ArtifactRenderError(
                    "The requested snippet could not be rendered."
                )
        except BaseException:
            if process.poll() is None:
                self._stop(process)
            raise

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
