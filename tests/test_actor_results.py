import unittest
from unittest.mock import Mock

from vidxp.capabilities.actor.results import (
    ActorClusterNotFoundError,
    actor_cluster,
    actor_clusters,
    actor_detections,
)
from vidxp.capabilities.actor.schemas import ActorDetectionsInput
from vidxp.core.contracts import IndexConfig
from vidxp.core.cursors import encode_cursor
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
            limit=51,
            offset=0,
        )

    def test_actor_clusters_summarize_detection_ranges(self):
        storage = Mock()
        legacy_records = [
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
        storage.records.side_effect = [[], legacy_records]

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
        self.assertEqual(
            storage.records.call_args_list[0].kwargs,
            {
                "filters": {"record_kind": "cluster_summary"},
                "limit": 51,
                "offset": 0,
            },
        )
        self.assertEqual(
            storage.records.call_args_list[1].kwargs,
            {"limit": 1000, "offset": 0},
        )

    def test_actor_cluster_reads_only_the_selected_cluster_once(self):
        storage = Mock()
        detection_records = [
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
        ]
        storage.records.side_effect = [[], detection_records]

        summary = actor_cluster(
            self.config,
            "cluster-1",
            storage=storage,
        )

        self.assertEqual(summary.cluster_id, "cluster-1")
        self.assertEqual(summary.detection_count, 2)
        self.assertEqual(summary.first_timestamp, 1.5)
        self.assertEqual(summary.last_timestamp, 4.5)
        self.assertEqual(
            storage.records.call_args_list[0].kwargs,
            {
                "filters": {
                    "record_kind": "cluster_summary",
                    "summary_cluster_id": "cluster-1",
                },
                "limit": 2,
            },
        )
        self.assertEqual(
            storage.records.call_args_list[1].kwargs,
            {"filters": {"cluster_id": "cluster-1"}},
        )

    def test_actor_cluster_uses_the_typed_not_found_error(self):
        storage = Mock()
        storage.records.return_value = []

        with self.assertRaises(ActorClusterNotFoundError):
            actor_cluster(
                self.config,
                "missing",
                storage=storage,
            )

    def test_actor_clusters_reject_cross_media_identity_collision(self):
        storage = Mock()
        legacy_records = [
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
        storage.records.side_effect = [[], legacy_records]

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
        records = [
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
        storage.records.side_effect = lambda *args, **options: records[
            options["offset"] : options["offset"] + options["limit"]
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
        self.assertEqual(
            [call.kwargs["offset"] for call in storage.records.call_args_list],
            [0, 2],
        )
        self.assertEqual(
            [call.kwargs["limit"] for call in storage.records.call_args_list],
            [3, 3],
        )

    def test_actor_detection_cursor_rejects_noncanonical_payloads(self):
        cluster_id = "cluster-1"
        scope = (
            f"actor:detections:{self.config.fingerprint()}:{cluster_id}"
        )
        storage = Mock()

        for position in (
            {"offset": True},
            {"offset": 0, "after": [0, "detection-1"]},
        ):
            with self.subTest(position=position):
                with self.assertRaises(CapabilityRequestError):
                    actor_detections(
                        self.config,
                        cluster_id,
                        storage=storage,
                        page_size=2,
                        cursor=encode_cursor(scope, position),
                    )
        storage.records.assert_not_called()

    def test_actor_cluster_pages_use_opaque_cursors(self):
        storage = Mock()
        records = [
            {
                "record_kind": "cluster_summary",
                "summary_cluster_id": f"cluster-{index}",
                "video_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "detection_count": index + 1,
                "first_timestamp": float(index),
                "last_timestamp": float(index) + 0.5,
            }
            for index in range(3)
        ]
        storage.records.side_effect = lambda *args, **options: records[
            options["offset"] : options["offset"] + options["limit"]
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

        self.assertIsNone(first.total)
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
