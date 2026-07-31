import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from vidxp.application_models import (
    ApplicationError,
    CapabilityIdentityMode,
    CapabilityRole,
    CreateIndexCommand,
    ListMediaCommand,
    MediaAsset,
    MediaPage,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capability_service import CapabilityService
from vidxp.control_plane import ControlPlaneApplication
from vidxp.core.media import MediaState, MediaStream
from vidxp.core.snapshots import GenerationReference, IndexSnapshot
from vidxp.repository_layout import RepositoryLayout


MEDIA_ID = "123456781234423481234567890abcde"
OTHER_MEDIA_ID = "223456781234423481234567890abcde"
GENERATION_ID = "323456781234423481234567890abcde"
SNAPSHOT_ID = "423456781234423481234567890abcde"


def media_asset(media_id: str, filename: str) -> MediaAsset:
    return MediaAsset(
        media_id=media_id,
        video_id=media_id,
        original_filename=filename,
        sha256="a" * 64,
        byte_size=10,
        detected_mime_type="video/mp4",
        container="mp4",
        duration_seconds=2,
        streams=(
            MediaStream(
                index=0,
                kind="video",
                codec="h264",
                width=1,
                height=1,
            ),
        ),
        state=MediaState.ready,
        created_at=datetime.now(timezone.utc),
    )


class ControlPlaneWorkspaceTests(unittest.TestCase):
    def test_index_preflight_rejects_unknown_capability_with_next_action(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            media = Mock()
            application = ControlPlaneApplication(
                layout=RepositoryLayout(root=root),
                capabilities=CapabilityService(create_capability_registry()),
                media=media,
                artifacts=Mock(),
                index_status=lambda: None,
                model_cache=root / "models",
            )

            with self.assertRaises(ApplicationError) as raised:
                application.preflight_index(
                    CreateIndexCommand(
                        media_id=MEDIA_ID,
                        modalities=("unknown",),
                    )
                )

        error = raised.exception.to_dict()["details"]["errors"][0]
        self.assertEqual(error["reason"], "capability_unknown")
        self.assertEqual(error["requested"], ["unknown"])
        self.assertIn("get_workspace", error["next_action"])
        media.get.assert_not_called()

    def test_workspace_projects_index_coverage_roles_and_next_actions(self):
        indexed = media_asset(MEDIA_ID, "indexed.mp4")
        unindexed = media_asset(OTHER_MEDIA_ID, "new.mp4")
        snapshot = IndexSnapshot(
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
                    record_counts={"scene": 12, "actor": 4},
                    store_size_bytes_at_commit=100,
                )
            },
        )
        media = Mock()
        media.list.return_value = MediaPage(
            items=(indexed, unindexed),
            total=2,
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            application = ControlPlaneApplication(
                layout=RepositoryLayout(root=root),
                capabilities=CapabilityService(create_capability_registry()),
                media=media,
                artifacts=Mock(),
                index_status=lambda: {
                    "schema_version": 2,
                    "state": "ready",
                    "stage": "status",
                    "message": "Index ready.",
                },
                active_snapshot=lambda: snapshot,
                model_cache=root / "models",
            )

            workspace = application.workspace(ListMediaCommand())

        self.assertEqual(workspace.media_total, 2)
        self.assertEqual(
            workspace.next_actions,
            ("index_media", "find_moments", "answer_video"),
        )
        indexed_projection = workspace.media[0]
        self.assertTrue(indexed_projection.in_active_snapshot)
        by_name = {
            capability.name: capability
            for capability in indexed_projection.capabilities
        }
        self.assertEqual(by_name["scene"].record_count, 12)
        self.assertEqual(
            by_name["scene"].roles,
            (CapabilityRole.searchable, CapabilityRole.queryable),
        )
        self.assertEqual(by_name["actor"].record_count, 4)
        self.assertEqual(
            by_name["actor"].identity_mode,
            CapabilityIdentityMode.anonymous_clusters,
        )
        self.assertEqual(workspace.media[1].capabilities[0].roles, ())


if __name__ == "__main__":
    unittest.main()
