import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
)
from vidxp.core.storage import IndexStorage, metadata_filter


class ChromaStorageIntegrationTests(unittest.TestCase):
    def test_two_videos_can_coexist_query_and_delete_by_video(self):
        with TemporaryDirectory() as directory:
            config = IndexConfig(
                dataset="sample",
                split="test",
                run_id="run-1",
                enabled_modalities=("scene",),
                storage_directory=directory,
            )
            with IndexStorage(config) as storage:
                records = [
                    StorageRecord(
                        source_id=f"run-1:{video_id}:scene:f0",
                        embedding=embedding,
                        metadata={
                            "dataset": "sample",
                            "split": "test",
                            "run_id": "run-1",
                            "video_id": video_id,
                            "modality": "scene",
                            "source_id": f"run-1:{video_id}:scene:f0",
                            "frame_index": 0,
                            "timestamp": 0.0,
                            "start": 0.0,
                            "end": 1.0,
                            "fps": 1.0,
                            "duration": 1.0,
                        },
                    )
                    for video_id, embedding in (
                        ("video-1", [1.0, 0.0]),
                        ("video-2", [0.0, 1.0]),
                    )
                ]
                storage.upsert(
                    "scene",
                    records,
                    batch_size=2,
                    cancellation=CancellationToken(),
                )

                filtered = storage.query(
                    "scene",
                    [1.0, 0.0],
                    top_k=2,
                    video_id="video-1",
                )
                self.assertEqual(
                    [row["metadata"]["video_id"] for row in filtered],
                    ["video-1"],
                )

                storage.delete_video("scene", "video-1")
                remaining = storage.collection("scene").get(
                    where=metadata_filter(config),
                    include=["metadatas"],
                )
                self.assertEqual(
                    [item["video_id"] for item in remaining["metadatas"]],
                    ["video-2"],
                )

    def test_generation_scope_and_cleanup_use_chroma_in_filter(self):
        with TemporaryDirectory() as directory:
            config = IndexConfig(
                dataset="sample",
                split="test",
                run_id="run-1",
                enabled_modalities=("scene",),
                storage_directory=directory,
            )
            with IndexStorage(config) as storage:
                storage.upsert(
                    "scene",
                    [
                        StorageRecord(
                            source_id=f"source-{index}",
                            embedding=embedding,
                            metadata={
                                "dataset": "sample",
                                "split": "test",
                                "run_id": "run-1",
                                "video_id": f"video-{index}",
                                "generation_id": generation_id,
                            },
                        )
                        for index, generation_id, embedding in (
                            (1, "generation-1", [1.0, 0.0]),
                            (2, "generation-2", [0.0, 1.0]),
                        )
                    ],
                    batch_size=2,
                    cancellation=CancellationToken(),
                )

                scoped = storage.query(
                    "scene",
                    [1.0, 0.0],
                    top_k=2,
                    generation_ids=("generation-1",),
                )
                self.assertEqual(
                    [
                        row["metadata"]["generation_id"]
                        for row in scoped
                    ],
                    ["generation-1"],
                )

                storage.delete_generation(
                    "generation-1",
                    modalities=("scene",),
                )
                remaining = storage.records("scene")
                self.assertEqual(
                    [item["generation_id"] for item in remaining],
                    ["generation-2"],
                )

    def test_read_only_store_fails_closed_without_database_or_collection(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            config = IndexConfig(
                dataset="sample",
                split="test",
                run_id="run-1",
                enabled_modalities=("scene",),
                storage_directory=path / "missing",
            )
            with self.assertRaises(FileNotFoundError):
                IndexStorage(config, create=False)
            self.assertFalse(config.index_directory.exists())

            with IndexStorage(config) as storage:
                storage.collection("scene")
            read_only = IndexStorage(config, create=False)
            read_only.client.delete_collection("scene")
            with (
                self.assertRaises(FileNotFoundError),
                read_only,
            ):
                read_only.records("scene")


if __name__ == "__main__":
    unittest.main()
