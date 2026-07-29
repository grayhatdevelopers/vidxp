from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import subprocess
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from filelock import FileLock

from vidxp.core.media import (
    InvalidMediaError,
    MediaProbeUnavailableError,
    MediaProbe,
    MediaImportLimitError,
    MediaStream,
    MediaStoreIntegrityError,
    safe_media_suffix,
    StagedMedia,
    StoredMedia,
)
from vidxp.infrastructure.local_files import (
    prepare_managed_destination,
)
from vidxp.infrastructure.local_objects import LocalObjectStore


class LocalMediaStore:
    def __init__(self, root: Path, *, max_bytes: int) -> None:
        self.root = root
        self.objects = root / "objects"
        self.staging = root / ".staging"
        self.max_bytes = max_bytes
        self._managed = LocalObjectStore(root)

    def stage_local(self, path: Path) -> StagedMedia:
        source = path.resolve(strict=True)
        if not source.is_file():
            raise FileNotFoundError("The local media source is not a file.")
        initial = source.stat()
        if initial.st_size > self.max_bytes:
            raise MediaImportLimitError(
                "The media exceeds the configured import limit."
            )

        prepare_managed_destination(
            self.root,
            ".staging/.media-placeholder",
        )
        temporary: Path | None = None
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with source.open("rb") as reader:
                opened_before = os.fstat(reader.fileno())
                with tempfile.NamedTemporaryFile(
                    mode="w+b",
                    dir=self.staging,
                    prefix=".media.",
                    suffix=".tmp",
                    delete=False,
                ) as writer:
                    temporary = Path(writer.name)
                    while chunk := reader.read(1024 * 1024):
                        byte_size += len(chunk)
                        if byte_size > self.max_bytes:
                            raise MediaImportLimitError(
                                "The media exceeds the configured import limit."
                            )
                        digest.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                opened_after = os.fstat(reader.fileno())

            current = source.stat()
            if (
                opened_before.st_size != opened_after.st_size
                or opened_before.st_mtime_ns != opened_after.st_mtime_ns
                or opened_after.st_size != current.st_size
                or opened_after.st_mtime_ns != current.st_mtime_ns
                or (
                    opened_after.st_ino
                    and current.st_ino
                    and opened_after.st_ino != current.st_ino
                )
            ):
                raise MediaStoreIntegrityError(
                    "The media changed while it was being imported."
                )

            checksum = digest.hexdigest()
            if byte_size == 0:
                raise InvalidMediaError("The media file is empty.")
            suffix = safe_media_suffix(source)
            storage_key = (
                f"objects/{checksum[:2]}/{checksum[2:4]}/"
                f"{checksum}{suffix}"
            )
            staged = StagedMedia(
                sha256=checksum,
                byte_size=byte_size,
                storage_key=storage_key,
                path=temporary,
            )
            temporary = None
            return staged
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def publication_lock(self, sha256: str) -> AbstractContextManager[None]:
        lock_path = prepare_managed_destination(
            self.root,
            f".locks/{sha256}.lock",
        )
        return FileLock(lock_path)

    def publish(self, staged: StagedMedia) -> StoredMedia:
        try:
            path, checksum, byte_size = self._managed.publish(
                staged.path,
                staged.storage_key,
                expected_sha256=staged.sha256,
                replace_corrupt=True,
            )
        except RuntimeError as exc:
            raise MediaStoreIntegrityError(
                "The staged media failed its integrity check."
            ) from exc
        return StoredMedia(
            sha256=checksum,
            byte_size=byte_size,
            storage_key=staged.storage_key,
            local_path=path,
        )

    def discard(self, staged: StagedMedia) -> None:
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
        try:
            return self._managed.verify(
                storage_key,
                sha256=sha256,
                byte_size=byte_size,
            )
        except RuntimeError as exc:
            raise MediaStoreIntegrityError(
                "Managed media failed its integrity check."
            ) from exc

    def resolve(self, storage_key: str) -> Path:
        self.objects.mkdir(parents=True, exist_ok=True)
        return self._managed.resolve(storage_key)


class FFprobeMediaProbe:
    _MIME_TYPES = {
        "avi": "video/x-msvideo",
        "matroska": "video/x-matroska",
        "mov": "video/quicktime",
        "mp4": "video/mp4",
        "mpeg": "video/mpeg",
        "webm": "video/webm",
    }

    def __init__(
        self,
        executable: str = "ffprobe",
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def probe(self, path: Path) -> MediaProbe:
        output = self._output(path)
        try:
            payload = json.loads(output)
            if not isinstance(payload, dict):
                raise TypeError("ffprobe root must be an object")
            stream_items = payload.get("streams", ())
            if not isinstance(stream_items, list):
                raise TypeError("ffprobe streams must be a list")
            streams = tuple(
                self._stream(item)
                for item in stream_items
                if isinstance(item, dict)
            )
            if len(streams) != len(stream_items):
                raise TypeError("ffprobe stream entries must be objects")
            format_data = payload["format"]
            if not isinstance(format_data, dict):
                raise TypeError("ffprobe format must be an object")
            formats = tuple(
                part.strip()
                for part in str(format_data["format_name"]).split(",")
                if part.strip()
            )
            duration = float(format_data["duration"])
        except (
            AttributeError,
            KeyError,
            OverflowError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidMediaError("The media probe result is invalid.") from exc
        if (
            not formats
            or not math.isfinite(duration)
            or duration <= 0
            or len(streams) > 128
            or not any(stream.kind == "video" for stream in streams)
        ):
            raise InvalidMediaError("The file is not a valid supported video.")
        container = self._preferred_container(formats, path)
        return MediaProbe(
            detected_mime_type=self._MIME_TYPES.get(
                container,
                mimetypes.guess_type(path.name)[0]
                or "application/octet-stream",
            ),
            container=container,
            duration_seconds=duration,
            streams=streams,
        )

    def _output(self, path: Path) -> bytes:
        try:
            with (
                tempfile.TemporaryFile() as stdout,
                tempfile.TemporaryFile() as stderr,
            ):
                subprocess.run(
                    [
                        self.executable,
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-show_entries",
                        (
                            "format=format_name,duration:"
                            "stream=index,codec_type,codec_name,width,height,"
                            "channels,sample_rate"
                        ),
                        "-of",
                        "json",
                        str(path),
                    ],
                    check=True,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=self.timeout_seconds,
                )
                stdout.seek(0, os.SEEK_END)
                if stdout.tell() > 1024 * 1024:
                    raise InvalidMediaError(
                        "The media probe result is unexpectedly large."
                    )
                stdout.seek(0)
                return stdout.read()
        except OSError as exc:
            raise MediaProbeUnavailableError(
                "The configured ffprobe executable is unavailable."
            ) from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise InvalidMediaError(
                "The media could not be validated by ffprobe."
            ) from exc

    @staticmethod
    def _stream(item: dict[str, Any]) -> MediaStream:
        def optional_int(name: str) -> int | None:
            value = item.get(name)
            return None if value in (None, "", 0, "0") else int(value)

        return MediaStream(
            index=int(item["index"]),
            kind=str(item["codec_type"]),
            codec=str(item["codec_name"]),
            width=optional_int("width"),
            height=optional_int("height"),
            channels=optional_int("channels"),
            sample_rate=optional_int("sample_rate"),
        )

    @staticmethod
    def _preferred_container(
        formats: tuple[str, ...],
        path: Path,
    ) -> str:
        if "matroska" in formats and "webm" in formats:
            return "webm" if path.suffix.lower() == ".webm" else "matroska"
        for candidate in ("matroska", "webm", "mp4", "mov", "avi", "mpeg"):
            if candidate in formats:
                return candidate
        return formats[0]
