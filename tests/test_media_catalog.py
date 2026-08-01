import json
import os
import subprocess
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vidxp.core.artifacts import ArtifactKind, ArtifactRecord
from vidxp.core.media import (
    MediaImportLimitError,
    MediaRecord,
    MediaState,
    MediaStream,
)
from vidxp.infrastructure.local_catalog import LocalCatalog
from vidxp.infrastructure.local_artifacts import LocalArtifactStore
from vidxp.infrastructure.local_media import (
    FFprobeMediaProbe,
    InvalidMediaError,
    LocalMediaStore,
)
from vidxp.infrastructure.local_files import prepare_managed_destination
from vidxp.infrastructure.local_objects import LocalObjectStore


MEDIA_ID = "123456781234423481234567890abcde"
OTHER_MEDIA_ID = "223456781234423481234567890abcde"
ARTIFACT_ID = "323456781234423481234567890abcde"


def media_record(
    media_id: str = MEDIA_ID,
    *,
    checksum: str = "1" * 64,
) -> MediaRecord:
    return MediaRecord(
        media_id=media_id,
        video_id=media_id,
        sha256=checksum,
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
        storage_key=f"objects/{checksum[:2]}/{checksum}.mp4",
        state=MediaState.ready,
        created_at=datetime.now(timezone.utc),
    )


class LocalCatalogTests(unittest.TestCase):
    def test_catalog_enforces_sqlite_integrity_and_schema_version(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "catalog.sqlite3"
            catalog = LocalCatalog(database)
            with catalog.engine.connect() as connection:
                self.assertEqual(
                    set(
                        connection.exec_driver_sql(
                            "SELECT name FROM sqlite_master "
                            "WHERE type = 'table'"
                        ).scalars()
                    ),
                    {
                        "artifact_requests",
                        "artifacts",
                        "catalog_metadata",
                        "media",
                        "media_import_requests",
                        "upload_intents",
                        "upload_quota",
                        "upload_session_files",
                        "upload_sessions",
                    },
                )
                self.assertEqual(
                    connection.exec_driver_sql(
                        "SELECT schema_version FROM catalog_metadata"
                    ).scalar_one(),
                    4,
                )
                self.assertEqual(
                    connection.exec_driver_sql(
                        "PRAGMA foreign_keys"
                    ).scalar_one(),
                    1,
                )
                self.assertEqual(
                    connection.exec_driver_sql(
                        "PRAGMA busy_timeout"
                    ).scalar_one(),
                    30_000,
                )
            with catalog.transaction() as connection:
                connection.exec_driver_sql(
                    "UPDATE catalog_metadata SET schema_version = 999"
                )
            catalog.close()

            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                LocalCatalog(database)

    def test_catalog_persists_and_deduplicates_media_by_checksum(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "catalog.sqlite3"
            first = LocalCatalog(database)
            record = media_record()
            self.assertEqual(first.put_media(record), record)

            reopened = LocalCatalog(database)
            self.assertEqual(reopened.get_media(MEDIA_ID), record)
            duplicate = media_record(OTHER_MEDIA_ID)
            self.assertEqual(reopened.put_media(duplicate), record)
            self.assertEqual(reopened.list_media(limit=10), (record,))

    def test_artifact_requires_cataloged_media_and_survives_reopen(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "catalog.sqlite3"
            catalog = LocalCatalog(database)
            catalog.put_media(media_record())
            artifact = ArtifactRecord(
                artifact_id=ARTIFACT_ID,
                media_id=MEDIA_ID,
                request_key="3" * 64,
                kind=ArtifactKind.actor_overlay,
                profile="default",
                mime_type="video/mp4",
                byte_size=10,
                sha256="2" * 64,
                storage_key=f"objects/32/{ARTIFACT_ID}.mp4",
                created_at=datetime.now(timezone.utc),
            )

            catalog.put_artifact(artifact)

            self.assertEqual(
                LocalCatalog(database).get_artifact(ARTIFACT_ID),
                artifact,
            )
            self.assertEqual(
                LocalCatalog(database).get_artifact_by_request("3" * 64),
                artifact,
            )


class LocalMediaStoreTests(unittest.TestCase):
    def test_import_is_staged_hashed_once_and_atomically_published(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            store = LocalMediaStore(root / "media", max_bytes=100)

            staged = store.stage_local(source)
            self.assertEqual(staged.byte_size, 5)
            self.assertEqual(
                staged.sha256,
                "0cab1c9617404faf2b24e221e189ca5945813e14"
                "d3f766345b09ca13bbe28ffc",
            )
            self.assertTrue(staged.path.is_file())

            with store.publication_lock(staged.sha256):
                stored = store.publish(staged)

            self.assertFalse(staged.path.exists())
            self.assertEqual(store.resolve(stored.storage_key), stored.local_path)
            self.assertEqual(stored.local_path.read_bytes(), b"video")

    def test_size_limit_and_unsafe_storage_keys_fail_closed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"too-large")
            store = LocalMediaStore(root / "media", max_bytes=3)

            with self.assertRaisesRegex(MediaImportLimitError, "import limit"):
                store.stage_local(source)
            with self.assertRaises(ValueError):
                store.resolve("../outside.mp4")

    def test_managed_storage_rejects_symlink_escapes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            media_root = root / "media"
            (media_root / "objects").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (outside / "secret.mp4").write_bytes(b"secret")
            link = media_root / "objects" / "escape"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlink creation is unavailable: {exc}")

            store = LocalMediaStore(media_root, max_bytes=100)
            with self.assertRaises((FileNotFoundError, PermissionError)):
                store.resolve("objects/escape/secret.mp4")

    def test_destination_rejects_junction_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            junction = root / "objects" / "junction"
            junction.mkdir(parents=True)
            original = getattr(Path, "is_junction", lambda _item: False)

            with patch.object(
                Path,
                "is_junction",
                lambda item: item == junction or original(item),
                create=True,
            ):
                with self.assertRaises(PermissionError):
                    prepare_managed_destination(
                        root,
                        "objects/junction/video.mp4",
                    )

    def test_destination_syncs_each_new_directory_entry(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "media"
            with patch(
                "vidxp.infrastructure.local_files.sync_parent_directory"
            ) as sync:
                prepare_managed_destination(
                    root,
                    "objects/aa/video.mp4",
                )

        self.assertEqual(
            [call.args[0] for call in sync.call_args_list],
            [
                root.parent,
                root,
                root / "objects",
            ],
        )

    def test_failed_publication_rollback_syncs_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "staged.mp4"
            source.write_bytes(b"video")
            store = LocalObjectStore(root / "objects")
            destination = store.root / "aa" / "video.mp4"
            destination.parent.mkdir(parents=True)
            original_resolve = Path.resolve

            def resolve(path, *args, **kwargs):
                if path == destination:
                    raise OSError("post-publication validation failed")
                return original_resolve(path, *args, **kwargs)

            with (
                patch.object(Path, "resolve", resolve),
                patch(
                    "vidxp.infrastructure.local_files."
                    "sync_parent_directory"
                ) as sync,
                self.assertRaisesRegex(
                    OSError,
                    "post-publication validation failed",
                ),
            ):
                store.publish(
                    source,
                    "aa/video.mp4",
                    expected_sha256=None,
                    replace_corrupt=False,
                )

            self.assertFalse(destination.exists())
            self.assertEqual(
                [call.args[0] for call in sync.call_args_list],
                [destination.parent, root, destination.parent],
            )

    def test_publication_rejects_symlinked_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            store = LocalMediaStore(root / "media", max_bytes=100)
            staged = store.stage_local(source)
            first_parent = store.root / "objects" / staged.sha256[:2]
            outside = root / "outside"
            outside.mkdir()
            first_parent.parent.mkdir(parents=True)
            try:
                first_parent.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                store.discard(staged)
                self.skipTest(f"Symlink creation is unavailable: {exc}")

            with self.assertRaises(PermissionError):
                store.publish(staged)
            self.assertEqual(tuple(outside.iterdir()), ())
            store.discard(staged)

    def test_reimport_repairs_same_size_corruption(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            store = LocalMediaStore(root / "media", max_bytes=100)
            first = store.publish(store.stage_local(source))
            first.local_path.write_bytes(b"bideo")

            repaired = store.publish(store.stage_local(source))

            self.assertEqual(repaired.local_path.read_bytes(), b"video")
            self.assertEqual(
                store.verify(
                    repaired.storage_key,
                    sha256=repaired.sha256,
                    byte_size=repaired.byte_size,
                ),
                repaired.local_path,
            )

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_publication_rejects_windows_junction_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            store = LocalMediaStore(root / "media", max_bytes=100)
            staged = store.stage_local(source)
            parent = store.objects / staged.sha256[:2]
            parent.parent.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            result = subprocess.run(
                [
                    "cmd",
                    "/c",
                    "mklink",
                    "/J",
                    str(parent),
                    str(outside),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                store.discard(staged)
                self.skipTest("Junction creation is unavailable.")

            with self.assertRaises(PermissionError):
                store.publish(staged)
            self.assertEqual(tuple(outside.iterdir()), ())
            store.discard(staged)


class FFprobeTests(unittest.TestCase):
    def test_probe_normalizes_stream_and_container_metadata(self):
        payload = {
            "format": {"format_name": "mov,mp4", "duration": "2.5"},
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "channels": 2,
                    "sample_rate": "48000",
                },
            ],
        }
        with patch.object(
            FFprobeMediaProbe,
            "_output",
            return_value=json.dumps(payload).encode(),
        ):
            result = FFprobeMediaProbe().probe(Path("video.mp4"))

        self.assertEqual(result.container, "mp4")
        self.assertEqual(result.detected_mime_type, "video/mp4")
        self.assertEqual(result.streams[0].codec, "h264")
        self.assertEqual(result.streams[1].sample_rate, 48000)

    def test_probe_rejects_malformed_or_non_video_results(self):
        with patch.object(
            FFprobeMediaProbe,
            "_output",
            return_value=b'{"format":{"format_name":"mp4","duration":"nan"}}',
        ):
            with self.assertRaises(InvalidMediaError):
                FFprobeMediaProbe().probe(Path("bad.mp4"))

    def test_probe_distinguishes_matroska_from_webm_by_extension(self):
        payload = {
            "format": {
                "format_name": "matroska,webm",
                "duration": "2.5",
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                }
            ],
        }
        with patch.object(
            FFprobeMediaProbe,
            "_output",
            return_value=json.dumps(payload).encode(),
        ):
            matroska = FFprobeMediaProbe().probe(Path("video.mkv"))
            webm = FFprobeMediaProbe().probe(Path("video.webm"))

        self.assertEqual(matroska.detected_mime_type, "video/x-matroska")
        self.assertEqual(webm.detected_mime_type, "video/webm")


class LocalArtifactStoreTests(unittest.TestCase):
    def test_publication_rejects_symlinked_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalArtifactStore(root / "artifacts")
            staged = store.stage(ARTIFACT_ID, suffix=".mp4")
            staged.path.write_bytes(b"video")
            outside = root / "outside"
            outside.mkdir()
            parent = store.objects / ARTIFACT_ID[:2]
            parent.parent.mkdir(parents=True)
            try:
                parent.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                store.discard(staged)
                self.skipTest(f"Symlink creation is unavailable: {exc}")

            with self.assertRaises(PermissionError):
                store.publish(staged)
            self.assertEqual(tuple(outside.iterdir()), ())
            store.discard(staged)


if __name__ == "__main__":
    unittest.main()
