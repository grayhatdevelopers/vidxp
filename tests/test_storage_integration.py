import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
)
from vidxp.core.storage import IndexStorage, metadata_filter
from vidxp.capabilities.actor.config import actor_config
from vidxp.capabilities.actor.indexing import ActorIndexState, finalize_actor_index


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


    def test_actor_finalization_persists_cluster_summary_with_matching_embedding_dimension(self):
        import numpy as np
        from unittest.mock import Mock

        with TemporaryDirectory() as directory:
            config = IndexConfig(
                dataset="sample",
                split="test",
                run_id="actors",
                video_id="video-1",
                generation_id="generation-1",
                enabled_modalities=("actor",),
                storage_directory=directory,
            )

            centroid = np.zeros(128, dtype="float32")
            centroid[0] = 1.0

            cluster_id = "generation-1:actors:video-1:actor-cluster:1"

            state = ActorIndexState(
                models=Mock(),
                known_ids=[cluster_id],
                known_encodings=[centroid],
                cluster_sizes={cluster_id: 4},
                cluster_ranges={cluster_id: (1.0, 3.0)},
            )

            with IndexStorage(config) as storage:
                storage.upsert(
                    "actor",
                    [
                        StorageRecord(
                            source_id="detection-1",
                            embedding=centroid.tolist(),
                            metadata={
                                **config.record_identity(
                                    "actor", "detection-1"
                                ),
                                "detection_id": "detection-1",
                                "cluster_id": cluster_id,
                                "frame_index": 0,
                                "timestamp": 1.0,
                                "bbox_top": 0,
                                "bbox_right": 10,
                                "bbox_bottom": 10,
                                "bbox_left": 0,
                            },
                        )
                    ],
                    batch_size=1,
                    cancellation=CancellationToken(),
                )

                finalize_actor_index(
                    state,
                    config=config,
                    storage=storage,
                )

                result = storage.collection("actor").get(
                    include=["embeddings", "metadatas"],
                )

                summary_embeddings = [
                    embedding
                    for embedding, metadata in zip(
                        result["embeddings"],
                        result["metadatas"],
                    )
                    if metadata.get("record_kind") == "cluster_summary"
                ]

                self.assertEqual(len(summary_embeddings), 1)
                self.assertEqual(len(summary_embeddings[0]), 128)
                self.assertEqual(list(summary_embeddings[0]), centroid.tolist())

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
