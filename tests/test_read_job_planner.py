import unittest
from unittest.mock import Mock

from vidxp.application_models import (
    CreateActorOverlayCommand,
    QueryVideoCommand,
    SearchCommand,
)
from vidxp.core.contracts import IndexConfig
from vidxp.read_job_planner import LocalReadJobPlanner
from vidxp.repository_layout import RepositoryLayout


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"


class LocalReadJobPlannerTests(unittest.TestCase):
    def setUp(self):
        self.layout = RepositoryLayout(root="unused")
        self.config = IndexConfig.local(
            video_id=MEDIA_ID,
            enabled_modalities=("scene", "actor"),
            snapshot_id=SNAPSHOT_ID,
            snapshot_sha256="a" * 64,
            storage_directory=self.layout.index_store,
            collection_names={"scene": "scene", "actor": "actor"},
        )
        self.index = Mock()
        self.index.active_config.return_value = self.config
        self.planner = LocalReadJobPlanner(
            layout=self.layout,
            index=self.index,
        )

    def test_search_job_carries_only_the_logical_snapshot_reference(self):
        request = self.planner.plan_search(
            SearchCommand(modalities=("scene",), query="a taxi"),
        )

        self.assertEqual(request.snapshot.snapshot_id, SNAPSHOT_ID)
        self.assertEqual(request.snapshot.snapshot_sha256, "a" * 64)
        self.assertNotIn(
            "storage_directory",
            request.model_dump(mode="json"),
        )
        self.index.open_store.assert_not_called()

    def test_actor_job_pins_snapshot_without_opening_vector_storage(self):
        request = self.planner.plan_actor_overlay(
            CreateActorOverlayCommand(cluster_id="actor-1"),
        )

        self.assertEqual(request.snapshot.snapshot_id, SNAPSHOT_ID)
        self.index.open_store.assert_not_called()

    def test_query_job_carries_the_same_logical_snapshot_reference(self):
        request = self.planner.plan_query(
            QueryVideoCommand(
                question="What happens?",
                modalities=("scene",),
            )
        )

        self.assertEqual(request.command.modalities, ("scene",))
        self.assertEqual(request.snapshot.snapshot_id, SNAPSHOT_ID)
        self.assertEqual(request.snapshot.snapshot_sha256, "a" * 64)
        self.index.open_store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
