import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import httpx
from chromadb.errors import (
    InvalidArgumentError,
    InvalidDimensionException,
    VersionMismatchError,
)

from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
)
from vidxp.core.storage import (
    IndexStorage,
    IndexStorageUnavailableError,
    SnapshotScopedIndexStore,
    directory_size,
    metadata_filter,
)


class FakeCollection:
    def __init__(self):
        self.upserts = []
        self.deletes = []
        self.query_options = None
        self.get_options = None

    def upsert(self, **options):
        self.upserts.append(options)

    def delete(self, **options):
        self.deletes.append(options)

    def query(self, **options):
        self.query_options = options
        return {
            "ids": [["source-1"]],
            "metadatas": [[{"video_id": "video-1"}]],
            "distances": [[0.25]],
        }

    def get(self, **options):
        self.get_options = options
        return {
            "ids": ["source-3", "source-1"],
            "metadatas": [
                {
                    "frame_index": 3,
                    "detection_id": "d3",
                    "cluster_id": "1",
                },
                {
                    "frame_index": 1,
                    "detection_id": "d1",
                    "cluster_id": "1",
                },
            ]
        }


class FakeClient:
    def __init__(self, collection):
        self.value = collection
        self.collection_options = None
        self.collection_calls = 0

    def get_or_create_collection(self, **options):
        self.collection_calls += 1
        self.collection_options = options
        return self.value


def fake_storage(config, collection):
    storage = object.__new__(IndexStorage)
    storage.config = config
    storage.path = config.index_directory
    storage.client = FakeClient(collection)
    storage._client_factory = MagicMock(remote=False)
    storage._create = True
    storage._collections = {}
    storage._names = {
        "speech": "speech",
        "scene": "scene",
        "actor": "actor",
    }
    return storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.config = IndexConfig(
            dataset="didemo",
            split="test",
            run_id="run-1",
            enabled_modalities=("scene",),
        )

    def test_remote_transport_failures_are_retryable_storage_failures(self):
        storage = object.__new__(IndexStorage)
        storage._client_factory = MagicMock(remote=True)

        with self.assertRaises(IndexStorageUnavailableError):
            storage._call(
                MagicMock(side_effect=httpx.ConnectError("offline"))
            )

    def test_remote_validation_failures_retain_their_original_type(self):
        storage = object.__new__(IndexStorage)
        storage._client_factory = MagicMock(remote=True)

        for failure in (
            InvalidArgumentError("invalid filter"),
            InvalidDimensionException("wrong embedding dimensions"),
            VersionMismatchError("incompatible client and server"),
            ValueError("invalid schema"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)):
                    storage._call(MagicMock(side_effect=failure))

    def test_read_only_storage_uses_the_public_collection_api(self):
        with TemporaryDirectory() as directory:
            client = MagicMock()
            client.list_collections.return_value = ["scene"]
            factory = MagicMock(remote=False)
            factory.create.return_value = client

            with IndexStorage(
                replace(self.config, storage_directory=directory),
                create=False,
                client_factory=factory,
            ):
                pass

            client.list_collections.assert_called_once_with()

    def test_remote_store_size_is_unknown(self):
        storage = object.__new__(IndexStorage)
        storage._client_factory = MagicMock(remote=True)

        self.assertIsNone(storage.size_bytes())

    def test_upserts_are_split_into_declared_write_batches(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)
        records = [
            StorageRecord(
                source_id=f"source-{index}",
                embedding=[float(index)],
                metadata={"index": index},
            )
            for index in range(5)
        ]

        stored = storage.upsert(
            "scene",
            records,
            batch_size=2,
            cancellation=CancellationToken(),
        )

        self.assertEqual(stored, 5)
        self.assertEqual(
            [len(call["ids"]) for call in collection.upserts],
            [2, 2, 1],
        )
        self.assertEqual(
            storage.client.collection_options["metadata"],
            {"hnsw:space": "l2"},
        )

        storage.upsert(
            "scene",
            records[:1],
            batch_size=1,
            cancellation=CancellationToken(),
        )
        self.assertEqual(storage.client.collection_calls, 1)

    def test_query_requests_distances_and_applies_run_and_video_filter(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)

        rows = storage.query(
            "scene",
            [0.1, 0.2],
            top_k=7,
            video_id="video-1",
        )

        self.assertEqual(rows[0]["raw_distance"], 0.25)
        self.assertEqual(
            collection.query_options["include"],
            ["metadatas", "distances"],
        )
        self.assertEqual(collection.query_options["n_results"], 7)
        clauses = collection.query_options["where"]["$and"]
        self.assertIn({"video_id": "video-1"}, clauses)
        self.assertIn({"run_id": "run-1"}, clauses)

    def test_query_and_records_scope_to_generation_ids(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)
        generation_ids = ("generation-1", "generation-2")

        storage.query(
            "scene",
            [0.1, 0.2],
            top_k=7,
            generation_ids=generation_ids,
        )
        query_clauses = collection.query_options["where"]["$and"]
        self.assertIn(
            {
                "generation_id": {
                    "$in": ["generation-1", "generation-2"]
                }
            },
            query_clauses,
        )

        storage.records(
            "scene",
            generation_ids=generation_ids,
        )
        record_clauses = collection.get_options["where"]["$and"]
        self.assertIn(
            {
                "generation_id": {
                    "$in": ["generation-1", "generation-2"]
                }
            },
            record_clauses,
        )

    def test_empty_generation_scope_returns_no_records(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)

        self.assertEqual(
            storage.query(
                "scene",
                [0.1, 0.2],
                top_k=7,
                generation_ids=(),
            ),
            [],
        )
        self.assertEqual(
            storage.records("scene", generation_ids=()),
            [],
        )
        self.assertIsNone(collection.query_options)
        self.assertIsNone(collection.get_options)

    def test_records_apply_capability_filters(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)

        records = storage.records(
            "actor",
            video_id="video-1",
            filters={"cluster_id": "1"},
        )

        self.assertEqual(
            [item["detection_id"] for item in records],
            ["d3", "d1"],
        )
        clauses = collection.get_options["where"]["$and"]
        self.assertIn({"run_id": "run-1"}, clauses)
        self.assertIn({"video_id": "video-1"}, clauses)
        self.assertIn({"cluster_id": "1"}, clauses)

    def test_count_records_uses_ids_without_loading_metadata(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)

        count = storage.count_records(
            "scene",
            video_id="video-1",
            generation_ids=("generation-1",),
        )

        self.assertEqual(count, 2)
        self.assertEqual(collection.get_options["include"], [])

    def test_record_cleanup_remains_scoped_to_capability_and_run(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)

        storage.delete_records(
            "actor",
            video_id="video-1",
            filters={"cluster_id": "3"},
        )

        clauses = collection.deletes[0]["where"]["$and"]
        self.assertIn({"run_id": "run-1"}, clauses)
        self.assertIn({"video_id": "video-1"}, clauses)
        self.assertIn({"cluster_id": "3"}, clauses)

    def test_generation_cleanup_uses_generation_filter(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)

        storage.delete_generation("generation-1", modalities=("scene",))

        clauses = collection.deletes[0]["where"]["$and"]
        self.assertIn(
            {"generation_id": {"$in": ["generation-1"]}},
            clauses,
        )
        self.assertIn({"run_id": "run-1"}, clauses)

    def test_directory_size_only_counts_files_under_requested_path(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "a.bin").write_bytes(b"12")
            (root / "nested" / "b.bin").write_bytes(b"345")

            self.assertEqual(directory_size(root), 5)

    def test_metadata_filter_keeps_run_identity_without_video_filter(self):
        where = metadata_filter(self.config)
        self.assertNotIn({"video_id": None}, where["$and"])

    def test_snapshot_scope_delegates_reads_and_lifecycle(self):
        store = MagicMock(spec=IndexStorage)
        store.query.return_value = [{"source_id": "source-1"}]
        store.records.return_value = [{"source_id": "source-1"}]
        store.size_bytes.return_value = 42
        scoped = SnapshotScopedIndexStore(
            store,
            ("generation-1", "generation-2", "generation-1"),
        )

        with scoped as entered:
            self.assertIs(entered, scoped)
            self.assertEqual(
                scoped.query("scene", [0.1], top_k=2),
                [{"source_id": "source-1"}],
            )
            self.assertEqual(
                scoped.records("scene"),
                [{"source_id": "source-1"}],
            )
            self.assertEqual(scoped.size_bytes(), 42)

        store.__enter__.assert_called_once_with()
        store.__exit__.assert_called_once()
        store.query.assert_called_once_with(
            "scene",
            [0.1],
            top_k=2,
            video_id=None,
            generation_ids=("generation-1", "generation-2"),
            filters=None,
        )
        store.records.assert_called_once_with(
            "scene",
            video_id=None,
            generation_ids=("generation-1", "generation-2"),
            filters=None,
        )

        scoped.close()
        store.close.assert_called_once_with()

    def test_snapshot_scope_exposes_no_mutation_api(self):
        scoped = SnapshotScopedIndexStore(
            MagicMock(spec=IndexStorage),
            ("generation-1",),
        )
        for method in (
            "clear",
            "delete_video",
            "delete_records",
            "delete_generation",
            "upsert",
        ):
            self.assertFalse(hasattr(scoped, method))

    def test_explicit_empty_generation_cleanup_selects_nothing(self):
        collection = FakeCollection()
        storage = fake_storage(self.config, collection)

        storage.delete_generation("generation-1", modalities=())

        self.assertEqual(collection.deletes, [])


if __name__ == "__main__":
    unittest.main()
