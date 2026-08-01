from __future__ import annotations

import json
from pathlib import Path
import hashlib
from uuid import uuid4

from vidxp.application_models import (
    ImportMediaCommand,
    ListMediaCommand,
    MediaAsset,
    MediaPage,
)
from vidxp.core.media import (
    MediaRecord,
    MediaState,
    QuarantinedMedia,
    StagedMedia,
    MediaUnavailableError,
    utc_now,
)
from vidxp.core.cursors import (
    CursorError,
    decode_offset_cursor,
    encode_offset_cursor,
)
from vidxp.ports import (
    LocalFileResource,
    MediaCatalogPort,
    MediaProbePort,
    MediaStorePort,
)
from vidxp.settings import ApplicationMode, VidXPSettings


class MediaImportNotAllowedError(PermissionError):
    """Raised when a local path is outside the local import policy."""


class MediaIdempotencyConflictError(FileExistsError):
    """Raised when an import key is reused for different media content."""


def media_asset(record: MediaRecord) -> MediaAsset:
    return MediaAsset(
        schema_version=record.schema_version,
        media_id=record.media_id,
        video_id=record.video_id,
        original_filename=record.original_filename,
        sha256=record.sha256,
        byte_size=record.byte_size,
        declared_mime_type=record.declared_mime_type,
        detected_mime_type=record.detected_mime_type,
        container=record.container,
        duration_seconds=record.duration_seconds,
        streams=record.streams,
        state=record.state,
        created_at=record.created_at,
    )


class MediaService:
    def __init__(
        self,
        *,
        settings: VidXPSettings,
        catalog: MediaCatalogPort,
        store: MediaStorePort,
        probe: MediaProbePort,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.store = store
        self.probe = probe

    def import_local(self, command: ImportMediaCommand) -> MediaAsset:
        if self.settings.mode != ApplicationMode.local:
            raise MediaImportNotAllowedError(
                "Local path imports are available only in local mode."
            )
        source = self.resolve_local_source(command.path)
        return self._import(
            source,
            original_filename=command.original_filename or source.name,
            declared_mime_type=command.declared_mime_type,
        )

    def import_quarantined(
        self,
        media: QuarantinedMedia,
        *,
        request_key: str | None = None,
    ) -> MediaAsset:
        try:
            source = media.path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise MediaUnavailableError(
                "The quarantined media source is unavailable."
            ) from exc
        if not source.is_file():
            raise MediaUnavailableError(
                "The quarantined media source is not a file."
            )
        return self._import(
            source,
            original_filename=media.original_filename,
            declared_mime_type=media.declared_mime_type,
            request_key=request_key,
        )

    def _import(
        self,
        source: Path,
        *,
        original_filename: str,
        declared_mime_type: str | None,
        request_key: str | None = None,
    ) -> MediaAsset:
        staged = self.store.stage_local(source)
        try:
            with self.store.publication_lock(staged.sha256):
                if request_key is not None:
                    request_fingerprint = self._import_fingerprint(
                        staged,
                        original_filename=original_filename,
                        declared_mime_type=declared_mime_type,
                    )
                    try:
                        completed = self.catalog.reserve_media_import(
                            request_key,
                            request_fingerprint,
                        )
                    except FileExistsError as exc:
                        raise MediaIdempotencyConflictError from exc
                    if completed is not None:
                        return media_asset(completed)
                result = self._publish_import(
                    original_filename=original_filename,
                    declared_mime_type=declared_mime_type,
                    staged=staged,
                )
                if request_key is not None:
                    record = self.catalog.get_media(result.media_id)
                    if record is None:
                        raise RuntimeError(
                            "The imported media record is unavailable."
                        )
                    try:
                        self.catalog.complete_media_import(
                            request_key,
                            request_fingerprint,
                            record,
                        )
                    except FileExistsError as exc:
                        raise MediaIdempotencyConflictError from exc
                return result
        finally:
            self.store.discard(staged)

    @staticmethod
    def _import_fingerprint(
        staged: StagedMedia,
        *,
        original_filename: str,
        declared_mime_type: str | None,
    ) -> str:
        payload = json.dumps(
            {
                "version": 1,
                "sha256": staged.sha256,
                "original_filename": original_filename,
                "declared_mime_type": declared_mime_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def _publish_import(
        self,
        *,
        original_filename: str,
        declared_mime_type: str | None,
        staged: StagedMedia,
    ) -> MediaAsset:
        if existing := self.catalog.get_media_by_checksum(staged.sha256):
            self.store.publish(
                staged.model_copy(
                    update={"storage_key": existing.storage_key}
                )
            )
            return media_asset(existing)
        probe = self.probe.probe(staged.path)
        stored = self.store.publish(staged)
        media_id = uuid4().hex
        record = MediaRecord(
            media_id=media_id,
            video_id=media_id,
            sha256=stored.sha256,
            original_filename=original_filename,
            byte_size=stored.byte_size,
            declared_mime_type=declared_mime_type,
            detected_mime_type=probe.detected_mime_type,
            container=probe.container,
            duration_seconds=probe.duration_seconds,
            streams=probe.streams,
            storage_key=stored.storage_key,
            state=MediaState.ready,
            created_at=utc_now(),
        )
        try:
            authoritative = self.catalog.put_media(record)
        except BaseException:
            try:
                retained = self.catalog.get_media_by_checksum(stored.sha256)
            except Exception:
                retained = None
            if retained is None:
                try:
                    self.store.delete(stored.storage_key)
                except OSError:
                    pass
            raise
        if authoritative.storage_key != stored.storage_key:
            try:
                self.store.delete(stored.storage_key)
            except OSError:
                pass
        return media_asset(authoritative)

    def get(self, media_id: str) -> MediaAsset:
        return media_asset(self.require_record(media_id))

    def list(self, command: ListMediaCommand) -> MediaPage:
        scope = hashlib.sha256(
            str(self.settings.repository_root.resolve()).encode()
        ).hexdigest()
        try:
            offset = decode_offset_cursor(command.cursor, scope=scope)
        except CursorError as exc:
            raise ValueError("The media cursor is invalid.") from exc
        total = self.catalog.count_media()
        if offset > total:
            raise ValueError("The media cursor is outside the result set.")
        items = tuple(
            media_asset(record)
            for record in self.catalog.list_media(
                limit=command.page_size,
                offset=offset,
            )
        )
        next_offset = offset + len(items)
        cursor = (
            encode_offset_cursor(next_offset, scope=scope)
            if next_offset < total
            else None
        )
        return MediaPage(
            items=items,
            total=total,
            next_cursor=cursor,
        )

    def require_record(self, media_id: str) -> MediaRecord:
        record = self.catalog.get_media(media_id)
        if record is None or record.state != MediaState.ready:
            raise MediaUnavailableError("The media asset is unavailable.")
        return record

    def content(self, media_id: str) -> LocalFileResource:
        record = self.require_record(media_id)
        try:
            path = self.store.verify(
                record.storage_key,
                sha256=record.sha256,
                byte_size=record.byte_size,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise MediaUnavailableError(
                "The managed media content is unavailable."
            ) from exc
        return LocalFileResource(
            path=path,
            filename=record.original_filename,
            mime_type=record.detected_mime_type,
            byte_size=record.byte_size,
            etag=record.sha256,
        )

    def resolve_local_source(self, path: Path) -> Path:
        """Canonicalize a local import source against configured boundaries."""

        if self.settings.mode != ApplicationMode.local:
            raise MediaImportNotAllowedError(
                "Local path imports are available only in local mode."
            )
        try:
            source = path.expanduser().resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise MediaUnavailableError(
                "The local media source is unavailable."
            ) from exc
        if not source.is_file():
            raise MediaUnavailableError(
                "The local media source is not a file."
            )
        roots = self.settings.trusted_local_import_roots
        if not roots:
            return source
        for configured in roots:
            try:
                root = configured.expanduser().resolve(strict=True)
            except (FileNotFoundError, OSError):
                continue
            if root.is_dir() and source.is_relative_to(root):
                return source
        raise MediaImportNotAllowedError(
            "The local media source is outside the trusted import roots."
        )
