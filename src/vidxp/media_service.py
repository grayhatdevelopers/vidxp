from __future__ import annotations

from pathlib import Path
import base64
import hashlib
import json
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
    StagedMedia,
    MediaUnavailableError,
    utc_now,
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
        source = self._resolve_import_source(command.path)
        staged = self.store.stage_local(source)
        try:
            with self.store.publication_lock(staged.sha256):
                return self._publish_import(command, source, staged)
        finally:
            self.store.discard(staged)

    def _publish_import(
        self,
        command: ImportMediaCommand,
        source: Path,
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
            original_filename=command.original_filename or source.name,
            byte_size=stored.byte_size,
            declared_mime_type=command.declared_mime_type,
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
        offset = self._decode_cursor(command.cursor, scope)
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
            self._encode_cursor(next_offset, scope)
            if next_offset < total
            else None
        )
        return MediaPage(
            items=items,
            total=total,
            next_cursor=cursor,
        )

    @staticmethod
    def _encode_cursor(offset: int, scope: str) -> str:
        payload = json.dumps(
            {"version": 1, "scope": scope, "offset": offset},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return base64.urlsafe_b64encode(payload).decode()

    @staticmethod
    def _decode_cursor(cursor: str | None, scope: str) -> int:
        if cursor is None:
            return 0
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(cursor.encode()).decode()
            )
            if (
                not isinstance(payload, dict)
                or payload.get("version") != 1
                or payload.get("scope") != scope
            ):
                raise ValueError
            offset = int(payload["offset"])
        except (
            KeyError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("The media cursor is invalid.") from exc
        if offset < 0:
            raise ValueError("The media cursor is invalid.")
        return offset

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

    def _resolve_import_source(self, path: Path) -> Path:
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
