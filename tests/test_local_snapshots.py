import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from unittest.mock import MagicMock, Mock

from vidxp.core.contracts import (
    CancellationToken,
    INDEX_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    IndexConfig,
    IndexCancelledError,
    IndexSchemaError,
    StorageRecord,
)
from vidxp.core.storage import IndexStorage
from vidxp.core.manifest import MANIFEST_FILE, write_json_atomic
from vidxp.infrastructure.local_snapshots import LocalSnapshotRepository
from vidxp.infrastructure.local_index import LocalIndexBackend


class LocalSnapshotRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = LocalSnapshotRepository(
            Path(self.temporary.name) / "indexes"
        )
        self.config = IndexConfig.local(
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.store,
        )

    def generation(
        self,
        media_id: str,
        *,
        input_sha: str,
        store_size_bytes_at_commit: int | None = 123,
    ):
        generation_id = self.repository.new_generation_id()
        config = replace(
            self.config,
            video_id=media_id,
            generation_id=generation_id,
            generation_directory=self.repository.generation_directory(
                generation_id
            ),
        )
        return config, self.write_generation(
            config,
            media_id=media_id,
            input_sha=input_sha,
            store_size_bytes_at_commit=store_size_bytes_at_commit,
        )

    def write_generation(
        self,
        config: IndexConfig,
        *,
        media_id: str,
        input_sha: str,
        record_counts: dict[str, int] | None = None,
        store_size_bytes_at_commit: int | None = 123,
    ):
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "dataset": config.dataset,
            "split": config.split,
            "run_id": config.run_id,
            "generation_id": config.generation_id,
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
                    "sha256": input_sha,
                    "checksums": {"video": input_sha},
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
            "record_counts": {
                modality: (record_counts or {}).get(modality, 0)
                for modality in config.enabled_modalities
            },
            "store_size_bytes_at_commit": store_size_bytes_at_commit,
        }
        write_json_atomic(
            config.run_directory / MANIFEST_FILE,
            manifest,
        )
        return self.repository.generation_reference(
            generation_id=str(config.generation_id),
            media_id=media_id,
        )

    def test_unknown_store_size_round_trips_through_snapshot_metadata(self):
        config, reference = self.generation(
            "a",
            input_sha="a" * 64,
            store_size_bytes_at_commit=None,
        )

        snapshot = self.repository.publish_generation(reference, config)

        self.assertIsNone(reference.store_size_bytes_at_commit)
        self.assertIsNone(
            snapshot.generations["a"].store_size_bytes_at_commit
        )

    def test_add_reindex_remove_and_clear_publish_immutable_snapshots(self):
        config_a1, a1 = self.generation("a", input_sha="a" * 64)
        snapshot_a1 = self.repository.publish_generation(a1, config_a1)
        original_a1 = (
            self.repository.snapshots / f"{snapshot_a1.snapshot_id}.json"
        ).read_bytes()

        config_b1, b1 = self.generation("b", input_sha="b" * 64)
        snapshot_b1 = self.repository.publish_generation(b1, config_b1)
        self.assertEqual(
            set(snapshot_b1.generations),
            {"a", "b"},
        )

        config_a2, a2 = self.generation("a", input_sha="c" * 64)
        snapshot_a2 = self.repository.publish_generation(a2, config_a2)
        self.assertEqual(
            snapshot_a2.generations["a"].generation_id,
            a2.generation_id,
        )
        self.assertEqual(
            snapshot_a2.generations["b"].generation_id,
            b1.generation_id,
        )
        self.assertEqual(
            (
                self.repository.snapshots
                / f"{snapshot_a1.snapshot_id}.json"
            ).read_bytes(),
            original_a1,
        )
        self.assertTrue(
            self.repository.generation_directory(a1.generation_id).is_dir()
        )

        self.assertTrue(self.repository.remove("a"))
        self.assertEqual(
            set(self.repository.read_active(required=True).generations),
            {"b"},
        )
        self.assertFalse(self.repository.remove("missing"))
        self.assertTrue(self.repository.clear())
        self.assertEqual(
            self.repository.status()["state"],
            "empty",
        )
        self.assertFalse(self.repository.clear())

    def test_pointer_failure_preserves_previous_active_snapshot(self):
        config_a1, a1 = self.generation("a", input_sha="a" * 64)
        previous = self.repository.publish_generation(a1, config_a1)
        pointer_before = self.repository.active_pointer.read_bytes()
        config_a2, a2 = self.generation("a", input_sha="b" * 64)

        real_write = write_json_atomic

        def fail_pointer(path, payload):
            if path == self.repository.active_pointer:
                raise OSError("injected pointer failure")
            return real_write(path, payload)

        with (
            patch(
                "vidxp.infrastructure.local_snapshots.write_json_atomic",
                side_effect=fail_pointer,
            ),
            self.assertRaisesRegex(OSError, "injected"),
        ):
            self.repository.publish_generation(a2, config_a2)

        self.assertEqual(
            self.repository.active_pointer.read_bytes(),
            pointer_before,
        )
        self.assertEqual(
            self.repository.read_active(required=True).snapshot_id,
            previous.snapshot_id,
        )
        self.assertTrue(
            self.repository.generation_directory(a2.generation_id).is_dir()
        )

    def test_completed_job_replay_returns_committed_generation(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        committed = self.repository.publish_generation(reference, config)
        backend = LocalIndexBackend(
            Mock(),
            Mock(),
            self.repository.layout,
        )
        storage = MagicMock()
        storage.__enter__.return_value = storage
        build = IndexConfig.local(
            video_id="a",
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.indexes,
        )

        with (
            patch.object(
                backend,
                "_open_committed_storage",
                return_value=storage,
            ),
            patch.object(backend, "_validate_snapshot_storage"),
            patch(
                "vidxp.infrastructure.local_index.index_video"
            ) as index_video,
        ):
            result = backend.create(
                Path("video.mp4"),
                config=build,
                progress=None,
                cancellation=None,
                source_name="video.mp4",
                source_checksum="a" * 64,
                operation_id=reference.generation_id,
            )

        self.assertEqual(result["generation_id"], reference.generation_id)
        self.assertEqual(result["snapshot_id"], committed.snapshot_id)
        index_video.assert_not_called()

    def test_completed_generation_replay_publishes_without_rebuilding(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        backend = LocalIndexBackend(
            Mock(),
            Mock(),
            self.repository.layout,
        )
        build = IndexConfig.local(
            video_id="a",
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.indexes,
        )

        with patch(
            "vidxp.infrastructure.local_index.index_video"
        ) as index_video:
            result = backend.create(
                Path("video.mp4"),
                config=build,
                progress=None,
                cancellation=None,
                source_name="video.mp4",
                source_checksum="a" * 64,
                operation_id=reference.generation_id,
            )

        self.assertEqual(result["generation_id"], reference.generation_id)
        self.assertEqual(
            self.repository.read_active(required=True)
            .generations["a"]
            .generation_id,
            reference.generation_id,
        )
        index_video.assert_not_called()

    def test_replay_repairs_counts_after_crash_before_generation_validation(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        manifest_path = (
            self.repository.generation_directory(reference.generation_id)
            / MANIFEST_FILE
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("record_counts")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        backend = LocalIndexBackend(
            Mock(),
            Mock(),
            self.repository.layout,
        )
        build = IndexConfig.local(
            video_id="a",
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.indexes,
        )
        storage = MagicMock()
        storage.__enter__.return_value = storage
        storage.records.return_value = [
            {
                "generation_id": reference.generation_id,
                "video_id": "a",
                "modality": "scene",
            }
        ]

        with (
            patch(
                "vidxp.infrastructure.local_index.IndexStorage",
                return_value=storage,
            ),
            patch(
                "vidxp.infrastructure.local_index.index_video"
            ) as index_video,
        ):
            result = backend.create(
                Path("video.mp4"),
                config=build,
                progress=None,
                cancellation=None,
                source_name="video.mp4",
                source_checksum="a" * 64,
                operation_id=reference.generation_id,
            )

        self.assertEqual(result["record_counts"], {"scene": 1})
        repaired = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(repaired["record_counts"], {"scene": 1})
        index_video.assert_not_called()

    def test_corrupt_or_missing_generation_fails_closed(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        self.repository.publish_generation(reference, config)
        manifest = (
            self.repository.generation_directory(reference.generation_id)
            / MANIFEST_FILE
        )
        manifest.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(IndexSchemaError, "integrity"):
            self.repository.read_active(required=True)

    def test_repository_lease_is_scoped_to_one_repository(self):
        other = LocalSnapshotRepository(
            Path(self.temporary.name) / "other" / "indexes"
        )
        with self.repository.lease():
            self.assertTrue(self.repository.mutation_in_progress())
            self.assertFalse(other.mutation_in_progress())

    def test_snapshot_configuration_moves_between_runtime_devices(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        snapshot = self.repository.publish_generation(reference, config)

        active, loaded = self.repository.active_config(device="cuda")

        self.assertEqual(loaded.snapshot_id, snapshot.snapshot_id)
        self.assertEqual(active.device, "cuda")
        self.assertEqual(len(active.snapshot_sha256), 64)
        self.assertEqual(
            active.fingerprint(),
            snapshot.config_fingerprint,
        )

    def test_historical_snapshot_configuration_reopens_by_checksum(self):
        config_a, reference_a = self.generation("a", input_sha="a" * 64)
        snapshot_a = self.repository.publish_generation(reference_a, config_a)
        active_a, _ = self.repository.active_config(device="cpu")
        config_b, reference_b = self.generation("b", input_sha="b" * 64)
        snapshot_b = self.repository.publish_generation(reference_b, config_b)

        historical = self.repository.config_for_snapshot(
            snapshot_a.snapshot_id,
            snapshot_sha256=active_a.snapshot_sha256,
            device="cuda",
        )

        self.assertNotEqual(snapshot_a.snapshot_id, snapshot_b.snapshot_id)
        self.assertEqual(historical.snapshot_id, snapshot_a.snapshot_id)
        self.assertEqual(
            historical.snapshot_sha256,
            active_a.snapshot_sha256,
        )
        self.assertEqual(historical.device, "cuda")
        self.assertEqual(
            historical.fingerprint(),
            snapshot_a.config_fingerprint,
        )

    def test_profile_mismatch_is_rejected_before_replacing_other_media(self):
        config_a, a = self.generation("a", input_sha="a" * 64)
        self.repository.publish_generation(a, config_a)
        config_b, b = self.generation("b", input_sha="b" * 64)
        self.repository.publish_generation(b, config_b)

        changed = replace(
            config_a,
            frame_stride=2,
            generation_id=self.repository.new_generation_id(),
        )
        changed = replace(
            changed,
            generation_directory=self.repository.generation_directory(
                changed.generation_id
            ),
        )
        changed_ref = self.write_generation(
            changed,
            media_id="a",
            input_sha="c" * 64,
        )
        with self.assertRaisesRegex(IndexSchemaError, "same index profile"):
            self.repository.publish_generation(changed_ref, changed)

    def test_snapshot_documents_are_canonical_json_objects(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        snapshot = self.repository.publish_generation(reference, config)
        payload = json.loads(
            (
                self.repository.snapshots / f"{snapshot.snapshot_id}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(payload["snapshot_id"], snapshot.snapshot_id)

    def test_build_failure_or_cancellation_preserves_active_snapshot(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        active = self.repository.publish_generation(reference, config)
        pointer_before = self.repository.active_pointer.read_bytes()
        backend = LocalIndexBackend(
            Mock(),
            Mock(),
            self.repository.layout,
        )
        build = IndexConfig.local(
            video_id="b" * 64,
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.indexes,
        )

        for failure in (
            RuntimeError("injected build failure"),
            IndexCancelledError("injected cancellation"),
        ):
            storage = MagicMock()
            storage.__enter__.return_value = storage
            storage.count_records.return_value = 0

            def fail_index(*_args, **kwargs):
                write_json_atomic(
                    kwargs["config"].run_directory / MANIFEST_FILE,
                    {"state": "interrupted"},
                )
                raise failure

            with (
                self.subTest(failure=type(failure).__name__),
                patch(
                    "vidxp.infrastructure.local_index.IndexStorage",
                    return_value=storage,
                ),
                patch(
                    "vidxp.infrastructure.local_index.index_video",
                    side_effect=fail_index,
                ),
                self.assertRaises(type(failure)),
            ):
                backend.create(
                    Path("video.mp4"),
                    config=build,
                    progress=None,
                    cancellation=None,
                    source_name=None,
                    source_checksum="b" * 64,
                )

            self.assertEqual(
                self.repository.active_pointer.read_bytes(),
                pointer_before,
            )
            self.assertEqual(
                self.repository.read_active(required=True).snapshot_id,
                active.snapshot_id,
            )
            retained = {
                path.name
                for path in self.repository.generations.iterdir()
                if path.is_dir()
            }
            self.assertEqual(retained, {reference.generation_id})

    def test_reader_can_finish_against_snapshot_resolved_before_reindex(self):
        config_a1, a1 = self.generation("a", input_sha="a" * 64)
        first = self.repository.publish_generation(a1, config_a1)
        old_config, _ = self.repository.active_config(device="cpu")
        config_a2, a2 = self.generation("a", input_sha="b" * 64)
        self.repository.publish_generation(a2, config_a2)
        backend = LocalIndexBackend(
            Mock(),
            Mock(),
            self.repository.layout,
        )
        storage = MagicMock()
        storage.records.return_value = []
        storage.count_records.return_value = 0

        with patch(
            "vidxp.infrastructure.local_index.IndexStorage",
            return_value=storage,
        ):
            scoped_store = backend.open_store(old_config)
            storage.reset_mock()
            with scoped_store as scoped:
                scoped.records("scene")

        self.assertNotEqual(
            self.repository.read_active(required=True).snapshot_id,
            first.snapshot_id,
        )
        storage.records.assert_called_once_with(
            "scene",
            video_id=None,
            generation_ids=(a1.generation_id,),
            filters=None,
        )

    def test_reader_rejects_tampered_resolved_snapshot(self):
        config, reference = self.generation("a", input_sha="a" * 64)
        self.repository.publish_generation(reference, config)
        resolved, _ = self.repository.active_config(device="cpu")
        snapshot_path = (
            self.repository.snapshots / f"{resolved.snapshot_id}.json"
        )
        snapshot_path.write_text("{}\n", encoding="utf-8")
        backend = LocalIndexBackend(
            Mock(),
            Mock(),
            self.repository.layout,
        )

        with self.assertRaisesRegex(IndexSchemaError, "integrity"):
            backend.open_store(resolved)

    def test_reader_fails_closed_after_committed_record_loss(self):
        generation_id = self.repository.new_generation_id()
        config = replace(
            self.config,
            video_id="a",
            generation_id=generation_id,
            generation_directory=self.repository.generation_directory(
                generation_id
            ),
        )
        with IndexStorage(config) as storage:
            storage.upsert(
                "scene",
                [
                    StorageRecord(
                        source_id="source-1",
                        embedding=[1.0, 0.0],
                        metadata={
                            **config.record_identity("scene", "source-1"),
                        },
                    )
                ],
                batch_size=1,
                cancellation=CancellationToken(),
            )
        reference = self.write_generation(
            config,
            media_id="a",
            input_sha="a" * 64,
            record_counts={"scene": 1},
        )
        self.repository.publish_generation(reference, config)
        active, _ = self.repository.active_config(device="cpu")
        runtime = Mock()
        runtime.backends.torch_device = "cpu"
        backend = LocalIndexBackend(
            Mock(),
            runtime,
            self.repository.layout,
        )

        with backend.open_store(active):
            pass
        with IndexStorage(config) as storage:
            storage.delete_generation(generation_id)

        with backend.open_store(active):
            pass
        with self.assertRaisesRegex(IndexSchemaError, "record counts"):
            backend.status(self.repository.indexes)

    def test_cancellation_after_validation_does_not_publish(self):
        config_a, reference_a = self.generation("a", input_sha="a" * 64)
        previous = self.repository.publish_generation(reference_a, config_a)
        pointer_before = self.repository.active_pointer.read_bytes()
        runtime = Mock()
        runtime.backends.torch_device = "cpu"
        backend = LocalIndexBackend(
            Mock(),
            runtime,
            self.repository.layout,
        )
        build = IndexConfig.local(
            video_id="b" * 64,
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.indexes,
        )
        storage = MagicMock()
        storage.__enter__.return_value = storage
        storage.records.return_value = []
        storage.count_records.return_value = 0
        cancellation = Mock()
        cancellation.raise_if_cancelled.side_effect = (
            None,
            None,
            IndexCancelledError("late cancellation"),
        )

        def complete_index(*_args, **kwargs):
            self.write_generation(
                kwargs["config"],
                media_id="b" * 64,
                input_sha="b" * 64,
            )
            return {}

        with (
            patch(
                "vidxp.infrastructure.local_index.IndexStorage",
                return_value=storage,
            ),
            patch(
                "vidxp.infrastructure.local_index.index_video",
                side_effect=complete_index,
            ),
            self.assertRaises(IndexCancelledError),
        ):
            backend.create(
                Path("video.mp4"),
                config=build,
                progress=None,
                cancellation=cancellation,
                source_name=None,
                source_checksum="b" * 64,
            )

        self.assertEqual(
            self.repository.active_pointer.read_bytes(),
            pointer_before,
        )
        self.assertEqual(
            self.repository.read_active(required=True).snapshot_id,
            previous.snapshot_id,
        )

    def test_abandoned_generation_directory_and_records_are_cleaned(self):
        generation_id = self.repository.new_generation_id()
        generation_directory = self.repository.generation_directory(
            generation_id
        )
        write_json_atomic(
            generation_directory / MANIFEST_FILE,
            {"state": "running", "generation_id": generation_id},
        )
        config = IndexConfig.local(
            video_id="a",
            generation_id=generation_id,
            generation_directory=generation_directory,
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
            storage_directory=self.repository.store,
        )
        with IndexStorage(config) as storage:
            storage.upsert(
                "scene",
                [
                    StorageRecord(
                        source_id="abandoned-source",
                        embedding=[1.0],
                        metadata={
                            **config.record_identity(
                                "scene",
                                "abandoned-source",
                            ),
                        },
                    )
                ],
                batch_size=1,
                cancellation=CancellationToken(),
            )

        LocalIndexBackend._cleanup_abandoned_generations(
            self.repository,
            config,
        )

        self.assertFalse(generation_directory.exists())
        with IndexStorage(config, create=False) as storage:
            self.assertEqual(storage.records("scene"), [])

    def test_real_chroma_reader_remains_pinned_across_reindex(self):
        references = []
        for input_sha, embedding in (
            ("a" * 64, [1.0, 0.0]),
            ("b" * 64, [0.0, 1.0]),
        ):
            generation_id = self.repository.new_generation_id()
            config = replace(
                self.config,
                video_id="a",
                generation_id=generation_id,
                generation_directory=self.repository.generation_directory(
                    generation_id
                ),
            )
            with IndexStorage(config) as storage:
                storage.upsert(
                    "scene",
                    [
                        StorageRecord(
                            source_id=f"source-{generation_id}",
                            embedding=embedding,
                            metadata={
                                **config.record_identity(
                                    "scene",
                                    f"source-{generation_id}",
                                ),
                            },
                        )
                    ],
                    batch_size=1,
                    cancellation=CancellationToken(),
                )
            reference = self.write_generation(
                config,
                media_id="a",
                input_sha=input_sha,
                record_counts={"scene": 1},
            )
            snapshot = self.repository.publish_generation(reference, config)
            active_config, _ = self.repository.active_config(device="cpu")
            references.append(
                (reference, snapshot, active_config)
            )

        runtime = Mock()
        runtime.backends.torch_device = "cpu"
        backend = LocalIndexBackend(
            Mock(),
            runtime,
            self.repository.layout,
        )
        first_reference, _, first_config = references[0]
        second_reference, _, second_config = references[1]
        with backend.open_store(first_config) as first_reader:
            first_records = first_reader.records("scene")
        with backend.open_store(second_config) as second_reader:
            second_records = second_reader.records("scene")

        self.assertEqual(
            {record["generation_id"] for record in first_records},
            {first_reference.generation_id},
        )
        self.assertEqual(
            {record["generation_id"] for record in second_records},
            {second_reference.generation_id},
        )


if __name__ == "__main__":
    unittest.main()
