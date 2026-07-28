import unittest
from unittest.mock import Mock

from vidxp.capabilities.actor.results import (
    ActorClusterNotFoundError,
    actor_clusters,
    actor_detections,
)
from vidxp.capabilities.actor.schemas import ActorDetectionsInput
from vidxp.core.contracts import IndexConfig
from vidxp.capabilities.contracts import CapabilityRequestError


MEDIA_ID = "123456781234423481234567890abcde"
OTHER_MEDIA_ID = "223456781234423481234567890abcde"
GENERATION_ID = "323456781234423481234567890abcde"


class ActorResultTests(unittest.TestCase):
    def setUp(self):
        self.config = IndexConfig.local(video_id=MEDIA_ID)

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
                "video_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "modality": "actor",
                "source_id": "actor:d2",
            }
        ]

        page = actor_detections(
            self.config,
            "3",
            storage=storage,
            page_size=50,
            cursor=None,
        )

        self.assertEqual(page.detections[0].bbox, (1, 4, 5, 0))
        storage.records.assert_called_once_with(
            "actor",
            filters={"cluster_id": "3"},
            limit=1000,
            offset=0,
        )

    def test_actor_clusters_summarize_detection_ranges(self):
        storage = Mock()
        storage.records.return_value = [
            {
                "cluster_id": "cluster-1",
                "video_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "timestamp": 4.5,
            },
            {
                "cluster_id": "cluster-1",
                "video_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "timestamp": 1.5,
            },
            {
                "cluster_id": "cluster-2",
                "video_id": OTHER_MEDIA_ID,
                "generation_id": GENERATION_ID,
                "timestamp": 9.0,
            },
        ]

        page = actor_clusters(
            self.config,
            storage=storage,
            page_size=50,
            cursor=None,
        )
        clusters = page.clusters

        self.assertEqual(
            [cluster.cluster_id for cluster in clusters],
            ["cluster-1", "cluster-2"],
        )
        self.assertEqual(clusters[0].detection_count, 2)
        self.assertEqual(clusters[0].first_timestamp, 1.5)
        self.assertEqual(clusters[0].last_timestamp, 4.5)
        self.assertEqual(clusters[1].media_id, OTHER_MEDIA_ID)
        storage.records.assert_called_once_with(
            "actor",
            limit=1000,
            offset=0,
        )

    def test_actor_clusters_reject_cross_media_identity_collision(self):
        storage = Mock()
        storage.records.return_value = [
            {
                "cluster_id": "legacy-cluster",
                "video_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "timestamp": 1.0,
            },
            {
                "cluster_id": "legacy-cluster",
                "video_id": OTHER_MEDIA_ID,
                "generation_id": GENERATION_ID,
                "timestamp": 2.0,
            },
        ]

        with self.assertRaisesRegex(RuntimeError, "multiple index identities"):
            actor_clusters(
                self.config,
                storage=storage,
                page_size=50,
                cursor=None,
            )

    def test_actor_detection_cursor_round_trips_through_public_input(self):
        cluster_id = "cluster-" + ("a" * 64)
        storage = Mock()
        storage.records.return_value = [
            {
                "detection_id": f"detection-{index}",
                "cluster_id": cluster_id,
                "frame_index": index,
                "timestamp": index / 10,
                "bbox_top": 1,
                "bbox_right": 4,
                "bbox_bottom": 5,
                "bbox_left": 0,
                "dataset": "local",
                "split": "local",
                "run_id": "default",
                "video_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "modality": "actor",
                "source_id": f"actor:detection-{index}",
            }
            for index in range(3)
        ]

        first = actor_detections(
            self.config,
            cluster_id,
            storage=storage,
            page_size=2,
            cursor=None,
        )

        self.assertIsNotNone(first.next_cursor)
        self.assertGreater(len(first.next_cursor or ""), 128)
        request = ActorDetectionsInput(
            cluster_id=cluster_id,
            page_size=2,
            cursor=first.next_cursor,
        )
        second = actor_detections(
            self.config,
            request.cluster_id,
            storage=storage,
            page_size=request.page_size,
            cursor=request.cursor,
        )

        self.assertEqual(
            [detection.detection_id for detection in second.detections],
            ["detection-2"],
        )
        self.assertIsNone(second.next_cursor)

    def test_actor_cluster_pages_use_opaque_cursors(self):
        storage = Mock()
        storage.records.return_value = [
            {
                "cluster_id": f"cluster-{index}",
                "video_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "timestamp": float(index),
            }
            for index in range(3)
        ]
        first = actor_clusters(
            self.config,
            storage=storage,
            page_size=2,
            cursor=None,
        )
        second = actor_clusters(
            self.config,
            storage=storage,
            page_size=2,
            cursor=first.next_cursor,
        )

        self.assertEqual(first.total, 3)
        self.assertEqual(len(first.clusters), 2)
        self.assertIsNotNone(first.next_cursor)
        self.assertEqual(
            [cluster.cluster_id for cluster in second.clusters],
            ["cluster-2"],
        )
        self.assertIsNone(second.next_cursor)

        with self.assertRaises(CapabilityRequestError):
            actor_detections(
                self.config,
                "cluster-2",
                storage=storage,
                page_size=2,
                cursor=first.next_cursor,
            )

    def test_actor_detections_rejects_an_empty_cluster(self):
        storage = Mock()
        storage.records.return_value = []

        with self.assertRaises(ActorClusterNotFoundError):
            actor_detections(
                self.config,
                "missing",
                storage=storage,
                page_size=50,
                cursor=None,
            )


if __name__ == "__main__":
    unittest.main()
