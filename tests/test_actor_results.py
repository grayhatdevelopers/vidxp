import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock, patch

from vidxp.capabilities.actor.results import (
    ActorClusterNotFoundError,
    actor_clusters,
    actor_detections,
    render_actor_result,
)
from vidxp.core.contracts import IndexConfig


class ActorResultTests(unittest.TestCase):
    def setUp(self):
        self.config = IndexConfig.local(video_id="video-1")

    def test_actor_detection_metadata_is_converted_once(self):
        storage = Mock()
        storage.records.return_value = [
            {
                "detection_id": "d2",
                "cluster_id": "3",
                "frame_index": 2,
                "timestamp": 0.2,
                "bbox_top": 1,
                "bbox_right": 4,
                "bbox_bottom": 5,
                "bbox_left": 0,
                "dataset": "local",
                "split": "local",
                "run_id": "default",
                "video_id": "video-1",
                "modality": "actor",
                "source_id": "actor:d2",
            }
        ]

        detections = actor_detections(
            self.config,
            "3",
            storage=storage,
        )

        self.assertEqual(detections[0].bbox, (1, 4, 5, 0))
        storage.records.assert_called_once_with(
            "actor",
            video_id="video-1",
            filters={"cluster_id": "3"},
        )
        storage.close.assert_not_called()

    def test_actor_clusters_summarize_detection_ranges(self):
        storage = Mock()
        storage.records.return_value = [
            {"cluster_id": "1", "timestamp": 4.5},
            {"cluster_id": "1", "timestamp": 1.5},
            {"cluster_id": "2", "timestamp": 9.0},
        ]

        clusters = actor_clusters(self.config, storage=storage)

        self.assertEqual(
            [cluster.cluster_id for cluster in clusters],
            ["1", "2"],
        )
        self.assertEqual(clusters[0].detection_count, 2)
        self.assertEqual(clusters[0].first_timestamp, 1.5)
        self.assertEqual(clusters[0].last_timestamp, 4.5)

    def test_render_actor_result_rejects_an_empty_cluster(self):
        storage = Mock()
        storage.records.return_value = []

        with self.assertRaises(ActorClusterNotFoundError):
            render_actor_result(
                self.config,
                "missing",
                "input.mp4",
                "output.mp4",
                storage=storage,
            )

    def test_actor_detections_rejects_an_empty_cluster(self):
        storage = Mock()
        storage.records.return_value = []

        with self.assertRaises(ActorClusterNotFoundError):
            actor_detections(
                self.config,
                "missing",
                storage=storage,
            )

    def test_render_actor_result_returns_output_details(self):
        storage = Mock()
        storage.records.return_value = [
            {
                "detection_id": "d2",
                "cluster_id": "3",
                "frame_index": 2,
                "timestamp": 0.2,
                "bbox_top": 1,
                "bbox_right": 4,
                "bbox_bottom": 5,
                "bbox_left": 0,
                "dataset": "local",
                "split": "local",
                "run_id": "default",
                "video_id": "video-1",
                "modality": "actor",
                "source_id": "actor:d2",
            }
        ]
        with TemporaryDirectory() as directory:
            output = Path(directory) / "actor.mp4"
            with patch(
                "vidxp.capabilities.actor.results.render_actor_video"
            ) as renderer:
                result = render_actor_result(
                    self.config,
                    "3",
                    "input.mp4",
                    output,
                    storage=storage,
                )

        renderer.assert_called_once()
        self.assertEqual(result.output_path, output)
        self.assertEqual(result.detection_count, 1)


if __name__ == "__main__":
    unittest.main()
