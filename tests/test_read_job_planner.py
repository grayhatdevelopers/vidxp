import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from vidxp.application_models import (
    ApplicationError,
    CreateActorOverlayCommand,
    QueryVideoCommand,
    SearchCommand,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.contracts import IndexConfig
from vidxp.core.snapshots import GenerationReference, IndexSnapshot
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
        self.snapshot = IndexSnapshot(
            snapshot_id=SNAPSHOT_ID,
            created_at=datetime.now(timezone.utc),
            config_fingerprint="b" * 64,
            configuration={},
            generations={
                MEDIA_ID: GenerationReference(
                    generation_id=GENERATION_ID,
                    media_id=MEDIA_ID,
                    manifest_sha256="c" * 64,
                    input_sha256="d" * 64,
                    config_fingerprint="e" * 64,
                    modalities=("scene", "actor"),
                    record_counts={"scene": 2, "actor": 1},
                    store_size_bytes_at_commit=100,
                )
            },
        )
        self.index = Mock()
        self.index.active_snapshot.return_value = (
            self.config,
            self.snapshot,
        )
        self.planner = LocalReadJobPlanner(
            layout=self.layout,
            registry=create_capability_registry(),
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

    def test_omitted_search_capabilities_exclude_non_searchable_actor(self):
        request = self.planner.plan_search(SearchCommand(query="a taxi"))

        self.assertEqual(request.command.modalities, ("scene",))

    def test_explicit_actor_search_fails_before_a_job_can_be_submitted(self):
        with self.assertRaises(ApplicationError) as raised:
            self.planner.plan_search(
                SearchCommand(modalities=("actor",), query="Harry")
            )

        error = raised.exception.to_dict()["details"]
        self.assertEqual(error["errors"][0]["reason"], "capability_role_unsupported")
        self.assertEqual(error["errors"][0]["requested"], ["actor"])
        self.assertEqual(error["errors"][0]["available"], ["scene"])

    def test_actor_remains_available_to_grounded_query(self):
        request = self.planner.plan_query(
            QueryVideoCommand(
                question="When does this actor appear?",
                modalities=("actor",),
            )
        )

        self.assertEqual(request.command.modalities, ("actor",))

    def test_media_outside_active_snapshot_fails_during_planning(self):
        with self.assertRaises(ApplicationError) as raised:
            self.planner.plan_search(
                SearchCommand(
                    media_id="423456781234423481234567890abcde",
                    query="a taxi",
                )
            )

        error = raised.exception.to_dict()["details"]["errors"][0]
        self.assertEqual(error["reason"], "media_not_indexed")
        self.assertEqual(error["available"], [MEDIA_ID])


if __name__ == "__main__":
    unittest.main()
