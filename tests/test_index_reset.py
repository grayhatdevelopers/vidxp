import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from vidxp.core.contracts import (
    INDEX_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    IndexConfig,
    IndexSchemaError,
)
from vidxp.core.manifest import MANIFEST_FILE, sha256_file, write_json_atomic
from vidxp.core.snapshots import GenerationReference
from vidxp.core.storage import IndexStorage
from vidxp.infrastructure.local_snapshots import LocalSnapshotRepository


MEDIA_ID = "123456781234423481234567890abcde"
OTHER_MEDIA_ID = "223456781234423481234567890abcde"
INPUT_SHA = "b" * 64
INCOMPATIBLE_SCHEMA = INDEX_SCHEMA_VERSION - 1


class StaleRepositoryTests(unittest.TestCase):
    """Recovery after an intentional index schema change (#167)."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = LocalSnapshotRepository(
            Path(self.temporary.name) / "indexes"
        )
        self.repository.ensure_directories()
        self.config = IndexConfig.local(
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.store,
        )

    def write_generation(
        self,
        media_id: str,
        *,
        index_schema_version: int,
    ) -> GenerationReference:
        generation_id = self.repository.new_generation_id()
        config = replace(
            self.config,
            video_id=media_id,
            generation_id=generation_id,
            generation_directory=self.repository.generation_directory(
                generation_id
            ),
        )
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "index_schema_version": index_schema_version,
            "dataset": config.dataset,
            "split": config.split,
            "run_id": config.run_id,
            "generation_id": generation_id,
            "state": "complete",
            "created_at": now,
            "updated_at": now,
            "completed_at": now,
            "config_fingerprint": config.fingerprint(),
            "execution_fingerprint": "e" * 64,
            "configuration": config.to_dict(),
            "models": {},
            "git": {},
            "environment": {},
            "inputs": {
                media_id: {
                    "sha256": INPUT_SHA,
                    "checksums": {"video": INPUT_SHA},
                    "size": 1,
                    "source_name": f"{media_id}.mp4",
                    "path": None,
                    "metadata": {},
                }
            },
            "videos": {
                media_id: {
                    "state": "complete",
                    "started_at": now,
                    "stages": {},
                    "completed_at": now,
                    "summary": {},
                }
            },
            "completed_videos": [media_id],
            "failed_videos": [],
            "interrupted_videos": [],
            "processed_frames": 0,
            "record_counts": {"scene": 1},
            "store_size_bytes_at_commit": 123,
        }
        manifest_path = config.run_directory / MANIFEST_FILE
        write_json_atomic(manifest_path, manifest)
        return GenerationReference(
            generation_id=generation_id,
            media_id=media_id,
            manifest_sha256=sha256_file(manifest_path),
            input_sha256=INPUT_SHA,
            config_fingerprint=config.fingerprint(),
            modalities=("scene",),
            record_counts={"scene": 1},
            store_size_bytes_at_commit=123,
        )

    def publish_stale_snapshot(self, *media_ids: str) -> None:
        """Publish a snapshot whose generations an older schema wrote."""

        generations = {
            media_id: self.write_generation(
                media_id,
                index_schema_version=INCOMPATIBLE_SCHEMA,
            )
            for media_id in media_ids
        }
        self.repository._publish(
            generations=generations,
            config_fingerprint=self.config.fingerprint(),
            configuration=self.repository.snapshot_configuration(self.config),
        )

    def test_an_incompatible_index_cannot_be_read_cleared_or_inspected(self):
        self.publish_stale_snapshot(MEDIA_ID)

        for operation in (
            self.repository.read_active,
            self.repository.clear,
            self.repository.status,
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(IndexSchemaError):
                    operation()

    def test_the_snapshot_document_is_still_readable(self):
        self.publish_stale_snapshot(MEDIA_ID)

        resolved = self.repository.read_active_document()

        self.assertIsNotNone(resolved)
        assert resolved is not None
        _pointer, snapshot = resolved
        self.assertEqual(tuple(snapshot.generations), (MEDIA_ID,))

    def test_describing_the_active_generations_changes_nothing(self):
        self.publish_stale_snapshot(MEDIA_ID, OTHER_MEDIA_ID)

        summary = self.repository.describe_active_generations()
        again = self.repository.describe_active_generations()

        self.assertTrue(summary["discarded"])
        self.assertEqual(
            summary["media_ids"],
            tuple(sorted((MEDIA_ID, OTHER_MEDIA_ID))),
        )
        self.assertEqual(summary["modalities"], ("scene",))
        self.assertEqual(len(summary["generation_ids"]), 2)
        self.assertEqual(summary, again)

    def test_discarding_recovers_a_repository_an_older_schema_wrote(self):
        self.publish_stale_snapshot(MEDIA_ID)

        discarded = self.repository.discard_generations()

        self.assertTrue(discarded)
        active = self.repository.read_active()
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active.generations, {})
        self.assertEqual(self.repository.status()["state"], "empty")

    def test_discarding_an_empty_repository_reports_no_work(self):
        self.assertFalse(self.repository.discard_generations())
        self.assertEqual(
            self.repository.describe_active_generations()["discarded"],
            False,
        )

    def test_a_compatible_index_is_unaffected_by_the_new_read_path(self):
        reference = self.write_generation(
            MEDIA_ID,
            index_schema_version=INDEX_SCHEMA_VERSION,
        )
        self.repository._publish(
            generations={MEDIA_ID: reference},
            config_fingerprint=self.config.fingerprint(),
            configuration=self.repository.snapshot_configuration(self.config),
        )

        validated = self.repository.read_active()
        document = self.repository.read_active_document()

        self.assertIsNotNone(validated)
        self.assertIsNotNone(document)
        assert validated is not None and document is not None
        self.assertEqual(validated.snapshot_id, document[1].snapshot_id)


class DropAllCollectionsTests(unittest.TestCase):
    def storage(self, names: tuple[str, ...]) -> IndexStorage:
        with TemporaryDirectory() as directory:
            config = IndexConfig.local(
                enabled_modalities=("scene",),
                collection_names={"scene": "scene"},
                storage_directory=Path(directory),
            )
            storage = IndexStorage.__new__(IndexStorage)
            storage.config = config
            storage._names = dict(config.collection_names)
            storage._collections = {"scene": object()}
            storage.client = Mock()
            storage.client.list_collections.return_value = [
                Mock(name=name) for name in names
            ]
            for collection, name in zip(
                storage.client.list_collections.return_value,
                names,
            ):
                collection.name = name
            return storage

    def test_every_collection_is_dropped_even_when_unnamed_by_the_config(self):
        storage = self.storage(("scene", "retired_profile"))

        dropped = storage.drop_all_collections()

        self.assertEqual(dropped, ("retired_profile", "scene"))
        self.assertEqual(
            sorted(
                call.args[0]
                for call in storage.client.delete_collection.call_args_list
            ),
            ["retired_profile", "scene"],
        )
        self.assertEqual(storage._collections, {})

    def test_an_empty_store_drops_nothing(self):
        storage = self.storage(())

        self.assertEqual(storage.drop_all_collections(), ())
        storage.client.delete_collection.assert_not_called()


class ResetIndexOperationTests(unittest.TestCase):
    def application(self, root: str | Path, *, backend: Mock):
        from vidxp.application import VidXPApplication
        from vidxp.capabilities.registry import create_capability_registry
        from vidxp.repository_layout import RepositoryLayout
        from vidxp.runtime import ModelRuntime
        from vidxp.settings import VidXPSettings

        settings = VidXPSettings(
            repository_root=Path(root),
            runtime_backend="cpu",
        )
        return VidXPApplication(
            settings=settings,
            layout=RepositoryLayout(root=Path(root)),
            registry=create_capability_registry(),
            runtime=ModelRuntime(settings),
            index_backend=backend,
            media=Mock(),
            artifacts=Mock(),
            index_status=lambda: None,
        )

    def test_the_backend_summary_becomes_a_typed_result(self):
        backend = Mock()
        backend.reset.return_value = {
            "discarded": True,
            "media_ids": (MEDIA_ID,),
            "generation_ids": ("323456781234423481234567890abcde",),
            "modalities": ("scene",),
            "collections": ("scene",),
        }
        with TemporaryDirectory() as root:
            application = self.application(root, backend=backend)

            result = application.reset_index()

        self.assertTrue(result.discarded)
        self.assertEqual(result.media_ids, (MEDIA_ID,))
        self.assertEqual(result.modalities, ("scene",))
        self.assertEqual(result.collections, ("scene",))

    def test_an_untouched_repository_reports_nothing_discarded(self):
        backend = Mock()
        backend.reset.return_value = {
            "discarded": False,
            "media_ids": (),
            "generation_ids": (),
            "modalities": (),
            "collections": (),
        }
        with TemporaryDirectory() as root:
            application = self.application(root, backend=backend)

            result = application.reset_index()

        self.assertFalse(result.discarded)
        self.assertEqual(result.media_ids, ())
        self.assertEqual(result.collections, ())


if __name__ == "__main__":
    unittest.main()
