import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from vidxp.application_models import (
    ActorOverlayProfile,
    CreateSnippetCommand,
    ImportMediaCommand,
    ListMediaCommand,
    SnippetProfile,
)
from vidxp.artifact_service import (
    ArtifactService,
    ArtifactUnavailableError,
    InvalidArtifactError,
)
from vidxp.core.artifacts import ArtifactState
from vidxp.core.contracts import CancellationToken, IndexCancelledError
from vidxp.core.media import (
    MediaProbe,
    QuarantinedMedia,
    MediaRecord,
    MediaState,
    MediaStream,
    StagedMedia,
    StoredMedia,
)
from vidxp.execution import ExecutionContext
from vidxp.infrastructure.local_artifacts import (
    FFmpegSnippetRenderer,
    LocalArtifactStore,
)
from vidxp.infrastructure.local_catalog import LocalCatalog
from vidxp.infrastructure.local_media import InvalidMediaError
from vidxp.media_service import (
    MediaIdempotencyConflictError,
    MediaService,
)
from vidxp.ports import LocalFileResource
from vidxp.settings import VidXPSettings


MEDIA_ID = "123456781234423481234567890abcde"
JOB_ID = "223456781234423481234567890abcde"
ARTIFACT_ID = "323456781234423481234567890abcde"


def record() -> MediaRecord:
    return MediaRecord(
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        sha256="1" * 64,
        original_filename="video.mp4",
        byte_size=5,
        detected_mime_type="video/mp4",
        container="mp4",
        duration_seconds=2,
        streams=(
            MediaStream(
                index=0,
                kind="video",
                codec="h264",
                width=1,
                height=1,
            ),
        ),
        storage_key="objects/11/video.mp4",
        state=MediaState.ready,
        created_at=datetime.now(timezone.utc),
    )


class MediaServiceTests(unittest.TestCase):
    def service(self, root: Path):
        catalog = Mock()
        store = Mock()
        store.publication_lock.return_value = nullcontext()
        probe = Mock()
        service = MediaService(
            settings=VidXPSettings(
                repository_root=root,
                runtime_backend="cpu",
            ),
            catalog=catalog,
            store=store,
            probe=probe,
        )
        return service, catalog, store, probe

    def test_import_probes_staging_before_publishing_and_cataloging(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"video")
            service, catalog, store, probe = self.service(root)
            staged = StagedMedia(
                sha256="1" * 64,
                byte_size=5,
                storage_key="objects/11/video.mp4",
                path=root / "staged.tmp",
            )
            stored = StoredMedia(
                sha256=staged.sha256,
                byte_size=5,
                storage_key=staged.storage_key,
                local_path=root / "managed.mp4",
            )
            store.stage_local.return_value = staged
            store.publish.return_value = stored
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=2,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            catalog.get_media_by_checksum.return_value = None
            catalog.put_media.side_effect = lambda item: item
            with patch("vidxp.media_service.uuid4") as identifier:
                identifier.return_value.hex = MEDIA_ID
                result = service.import_local(ImportMediaCommand(path=source))

        self.assertEqual(result.media_id, MEDIA_ID)
        store.publication_lock.assert_called_once_with(staged.sha256)
        probe.probe.assert_called_once_with(staged.path)
        store.publish.assert_called_once_with(staged)
        catalog.put_media.assert_called_once()
        store.discard.assert_called_once_with(staged)

    def test_invalid_probe_never_publishes_or_catalogs_media(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"video")
            service, catalog, store, probe = self.service(root)
            staged = StagedMedia(
                sha256="1" * 64,
                byte_size=5,
                storage_key="objects/11/video.mp4",
                path=root / "staged.tmp",
            )
            store.stage_local.return_value = staged
            catalog.get_media_by_checksum.return_value = None
            probe.probe.side_effect = InvalidMediaError("invalid")

            with self.assertRaises(InvalidMediaError):
                service.import_local(ImportMediaCommand(path=source))

        store.publish.assert_not_called()
        catalog.put_media.assert_not_called()
        store.discard.assert_called_once_with(staged)

    def test_quarantined_import_reuses_the_same_ingestion_pipeline(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "upload.mp4"
            source.write_bytes(b"video")
            service, catalog, store, probe = self.service(root)
            staged = StagedMedia(
                sha256="1" * 64,
                byte_size=5,
                storage_key="objects/11/video.mp4",
                path=root / "staged.tmp",
            )
            stored = StoredMedia(
                sha256=staged.sha256,
                byte_size=5,
                storage_key=staged.storage_key,
                local_path=root / "managed.mp4",
            )
            store.stage_local.return_value = staged
            store.publish.return_value = stored
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=2,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            catalog.get_media_by_checksum.return_value = None
            catalog.put_media.side_effect = lambda item: item
            with patch("vidxp.media_service.uuid4") as identifier:
                identifier.return_value.hex = MEDIA_ID
                result = service.import_quarantined(
                    QuarantinedMedia(
                        path=source,
                        original_filename="client-name.mp4",
                        declared_mime_type="video/mp4",
                    )
                )

        self.assertEqual(result.original_filename, "client-name.mp4")
        store.stage_local.assert_called_once_with(source.resolve())
        probe.probe.assert_called_once_with(staged.path)
        store.publish.assert_called_once_with(staged)
        store.discard.assert_called_once_with(staged)

    def test_quarantined_import_completes_durable_idempotency_record(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "upload.mp4"
            source.write_bytes(b"video")
            service, catalog, store, probe = self.service(root)
            staged = StagedMedia(
                sha256="1" * 64,
                byte_size=5,
                storage_key="objects/11/video.mp4",
                path=root / "staged.tmp",
            )
            stored = StoredMedia(
                sha256=staged.sha256,
                byte_size=5,
                storage_key=staged.storage_key,
                local_path=root / "managed.mp4",
            )
            store.stage_local.return_value = staged
            store.publish.return_value = stored
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=2,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            catalog.reserve_media_import.return_value = None
            catalog.get_media_by_checksum.return_value = None
            imported = record()
            catalog.put_media.return_value = imported
            catalog.get_media.return_value = imported

            result = service.import_quarantined(
                QuarantinedMedia(
                    path=source,
                    original_filename="upload.mp4",
                ),
                request_key="request-key",
            )

        self.assertEqual(result.media_id, MEDIA_ID)
        fingerprint = catalog.reserve_media_import.call_args.args[1]
        self.assertEqual(len(fingerprint), 64)
        catalog.complete_media_import.assert_called_once_with(
            "request-key",
            fingerprint,
            imported,
        )

    def test_quarantined_import_rejects_reused_key_for_other_content(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "upload.mp4"
            source.write_bytes(b"video")
            service, catalog, store, _probe = self.service(root)
            staged = StagedMedia(
                sha256="2" * 64,
                byte_size=5,
                storage_key="objects/22/video.mp4",
                path=root / "staged.tmp",
            )
            store.stage_local.return_value = staged
            catalog.reserve_media_import.side_effect = FileExistsError

            with self.assertRaises(MediaIdempotencyConflictError):
                service.import_quarantined(
                    QuarantinedMedia(
                        path=source,
                        original_filename="upload.mp4",
                    ),
                    request_key="request-key",
                )

        store.publish.assert_not_called()

    def test_local_catalog_persists_media_import_idempotency(self):
        with TemporaryDirectory() as directory:
            catalog = LocalCatalog(Path(directory) / "catalog.sqlite3")
            item = record()
            catalog.put_media(item)

            self.assertIsNone(
                catalog.reserve_media_import(
                    "request-key",
                    "f" * 64,
                )
            )
            catalog.complete_media_import(
                "request-key",
                "f" * 64,
                item,
            )
            replay = catalog.reserve_media_import(
                "request-key",
                "f" * 64,
            )

            self.assertEqual(replay, item)
            with self.assertRaises(FileExistsError):
                catalog.reserve_media_import(
                    "request-key",
                    "e" * 64,
                )

    def test_existing_checksum_is_reused_without_reprobe(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"video")
            service, catalog, store, probe = self.service(root)
            staged = StagedMedia(
                sha256="1" * 64,
                byte_size=5,
                storage_key="objects/11/video.mp4",
                path=root / "staged.tmp",
            )
            store.stage_local.return_value = staged
            catalog.get_media_by_checksum.return_value = record()

            result = service.import_local(ImportMediaCommand(path=source))

        self.assertEqual(result.media_id, MEDIA_ID)
        probe.probe.assert_not_called()
        store.publish.assert_called_once_with(staged)
        store.discard.assert_called_once_with(staged)

    def test_catalog_failure_rolls_back_new_managed_content(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "video.mp4"
            source.write_bytes(b"video")
            service, catalog, store, probe = self.service(root)
            staged = StagedMedia(
                sha256="1" * 64,
                byte_size=5,
                storage_key="objects/11/video.mp4",
                path=root / "staged.tmp",
            )
            stored = StoredMedia(
                sha256=staged.sha256,
                byte_size=5,
                storage_key=staged.storage_key,
                local_path=root / "managed.mp4",
            )
            store.stage_local.return_value = staged
            store.publish.return_value = stored
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=2,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            catalog.get_media_by_checksum.return_value = None
            catalog.put_media.side_effect = RuntimeError("catalog failed")

            with self.assertRaisesRegex(RuntimeError, "catalog failed"):
                service.import_local(ImportMediaCommand(path=source))

        store.delete.assert_called_once_with(stored.storage_key)

    def test_media_pages_are_bounded_and_cursor_scoped(self):
        with TemporaryDirectory() as directory:
            service, catalog, _store, _probe = self.service(Path(directory))
            catalog.count_media.return_value = 3
            catalog.list_media.side_effect = [
                (record(), record().model_copy(
                    update={
                        "media_id": "223456781234423481234567890abcde",
                        "video_id": "223456781234423481234567890abcde",
                        "sha256": "2" * 64,
                    }
                )),
                (record().model_copy(
                    update={
                        "media_id": "323456781234423481234567890abcde",
                        "video_id": "323456781234423481234567890abcde",
                        "sha256": "3" * 64,
                    }
                ),),
            ]

            first = service.list(ListMediaCommand(page_size=2))
            second = service.list(
                ListMediaCommand(page_size=2, cursor=first.next_cursor)
            )

        self.assertEqual(first.total, 3)
        self.assertEqual(len(first.items), 2)
        self.assertIsNotNone(first.next_cursor)
        self.assertEqual(len(second.items), 1)
        self.assertIsNone(second.next_cursor)
        self.assertEqual(
            catalog.list_media.call_args_list[1].kwargs["offset"],
            2,
        )


class ArtifactServiceTests(unittest.TestCase):
    def test_actor_overlay_uses_catalog_media_and_store_owned_destination(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            media = Mock()
            media.content.return_value = LocalFileResource(
                path=source,
                filename="source.mp4",
                mime_type="video/mp4",
                byte_size=6,
                etag="1" * 64,
            )
            catalog = Mock()
            catalog.get_artifact_by_request.return_value = None
            catalog.put_artifact.side_effect = lambda item: item
            renderer = Mock()
            renderer.render.side_effect = (
                lambda _source, destination, _cluster, _detections, **_kwargs:
                destination.write_bytes(b"rendered")
            )
            probe = Mock()
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=1,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            store = LocalArtifactStore(root / "artifacts")
            service = ArtifactService(
                catalog=catalog,
                store=store,
                media=media,
                probe=probe,
                actor_renderer=renderer,
                snippet_renderer=Mock(),
                max_snippet_duration_seconds=300,
            )
            with patch("vidxp.artifact_service.uuid4") as identifier:
                identifier.return_value.hex = ARTIFACT_ID
                result = service.create_actor_overlay(
                    media_id=MEDIA_ID,
                    generation_id="223456781234423481234567890abcde",
                    cluster_id="cluster",
                    detections=[{"frame_index": 1}],
                    profile=ActorOverlayProfile.default,
                )

        self.assertEqual(result.artifact_id, ARTIFACT_ID)
        self.assertEqual(result.media_id, MEDIA_ID)
        self.assertEqual(result.byte_size, len(b"rendered"))
        renderer.render.assert_called_once()
        self.assertNotIn("path", result.model_dump(mode="json"))
        catalog.put_artifact.assert_called_once()

    def test_progress_failure_after_catalog_commit_keeps_artifact_content(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            media = Mock()
            media.content.return_value = LocalFileResource(
                path=source,
                filename="source.mp4",
                mime_type="video/mp4",
                byte_size=6,
                etag="1" * 64,
            )
            catalog = Mock()
            catalog.get_artifact_by_request.return_value = None
            catalog.put_artifact.side_effect = lambda item: item
            renderer = Mock()
            renderer.render.side_effect = (
                lambda _source, destination, _cluster, _detections, **_kwargs:
                destination.write_bytes(b"rendered")
            )
            probe = Mock()
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=1,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            store = LocalArtifactStore(root / "artifacts")
            service = ArtifactService(
                catalog=catalog,
                store=store,
                media=media,
                probe=probe,
                actor_renderer=renderer,
                snippet_renderer=Mock(),
                max_snippet_duration_seconds=300,
            )

            def fail_final_progress(event):
                if event["stage"] == "complete":
                    raise RuntimeError("progress unavailable")

            with (
                patch("vidxp.artifact_service.uuid4") as identifier,
                self.assertRaisesRegex(RuntimeError, "progress unavailable"),
            ):
                identifier.return_value.hex = ARTIFACT_ID
                service.create_actor_overlay(
                    media_id=MEDIA_ID,
                    generation_id="223456781234423481234567890abcde",
                    cluster_id="cluster",
                    detections=[],
                    profile=ActorOverlayProfile.default,
                    execution=ExecutionContext(progress=fail_final_progress),
                )

            committed = catalog.put_artifact.call_args.args[0]
            self.assertTrue(store.resolve(committed.storage_key).is_file())

    def test_durable_replay_recovers_object_published_before_catalog_commit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            media = Mock()
            media.content.return_value = LocalFileResource(
                path=source,
                filename="source.mp4",
                mime_type="video/mp4",
                byte_size=6,
                etag="1" * 64,
            )
            catalog = Mock()
            catalog.get_artifact_by_request.return_value = None
            catalog.put_artifact.side_effect = lambda item: item
            renderer = Mock()
            probe = Mock()
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=1,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            store = LocalArtifactStore(root / "artifacts")
            orphan = store.stage(JOB_ID, suffix=".mp4")
            orphan.path.write_bytes(b"rendered-before-crash")
            store.publish(orphan)
            service = ArtifactService(
                catalog=catalog,
                store=store,
                media=media,
                probe=probe,
                actor_renderer=renderer,
                snippet_renderer=Mock(),
                max_snippet_duration_seconds=300,
            )

            result = service.create_actor_overlay(
                media_id=MEDIA_ID,
                generation_id="423456781234423481234567890abcde",
                cluster_id="cluster",
                detections=[],
                profile=ActorOverlayProfile.default,
                job_id=JOB_ID,
                execution=ExecutionContext(job_id=JOB_ID),
            )

            self.assertEqual(result.artifact_id, JOB_ID)
            renderer.render.assert_not_called()
            catalog.put_artifact.assert_called_once()

    def test_ffmpeg_snippet_renderer_terminates_on_cancellation(self):
        cancellation = CancellationToken()
        cancellation.cancel()
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        renderer = FFmpegSnippetRenderer()

        with (
            patch(
                "vidxp.infrastructure.local_artifacts.subprocess.Popen",
                return_value=process,
            ),
            self.assertRaises(IndexCancelledError),
        ):
            renderer.render(
                Path("source.mp4"),
                Path("snippet.mp4"),
                start_seconds=0,
                end_seconds=1,
                compatible_mp4=True,
                cancellation=cancellation,
                progress=None,
            )

        process.terminate.assert_called_once()

    def test_actor_renderer_failure_leaves_no_staged_or_ready_artifact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            media = Mock()
            media.content.return_value = LocalFileResource(
                path=source,
                filename="source.mp4",
                mime_type="video/mp4",
                byte_size=6,
                etag="1" * 64,
            )
            catalog = Mock()
            catalog.get_artifact_by_request.return_value = None
            renderer = Mock()
            renderer.render.side_effect = RuntimeError("render failed")
            store = LocalArtifactStore(root / "artifacts")
            service = ArtifactService(
                catalog=catalog,
                store=store,
                media=media,
                probe=Mock(),
                actor_renderer=renderer,
                snippet_renderer=Mock(),
                max_snippet_duration_seconds=300,
            )
            with (
                patch("vidxp.artifact_service.uuid4") as identifier,
                self.assertRaisesRegex(RuntimeError, "render failed"),
            ):
                identifier.return_value.hex = ARTIFACT_ID
                service.create_actor_overlay(
                    media_id=MEDIA_ID,
                    generation_id="223456781234423481234567890abcde",
                    cluster_id="cluster",
                    detections=[],
                    profile=ActorOverlayProfile.default,
                )

            self.assertFalse(any(store.staging.glob("*")))
            catalog.put_artifact.assert_not_called()

    def test_invalid_rendered_media_is_not_published_or_cataloged(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            media = Mock()
            media.content.return_value = LocalFileResource(
                path=source,
                filename="source.mp4",
                mime_type="video/mp4",
                byte_size=6,
                etag="1" * 64,
            )
            catalog = Mock()
            catalog.get_artifact_by_request.return_value = None
            renderer = Mock()
            renderer.render.side_effect = (
                lambda _source, destination, _cluster, _detections, **_kwargs:
                destination.write_bytes(b"not-video")
            )
            probe = Mock()
            probe.probe.side_effect = InvalidMediaError("invalid")
            store = LocalArtifactStore(root / "artifacts")
            service = ArtifactService(
                catalog=catalog,
                store=store,
                media=media,
                probe=probe,
                actor_renderer=renderer,
                snippet_renderer=Mock(),
                max_snippet_duration_seconds=300,
            )

            with self.assertRaises(InvalidArtifactError):
                service.create_actor_overlay(
                    media_id=MEDIA_ID,
                    generation_id="223456781234423481234567890abcde",
                    cluster_id="cluster",
                    detections=[],
                    profile=ActorOverlayProfile.default,
                )

            self.assertFalse(any(store.staging.glob("*")))
            catalog.put_artifact.assert_not_called()

    def test_snippet_is_bounded_and_reuses_ready_request(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            media = Mock()
            media.require_record.return_value = record()
            media.content.return_value = LocalFileResource(
                path=source,
                filename="source.mp4",
                mime_type="video/mp4",
                byte_size=6,
                etag="1" * 64,
            )
            catalog = Mock()
            catalog.get_artifact_by_request.return_value = None
            catalog.put_artifact.side_effect = lambda item: item
            renderer = Mock()
            renderer.render.side_effect = (
                lambda _source, destination, **_kwargs:
                destination.write_bytes(b"rendered")
            )
            probe = Mock()
            probe.probe.return_value = MediaProbe(
                detected_mime_type="video/mp4",
                container="mp4",
                duration_seconds=1,
                streams=(
                    MediaStream(
                        index=0,
                        kind="video",
                        codec="h264",
                        width=1,
                        height=1,
                    ),
                ),
            )
            store = LocalArtifactStore(root / "artifacts")
            service = ArtifactService(
                catalog=catalog,
                store=store,
                media=media,
                probe=probe,
                actor_renderer=Mock(),
                snippet_renderer=renderer,
                max_snippet_duration_seconds=1,
            )
            command = CreateSnippetCommand(
                media_id=MEDIA_ID,
                start_seconds=0,
                end_seconds=1,
                profile=SnippetProfile.compatible_mp4,
            )

            created = service.create_snippet(command)
            catalog.get_artifact_by_request.return_value = (
                catalog.put_artifact.call_args.args[0]
            )
            reused = service.create_snippet(command)
            cached_record = catalog.put_artifact.call_args.args[0]
            store.delete(cached_record.storage_key)
            regenerated = service.create_snippet(command)

            self.assertEqual(reused.artifact_id, created.artifact_id)
            self.assertNotEqual(regenerated.artifact_id, created.artifact_id)
            self.assertEqual(renderer.render.call_count, 2)
            catalog.invalidate_artifact_request.assert_called_once_with(
                cached_record.request_key,
                cached_record.artifact_id,
            )

    def test_expired_artifact_is_unavailable(self):
        catalog = Mock()
        expired = Mock()
        catalog.get_artifact.return_value = expired
        expired.state = ArtifactState.ready
        expired.expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        service = ArtifactService(
            catalog=catalog,
            store=Mock(),
            media=Mock(),
            probe=Mock(),
            actor_renderer=Mock(),
            snippet_renderer=Mock(),
            max_snippet_duration_seconds=1,
        )

        with self.assertRaises(ArtifactUnavailableError):
            service.require_record(ARTIFACT_ID)


if __name__ == "__main__":
    unittest.main()
